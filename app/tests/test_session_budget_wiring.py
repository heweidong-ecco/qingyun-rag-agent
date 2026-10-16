""" **接线**回归：7 条真正调 LLM 的对话链，每条都要真的问过会话预算。

## 为什么需要这个文件（`test_session_budget_offline.py` 不管这件事）

那边测的是**函数本身**对不对（数据源 / 窗口 / key / 判定）。
**函数全对但没人调它，等于没有上限** —— 而且**所有测试照样全绿**。

同型先例就在本仓：本仓的复盘记录（⛔ 不随产品仓提供） ——
   「**门挂在别处，就等于没有门**」。的 `test_max_tokens_wiring.py` 也是为这个才建的。

## 为什么是【这 7 条】（2026-10-01 逐条实测）

只收**真的调 LLM、真的烧 token** 的对话端点。原先另有 **2 条【故意不在表里】** ——
`/rag/async_ask` · `/rag/parallel_ask`（**纯 mock**，`await asyncio.sleep(2)` 后返回假字符串）
⇒ 接上会话上限是**错的**（会让不花钱的接口占额度甚至被拦）。

**2026-10-04：那 2 条端点已【整体删除】** ⇒ 本文件的**反向守卫一并删除**。

**计数沿革**：4 条 →（2026-10-03）3 条（`/rag/ask` 删）
→（2026-10-04）2 条（`/rag/jwt_ask` 删）→ **0 条**（另 2 条端点也删了）。

**反向守卫为什么删，而不是留成空清单**：端点一删，那张豁免清单**空了**
`for` 体**一次都不跑** ⇒ 用例**永远绿**，**什么也没钉住**。留着一个永远绿的用例
比删掉更坏 —— 它看起来像"有防护"（本仓 ` 反复记过这一族）。
**保护换了住处**：`app/tests/test_removed_endpoints.py::test_rag_async_ask_stays_removed` /
`::test_rag_parallel_ask_stays_removed`（判据 = **404**）。
**代价**：将来再加「不调 LLM」的端点，**不会有测试自动拉红**叫去接线）。
同款处理在 `test_breaker_wiring.py` 的模块注释里（两张清单同时清空）。

裁定见 本仓的决策记录（⛔ 不随产品仓提供） 备选方案·三
   （本表最初**列错**过 —— 把上面几条也列了进去，核实后才剔除）。
"""
import ast
import pathlib

import pytest

# (文件, 函数名) —— 表就是**权威清单**，与 `test_max_tokens_wiring.py` 的 `EXPECTED_ROLES` 同性质
#   后者 2026-10-02（`①b`）由 `EXPECTED_MAX_TOKENS` 改名而来。
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
# 2026-10-04 删除。原钉：2 条**不调 LLM** 的 mock 端点（`/rag/async_ask`
# `/rag/parallel_ask`）**不许**被接上会话上限（接上 ⇒ 用户为一次**没发生**的 LLM 调用被扣额度）。
#
# **删它的理由不是"不需要了"，是"它已经变成恒绿的假守卫"**：
#    那两条端点已**整体删除** ⇒ 豁免清单**空了** ⇒ `for` 体一次都不跑 ⇒ **永远绿**。
#    留着一个永远绿的用例，比删掉它更坏 —— 它看起来像"有防护"。
#
# **保护没有丢，是换了住处**：`app/tests/test_removed_endpoints.py` 的
#    `test_rag_async_ask_stays_removed` / `test_rag_parallel_ask_stays_removed`（判据 = **404**）。
#
# **代价（知道再选）**：将来若再加一条「不调 LLM」的端点，**不会有测试自动拉红**叫去接线。
#    处理要求写在模块 docstring 与 本仓的决策记录（⛔ 不随产品仓提供） 里。
# ===================================================================================
