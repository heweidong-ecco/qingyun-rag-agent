"""三条【会真调 LLM】的链：每个调用点必须**先拦后记**，且端点必须把身份塞进 state。

## 为什么需要它 —— `test_session_budget_wiring.py` **一直是绿的**

那边断言的是「端点函数体里调了 `check_session_token_budget`」。
**三条链全都调了，那条测试全绿** —— 而它们**从不往 `token_usage_logs` 写一行**
⇒ `get_session_token_usage` 恒为 0、`get_global_daily_token_usage` 也看不到它们
⇒ **端点上的 B8 会话上限 + B11 全站熔断，对这三条链等于不存在**。

> **门在，锁坏了。** 这是本仓 本仓的复盘记录（⛔ 不随产品仓提供）
> 「**门挂在别处，就等于没有门**」的同族 —— 这次门**挂在正确的位置**，
> 而**门后面的计量表没接上**。本文件钉的就是**那张表**。

裁定与逐文件清单 ⇒ 本仓的决策记录（⛔ 不随产品仓提供）

## 两条必须一起读的话

1. **本文件上半是【形状】判据** —— 本仓对它有过教训（：**守卫的形状盲区**）
   ⇒ **不许只留那一条**。下半（`-k behavior`）是**行为**判据
   （假 LLM 驱动三张图，断言 `record_usage` 真的收到对的 `user_name`/`thread_id`）。
   > **两者缺一都不够**：上半能过而记账写错参数（形状对、语义错）；
   > 下半能过而某个节点又被人删了守卫（行为对、覆盖面漏）。
2. **它红了不要删它** —— 红的意思是「这条链又开始免费跑了」。
   若你**有意**摘掉某条链的记账，先改  的范围表，**再**改本文件的 `GRAPHS`/`ENDPOINTS`。

## 为什么用 AST，不用 grep

`record_usage` / `check_token_budget` 这些词**在注释与 docstring 里也大量出现**
（本仓三张图的注释都很长）⇒ grep 会把「注释里提到」当成「代码里调了」。
同款理由见 `app/tests/test_session_budget_offline.py` 的文件头。
"""
import ast
import pathlib

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

import agent.agent_checkpointer as ac
import agent.agent_graph as agent_graph
import agent.agent_graph_advanced_learning as agl
import billing.token_tracker as token_tracker
# 复用既有夹具（不复制第三份）：真流式的假模型与 `bind_tools` 的替代品。
from test_agent_sse import _FakeStreamingModel, _bind

_API = pathlib.Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层

# 三张会真调 LLM 的图（§一）
GRAPHS = [
    "agent/agent_graph.py",                      # /agent/langgraph_chat
    "agent/agent_checkpointer.py",               # /agent/memory_chat
    "agent/agent_graph_advanced_learning.py",    # /agent/advanced_chat
]

# 6 个端点：各自必须把身份塞进【初始 state】
ENDPOINTS = [
    ("routing/api_v1_agent.py", "langgraph_chat"),
    ("routing/api_v1_agent.py", "langgraph_chat_stream"),
    ("routing/api_v1_agent.py", "advanced_agent_chat"),
    ("routing/api_v1_agent.py", "advanced_agent_chat_stream"),
    ("routing/api_v1_agent.py", "memory_chat"),
    ("routing/api_v1_agent.py", "memory_chat_stream"),
]

GUARD = "check_token_budget"
BILL = "record_from_response"
STATE_KEYS = {"user_name", "thread_id"}
# `invoke` / `astream` 一族 —— 端点就是用它们把初始 state 喂进图的
_GRAPH_CALLS = ("invoke", "ainvoke", "stream", "astream", "batch", "abatch")


def _own_called_names(fn: ast.AST) -> set:
    """函数**自己**调到的名字 —— **不下钻到它内部定义的嵌套函数 / lambda**。

    **为什么必须用「自己的」**：这三张图大量用「工厂函数里套节点函数」
       （`create_search_subgraph` 里定义 `search_summarize`）。
       若下钻，`create_react_subgraph` 会因为**内层** `agent_decide` 调了守卫
       而被判为「已拦已记」—— **而它自己那个 LLM 调用点仍然裸奔**。那是**假通过**，
       比漏报更坏：它会让这个守卫在「有人把记账写进另一个嵌套函数」时静默失效。
    """
    names = set()

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue                      # 别人的函数，不归我
            if isinstance(child, ast.Call):
                f = child.func
                names.add(getattr(f, "id", None) or getattr(f, "attr", None))
            walk(child)

    walk(fn)
    return names


def _llm_sites_by_innermost_function(tree: ast.AST):
    """每个 LLM 调用点**只归给最内层**包住它的那个函数。

    ⇒ 同一个调用点**不会被内外两层各报一次**（否则 `agent_graph_advanced_learning.py`
      会报出 11 条，其中 5 条是重复的，看输出的人会以为有 11 个洞）。
    """
    found = {}                                # id(node) -> [node, [call_node, ...]]

    def visit(node, current_fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current_fn = node
            found.setdefault(id(node), [node, []])
        if current_fn is not None and isinstance(node, ast.Call) and _is_llm_call(node):
            found[id(current_fn)][1].append(node)
        for child in ast.iter_child_nodes(node):
            visit(child, current_fn)

    visit(tree, None)
    return [v for v in found.values() if v[1]]


def _is_llm_call(node: ast.Call) -> bool:
    """只认 `llm…` 开头对象上的流式/调用方法。

    **判据是「对象名以 `llm` 开头」** —— 因为工具也叫 `.invoke`
       （`date_today.invoke("")` · `web_search.invoke(q)` · `calculator.invoke(e)`），
       把它们算进来会让「不调 LLM 的节点」被误判成「调了 LLM 却不记账」。
       本仓的取名恰好是干净的：三张图里所有模型对象都叫 `llm` / `llm_xxx`。
    """
    f = node.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr in ("stream", "invoke", "astream", "ainvoke")
        and isinstance(f.value, ast.Name)
        and f.value.id.startswith("llm")
    )


def _find_function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


@pytest.mark.parametrize("filename", GRAPHS)
def test_every_llm_node_both_guards_and_bills(filename):
    """**每个**调 LLM 的节点函数，必须同时出现「拦」与「记」。

    本测试红了不要删 —— 它红的意思是"这张图里有个节点又开始免费跑 LLM 了"。
    """
    tree = ast.parse((_API / filename).read_text(encoding="utf-8"))

    offenders = []
    for node, llm_sites in _llm_sites_by_innermost_function(tree):
        names = _own_called_names(node)
        missing = [g for g in (GUARD, BILL) if g not in names]
        if missing:
            offenders.append(
                f"  {filename}:{node.lineno} {node.name}() —— 有 LLM 调用点 "
                f"(行 {[n.lineno for n in llm_sites]})，缺 {missing}"
            )

    assert not offenders, (
        f"{filename} 里有节点调了 LLM 却**没先拦后记**：\n" + "\n".join(offenders)
        + "\n  ⇒ 这条链对配额等于不存在（守卫读的计数器它从不写），而**所有测试照样全绿**。"
    )


@pytest.mark.parametrize("filename,func_name", ENDPOINTS)
def test_endpoint_passes_identity_into_state(filename, func_name):
    """端点喂给图的**初始 state** 必须含 `user_name` 与 `thread_id`。

    **为什么这条不能省**：图里记账读的是 `state.get("thread_id", "unknown")` ——
       **端点不传，它不会报错**，只会**静默记成 `"unknown"`**
       ⇒ 账是记上了，但**归不到任何人头上**（配额照样拦不住具体的人）。
    """
    tree = ast.parse((_API / filename).read_text(encoding="utf-8"))
    fn = _find_function(tree, func_name)
    assert fn is not None, (
        f"{filename} 里找不到函数 {func_name}() —— 端点被改名/删掉了？"
        "  那要同步改本文件的 ENDPOINTS 与  的范围表"
    )

    keys = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _GRAPH_CALLS or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Dict):
            keys |= {
                k.value for k in first.keys if isinstance(k, ast.Constant)
            }
        elif isinstance(first, ast.Lambda):
            # `/agent/*/stream` 那几条把 state 写在 `lambda: graph.astream({...})` 里
            for inner in ast.walk(first):
                if isinstance(inner, ast.Dict):
                    keys |= {
                        k.value for k in inner.keys if isinstance(k, ast.Constant)
                    }

    missing = STATE_KEYS - keys
    assert not missing, (
        f"{filename}:{fn.lineno} 的 {func_name}() 初始 state 缺 {sorted(missing)}\n"
        "  ⇒ 图里会把账记成 'unknown'（**不报错、不 500**），配额拦不住具体的人。\n"
        "  修法：往那个 dict 里补 `\"user_name\": user_name, \"thread_id\": thread_id`"
        "（⚠️ `thread_id` 传**原值**，⛔ 不是 `sess`）。"
    )


# ============================================================================
# 下半 · 【行为】判据（`-k behavior`）—— 真图 + 假模型，看 `record_usage` 收到什么
# ============================================================================
# **为什么形状判据不够**：上半只能证明"函数里出现了那个名字"。
#    它**证明不了**参数对不对 —— 传 `thread_id` 却把 `sess` 当值传进去、或
#    `purpose` 填错、或模型名写死，上半**一条都抓不到**。
# 反向也成立：行为判据只跑**被 invoke 到的那条分支** ⇒ ①②两条都要留。

ID = {"user_name": "isolation_a", "thread_id": "A-thread-001"}
USAGE = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


class _BillingFakeModel(_FakeStreamingModel):
    """假模型：**流式**在最后一块挂 `usage_metadata`，**非流式**回一个路由词。

    非流式那条必须自己实现 —— `_FakeStreamingModel._generate` 是**故意 raise** 的
       （那份文件要求"必须走流式"），而 `supervisor` 用的正是 `llm.invoke`。
    """

    model_name: str = "fake-billing-model"
    route_word: str = "REACT"

    def bind_tools(self, tools, **kw):
        """必须实现 —— react 子图在**构建期**就 `llm_react.bind_tools(tools)`，
           而 `BaseChatModel.bind_tools` 默认是 `raise NotImplementedError`。
           返回 `_bind(self)`：形态与真 `bind_tools` 一致（`RunnableBinding`，能透传 `config`）。"""
        return _bind(self)

    def _stream(self, messages, stop=None, run_manager=None, **kw):
        yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kw)
        # 照抄真 provider：`usage_metadata` 挂在**最后一块**（`content=""`）上
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", usage_metadata=dict(USAGE)))

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=self.route_word, usage_metadata=dict(USAGE)))])


class _ExplodingModel(_FakeStreamingModel):
    """**一被调用就炸**的假模型 —— 用来证明"拦在 LLM 调用【之前】"。"""

    def _stream(self, messages, stop=None, run_manager=None, **kw):
        raise AssertionError("预算已用完却仍然调了模型 ⇒ 守卫没拦住（拦在调用【之后】＝没拦）")

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        raise AssertionError("预算已用完却仍然调了模型 ⇒ 守卫没拦住")


def _capture_records(monkeypatch) -> list:
    """把真记账换掉 —— 三条链**最终都汇到 `token_tracker.record_usage` 这一个口**。"""
    rows = []
    monkeypatch.setattr(token_tracker, "record_usage", lambda **kw: rows.append(kw))
    return rows


def _allow_budget(monkeypatch, *modules):
    """把预算守卫放行（真实现要连库）。别删 —— 删了用例会去连真库。"""
    for mod in modules:
        monkeypatch.setattr(mod, "check_token_budget", lambda *a, **k: True)


def _state(**overrides):
    return {"messages": [HumanMessage(content="你好")], **ID, **overrides}


def _assert_identity(rows):
    """**每一笔**的行都必须带对的身份 —— 这是本组用例的核心断言。"""
    assert rows, "一笔账都没记上 ⇒ 这条链又在免费跑"
    for i, row in enumerate(rows):
        assert row["user_name"] == ID["user_name"], (
            f"第 {i} 笔的 user_name 是 {row['user_name']!r}，不是 {ID['user_name']!r}"
            " —— 账记上了但归不到具体的人头上")
        assert row["thread_id"] == ID["thread_id"], (
            f"第 {i} 笔的 thread_id 是 {row['thread_id']!r}，不是 {ID['thread_id']!r}"
            " —— ⚠️ 常见错因：把 `session_key()` 的 `sess` 当原值传进来了")


def test_behavior_chain1_langgraph_records_identity(monkeypatch):
    """链 1（`/agent/langgraph_chat`）—— 真图 + 假模型，记 1 笔且身份正确。"""
    rows = _capture_records(monkeypatch)
    _allow_budget(monkeypatch, agent_graph)
    monkeypatch.setattr(agent_graph, "llm_with_tools", _bind(_BillingFakeModel()))
    # 2026-10-13：本图首轮会检索长期记忆（走外网 embedding）⇒ 打桩。
    import agent.memory_store as _ms
    monkeypatch.setattr(_ms, "search_user_memory", lambda *a, **k: [])

    agent_graph.build_agent_graph().invoke(
        _state(), config={"configurable": {"thread_id": "sess-a"}})

    _assert_identity(rows)
    assert len(rows) == 1, f"本图只有 1 个 LLM 调用点，实记 {len(rows)}"
    assert rows[0]["purpose"] == "agent_decision"
    assert rows[0]["model"] == "fake-billing-model", "⛔ 模型名不许写死"
    assert (rows[0]["prompt_tokens"], rows[0]["completion_tokens"]) == (11, 7)


def test_behavior_chain3_memory_records_identity(monkeypatch):
    """链 3（`/agent/memory_chat`）—— **本用例是那个 bug 的行为层墓碑**。

    改之前：`hasattr(response, "usage")` 恒假 ⇒ `record_usage` **从来没被调到过**
       ⇒ 本用例会以「一笔账都没记上」**变红**。
    改之后：记 1 笔，`purpose` 是 `agent_decision`（不再是那个错的 `query_rewrite`）。
    """
    rows = _capture_records(monkeypatch)
    _allow_budget(monkeypatch, ac)
    monkeypatch.setattr(ac, "llm_with_tools", _bind(_BillingFakeModel()))

    ac.build_checkpointer_agent("memory").invoke(
        _state(), config={"configurable": {"thread_id": "sess-a"}})

    _assert_identity(rows)
    assert len(rows) == 1, f"本图只有 1 个 LLM 调用点，实记 {len(rows)}"
    assert rows[0]["purpose"] == "agent_decision", (
        "⛔ 不许退回 `query_rewrite` —— 这是个对话端点，不是查询改写")


def test_behavior_chain2_advanced_records_identity(monkeypatch):
    """链 2（`/agent/advanced_chat`）—— 6 个调用点里走到 **2 个**（supervisor + react agent）。

    路线：`supervisor` 回 `"REACT"` ⇒ react 子图 ⇒ 假模型不吐 `tool_calls` ⇒ `summarize`。
    **只覆盖 2 个节点是故意的**：另外 4 个在别的分支上，要各跑一遍得再造 4 套假回复
       （而且 `search` 分支会**真联网**）。本用例要证的是"**同一张图里跨节点身份一致**"，
       不是"每个节点都覆盖到" —— 那件事由上半的 AST 判据按节点兜。
    """
    rows = _capture_records(monkeypatch)
    _allow_budget(monkeypatch, agl)
    fake = _BillingFakeModel()
    # ① `make_llm`：react 子图的 `llm_react` 是在 `create_react_subgraph()` **构建期**造的
    #    这里给**裸模型**（不是 `_bind(fake)`）—— 下面它自己要 `bind_tools`
    monkeypatch.setattr(agl, "make_llm", lambda *a, **k: fake)
    # ② 模块级实例：节点在**调用期**按名字找它们（光打 `make_llm` 管不到这几个）
    for name in ("llm", "llm_search", "llm_calc", "llm_date"):
        monkeypatch.setattr(agl, name, fake)
    # ③ mem0 两个入口：不打 ⇒ `supervisor` 会**真去连 mem0**（网络 + 慢）
    monkeypatch.setattr(agl, "search_user_memory", lambda *a, **k: [])
    monkeypatch.setattr(agl, "inject_memories_to_prompt", lambda prompt, state: prompt)

    result = agl.build_advanced_agent().invoke(
        _state(memory_space="default"),
        config={"configurable": {"thread_id": "sess-a"}},
    )

    assert result.get("intent") == "REACT", f"假模型没把意图路由成 REACT：{result.get('intent')!r}"
    _assert_identity(rows)
    assert len(rows) == 2, (
        f"supervisor + react agent 应各记 1 笔，实记 {len(rows)}："
        f"{[r['purpose'] for r in rows]}")
    assert {r["purpose"] for r in rows} == {"agent_decision"}


def test_behavior_missing_identity_falls_back_not_crash(monkeypatch):
    """端点**没**传身份时 ⇒ 记成 `"unknown"`，**不是 `KeyError` / 500**。

    为什么这条要单独留着：图里一律用 `.get(..., "unknown")` 而**不是下标**。
       旧调用方（`api_v1.py` / `api_v1_rag.py`）不传这两个键 —— 用下标会把那些路径**打挂**。
       缺身份**只该漏记到 "unknown"，不该让请求 500**。
    """
    rows = _capture_records(monkeypatch)
    _allow_budget(monkeypatch, agent_graph)
    monkeypatch.setattr(agent_graph, "llm_with_tools", _bind(_BillingFakeModel()))
    # 2026-10-13：同上 —— 首轮会检索长期记忆。
    import agent.memory_store as _ms
    monkeypatch.setattr(_ms, "search_user_memory", lambda *a, **k: [])

    agent_graph.build_agent_graph().invoke(
        {"messages": [HumanMessage(content="你好")]},   # 故意不带 user_name / thread_id
        config={"configurable": {"thread_id": "sess-a"}},
    )

    assert len(rows) == 1
    assert rows[0]["user_name"] == "unknown"
    assert rows[0]["thread_id"] == "unknown"


def test_behavior_budget_exceeded_never_calls_the_model(monkeypatch):
    """守卫必须拦在**调用之前** —— 放之后钱已经花了，只能丢结果，等于没拦。

    假模型被换成**一调就炸**的那个：守卫若写错位置，本用例会以 `AssertionError` 变红，
       而不是"照样绿、只是答案被换成话术"。
    """
    rows = _capture_records(monkeypatch)
    monkeypatch.setattr(agent_graph, "check_token_budget", lambda *a, **k: False)
    monkeypatch.setattr(agent_graph, "llm_with_tools", _bind(_ExplodingModel()))

    out = agent_graph.build_agent_graph().invoke(
        _state(), config={"configurable": {"thread_id": "sess-a"}})

    assert out["messages"][-1].content == token_tracker.BUDGET_EXCEEDED_MSG
    assert rows == [], "被拦下来的那次**没花钱** ⇒ ⛔ 一笔账都不该记"
