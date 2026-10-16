"""会话级 token 上限· **离线可跑的那一半**。

## 为什么本文件【不带】`needs_db`

`app/billing/specs/token_tracker.md` 的 `①b · ` 把判据拆成两份，本文件是**必须进 CI** 的那份：

| 文件 | 标记 | 管什么 |
|---|---|---|
| **本文件** | **无**（CI 跑） | ① 数据源是不是**表**（不是内存）② SQL 的**范围与窗口**对不对 ③ 判定逻辑 |
| `test_session_budget.py` | `needs_db` | 真写库、真超限 ⇒ 真拒 |

**拆开的理由**：若把「内存里塞满也不影响判定」那条放进 `needs_db` 文件，
CI 会**整篇排除**它 ⇒ **B8 最核心的判据掉出 CI**。别为了少一个文件把它并回去。

## 本文件钉的四件事，以及各自为什么不能省

1. **数据源（`test_memory_is_not_the_source`）** —— **B8 的真正风险**。
   `record_usage` 里**已经在内存按 thread 累加**（`_thread_summary`），
   拿它当上限 ⇒ **重启即清零** ⇒ **重启一下就能绕开限额**。
2. **SQL 的三段范围** —— 用 **AST 取 `cur.execute(...)` 的字面量**，不用 grep：
   `thread_id` / `user_name` / `CURRENT_DATE` 这些词**在 docstring 里也会出现**，
   grep 会把「注释里提到」当成「SQL 里有」（本仓栽过 —— `docs/规范/开发规范.md` §3.1）。
   · **窗口**那条是  决策一的落地守卫；
   · **key** 那两条是  决策二的落地守卫。
3. **签名（`test_user_name_is_required`）** ——  决策二**明确不给 `user_name` 默认值**。
   给了默认值，漏传的调用点会**静默落进同一个桶** ⇒ 公共桶问题**换个形式回来**。
   这条把它**钉死在签名上**。
4. **判定逻辑** —— patch 掉取数函数，只看判定本身。

本文件**不写库、不连 Redis**。
"""
import ast
import inspect
import textwrap

import pytest

import billing.token_tracker as token_tracker

# 本文件统一用这对 key 调被测函数（决策二：key = user_name + thread_id）
U, T = "test_user", "t-session"


# ==================== 工具：从源码取 SQL 字面量（AST，不是 grep） ====================

def _sql_of(fn) -> str:
    """取出 `fn` 里 `cur.execute(...)` 的 **SQL 字符串字面量**。

    用 AST 而不是在源码里 `in` 判词：docstring / 注释里同样会出现
       `user_name` / `CURRENT_DATE` 这些词，grep 式判断会把它们**误当成 SQL 的一部分**。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "execute":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            return node.args[0].value
    raise AssertionError(
        f"{fn.__name__} 里找不到 `cur.execute(<字符串字面量>)` —— "
        "要么 SQL 被拼成了 f-string（那就没法静态核对，别这么写），要么函数被改了"
    )


# ==================== ① 数据源：表，不是内存 ====================

def test_memory_is_not_the_source():
    """**B8 最核心的那条判据**：把内存汇总塞满，判定也必须当它不存在。

    `_thread_summary` 是**进程内存**（`record_usage` 里累加），**重启即清零**。
    ⇒ 若哪天有人把数据源改回它，**本条立刻红** —— 那时「重启一下就能绕开限额」这个洞
      会**静默**回到产品里（没有任何别的测试会红）。

    本机无库时取数函数走 `except` 分支返回 0.0，断言同样成立 ——
       这条**不依赖 DB**，正是它必须待在无 marker 文件里的原因。
    """
    with token_tracker._lock:
        token_tracker._thread_summary["t-mem-only"]["total_tokens"] = 10 ** 9

    got = token_tracker.get_session_token_usage(U, "t-mem-only")

    assert got == 0, (
        f"内存里塞了 1e9，结果却是 {got!r} ⇒ 数据源跟着内存走了。\n"
        "  B8 的上限**必须查表** `token_usage_logs` —— 内存是进程内的，重启即清零，"
        "等于「重启就能绕开限额」。"
    )


# ==================== ② SQL 的范围与窗口（三处裁定的落地守卫） ====================

def test_session_query_is_scoped_by_thread_id():
    """**会话**维度：必须按 `thread_id` 过滤 —— 否则「会话级」不成立。"""
    sql = _sql_of(token_tracker.get_session_token_usage)
    assert "thread_id" in sql, (
        f"SQL 里没有 thread_id ⇒ 它统计的不是某个会话：\n{sql}"
    )


def test_session_query_is_scoped_by_user_name():
    """**用户**维度（决策二·乙）：必须**同时**按 `user_name` 过滤。

    不带的后果**很具体**，不是理论问题：`thread_id` 的默认值是 `"default"`
    ⇒ 所有没显式传它的调用者**共用同一个桶** ⇒ 谁先烧完 50000，
      **其他默认用户一起撞 429**（软共享状态变成硬拒绝）。

    本条与我先前写的 `test_session_query_has_no_user_filter` **正好相反** ——
       那条钉的是「key 只用 thread_id」，已被  决策二**推翻**。
       两处是**同一个问题的两个答案**，以 DEC 的最终裁定为准。
    """
    sql = _sql_of(token_tracker.get_session_token_usage)
    assert "user_name" in sql, (
        f"SQL 里没有 user_name ⇒ 不同用户的会话会串进同一个桶：\n{sql}"
    )


def test_session_query_window_is_today():
    """窗口 = **会话 × 今日**（决策一）—— 本条是它的**落地守卫**。

    删掉 `CURRENT_DATE` ⇒ 变成「纯会话累计」⇒ **本条红**。
       那是**另一个方案**（备选一·乙），不能悄悄换过去：
       它会让会话桶**被永久封死**、且用户**无自救手段**。⇒ 要改，先改 DEC。
    """
    sql = _sql_of(token_tracker.get_session_token_usage)
    assert "CURRENT_DATE" in sql, (
        f"会话用量的 SQL 没有日期窗口 ⇒ 窗口变成了「全程」，与  不符：\n{sql}"
    )


# ==================== ③ 签名：user_name 必填（决策二的结构性拦法） ====================

def test_user_name_is_required():
    """`user_name` **必须是必填位置参数**（无默认值）。

    为什么用测试钉签名：决策二要解决的问题是「**漏传用户 ⇒ 落进同一个桶**」。
       如果给了默认值（比如 `user_name="unknown"`），漏传的调用点**不报错、不告警**，
       只是**悄悄共用** `unknown` 那个桶 —— 公共桶问题**换个名字回来了**。
       ⇒ 用签名把它变成**调用即报错**，而不是靠人记得传。
    """
    for fn in (token_tracker.get_session_token_usage,
               token_tracker.check_session_token_budget):
        params = inspect.signature(fn).parameters
        assert "user_name" in params, f"{fn.__name__} 没有 user_name 参数"
        assert params["user_name"].default is inspect.Parameter.empty, (
            f"{fn.__name__} 的 user_name 有默认值 {params['user_name'].default!r} ⇒ "
            "漏传的调用点会静默共用一个桶， 决策二就白定了"
        )


# ==================== ④ 判定逻辑（取数被 patch 掉，只看判定） ====================

def test_rejects_when_over_limit(monkeypatch):
    """已用量 ≥ 上限 ⇒ 必须拒，且原因里说得出「会话」。"""
    from billing.token_config import SESSION_TOKEN_LIMIT

    monkeypatch.setattr(token_tracker, "get_session_token_usage",
                        lambda _u, _t: SESSION_TOKEN_LIMIT + 1)

    ok, why = token_tracker.check_session_token_budget(U, T)

    assert ok is False, "已用量超过会话上限，却放行了"
    assert "会话" in why, f"拒绝原因看不出是【会话】级：{why!r}"


def test_allows_when_under_limit(monkeypatch):
    """用量远低于上限 ⇒ 放行（别做成"只要查过就拒"）。"""
    monkeypatch.setattr(token_tracker, "get_session_token_usage", lambda _u, _t: 1)

    ok, why = token_tracker.check_session_token_budget(U, T)

    assert ok is True, f"只用了 1 token 却被拒：{why!r}"
    assert "会话" in why, f"放行原因看不出判的是【会话】预算：{why!r}"


def test_estimated_tokens_can_trip_it(monkeypatch):
    """接口留了 `estimated_tokens`：预估本身超剩余 ⇒ 也要拒（本轮调用点不传，但行为得对）。"""
    from billing.token_config import SESSION_TOKEN_LIMIT

    monkeypatch.setattr(token_tracker, "get_session_token_usage", lambda _u, _t: 0)

    ok, why = token_tracker.check_session_token_budget(
        U, T, estimated_tokens=SESSION_TOKEN_LIMIT + 1)

    assert ok is False, "预估消耗已超上限，却放行了"
    assert "预估" in why, f"拒绝原因没说明是【预估】超的：{why!r}"


@pytest.mark.parametrize("tid", ["", "unknown", None])
def test_blank_thread_id_still_judged(monkeypatch, tid):
    """`thread_id` 缺省/空也要能判 —— 不许因为"没有会话"就直接放行。

    这里只要求**不炸**且返回二元组：空 `thread_id` 落进哪个桶是调用方的事，
       但**不能抛异常**（否则一个漏传 `thread_id` 的端点会 500 而不是 429）。
    """
    monkeypatch.setattr(token_tracker, "get_session_token_usage", lambda _u, _t: 0)

    ok, why = token_tracker.check_session_token_budget(U, tid)

    assert isinstance(ok, bool) and isinstance(why, str)
