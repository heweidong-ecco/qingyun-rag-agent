"""`thread_cost_breakdown`（Trace 页成本轴）—— **离线可跑的那一半**。

本文件**不连库**（假 pg），⇒ **它能进 CI**。这是它存在的理由：
`test_trace_cost_db.py` 那半边验的是**真越权**（强得多），但 `needs_db` ⇒ **CI 永远不跑它**。
⇒ 万一有人后来把 `user_name` 条件删了，**只有这里的假 pg 会当天红**。

## 本文件钉的头一件事：**这个函数的 SQL 必须同时带两个条件**

`token_tracker.get_thread_cost` 就在隔壁，签名 `(thread_id)` —— **只按线程，不按用户**。
单看签名像越权洞（**实测不是**：全仓唯一调用点是本人的线程预算检查，见 `app/billing/specs/token_tracker.md`）。
于是"照着隔壁抄一个"变成**极容易发生**的事 —— 抄过来就是**任何登录用户拿个 thread_id 就能读别人的账**。
⇒ 本文件用一个**假 cursor 把真正执行的那条 SQL 与它的参数截下来**，逐条断言。

（反证检验：把 `owner_clause` 改成永远 `"1 = 1"` ⇒ `test_filters_by_user` 立刻红。）

## 它【不是】越权判据，别读错

「SQL 文本里有 `user_name = %s`」**是代理量**，不是「A 真的看不到 B 的」。
**真判据在 `test_trace_cost_db.py`**（真库、真两个用户、阳性对照）。
同族纪律。

判据（可打印）：`venv/bin/python -m pytest app/tests/test_trace_cost.py -q -p no:warnings`
"""
import ast
import inspect
from datetime import datetime, timezone

import pytest

import billing.token_tracker as tt


# ==================== 假 pg（照 `app/tests/test_approval_events.py` 的写法） ====================

class _FakeCursor:
    """把**真正执行的那条 SQL 与它的参数**记下来，供断言。

    `fetchall` 与 `fetchone` 各答各的 —— 被测函数**发了两次查询**
       （一次取明细、一次取整条线程的合计），两次的返回形状不同。
    """

    def __init__(self, log, rows, aggregate):
        self.log = log
        self._rows = rows
        self._aggregate = aggregate

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._aggregate


class _FakeConn:
    def __init__(self, rows, aggregate):
        self.log = []
        self._rows = rows
        self._aggregate = aggregate

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self.log, self._rows, self._aggregate)


def _patch_db(monkeypatch, rows=(), aggregate=(0, 0, 0.0)):
    """把 `db.get_db` 换成假连接，回 (log, 还原用)。

    被测函数体里是**惰性导入**（`from db import get_db`，见 `token_tracker.py` 顶部注释：
       模块层导入会把 psycopg2 也拉起来）⇒ **必须打 `db` 模块的属性**，打 `token_tracker` 上的没用。
    """
    import core.db as db

    conn = _FakeConn(list(rows), aggregate)
    monkeypatch.setattr(db, "get_db", lambda: conn)
    return conn.log


_DETAIL_COLUMNS = 7   # purpose / model / prompt / completion / total / cost / created_at


def _row(purpose="answer_generation", when=None):
    when = when or datetime(2026, 9, 20, 9, 16, 22, 280863)
    return (purpose, "qwen-turbo", 100, 84, 184, 0.0008, when)


# ==================== ① SQL 必须同时带 user_name 与 thread_id ====================

def test_filters_by_user(monkeypatch):
    """**本文件最重要的一条** —— 明细查询必须按 `user_name` 过滤。

    别把这条删了换成"看一眼源码里有" —— 那种判据**过不了反证检验**：
       源码里把条件删掉，肉眼看不出来；这条命令会变红。
    """
    log = _patch_db(monkeypatch, rows=[_row()], aggregate=(1, 184, 0.0008))
    tt.thread_cost_breakdown("alice", "t-1")

    sql, params = log[0]
    assert "user_name = %s" in sql, f"明细 SQL 没按用户过滤：{sql}"
    assert "thread_id = %s" in sql, f"明细 SQL 没按线程过滤：{sql}"
    assert params[0] == "alice", f"第一个参数必须是 user_name，实际 {params!r}"
    assert params[1] == "t-1", f"第二个参数必须是 thread_id，实际 {params!r}"


def test_total_query_also_filters_by_user(monkeypatch):
    """**合计那条 SQL 也要过滤** —— 只过滤明细会得到最坏的形态：

    列表只显示自己的，**合计却是全站的** ⇒ 页面看起来正常，数字是别人的。
    """
    log = _patch_db(monkeypatch, rows=[_row()], aggregate=(1, 184, 0.0008))
    tt.thread_cost_breakdown("alice", "t-1")

    assert len(log) == 2, f"应当恰好发两次查询（明细 + 合计），实际 {len(log)}"
    sql, params = log[1]
    assert "user_name = %s" in sql, f"合计 SQL 没按用户过滤：{sql}"
    assert params == ("alice", "t-1"), f"合计 SQL 参数不对：{params!r}"


def test_include_all_swaps_condition_without_second_sql(monkeypatch):
    """admin 例外 = **换条件**，不是**换 SQL**（两份 SQL 会漂）。

    判据不是"看一眼源码里是不是只有一个 SQL 字面量"（那过不了反证检验），
       而是**把两次调用截下来的 SQL 逐字相减，差集只许是那一处条件**。
    """
    log_a = _patch_db(monkeypatch, rows=[_row()], aggregate=(1, 184, 0.0008))
    tt.thread_cost_breakdown("boss", "t-1", include_all=True)
    gated = [sql for sql, _ in log_a]

    log_b = _patch_db(monkeypatch, rows=[_row()], aggregate=(1, 184, 0.0008))
    tt.thread_cost_breakdown("boss", "t-1")
    scoped = [sql for sql, _ in log_b]

    assert len(gated) == len(scoped) == 2, "两次调用都应各发两条查询"
    for a, b in zip(gated, scoped):
        assert a != b, "include_all 与不带它必须产生不同的 SQL（否则它根本没生效）"
        assert a.replace("1 = 1", "user_name = %s") == b, (
            f"两条 SQL 的差异**只许**是那一处条件，实际：\n  {a}\n  {b}"
        )

    # 条件换掉之后，`boss` **不该**再出现在参数里
    assert log_a[0][1] == ("t-1", tt._BREAKDOWN_LIMIT), f"参数应只剩 thread_id + limit：{log_a[0][1]!r}"


# ==================== ② 合计必须覆盖【整条线程】，不是 items 之和 ====================

def test_total_comes_from_sql_not_from_items(monkeypatch):
    """**有 `LIMIT` 就必须有独立合计** —— 否则"总花费"静默变成"最近 N 笔之和"。

    本用例：明细只回 2 行（模拟被截断），合计回 **50 笔 / 9999 tokens / ¥12.5**。
    ⇒ 函数必须答 **12.5**，不是那两行的和。
    """
    rows = [_row(), _row(when=datetime(2026, 9, 20, 9, 0, 0))]
    _patch_db(monkeypatch, rows=rows, aggregate=(50, 9999, 12.5))
    out = tt.thread_cost_breakdown("alice", "t-1")

    assert out["total"]["total_cost"] == 12.5
    assert out["total"]["total_tokens"] == 9999
    assert out["total"]["count"] == 50
    assert len(out["items"]) == 2, "明细仍应是 2 行（合计与明细各回各的）"
    assert out["truncated"] is True, "count(50) > len(items)(2) ⇒ 必须报截断"


def test_not_truncated_when_counts_match(monkeypatch):
    """反证另一半：条数相等时 **不许**报截断（否则页面会一直挂个假警告）。"""
    _patch_db(monkeypatch, rows=[_row()], aggregate=(1, 184, 0.0008))
    assert tt.thread_cost_breakdown("alice", "t-1")["truncated"] is False


# ==================== ③ 时间必须带区（具体化 B） ====================

def test_created_at_carries_utc_offset(monkeypatch):
    """`token_usage_logs.created_at` 是 **`TIMESTAMP`（无时区）**，PG 容器是 `Etc/UTC`。

    ⇒ 库里的 `2026-09-20 09:16:22` 是 **UTC**。
    ⇒ 若原样（或裸 `.isoformat`）回给前端，`new Date("…T09:16:22")` 会当成**本地时间**解析
      ⇒ 东八区用户看到的时刻静默**早 8 小时**。

    断言的是**后缀本身**，因为那一串是人类可读的、也是 JS 唯一能拿到的线索。
    （反证检验：把 `_iso_utc` 改成 `return dt.isoformat` ⇒ 本条立刻红。）
    """
    _patch_db(monkeypatch, rows=[_row()], aggregate=(1, 184, 0.0008))
    iso = tt.thread_cost_breakdown("alice", "t-1")["items"][0]["created_at"]

    assert iso.endswith("+00:00"), f"时间必须显式标 UTC，实际 {iso!r}"
    assert datetime.fromisoformat(iso).tzinfo is not None
    assert datetime.fromisoformat(iso) == datetime(2026, 9, 20, 9, 16, 22, 280863, tzinfo=timezone.utc)


def test_iso_utc_passes_none_through():
    """`created_at` 允许为 NULL（列有默认值但没 NOT NULL）⇒ 别在这里抛。"""
    assert tt._iso_utc(None) is None


# ==================== ④ 空串归一 / fail-open ====================

def test_empty_key_normalized_to_unknown(monkeypatch):
    """与 `record_usage` / `get_session_token_usage` **同一约定**：

    不归一的话，**记账桶与判定桶会分裂** —— 记账进 `"unknown"`、查询查 `''`，
       ⇒ 查询永远看不到用量，**而且是静默的**。
    """
    log = _patch_db(monkeypatch)
    tt.thread_cost_breakdown("", "")

    assert log[0][1] == ("unknown", "unknown", tt._BREAKDOWN_LIMIT), f"空串应归一到 unknown：{log[0][1]!r}"


def test_fail_open_on_db_error(monkeypatch, capsys):
    """查账失败 **不许把页面打成 500** —— 回零值 + 打一行日志（同 `get_thread_cost` 的风格）。

    断言"回零值"**同时**要断言"打了日志" —— 只测前者的话，
       **一个静默吞掉的实现也能过**（那才是最坏的形态：页面显示 ¥0.0000 而你以为真的没花钱）。
    """
    import core.db as db

    def _boom():
        raise RuntimeError("库挂了")

    monkeypatch.setattr(db, "get_db", _boom)
    out = tt.thread_cost_breakdown("alice", "t-1")

    assert out["items"] == []
    assert out["total"] == {"count": 0, "total_tokens": 0, "total_cost": 0.0}
    assert out["truncated"] is False
    assert "查询线程成本明细失败" in capsys.readouterr().out, "静默吞异常 = 页面显示零花费而不报警"


# ==================== ⑤ 不许复用没有用户条件的那个函数 ====================

def test_does_not_delegate_to_get_thread_cost():
    """**AST 判据**（照 `app/tests/test_cost_visibility.py` 的写法）。

    隔壁 `get_thread_cost(thread_id)` **不带用户条件**。最可能发生的回归是
    「既然已经有一个查线程花费的函数，那我复用它吧」—— **那一下就是越权**。

    为什么不是 `grep`：`grep` 会连注释一起命中（本仓有前科：
        「禁子串」守卫把注释也算进去了）。AST 只看**真的调用**。
    """
    src = inspect.getsource(tt.thread_cost_breakdown)
    tree = ast.parse(src.lstrip() if not src.startswith("def") else src)
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "get_thread_cost" not in called, "⛔ 复用了没有 user_name 过滤的 get_thread_cost（= 越权）"


def test_endpoint_declares_auth_dependency():
    """端点必须带 `get_current_user_hybrid` —— 否则它会**变成一条公开路由**。

    这一条与 （CI 里的那道门）**是两把尺子**：
       那道门在 CI 里读真实路由表（强），本条只读源码（弱、但失败信息更直白）。
       ⇒ **以那道门为准**，本条只是把原因写在近处。
    """
    import routing.api_v1_agent as api_v1_agent

    src = inspect.getsource(api_v1_agent.agent_trace_cost)
    assert "get_current_user_hybrid" in src, "端点少了鉴权依赖"


# ==================== ⑥ 端点的【接线】—— admin 标记真的传下去了吗 ====================

def _run_endpoint(monkeypatch, user_name):
    """直接跑端点协程（不过 HTTP 层）⇒ 无需真 API Key，本用例因而能进 CI。

    返回被 `thread_cost_breakdown` 收到的实参。
    """
    import asyncio
    import routing.api_v1_agent as m

    seen = []

    def _spy(user, thread, *, include_all):
        seen.append((user, thread, include_all))
        return {"items": [], "total": {"count": 0, "total_tokens": 0, "total_cost": 0.0}, "truncated": False}

    monkeypatch.setattr(m, "thread_cost_breakdown", _spy)
    monkeypatch.setattr(
        m, "get_user_role",
        lambda u: m.UserRole.ADMIN if u == "boss" else m.UserRole.FREE,
    )
    out = asyncio.run(m.agent_trace_cost("t-1", user_name))
    return seen[0], out


def test_endpoint_grants_include_all_to_admin(monkeypatch):
    """admin ⇒ `include_all=True`（决策 2：admin 例外**显式一行**）。

    反证检验：把那行改成写死的 `True` ⇒ 下面 `test_endpoint_does_not_grant_include_all_to_others` 红。
    """
    args, _ = _run_endpoint(monkeypatch, "boss")
    assert args == ("boss", "t-1", True), f"admin 应拿到 include_all=True，实际 {args!r}"


def test_endpoint_does_not_grant_include_all_to_others(monkeypatch):
    """**普通用户 ⇒ `include_all=False`** —— 这一条才是本族的越权判据。

    它与上面那条**必须成对**：只测 admin 拿到 `True`，
       一个**永远传 `True`** 的实现会绿着通过。
    """
    args, _ = _run_endpoint(monkeypatch, "alice")
    assert args == ("alice", "t-1", False), f"普通用户不该拿到 include_all=True，实际 {args!r}"


def test_endpoint_echoes_thread_id_and_requester(monkeypatch):
    """响应要把 `thread_id` 与 `requested_by` 带回去 —— 页面靠它确认"我看到的是哪个线程"。

    `requested_by` 是**请求者**，admin 接管时**不等于**数据属主（同  的 owner/actor 之分）。
    """
    _, out = _run_endpoint(monkeypatch, "boss")
    assert out["thread_id"] == "t-1"
    assert out["requested_by"] == "boss"


def test_breakdown_limit_is_bounded():
    """没有上限 ⇒ 用户开个长会话就能让响应无界增长（这条轴是**逐笔**回的）。

    上限存在本身**不是**判据的全部 —— 有上限就**必须**有独立合计，
    见 `test_total_comes_from_sql_not_from_items`。
    """
    assert isinstance(tt._BREAKDOWN_LIMIT, int) and 0 < tt._BREAKDOWN_LIMIT <= 10000


if __name__ == "__main__":       # 方便直接跑：`venv/bin/python app/tests/test_trace_cost.py`
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
