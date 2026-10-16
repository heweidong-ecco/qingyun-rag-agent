"""待接管队列的【接线】守卫· **纯离线，进 CI**。
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


# ==================== ① · 登记（langgraph_chat） ====================

def test_langgraph_chat_registers_when_stopped():
    """卡住时必须登记 —— 否则 `/agent/pending` 永远答 `count: 0`。"""
    fn = _find_fn(_API / "routing/api_v1_agent.py", "langgraph_chat")
    assert "register" in _called_names(fn), (
        "langgraph_chat 没有调 register ⇒ 卡在审批的会话不会被登记 ⇒ 队列永远是空的"
    )


def test_langgraph_chat_resolves_when_not_stopped():
    """**本轮没卡住 ⇒ 必须清掉上一次的登记**，否则那个 thread 会变成**假待办**。

    这条是**成对的**：只查 `register` 会漏掉这个更阴的错 ——
       它**不会让队列变空**，而是让它**越长越假**，且**没有任何报错**。
    """
    fn = _find_fn(_API / "routing/api_v1_agent.py", "langgraph_chat")
    assert "resolve" in _called_names(fn), (
        "langgraph_chat 只登记不注销 ⇒ 卡过一次的 thread 会永远留在队列里（假待办）"
    )


# ==================== ② · 注销（approve） ====================

def test_approve_resolves_the_thread():
    """批完（或拒完）必须注销 —— 否则前端显示"还在等"，其实早处理过了。"""
    fn = _find_fn(_API / "routing/api_v1_agent.py", "approve_agent_action")
    assert "resolve" in _called_names(fn), (
        "approve_agent_action 没有调 resolve ⇒ 批过的会话仍挂在队列里"
    )


# ==================== ③ · 端点本身 ====================

def test_pending_endpoint_reads_the_registry():
    """端点必须真的去读那张表（不是返回硬编码的空列表）。"""
    fn = _find_fn(_API / "routing/api_v1_agent.py", "list_pending_approvals")
    assert "list_pending" in _called_names(fn), (
        "list_pending_approvals 没有调 list_pending ⇒ 端点答的不是队列的真内容"
    )


def test_pending_endpoint_is_routed():
    """路由得挂上 —— 否则前面全对，**界面上仍然找不到入口**（硬门 D 判据③ 的反例）。"""
    fn = _find_fn(_API / "routing/api_v1_agent.py", "list_pending_approvals")
    routes = set()
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and dec.args:
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                routes.add(arg.value)
    assert "/agent/pending" in routes, (
        f"list_pending_approvals 的装饰器里没有 /agent/pending ⇒ 入口不存在（实际：{routes}）"
    )


# ==================== ④ · 启动自检 ====================

def test_startup_warns_about_backend_mismatch():
    """sqlite 后端下队列会静默丢 ⇒ **唯一的防线**是启动时那条警告，必须是接上的。"""
    fn = _find_fn(_API / "main.py", "startup_event")
    assert "warn_if_backend_mismatch" in _called_names(fn), (
        "startup_event 没有调 warn_if_backend_mismatch ⇒ sqlite 后端下队列丢了也不吭声"
    )
