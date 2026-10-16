"""全局日级 token 总额（`R1.4`）· **离线可跑的那一半**。
"""
import ast
import inspect
import textwrap

import pytest

import billing.token_tracker as token_tracker


# ==================== 工具：从源码取 SQL 字面量（AST，不是 grep） ====================

def _sql_of(fn) -> str:
    """取出 `fn` 里 `cur.execute(...)` 的 **SQL 字符串字面量**。

    与 `test_session_budget_offline.py` 的同名工具**故意重复**（而不是抽到公共模块）：
       它是**测试夹具**，各文件自己钉自己的判据；抽出去会让「这个测试在查什么」变远。
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


# ==================== ① 核心判据：「全局」= SQL 里没有 user_name ====================

def test_global_query_has_no_user_filter():
    """**本文件最重要的一条**。

    全局日级的 SQL **不许**出现 `user_name` —— 出现了它就退化成"单用户"，
    而且**看起来一切正常**（见文件头那张表）。
    """
    sql = _sql_of(token_tracker.get_global_daily_token_usage)
    assert "user_name" not in sql, (
        f"全局日级的 SQL 里出现了 user_name ⇒ 它统计的是【某个用户】，不是全站：\n{sql}\n"
        "  多半是从 get_daily_token_usage 抄过来忘了删过滤条件。"
    )


def test_the_two_daily_queries_are_actually_different():
    """**配对守卫**：`per-user` 那条**必须**有 `user_name`，`global` 那条**必须**没有。

    为什么单靠上一条不够：如果有人把 `get_global_daily_token_usage` 写成
       **转发给 `get_daily_token_usage`**（"复用嘛"），上一条会**照样通过**
       （它 AST 取不到 `execute` 会直接 `AssertionError` —— 那是碰巧挡住的）。
       本条把**两个函数的差异**钉成显式约束：**它们本来就该不同**。
    """
    user_sql = _sql_of(token_tracker.get_daily_token_usage)
    global_sql = _sql_of(token_tracker.get_global_daily_token_usage)

    assert "user_name" in user_sql, (
        f"单用户的那条 SQL 没有 user_name ⇒ 它不再是「按用户」的了：\n{user_sql}"
    )
    assert "user_name" not in global_sql, (
        f"全站的那条 SQL 有 user_name ⇒ 它不再是「全站」的了：\n{global_sql}"
    )


def test_global_query_is_today_only():
    """窗口 = **今日**（与  一致，也是  要素④「跨天自然重置」的前提）。

    删掉 `CURRENT_DATE` ⇒ 变成"有史以来总量" ⇒ **本条红**。
       那会让额度**一旦用满就永不自愈** —— 而  承诺的恢复方式正是"明天 0 点"。
    """
    sql = _sql_of(token_tracker.get_global_daily_token_usage)
    assert "CURRENT_DATE" in sql, (
        f"全局用量的 SQL 没有日期窗口 ⇒ 它统计的是【全部历史】，跨天不会重置：\n{sql}"
    )


def test_global_query_sums_everything():
    """必须是 `SUM(total_tokens)` 且**不带** `GROUP BY` —— 求的是**一个标量**。

    带 `GROUP BY user_name` 会让 `fetchone` 只拿到**第一行**（某一个用户的量），
       结果**偏小且随机**（取决于数据库返回顺序）—— 这种错最难查。
    """
    sql = _sql_of(token_tracker.get_global_daily_token_usage)
    assert "SUM(total_tokens)" in sql.replace("  ", " ").replace("\n", " "), (
        f"全局用量不是对 total_tokens 求和：\n{sql}"
    )
    assert "GROUP BY" not in sql.upper(), (
        f"全局用量的 SQL 带了 GROUP BY ⇒ fetchone() 只会拿到第一行：\n{sql}"
    )


# ==================== ② 判定逻辑（取数被 patch 掉，只看判定） ====================

def test_rejects_when_over_limit(monkeypatch):
    """全站已用量 ≥ 上限 ⇒ 必须拒，且原因里说得出「全站」。"""
    from billing.token_config import GLOBAL_DAILY_TOKEN_LIMIT

    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage",
                        lambda: GLOBAL_DAILY_TOKEN_LIMIT + 1)

    ok, why = token_tracker.check_global_daily_budget()

    assert ok is False, "全站用量已超上限，却放行了"
    assert "全站" in why, f"拒绝原因看不出是【全站】级：{why!r}"


def test_allows_when_under_limit(monkeypatch):
    """用量远低于上限 ⇒ 放行（别做成"只要查过就拒"）。"""
    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage", lambda: 1)

    ok, why = token_tracker.check_global_daily_budget()

    assert ok is True, f"只用了 1 token 却被拒：{why!r}"
    assert "全站" in why, f"放行原因看不出判的是【全站】预算：{why!r}"


def test_estimated_tokens_can_trip_it(monkeypatch):
    """接口留了 `estimated_tokens`：预估本身超剩余 ⇒ 也要拒。

    参数**收下却不用**是很容易漏的一处（签名看着对、行为是死的）——
       本条专治它：`estimated_tokens` 必须**真的参与判定**。
    """
    from billing.token_config import GLOBAL_DAILY_TOKEN_LIMIT

    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage", lambda: 0)

    ok, why = token_tracker.check_global_daily_budget(
        estimated_tokens=GLOBAL_DAILY_TOKEN_LIMIT + 1)

    assert ok is False, "预估消耗已超全站剩余额度，却放行了"
    assert "预估" in why, f"拒绝原因没说明是【预估】超的：{why!r}"


def test_limit_comes_from_token_config(monkeypatch):
    """阈值**必须**取自 `token_config`（的"唯一落点"），不许写死在函数里。

    做法是**把常量改小**再看行为跟不跟着动 —— 写死常量的实现**不会跟着动** ⇒ 红。
    """
    import billing.token_config as token_config

    monkeypatch.setattr(token_config, "GLOBAL_DAILY_TOKEN_LIMIT", 100)
    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage", lambda: 101)

    ok, why = token_tracker.check_global_daily_budget()

    assert ok is False, (
        f"把 token_config.GLOBAL_DAILY_TOKEN_LIMIT 改成 100、已用 101，却放行了：{why!r}\n"
        "  ⇒ 阈值没从 token_config 取。"
    )
    assert "100" in why, f"拒绝文案里应能看出阈值是 100：{why!r}"


def test_is_fail_open_when_db_unavailable(monkeypatch):
    """**取数失败 ⇒ 放行**（返回 0.0），**不是拒绝**。

    这条是**有意与 `app/routing/deps.py` 相反**的（那边是 fail-closed）：
       额度是**成本控制**，不是安全边界 ⇒ PG 抖一下不能把**全站**打死。
       这里的"全站"让代价**比  的会话级更大** ——
          fail-closed 的话，一次 DB 抖动 = **所有请求 429**。
    """
    import core.db as db

    def _boom(*_a, **_kw):
        raise RuntimeError("模拟 DB 不可用")

    monkeypatch.setattr(db, "get_db", _boom)

    got = token_tracker.get_global_daily_token_usage()

    assert got == 0.0, (
        f"DB 不可用时取数返回了 {got!r}，应为 0.0（fail-open）\n"
        "  ⛔ 如果它抛异常或返回一个大值，DB 抖一下就会把【全站】拒掉。"
    )


@pytest.mark.parametrize("est", [0, 1, 10 ** 9])
def test_never_raises_on_any_estimate(monkeypatch, est):
    """`estimated_tokens` 取任何值都只能返回**二元组**，不许抛。

    若抛异常，调用点会 **500 而不是 429** ——
       与  要修的「说反了的文案」是同一类问题。
    """
    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage", lambda: 0)

    ok, why = token_tracker.check_global_daily_budget(estimated_tokens=est)

    assert isinstance(ok, bool) and isinstance(why, str)
