"""**接线**回归：7 条真正调 LLM 的对话链，每条都要真的问过会话预算。
"""
import ast
import pathlib

import pytest

# (文件, 函数名) —— 表就是**权威清单**，与 `test_max_tokens_wiring.py` 的 `EXPECTED_ROLES` 同性质
#   后者 （`①b`）由 `EXPECTED_MAX_TOKENS` 改名而来。
EXPECTED_WIRED = [
    ("routing/api_v1_agent.py", "langgraph_chat"),        # POST /agent/langgraph_chat
    ("routing/api_v1_agent.py", "advanced_agent_chat"),   # POST /agent/advanced_chat
    ("routing/api_v1_agent.py", "agent_plan_execute"),    # POST /agent/plan_execute
    ("routing/api_v1_agent.py", "memory_chat"),           # POST /agent/memory_chat
    ("routing/api_v1_agent.py", "mcp_agent_chat"),        # POST /agent/mcp_chat
    ("routing/api_v1_rag.py", "stream_search"),           # POST /rag/stream_search（SSE）
    ("routing/api_v1_rag.py", "agent_websocket"),         # WS   /ws/agent
]

GUARD = "check_session_token_budget"
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
def test_endpoint_calls_session_budget(filename, func_name):
    """每一条链都必须**真的**调用 `check_session_token_budget`。

    本测试**红了不要删它** —— 它红的意思是"这条链现在对会话上限**完全不设防**"。
       若你是**故意**要摘掉某条链（比如它改成纯检索了），
       请先改  的范围表，**再**改本文件的 `EXPECTED_WIRED`。
    """
    tree = ast.parse((_API / filename).read_text(encoding="utf-8"))
    fn = _find_function(tree, func_name)

    assert fn is not None, (
        f"{filename} 里找不到函数 {func_name}() —— 端点被改名/删掉了？"
        "  那要同步改本文件的 EXPECTED_WIRED 与  的范围表"
    )
    assert GUARD in _calls_in(fn), (
        f"{filename}:{fn.lineno} 的 {func_name}() **没有**调用 {GUARD}()\n"
        "  ⇒ 这条对话链不受会话上限约束，而**所有测试照样全绿**（这正是本文件存在的理由）。\n"
        "  若是有意移除，先改  的范围表。"
    )


# ============ 【已删除】test_the_two_non_llm_endpoints_stay_unwired ============
# `/rag/parallel_ask`）**不许**被接上会话上限（接上 ⇒ 用户为一次**没发生**的 LLM 调用被扣额度）。
