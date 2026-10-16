"""审批留痕（§3.2）—— **谁、何时、为什么** 三要素里，本模块负责**「谁」**。

**为什么必须落 PG，不能只在内存**：待接管队列（`pending_approvals.py`）是**进程内存**，
   重启即空；而 `MemorySaver` 重启后**图也没了**。两边一起消失 ⇒ **事后什么都查不到**。
   留痕要活过重启，只能落库。

**为什么不塞进 `pending_approvals.py`**：那个模块的 docstring 全篇在论证
   「**为什么必须是内存**」，往里加 PG 写会把它变成"两种存储各说一半"的模块。

本模块是 **fail-open** 的：写/读失败一律**打一行日志继续** ——
   留痕是**旁路**，不该让「批准」这个动作 500，也不该让页面整页崩。

表形状 ⇒ 下面 `_DDL` · spec ⇒ `app/agent/specs/approval_audit.md`
"""
from typing import Optional

#: 本段是 `approval_events` 的**运行时权威** —— 与 `budget_intercepts` 同款
#:    （它也不在 `db.py` 的 `create_table()` 里，只在 `token_tracker.py` 里惰性建）。
#: `app/schema.sql` 是 `pg_dump` **生成的快照，不许手改**（它文件头自己写着）。
#:    本表要进那份快照，只能**重新导一次**，不是在那边手抄一段。
_DDL = """
CREATE TABLE IF NOT EXISTS approval_events (
    id            SERIAL PRIMARY KEY,
    owner         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    raw_thread_id TEXT,
    graph         TEXT,
    decision      TEXT NOT NULL,
    edited        BOOLEAN NOT NULL,
    rounds        INTEGER,
    reason        TEXT,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
)
"""

#: **顺序必须与 `list_decisions` 的 SELECT 列顺序一致**（靠 `zip` 配名，错位不报错）。
_COLUMNS = ("owner", "actor", "decision", "edited", "rounds", "reason")


def summarize_tool_calls(tool_calls) -> str:
    """待批的 `tool_calls` ⇒ 一句人读的摘要。**它就是留痕里的「为什么」**。

    只取工具名，**不带 `args`** —— args 可能很大、也可能含用户数据，
       留痕表不是放它的地方。
    """
    runs = []
    for tc in tool_calls or []:
        name = (tc or {}).get("name") or "?"
        if runs and runs[-1][0] == name:
            runs[-1][1] += 1
        else:
            runs.append([name, 1])
    if not runs:
        return "(无工具调用)"
    return "、".join(f"{n}×{c}" if c > 1 else n for n, c in runs)


def record_decision(*, owner: str, actor: str, decision: str, edited: bool,
                    raw_thread_id: Optional[str] = None, graph: Optional[str] = None,
                    rounds: Optional[int] = None, reason: Optional[str] = None) -> None:
    """记下一次**真的落到图上的裁决**。**永不抛异常**。

    `owner` 与 `actor` 是**两个身份**：`owner` = 会话是谁的，`actor` = 谁做的裁决。
       admin 接管 alice 的会话时**必然不同** —— 合成一个字段 ⇒ 留痕当场变假话、且不报错。

    全参数都是关键字（`*`）⇒ 调用点写错名字会当场 `TypeError`，不是静默写错列。
    """
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                cur.execute(
                    "INSERT INTO approval_events "
                    "(owner, actor, raw_thread_id, graph, decision, edited, rounds, reason) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (owner, actor, raw_thread_id, graph, decision, bool(edited),
                     rounds, reason),
                )
            conn.commit()
    except Exception as e:
        print(f"[ApprovalAudit] 记录裁决失败（已忽略）: {e}")


def list_decisions(*, owner: Optional[str], limit: int = 50, offset: int = 0) -> list:
    """读出裁决历史，**最新在前**。

    `owner` 是**必填关键字参数**（不给默认值）：`None` ⇒ **全量**（admin 那条路），
       给了 ⇒ 只出那一个人的。**没默认值 = 忘了传会当场 `TypeError`**，
       而不是静默地变成"全量"（那是本仓最恨的那类漏洞， 口径）。

    fail-open：库不可用 ⇒ 返回 `[]`（页面上少一段历史 好过整页 500）。

    **2026-10-08 加 `offset`**（`frontend/README.md` §六 分页）——
       端点用「**多取一条**」判 `has_more`（见 `api_v1_agent.agent_approval_history`）：
       调用方若要 N 条，**传 `limit=N+1`**，拿到 N+1 条就说明后面还有。
       **排序必须是【全序】**（`created_at DESC, id DESC`）—— 否则翻页会**漏行或重行**，
       而**不报任何错**。现有排序满足（同一个 `created_at` 由 `id` 打平）。
    """
    from core.db import get_db
    where = "" if owner is None else "WHERE owner = %s "
    params = () if owner is None else (owner,)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT owner, actor, decision, edited, rounds, reason "
                    "FROM approval_events " + where +
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    params + (limit, offset),
                )
                rows = cur.fetchall()
    except Exception as e:
        print(f"[ApprovalAudit] 读裁决历史失败（返回空）: {e}")
        return []
    return [dict(zip(_COLUMNS, row)) for row in rows]
