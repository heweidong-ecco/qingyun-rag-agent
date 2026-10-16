"""待接管会话的注册表（B5 · 硬门 D）。

**为什么需要它**：`MemorySaver`（`agent_graph.py` 里 `build_agent_graph` 的末行）
   **没有"列出所有 thread"的 API** —— 它只按 `thread_id` 取。
   ⇒ **没法从 checkpoint 反查"谁卡在审批"** ⇒ 只能自己记账。

**已知限制（v1）**：本表在**内存**里 ⇒ **进程重启即清空**。
   与**默认的** `MemorySaver` **是一致的**（那个也在内存）⇒ 两边一起丢，不会对不上。

   **但若设了 `AGENT_CHECKPOINT_BACKEND=sqlite`**：**图的状态落盘了、本表还在内存**
      **重启后图仍在等审批，而队列里查不到** ⇒ 会话变孤儿（谁也找不到它，它自己也走不下去）。
      **这个组合不会自己报错** —— 要等人重启后去队列里找才发现。
      ⇒ 启动时由 `warn_if_backend_mismatch` 给警告（唯一的防线）。

计划 / 裁定 ⇒ `app/routing/specs/api_v1_agent.md` 的「实施计划 ② · 」·
   模块 spec ⇒ `app/agent/specs/pending_approvals.md` · 判据 ⇒ `app/tests/test_pending_approvals.py`
"""
import threading
import time

# `threading.Lock` 不是装饰 —— 端点连 `agent_graph` 都在多线程下跑，
#    而 register / resolve / list_pending 会被不同请求并发调到。
_lock = threading.Lock()
_pending: dict[str, dict] = {}

# 一次会话允许的人工审批轮次上限（需求方 2026-10-05 裁「上限 3 轮」）。
# 可用 env `MAX_APPROVAL_ROUNDS` 覆盖 —— 见 `approval_round_cap`。
_DEFAULT_MAX_APPROVAL_ROUNDS = 3


def approval_round_cap() -> int:
    """一次会话允许的**人工审批轮次上限**（默认 **3**）。

    **为什么要有它**：放行后模型**又**要求敏感工具时会**重新入队**（`B5/B6` 修正，对），
       但**没有任何上限** ⇒ 模型可以无限要求、人工就得无限批（实测 3 次收敛，但**没有任何机制阻止 30 次**）。

    `MAX_APPROVAL_ROUNDS`（env）可调；**非法值回退到默认**（不抛 —— 启动时为一个数值配置炸掉服务不值当，
       与  那个"名字写错 ⇒ 静默失效"不同：这里**退化成的是保守的默认值而非零行为**）。
    """
    import os
    raw = os.getenv("MAX_APPROVAL_ROUNDS")
    if raw is None:
        return _DEFAULT_MAX_APPROVAL_ROUNDS
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_APPROVAL_ROUNDS
    return n if n >= 1 else _DEFAULT_MAX_APPROVAL_ROUNDS


def register(session_key: str, user_name: str, tool_calls: list, *,
             raw_thread_id: str | None = None, graph: str = "agent_graph",
             rounds: int = 1) -> None:
    """登记一个卡在审批的会话。同一键重复登记 ⇒ **覆盖**（不产生两条）。

    **2026-10-03（丙段）**：第一个形参由「裸 `thread_id`」改成**会话键**
       （`session_key(user_name, thread_id)`）—— 理由：登记表原先也按裸 `thread_id` 记账
       ⇒ 两个人用同一个 `thread_id` **在队列里也串号**，`/agent/approve` 会拿着
       别人的键去续跑。形参**改名**了（`thread_id` → `session_key`）是**故意的**：
       叫 `thread_id` 会让人继续往里塞裸值。

    `raw_thread_id`：调用方传进来的**原值**（不是拼过的键）。
      **必须有它**：`/agent/approve` 拿到的是**原 `thread_id`**，
         而它得先**按原值反查属主**、再按属主拼键（`find_by_raw_thread_id`）。
         没有这个字段就只剩"按调用方拼"一条路 ⇒ **admin 永远批不了别人的**（硬门 D 死掉）。
      不传 ⇒ 退回等于 `session_key`（**只为兼容老调用点 / 测试**；新代码一律显式传）。

    `graph`：这条会话**停在【哪张图】**的审批点上（`"agent_graph"` / `"checkpointer_agent"`）。
      **必须有它**（需求方 2026-10-03 裁）：`/agent/approve` 早先把 `agent_graph` **写死**了，
         而 `/agent/memory_chat` 走的是 `checkpointer_agent` ⇒ 光给它加审批门，
         **那个会话会永远停在审批点、没人放行**（比不加门还糟）。
         默认值 `"agent_graph"` 只是**兼容老调用点**；新调用点**一律显式写**。

    `rounds`：这条会话**第几轮**停在审批点（首次 = 1，每次"放行后又停"由 `/agent/approve` +1）。
      **必须是显式计数的**：`/agent/approve` 靠它判**到了上限没有**，
         到上限就**不再入队**、改为强制收尾 —— 否则模型可以无限要求敏感工具。
    """
    with _lock:
        _pending[session_key] = {
            "thread_id": session_key,          # 字段名保留（对外形状不变）= 拼过的键
            "raw_thread_id": raw_thread_id if raw_thread_id is not None else session_key,
            "user_name": user_name,
            "graph": graph,
            "tool_calls": list(tool_calls or []),
            "rounds": rounds,
            "since": time.time(),
        }


def resolve(session_key: str) -> None:
    """会话已不再等待审批 ⇒ 注销。

    **注销不存在的【是正常的】**（重复点批准 / 线程从没卡住过）⇒ **幂等，不抛异常**。
       `/agent/approve` 会调到它，而那条路**本来就会遇到"没有待审批任务"的情况**。
    """
    with _lock:
        _pending.pop(session_key, None)


def find_by_raw_thread_id(raw_thread_id: str) -> list[dict]:
    """按【调用方传的原 `thread_id`】反查登记 ⇒ `/agent/approve` 靠它定位**属主**。

    **为什么不能按调用方拼键就完事**：`/agent/pending` 是**跨用户队列**（硬门 D）
       ⇒ admin 得能批**别人**的会话。若用调用方自己的键，admin 会拼出 `admin:…`
       而属主的是 `alice:…` ⇒ **永远批不了**。⇒ 必须**先查属主**（本函数），再按属主拼键。

    返回 **list** 而不是单条：两个人可能都用 `thread_id="default"` ⇒ **会有多条**
       （这正是隔离问题本身）。挑哪条是**调用方**的裁决（`/agent/approve` 会据此拒绝歧义）。

    返回**浅拷贝**（同 `list_pending`）—— 别改返回值里的 `tool_calls`。
    """
    with _lock:
        return [dict(v) for v in _pending.values() if v["raw_thread_id"] == raw_thread_id]


def list_pending() -> list[dict]:
    """列出全部待接管会话，**按"卡住时间"升序** —— **卡得最久的排最前**（最该先处理）。

    返回的是**浅拷贝**（`dict(v)`）⇒ 调用方改返回值的**顶层字段**不会影响注册表；
       但 `tool_calls` 那个 list 是**共享**的（浅拷贝）—— 别去改它。
    """
    with _lock:
        return sorted((dict(v) for v in _pending.values()), key=lambda r: r["since"])


def clear() -> None:
    """清空。**仅供测试隔离用** —— 生产代码里没有调用点（别拿它当"重置"接口）。"""
    with _lock:
        _pending.clear()


def warn_if_backend_mismatch(logger=None) -> None:
    """checkpoint 落盘了、而本表在内存 ⇒ 重启后队列会丢。**启动时提醒。**

    只在 `AGENT_CHECKPOINT_BACKEND=sqlite` 时出声 —— 默认的内存后端**两边一致**，
       那时**必须安静**（恒响的警告 = 把真警告淹掉）。
    """
    import os
    if os.getenv("AGENT_CHECKPOINT_BACKEND") == "sqlite":
        msg = ("AGENT_CHECKPOINT_BACKEND=sqlite ⇒ 图状态会落盘，"
               "但待接管队列(pending_approvals)仍在内存 ⇒ 重启后队列会丢、会话变孤儿。"
               "见 app/agent/specs/pending_approvals.md")
        (logger.warning if logger else print)(msg)
