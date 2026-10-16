"""成本可见（`①b`）· **离线可跑的那一半**。
"""
import ast
import pathlib

_API = pathlib.Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层


# ==================== 工具（AST，不是 grep） ====================

def _find_fn(path: pathlib.Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name} 里找不到函数 {name}()")


def _called_names(fn: ast.AST) -> set:
    """函数体里**被调用到的名字**（`Name` / `Attribute` 两种写法都收）。"""
    names = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            names.add(f.id)
        elif isinstance(f, ast.Attribute):
            names.add(f.attr)
    return names


def _loaded_names(fn: ast.AST) -> set:
    """函数体里**被读取到的名字**（含 `Name` 与 `Attribute.attr`）——
    用来核对常量（如 `GLOBAL_DAILY_TOKEN_LIMIT`）有没有真被用上。"""
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _sql_literals(fn: ast.AST) -> list:
    """取出函数里 `cur.execute("...")` 的 **SQL 字符串字面量**。"""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            out.append(node.args[0].value)
    return out


# ==================== ① `/agent/cost/overview` 不许读进程内存 ====================

# 这三个是**进程内存**的 defaultdict（`token_tracker.py:39/42/45`），
#    只在 `record_usage` 里累加、**从不回读 DB** ⇒ 重启归零。
#    它们**不是没用的** —— `token_tracker.py:148` 的即时告警还在用。
#    本测试只钉「它不许当**对外展示**的数据源」。
_MEMORY_ONLY_SUMMARIES = {"get_user_summary", "get_purpose_summary", "get_thread_summary"}


def test_cost_overview_reads_the_db_not_process_memory():
    """问题①的判据：`/agent/cost/overview` 的总数必须来自库。

    改动前的写法是 `get_user_summary(user_name)` + `get_purpose_summary`。
    那两个重启即归零 ⇒ 一次重启就能让"花了多少钱"答 0，而**没有任何报错**。
    """
    fn = _find_fn(_API / "routing/api_v1_agent.py", "agent_cost_overview")
    called = _called_names(fn)

    assert "get_user_overview" in called, (
        "agent_cost_overview 没有调 get_user_overview —— 数据源可能又退回进程内存了"
    )
    leaked = called & _MEMORY_ONLY_SUMMARIES
    assert not leaked, (
        f"agent_cost_overview 还在读进程内存汇总：{sorted(leaked)}\n"
        "  ⚠️ 那些是 defaultdict、重启归零 ⇒ 这个端点会答 0 而不报错。"
    )


def test_user_overview_sql_is_per_user_and_all_time():
    """`get_user_overview` 的 SQL：**按用户**（有 `user_name` 过滤）· **全时**（无 `CURRENT_DATE`）。

    这两条都会**静默失败**：
      · 漏了 `user_name` ⇒ 退化成"全站"，**返回值正常、只是偏大**；
      · 混进 `CURRENT_DATE` ⇒ 退化成"今天"，**返回值正常、只是偏小**。
    ⇒ 与  的守卫（`test_global_daily_budget_offline.py`）是**同一族**，方向相反。
    """
    import billing.token_tracker as token_tracker

    fn = _find_fn(_API / "billing/token_tracker.py", "get_user_overview")
    sqls = _sql_literals(fn)
    assert sqls, "get_user_overview 里没有可解析的 SQL 字面量"

    sql = " ".join(sqls)
    assert "user_name" in sql, "SQL 里没有 user_name 过滤 ⇒ 退化成全站口径（静默偏大）"
    assert "CURRENT_DATE" not in sql, (
        "SQL 里出现了 CURRENT_DATE ⇒ 退化成当天口径（静默偏小）\n"
        "  ⚠️ 本函数的口径是【全时累计】；'今天'那类问题由 get_token_budget_info"
        " / get_daily_usage_cost 回答。"
    )
    assert "token_usage_logs" in sql, "SQL 没落在 token_usage_logs 上"
    # 签名核对：这个函数必须真的存在且可调用（别只靠 AST 找到名字）
    assert callable(token_tracker.get_user_overview)


# ==================== ② 全站日级额度要有出口 ====================

def test_token_budget_endpoint_exposes_global_daily_budget():
    """问题②的判据：`/agent/token/budget` 必须带出**全站**日级额度。

    / 让全站超 `1,000,000` tokens 时**所有人**吃 429 ——
    但在此之前，这个数**任何界面都看不到**（`get_global_daily_token_usage`
    全仓只被 `breaker` 调过）⇒ 看不见它逼近。
    """
    fn = _find_fn(_API / "routing/api_v1_agent.py", "agent_token_budget")
    called = _called_names(fn)
    loaded = _loaded_names(fn)

    assert "get_global_daily_token_usage" in called, (
        "agent_token_budget 没有读 get_global_daily_token_usage ⇒ 全站额度仍然没有出口"
    )
    assert "GLOBAL_DAILY_TOKEN_LIMIT" in loaded, (
        "agent_token_budget 没有带上 GLOBAL_DAILY_TOKEN_LIMIT ⇒ 客户端算不出『全站还剩多少』"
    )


def test_dashboard_summary_shows_global_daily_budget():
    """看板那一格也必须看得见全站额度（只加 API 不加界面，等于半个出口）。"""
    fn = _find_fn(_API / "billing" / "cost_dashboard.py", "get_dashboard_summary")
    assert "get_global_daily_token_usage" in _called_names(fn), (
        "看板的 get_dashboard_summary 没读全站用量 ⇒ 界面上看不到全站额度"
    )
