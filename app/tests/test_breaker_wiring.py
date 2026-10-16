"""**接线**回归：每一条真烧钱的链，都要真的问过断路器。
"""
import ast
import pathlib

import pytest

# (文件, 函数名) —— 表就是**权威清单**，与 `test_session_budget_wiring.py::EXPECTED_WIRED` 同性质
EXPECTED_WIRED = [
    # ===== 与 B8 完全相同的 7 条 =====
    ("routing/api_v1_agent.py", "langgraph_chat"),        # POST /agent/langgraph_chat
    ("routing/api_v1_agent.py", "advanced_agent_chat"),   # POST /agent/advanced_chat
    ("routing/api_v1_agent.py", "agent_plan_execute"),    # POST /agent/plan_execute
    ("routing/api_v1_agent.py", "memory_chat"),           # POST /agent/memory_chat
    ("routing/api_v1_agent.py", "mcp_agent_chat"),        # POST /agent/mcp_chat
    ("routing/api_v1_rag.py", "stream_search"),           # POST /rag/stream_search（SSE）
    ("routing/api_v1_rag.py", "agent_websocket"),         # WS   /ws/agent
    # ===== B11 新增：唯一一条【匿名可打且真花钱】的 =====
    ("routing/api_v1.py", "benchmark_embedding"),         # POST /rag/benchmark-embedding
]

# 用 `circuit` 这个【调用名】匹配。全仓已核：`circuit(` 只在 breaker.py 定义，
#    无同名函数 ⇒ 不会误判（别改成 `check_global_daily_budget` ——
#    那是被派发的一端，直接调它 = 绕开断路器这一层）。
GUARD = "circuit"
_API = pathlib.Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层


def _calls_in(fn: ast.AST) -> set:
    """函数体里**被调用到的名字**（只看 `Name` / `Attribute`，不看注释与字符串）。"""
    names = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        names.add(getattr(f, "id", None) or getattr(f, "attr", None))
    return names


def _find_function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


@pytest.mark.parametrize("filename,func_name", EXPECTED_WIRED)
def test_endpoint_calls_breaker(filename, func_name):
    """每一条链都必须**真的**调用 `circuit`。

    本测试**红了不要删它** —— 它红的意思是"这条链现在对全站熔断**完全不设防**"。
       若你是**故意**要摘掉某条链，先改本文件的 `EXPECTED_WIRED`，**再**动代码。
    """
    tree = ast.parse((_API / filename).read_text(encoding="utf-8"))
    fn = _find_function(tree, func_name)

    assert fn is not None, (
        f"{filename} 里找不到函数 {func_name}() —— 端点被改名/删掉了？"
        "  那要同步改本文件的 EXPECTED_WIRED"
    )
    assert GUARD in _calls_in(fn), (
        f"{filename}:{fn.lineno} 的 {func_name}() **没有**调用 {GUARD}()\n"
        "  ⇒ 这条链不受全站日级熔断约束，而**所有测试照样全绿**（这正是本文件存在的理由）。"
    )


# ============ 【已删除】test_the_two_non_spending_endpoints_stay_unwired ============
# `/rag/parallel_ask`）**不许**被接上断路器（接上 ⇒ 全站额度用尽时，不花额度的接口也被 429）。
