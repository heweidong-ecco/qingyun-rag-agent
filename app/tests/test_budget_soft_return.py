"""批 7：图内「预算已用完」的**软返回** → 429 / error 帧 的判据。
"""
import asyncio
import inspect
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

import agent.agent_checkpointer as ac
import agent.agent_graph as agent_graph
import agent.agent_graph_advanced as aga
import agent.agent_graph_advanced_learning as agl
import routing.api_v1_agent as m
from core.exceptions import AppException, ErrorCode
# 复用现成的假模型夹具，不另写一套（`test_billing_wiring.py:44` 也是这么做的）。
#    `_BillingFakeModel` 必须用**这一份**（不是 `test_agent_sse._FakeStreamingModel`）：
#       `agent_graph_advanced_learning.supervisor` 走的是**非流式** `llm.invoke()`，
#       而 `_FakeStreamingModel._generate` 是**故意 raise** 的（它只在真正的流式用例里成立）。
from test_agent_sse import _bind
from test_billing_wiring import _BillingFakeModel


REASON = "今日预算已用完（已使用 999 tokens，预算 1000 tokens）"
THREAD = "n11-thread"
STALE = "上一轮被拦了"

#: 假 `AIMessage` **必须带 `usage_metadata`** —— 节点会做 `usage.get(...)`，
#: 而 `hasattr(msg, "usage_metadata")` 对**没设过的**消息**也是真**（pydantic 字段默认 `None`）
#: ⇒ 少给这一个键，报的是 `NoneType.get` 的 AttributeError。
_USAGE = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}


def _state(**overrides):
    base = {"messages": [HumanMessage(content="你好")],
            "user_name": "u", "thread_id": THREAD, "memory_space": "default"}
    base.update(overrides)
    return base


class _ExplodingLLM:
    """**一被调用就炸**的假模型 —— 用来证明"拦在 LLM 调用【之前】"。

    少了这一层，用例只证明"结果里有标志"，证明不了"没白花钱" ——
       而"拦在调用之后"＝**没拦**（钱已经花了）。同 `test_billing_wiring.py` 的 `_ExplodingModel`。
    """

    model_name = "exploding"

    def _boom(self, *a, **k):
        raise AssertionError("预算已用完却仍然调了模型 ⇒ 守卫没拦住（拦在调用【之后】＝没拦）")

    stream = _boom
    astream = _boom
    invoke = _boom
    ainvoke = _boom

    def bind_tools(self, tools, **kw):
        return self


class _RouteFake(_BillingFakeModel):
    """正常的假模型（**流式 + 非流式都实现**）—— 预算够的时候用它跑完整轮。"""


# ==================== ① 文案：一个纯函数，所有端点共用 ====================

def test_拦截文案带上原因且不写死工具调用():
    """文案必须是**本轮**口径，不是「本次工具调用未执行」。

    理由：本批新增的 10 处软返回里，绝大多数**根本没有工具调用**
    （四张图的**入口节点**在调 LLM 之前就拦下了）⇒ 旧文案对它们**不准**。
    """
    msg = m.agent_budget_intercept_message(REASON)
    assert REASON in msg, f"文案里没带上原因，调用方看不出为什么：{msg!r}"
    assert "工具调用" not in msg, (
        f"文案写死了「工具调用」—— 而节点级软返回根本没有工具调用：{msg!r}"
    )


def test_拦截文案原因缺失时不返回空串():
    """图写了标志却没写原因 = 写入方的 bug。别静默变成空串 —— 调用方比现在还看不懂。"""
    for empty in ("", "   ", None):
        msg = m.agent_budget_intercept_message(empty)
        assert msg.strip(), f"{empty!r} 拼出了空串（`{{\"error\": \"\"}}` 跑出去前端等于没有错误）"


# ==================== ② 四张图：软返回置标志 + 拦在 LLM 调用之前 ====================
# **为什么是【直调节点】，不是"跑整张图再看最终 state"**

def _node_fn(graph, name, dept=None):
    """从**编译好的图**里取回节点函数本身。

    `func` 是同步节点、`afunc` 是异步节点（实测：`aga.chat_node` 只有 `afunc`）。
    取不到时**必须炸**，不许 `getattr(..., None)` 静默返回 —— 那会让整条判据
       退化成"调了个空函数"，而它**照样绿**。
    """
    g = graph.nodes[dept].bound if dept else graph
    b = g.nodes[name].bound
    fn = b.func or b.afunc
    assert fn is not None, f"从图里取不到节点 `{dept or ''}/{name}` 的函数 ⇒ 本用例的判据失效"
    return fn


def _call_node(fn, state, config=None):
    """直调节点。按**签名**决定传不传 `config` —— 本仓节点不统一
    （`search_summarize(state, config)` / `calc_execute(state)` 两种都有）。"""
    args = (state, config or {}) if len(inspect.signature(fn).parameters) >= 2 else (state,)
    return asyncio.run(fn(*args)) if inspect.iscoroutinefunction(fn) else fn(*args)


def _assert_marks(result, where):
    assert result and result.get("budget_intercept"), (
        f"[{where}] 被预算拦下了却没在 state 上留原因 ⇒ 端点无从转成 429：{result}"
    )


def _patch_no_llm(monkeypatch, mod, *names):
    """把该模块的预算检查打成"不够"，并把所有 LLM 打成**一调就炸**。"""
    monkeypatch.setattr(mod, "check_token_budget",
                        lambda u, estimated_tokens=500: False)
    for name in names:
        monkeypatch.setattr(mod, name, _ExplodingLLM(), raising=False)


def _no_memory(monkeypatch, mod, *names):
    """预算已被拒 ⇒ **不许再花那笔 embedding 的钱**（本批顺带堵掉的一类漏网）。

    改前 `agent_graph_advanced.chat_node` 与 `agent_graph_advanced_learning.supervisor`
    把记忆检索排在门**之上** ⇒ 被拒的那一轮照样打一次 DashScope embedding，
    而取回来的记忆**当场被丢弃**（走不到 `messages`）。
    这条漏洞最初是**在 CI（dummy key）里现形**的 —— 本机 `.env` 有真 key 所以"绿"。
    ⇒ 现在写成**一调就炸**：别退回"反正没打网络"这个看不见的事实。
     §四·
    """
    def _bomb(*a, **k):
        raise AssertionError(
            "预算已被拒，却仍然去取长期记忆（真打一次 embedding）⇒ 钱花在闸之前"
        )
    for name in names:
        monkeypatch.setattr(mod, name, _bomb, raising=False)


def test_agent_graph_软返回时置标志(monkeypatch):
    """`/agent/langgraph_chat` · `/agent/langgraph_chat/stream` 走的就是这张图。"""
    _patch_no_llm(monkeypatch, agent_graph, "llm_with_tools")

    _assert_marks(_call_node(agent_graph.agent_decide, _state()),
                  "agent_graph.agent_decide")


def test_agent_checkpointer_软返回时置标志(monkeypatch):
    """`/agent/memory_chat` · `/agent/memory_chat/stream` 走的就是这张图。"""
    _patch_no_llm(monkeypatch, ac, "llm_with_tools")

    _assert_marks(_call_node(ac.agent_decide, _state()),
                  "agent_checkpointer.agent_decide")


def test_mcp_agent_每个软返回出口都置标志(monkeypatch):
    """`/agent/mcp_chat` · `/agent/mcp_chat/stream` 走的就是这张图（治过工具那一类）。

    **两个出口都测** —— 这正是"掩蔽"发生的地方（见本节头注）。
    """
    async def _exploding_tools():
        return _ExplodingLLM()

    _patch_no_llm(monkeypatch, aga, "llm")
    monkeypatch.setattr(aga, "get_llm_with_mcp_tools", _exploding_tools)
    # 兜底的 `chat_node` 改前是"先取记忆、后查预算" ⇒ 被拒也花一次 embedding。见 `_no_memory`。
    _no_memory(monkeypatch, aga, "inject_memories_to_prompt")
    graph = aga.build_mcp_agent()

    _assert_marks(_call_node(_node_fn(graph, "agent"), _state()),
                  "mcp_agent.agent_decide（入口）")
    _assert_marks(_call_node(_node_fn(graph, "chat"), _state()),
                  "mcp_agent.chat_node（兜底）")


def test_advanced_agent_每个软返回出口都置标志(monkeypatch):
    """`/agent/advanced_chat` · `/agent/advanced_chat/stream` 走的就是这张图。

    **6 个出口逐个测**（这就是上一版"跑整张图"漏掉的全部）：
       `supervisor`(入口) · `chat` · `search_dept.search_summarize` · `calc_dept.calc_execute`
       · `translate_dept.translate_execute` · `react_dept.agent_decide`
    """
    _patch_no_llm(monkeypatch, agl, "llm", "llm_search", "llm_calc", "llm_date", "llm_react")
    monkeypatch.setattr(agl, "make_llm", lambda *a, **k: _ExplodingLLM())
    # 入口 `supervisor` 走的是 `search_user_memory`（**不是** `inject_memories_to_prompt`），
    #    `chat_node` / react 的 `agent_decide` 走后者 ⇒ **两条都要挡**，且都要炸。见 `_no_memory`。
    _no_memory(monkeypatch, agl, "inject_memories_to_prompt", "search_user_memory")
    graph = agl.build_advanced_agent()

    sites = [
        ("supervisor（入口）", _node_fn(graph, "supervisor"), None),
        ("chat_node（兜底）", _node_fn(graph, "chat"), None),
        ("search_summarize", _node_fn(graph, "search_summarize", dept="search_dept"), None),
        ("calc_execute", _node_fn(graph, "calc_execute", dept="calc_dept"), None),
        ("translate_execute", _node_fn(graph, "translate_execute", dept="translate_dept"), None),
        # 子图里的**节点名 ≠ 函数名**（实测：`react_dept` 里叫 `agent`，函数叫 `agent_decide`）。
        ("react_dept.agent_decide", _node_fn(graph, "agent", dept="react_dept"), None),
    ]
    for label, fn, cfg in sites:
        _assert_marks(_call_node(fn, _state(), cfg), f"advanced_agent.{label}")

    # `supervisor` 的软返回**必须继续给 `intent`** —— `route_by_intent` 读它，
    #    缺了当场 `KeyError` = **500**（而不是一句"预算用完了"）。只加键、不动 `intent`。
    out = _call_node(_node_fn(graph, "supervisor"), _state())
    assert out.get("intent") == "CHAT", (
        f"`supervisor` 的软返回没给 `intent` ⇒ 路由函数会 KeyError（500，不是一句「预算用完了」）：{out}"
    )


# ==================== ③ 入口清零：上轮的值不许串轮 ====================

def test_agent_graph_上一轮的标志不串轮(monkeypatch):
    monkeypatch.setattr(agent_graph, "check_token_budget",
                        lambda u, estimated_tokens=500: True)
    monkeypatch.setattr(agent_graph, "llm_with_tools", _bind(_RouteFake()))
    monkeypatch.setattr(agent_graph, "record_from_response", lambda *a, **k: None)
    import agent.memory_store as _ms
    monkeypatch.setattr(_ms, "search_user_memory", lambda *a, **k: [])

    out = agent_graph.build_agent_graph().invoke(
        _state(budget_intercept=STALE),
        config={"configurable": {"thread_id": f"{THREAD}-g2"}})

    assert out.get("budget_intercept") is None, (
        f"上一轮的拦截标志串到本轮了：{out.get('budget_intercept')!r} "
        f"⇒ 预算已经恢复，用户正常提问却仍被判成 429"
    )


def test_agent_checkpointer_上一轮的标志不串轮(monkeypatch):
    monkeypatch.setattr(ac, "check_token_budget",
                        lambda u, estimated_tokens=500: True)
    monkeypatch.setattr(ac, "llm_with_tools", _bind(_RouteFake()))
    monkeypatch.setattr(ac, "record_from_response", lambda *a, **k: None)

    out = ac.build_checkpointer_agent(backend="memory").invoke(
        _state(budget_intercept=STALE),
        config={"configurable": {"thread_id": f"{THREAD}-c2"}})

    assert out.get("budget_intercept") is None, (
        f"上一轮的拦截标志串到本轮了：{out.get('budget_intercept')!r}"
    )


def test_advanced_agent_上一轮的标志不串轮(monkeypatch):
    """本图**入口是 `supervisor`**，且它**原地改 `state` 再整个返回**（不是返回增量）
    ⇒ 清零写成 `state["budget_intercept"] = None`。删掉那一句本条必红。"""
    fake = _RouteFake()
    monkeypatch.setattr(agl, "check_token_budget",
                        lambda u, estimated_tokens=500: True)
    monkeypatch.setattr(agl, "make_llm", lambda *a, **k: fake)
    for name in ("llm", "llm_search", "llm_calc", "llm_date", "llm_react"):
        monkeypatch.setattr(agl, name, fake, raising=False)
    monkeypatch.setattr(agl, "search_user_memory", lambda *a, **k: [])
    monkeypatch.setattr(agl, "inject_memories_to_prompt", lambda p, s: p)
    monkeypatch.setattr(agl, "record_from_response", lambda *a, **k: None)

    out = agl.build_advanced_agent().invoke(
        _state(budget_intercept=STALE),
        config={"configurable": {"thread_id": f"{THREAD}-agl2"}})

    assert out.get("budget_intercept") is None, (
        f"上一轮的拦截标志串到本轮了：{out.get('budget_intercept')!r}"
    )


# ==================== ④ 端点：非流式 429 ====================

class _FakeGraph:
    """最小假图（**同步** `invoke`）—— 三条非流式端点用的都是 `graph.invoke`。"""

    def __init__(self, result):
        self._result = result

    def invoke(self, state, config=None, **kw):
        return self._result


def _blocked_result():
    """被拦之后的状态：标志在 state 上（的端点用例同款）。"""
    return {"messages": [AIMessage(content="今日Token预算已用完，请明天再试。")],
            "final_output": "今日Token预算已用完，请明天再试。",
            "budget_intercept": REASON}


def _patch_gates(monkeypatch):
    """两道门 + 追踪/留痕全短路 —— 它们各有自己的用例，碰 Redis/PG。"""
    monkeypatch.setattr(m, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "start_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "finish_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "check_budget_warning",
                        lambda *a, **k: {"warning": False, "message": ""})
    monkeypatch.setattr(m, "persist_turn", lambda *a, **k: None)


def _assert_429(coro):
    with pytest.raises(AppException) as ei:
        asyncio.run(coro)
    assert ei.value.error_code is ErrorCode.QUOTA_EXCEEDED, ei.value.error_code
    assert ei.value.status_code == 429, ei.value.status_code
    assert REASON in (ei.value.message or ""), (
        f"429 的正文里没带上拦截原因，调用方看不出为什么：{ei.value.message!r}"
    )


def test_langgraph_chat_被拦时回429(monkeypatch):
    """改前这里回 **200 + 一句"预算用完了"当答案** —— 调用方**看不出被拒了**。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "agent_graph", _FakeGraph(_blocked_result()))
    _assert_429(m.langgraph_chat(question="q", thread_id=THREAD, user_name="u"))


def test_memory_chat_被拦时回429(monkeypatch):
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "checkpointer_agent", _FakeGraph(_blocked_result()))
    _assert_429(m.memory_chat(question="q", thread_id=THREAD, user_name="u"))


def test_advanced_chat_被拦时回429(monkeypatch):
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "advanced_agent", _FakeGraph(_blocked_result()))
    _assert_429(m.advanced_agent_chat(question="q", thread_id=THREAD, user_name="u"))


def test_没被拦时非流式端点照常回答案(monkeypatch):
    """反向对照：状态里**根本没有 `budget_intercept` 这个键** ⇒ 端点必须用 `.get`。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "advanced_agent", _FakeGraph(
        {"messages": [AIMessage(content="正常答案")], "final_output": "正常答案"}))

    out = asyncio.run(m.advanced_agent_chat(question="q", thread_id=THREAD, user_name="u"))
    assert out["answer"] == "正常答案", out


# ==================== ④ 端点：流式 error 帧 ====================

class _StreamGraph:
    """最小假图：一条上游流 + 一份最终状态（的流式用例同款）。"""

    def __init__(self, values):
        self._values = values

    def astream(self, payload, config, stream_mode, **kw):
        async def gen():
            yield AIMessageChunk(content="正在想"), {"langgraph_node": "agent"}
        return gen()

    async def aget_state(self, config):
        return type("S", (), {"values": self._values})


def _collect_frames(resp):
    async def go():
        out = []
        async for piece in resp.body_iterator:
            out.append(piece.decode() if isinstance(piece, (bytes, bytearray)) else piece)
        return out
    return asyncio.run(go())


def _frames_of(coro):
    """跑一条流式端点，拿回它产出的所有帧（已解析成 dict / 原始串）。"""
    frames = _collect_frames(asyncio.run(coro))
    parsed = []
    for f in frames:
        s = f.strip()
        if s.startswith("data: "):
            body = s[len("data: "):]
            parsed.append(body if body == "[DONE]" else json.loads(body))
        elif s:
            parsed.append(s)
    return parsed


def _assert_error_frames(coro, monkeypatch):
    """流式的判据是「**只发 error 帧 + `[DONE]`**」—— **不许照旧发汇总帧**：
    汇总帧里有 `answer`，读起来就是"这轮正常答完了"，而那正是软拦截在流式上的形态。"""
    seen = []
    monkeypatch.setattr(m, "persist_turn",
                        lambda *a, **k: seen.append(k.get("status")))
    frames = _frames_of(coro)

    assert frames[-1] == "[DONE]", f"最后一帧不是结束哨兵：{frames[-1]!r}"
    errs = [f for f in frames if isinstance(f, dict) and "error" in f]
    assert errs, f"一条 error 帧都没发 ⇒ 前端看到的是「答案说了一半就没了」：{frames}"
    assert REASON in errs[0]["error"], f"error 帧里没带上拦截原因：{errs[0]!r}"
    assert not [f for f in frames if isinstance(f, dict) and "answer" in f], (
        f"发了带 `answer` 的汇总帧 ⇒ 读起来就是「这轮正常答完了」：{frames}"
    )
    assert "error" in seen, (
        f"这次没答成，`persist_turn` 的 status 却是 {seen!r}（应含 'error'）"
    )


def test_langgraph_chat_stream_被拦时发error帧(monkeypatch):
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "agent_graph",
                        _StreamGraph({"messages": [AIMessage(content="x")],
                                      "budget_intercept": REASON}))
    _assert_error_frames(
        m.langgraph_chat_stream(question="q", thread_id=THREAD, user_name="u"), monkeypatch)


def test_memory_chat_stream_被拦时发error帧(monkeypatch):
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "checkpointer_agent",
                        _StreamGraph({"messages": [AIMessage(content="x")],
                                      "budget_intercept": REASON}))
    _assert_error_frames(
        m.memory_chat_stream(question="q", thread_id=THREAD, user_name="u"), monkeypatch)


def test_advanced_chat_stream_被拦时发error帧(monkeypatch):
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "advanced_agent",
                        _StreamGraph({"final_output": "x", "budget_intercept": REASON}))
    _assert_error_frames(
        m.advanced_agent_chat_stream(question="q", thread_id=THREAD, user_name="u"), monkeypatch)


# ==================== ⑤ `/agent/approve`：补门（B8+B11）+ 补出口形状 ====================

OWNER = "u"
SESS = "u:approve-thread"


class _ApproveFakeGraph:
    """`/agent/approve` 的假图。

    **`get_state` 要按调用次序给不同的 `next`** —— 本端点在同一轮里查它**最多三次**
       （停没停在审批点 / 放行后有没有再停 / 触顶强制收尾后有没有再停）。
    **`invoke` 也要按次序给不同的结果** —— 本端点有**两个** `invoke`
       （正常续跑 / 触顶强制收尾）。每次都返回同一份的话，
       第二个 `invoke` 那条用例会被**第一个**检查拦下 ⇒ **为错误的理由变绿**（实测栽过）。
    """

    def __init__(self, next_seq, invoke_seq):
        self._next_seq = list(next_seq)
        self._invoke_seq = list(invoke_seq)
        self.invokes = 0

    def get_state(self, config):
        nxt = self._next_seq.pop(0) if self._next_seq else ()
        return type("S", (), {
            "next": nxt,
            "values": {"messages": [AIMessage(content="", tool_calls=[
                {"name": "calculator", "args": {"expression": "1+1"}, "id": "c1"}])]},
        })

    def update_state(self, config, values=None, as_node=None):
        return None

    def invoke(self, state, config=None, **kw):
        self.invokes += 1
        seq = self._invoke_seq
        return seq.pop(0) if len(seq) > 1 else seq[0]


def _patch_approve(monkeypatch, graph, rounds=1):
    """把队列 / 权限 / 注销全部短路，只留被测的那几段逻辑。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "find_by_raw_thread_id",
                        lambda tid: [{"user_name": OWNER, "thread_id": SESS,
                                      "graph": "agent_graph", "rounds": rounds}])
    monkeypatch.setattr(m, "agent_graph", graph)
    monkeypatch.setattr(m, "get_user_role", lambda u: None)
    resolved = []
    monkeypatch.setattr(m, "resolve", lambda sess: resolved.append(sess))
    monkeypatch.setattr(m, "register", lambda *a, **k: None)
    monkeypatch.setattr(m, "approval_round_cap", lambda: 3)
    return resolved


def test_approve_会话级超限时回429(monkeypatch):
    """B8 —— 本端点原先**没有**这道门。用的是**调用方**（谁发请求谁被限）。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "check_session_token_budget", lambda *a, **k: (False, REASON))
    _assert_429(m.approve_agent_action(thread_id=THREAD, approved=True, user_name=OWNER))


def test_approve_全站熔断时回429(monkeypatch):
    """B11 —— 与 B8 **并列、都要过**（别合并成一条）。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "circuit", lambda *a, **k: (False, REASON))
    _assert_429(m.approve_agent_action(thread_id=THREAD, approved=True, user_name=OWNER))


def test_approve_续跑那一轮被拦时回429(monkeypatch):
    """**出口形状** —— 放行后图从 `approval` 续跑，跑到入口节点 `agent` 时预算已耗尽
    ⇒ 软返回。不认这个标志的话，`answer` 就是那句"预算用完了"的**伪答案**（HTTP 200）。

    并且**必须 `resolve`** —— 图这一轮是**真跑完了**（软返回），不是停在审批点；
       不注销就留在队列里变成一条**永远批不了**的假待办。
    """
    graph = _ApproveFakeGraph([("approval",)], [_blocked_result()])
    resolved = _patch_approve(monkeypatch, graph)

    _assert_429(m.approve_agent_action(thread_id=THREAD, approved=True, user_name=OWNER))
    assert graph.invokes == 1, f"没真的续跑（invoke {graph.invokes} 次）⇒ 本用例没测到要测的路"
    assert resolved == [SESS], f"图跑完了却没注销 ⇒ 队列里留下假待办：{resolved}"


def test_approve_触顶强制收尾那一轮被拦时回429(monkeypatch):
    """**另一条 `invoke`**（的"触顶强制收尾"）—— 它也可能被预算拦下。

    ⇒ "两处 `invoke` 都要认标志"：只认头一个的话，走到这条路的用户拿到的
      `forced_finish=True` + `answer="预算用完了"`（看着像"模型被强制收口了"，其实是没跑）。
    """
    # next 序列：① 停在审批点 ② 放行后**又**停（⇒ 触顶分支）③ 强制收尾后**不再**停
    # invoke 序列：① 正常续跑（**不带**标志，否则会被上一个检查拦下、测不到这一条）
    #              ② 触顶强制收尾（**带**标志 —— 本条要测的就是它）
    graph = _ApproveFakeGraph([("approval",), ("approval",), ()],
                              [{"messages": [AIMessage(content="模型又要了敏感工具")]},
                               _blocked_result()])
    resolved = _patch_approve(monkeypatch, graph, rounds=3)   # rounds=3 ⇒ 触顶（cap 也是 3）

    _assert_429(m.approve_agent_action(thread_id=THREAD, approved=True, user_name=OWNER))
    assert graph.invokes == 2, (
        f"没走到强制收尾那条路（invoke {graph.invokes} 次）⇒ 本用例测的不是它"
    )
    assert resolved == [SESS], f"图跑完了却没注销：{resolved}"


def test_approve_没被拦时照常回答案(monkeypatch):
    """反向对照：状态里**根本没有** `budget_intercept` 这个键 ⇒ 端点必须用 `.get`。"""
    graph = _ApproveFakeGraph([("approval",), ()],
                              [{"messages": [AIMessage(content="正常答案")]}])
    resolved = _patch_approve(monkeypatch, graph)

    out = asyncio.run(m.approve_agent_action(thread_id=THREAD, approved=True, user_name=OWNER))
    assert out["status"] == "approved" and out["answer"] == "正常答案", out
    assert resolved == [SESS], resolved
