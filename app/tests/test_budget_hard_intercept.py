""" 预算**硬**拦截的判据（2026-10-05）。

## 测的是什么

需求方 2026-09-30 裁定：超预算必须**让调用方在响应里看得出"被拒了"**。
改前是**软拦截** —— 塞一条 `ToolMessage` 就 `continue` ⇒ **HTTP 仍 200**，
只有 LLM 自己看得见 ⇒「预算拦住了」这句话**只对 LLM 成立**。

## 为什么不是「在节点里抛异常」

裁定原文写的是「改成抛 `AppException(QUOTA_EXCEEDED)`」。**实测**：
在 LangGraph 节点里 `raise` 会把 checkpoint 留成
`next=('tools',)` + **一条没人回答的 `AIMessage(tool_calls)`**
⇒ 真 provider（OpenAI 口径）**400**，而 `agent` 是入口节点、**每轮都跑**
⇒ **那个 thread 从此废掉**（不是"下次再说"，是"下一次必定 500"）。

⇒ 拦截走 **state** 出来（`budget_intercept`），由**端点层**转成 429 / error 帧。

## 四条轴

| # | 轴 | 用例 |
|---|---|---|
| ① | 节点：不抛、置标志、**答满本轮每个 `tool_call`** | `test_被拦时*` |
| ② | 节点：没被拦时**不许**置标志（否则每次正常调用都 429） | `test_没被拦时*` |
| ③ | 图：被拦 ⇒ `tools` 之后**直接结束**，不回 `agent` 再调一次 | `test_被拦时图在tools之后*` |
| ④ | 端点：非流式 **429** · 流式 **error 帧** | `test_*端点*` |

**串轮守卫**（`test_上一轮的拦截标志不会串到下一轮`）—— `budget_intercept` 是**普通 state 键**
（last-write-wins + 落 checkpoint）⇒ 必须**每轮清零**，否则"上一轮被拦"会让
**下一轮不需要工具的正常提问也返回 429**。

判据（可打印）：
    `venv/bin/python -m pytest app/tests/test_budget_hard_intercept.py -q -p no:warnings`
"""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import agent.agent_graph_advanced as aga
import routing.api_v1_agent as m
from core.exceptions import AppException, ErrorCode

REASON = "今日 Token 预算已用完"
THREAD = "s13-thread"

#: 假 `AIMessage` **必须带 `usage_metadata`** —— `agent_decide` / `chat_node` 会做
#: `usage.get(...)`，而 `hasattr(msg, "usage_metadata")` 对**没设过的**消息**也是真**
#: （它是 pydantic 字段，默认 `None`）⇒ 少给这一个键，报的是 `NoneType.get` 的 AttributeError。
_USAGE = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}


# ==================== 夹具 ====================

def _ai_with_calls(*ids):
    """一条「要求调用 N 个工具」的 AI 消息。"""
    return AIMessage(content="", tool_calls=[
        {"name": "calculator", "args": {"expression": "1+1"}, "id": i} for i in ids
    ])


def _state(*ids, **extra):
    base = {"messages": [_ai_with_calls(*ids)], "user_name": "u", "thread_id": THREAD}
    base.update(extra)
    return base


def _drive_tool_execute(monkeypatch, state, blocked=True):
    """直接调**模块级**的 `tool_execute`（它是模块级的，不像 `agent_decide` 嵌在图里）。

    `call_mcp_tool` 打成 `fail`：**被拦的那次绝不许真去调工具**
       —— 这是"预算拦截"这个词的全部意义。
    """
    monkeypatch.setattr(aga, "record_tool_start", lambda *a, **k: None)
    monkeypatch.setattr(aga, "record_tool_end", lambda *a, **k: None)
    monkeypatch.setattr(aga, "check_multilevel_budget", lambda **k: (not blocked, REASON))
    monkeypatch.setattr(aga, "call_mcp_tool",
                        lambda *a, **k: pytest.fail("被预算拦下的那次不许真去调工具"))
    return asyncio.run(aga.tool_execute(state))


# ==================== ① 节点：不抛、置标志、答满 tool_call ====================

def test_被拦时节点不抛异常而是置标志(monkeypatch):
    """**不许 raise** —— 理由见文件头（会把 checkpoint 留成非法序列）。"""
    out = _drive_tool_execute(monkeypatch, _state("c1"))
    assert out["budget_intercept"] == REASON, (
        f"state 上没留下列拦截原因 ⇒ 端点无从把 429 报给调用方：{out}"
    )


def test_被拦时本轮每个_tool_call_都要有回应(monkeypatch):
    """**序列合法**：带 `tool_calls` 的 `AIMessage` 后面必须跟满 `ToolMessage`。

    这正是"在节点里 raise"做不到的那件事 —— 它一条都写不下去，
       于是那条 `AIMessage` 永远悬着（真 provider 直接 400）。
    """
    out = _drive_tool_execute(monkeypatch, _state("c1", "c2", "c3"))
    answered = [msg.tool_call_id for msg in out["messages"]
                if isinstance(msg, ToolMessage)]
    assert answered == ["c1", "c2", "c3"], (
        f"本轮有 3 个 tool_call 却只答了 {answered} ⇒ 消息序列非法"
    )


def test_被拦时工具名与原因都带在回应里(monkeypatch):
    """回应的正文要**带上原因** —— 它是 LLM 下一轮唯一能看到的东西。"""
    out = _drive_tool_execute(monkeypatch, _state("c1"))
    msg = out["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.name == "calculator"
    assert REASON in msg.content, f"回应里没带拦截原因：{msg.content!r}"


# ==================== ② 反向对照：没被拦时不许置标志 ====================

def test_没被拦时不置标志(monkeypatch):
    """别修成"恒置标志" —— 那会让**每一次正常调用**都被端点判成 429。"""
    async def _fake_call(*a, **k):
        return "正常结果"

    monkeypatch.setattr(aga, "record_tool_start", lambda *a, **k: None)
    monkeypatch.setattr(aga, "record_tool_end", lambda *a, **k: None)
    monkeypatch.setattr(aga, "check_multilevel_budget", lambda **k: (True, ""))
    monkeypatch.setattr(aga, "call_mcp_tool", _fake_call)
    out = asyncio.run(aga.tool_execute(_state("c1")))
    # 断言的是**键根本不在**（不是"它等于 None"）：写到这个键的地方**只许有一处**
    #    （`tool_execute` 的被拦分支），讲得清"什么时候它是可信的"。
    #    ⇒ 把写入改成无条件（`out["budget_intercept"] = reason`）本条即红。
    assert "budget_intercept" not in out, (
        f"没被拦却动了这个键 ⇒ 正常路径也在改它，清零口径不再单一：{out}"
    )
    assert out.get("budget_intercept") is None, (
        f"没被拦却置了标志 ⇒ 正常提问会被误报成 429：{out}"
    )
    assert isinstance(out["messages"][0], ToolMessage)
    assert out["messages"][0].content == "正常结果"


# ==================== ③ 图：被拦 ⇒ tools 之后直接结束 ====================

class _FakeLLM:
    """假 LLM。

    * `require_tool=True`（默认）：**每次都要求调工具** ⇒ 每跑一次 `agent_decide` 必然进 `tools`
      （"被拦之后会不会回 agent 重来"就是靠**数它的调用次数**判的）。
    * `require_tool=False`：直接给终稿 ⇒ 走 `agent → chat → END`（串轮用例要的形态）。
    """

    model_name = "fake-model"

    def __init__(self, require_tool=True):
        self.calls = 0
        self.require_tool = require_tool

    async def astream(self, messages, config=None):
        self.calls += 1
        if not self.require_tool:
            yield AIMessage(content="没问题", usage_metadata=_USAGE)
            return
        yield AIMessage(content="", usage_metadata=_USAGE, tool_calls=[
            {"name": "calculator", "args": {"expression": "1+1"}, "id": "c1"}])


def _patch_graph_leaves(monkeypatch, reason=REASON, require_tool=True):
    """把真图跑起来所需的叶子全打桩（LLM / MCP / 预算 / 记账 / 追踪）。"""
    llm = _FakeLLM(require_tool=require_tool)

    async def _fake_llm_with_tools():
        return llm

    monkeypatch.setattr(aga, "get_llm_with_mcp_tools", _fake_llm_with_tools)
    monkeypatch.setattr(aga, "llm", llm)
    monkeypatch.setattr(aga, "check_token_budget", lambda u, estimated_tokens=500: True)
    monkeypatch.setattr(aga, "check_multilevel_budget", lambda **k: (False, reason))
    monkeypatch.setattr(aga, "record_usage", lambda **k: None)
    monkeypatch.setattr(aga, "record_tool_start", lambda *a, **k: None)
    monkeypatch.setattr(aga, "record_tool_end", lambda *a, **k: None)
    monkeypatch.setattr(aga, "record_agent_decision", lambda *a, **k: None)
    monkeypatch.setattr(aga, "inject_memories_to_prompt", lambda p, s: p)
    monkeypatch.setattr(aga, "call_mcp_tool", lambda *a, **k: "不该被调用")
    return llm


def test_被拦时图在tools之后直接结束不回agent(monkeypatch):
    """回 `agent` 只会让它**再调一次同一个工具** ⇒ 再被拦一次 ⇒ 直到撞上递归上限。

    判据用**调用次数**（不读图结构）：`agent_decide` 每跑一次就多一次 LLM 调用。
    """
    llm = _patch_graph_leaves(monkeypatch)
    asyncio.run(aga.mcp_agent.ainvoke(
        _state("c1"), {"configurable": {"thread_id": f"{THREAD}-直接结束"}}))
    assert llm.calls == 1, (
        f"LLM 被调了 {llm.calls} 次 ⇒ 被拦之后又回到 agent 重来了一遍"
    )


def test_上一轮的拦截标志不会串到下一轮(monkeypatch):
    """**串轮守卫** —— 标志是普通 state 键（last-write-wins + 落 checkpoint）。

    ⇒ 必须由**入口节点**每轮清零。**删掉入口那句清零，本条必红**
       —— 红出来的值就是上一轮的 `上一轮被拦了`。

    为什么这不是"多此一举"：第三级（用户日预算）**午夜会重置**、一二级（元）**管理员能调**
       ⇒ 预算恢复之后用户回到同一个 thread，撞上的会是**被上一轮污染的正常提问**。
    """
    llm = _patch_graph_leaves(monkeypatch, require_tool=False)
    out = asyncio.run(aga.mcp_agent.ainvoke(
        _state("c1", budget_intercept="上一轮被拦了"),
        {"configurable": {"thread_id": f"{THREAD}-串轮"}}))

    assert out.get("budget_intercept") is None, (
        f"上一轮的拦截标志串到本轮了：{out.get('budget_intercept')!r} "
        f"⇒ 预算已经恢复，用户正常提问却仍被判成 429"
    )
    assert llm.calls >= 1, "假 LLM 一次都没被调用 ⇒ 这个用例根本没跑到决策节点"


def test_软返回那一轮的出口也要带上本轮的值(monkeypatch):
    """`agent_decide` 有**两个出口**（预算软返回 / 正常返回）。

    只给一个带**标志** ⇒ 走另一个出口的那一轮会**留着上一轮的值**
    ⇒ 端点拿旧原因回错的话，或者"本轮明明被拦了却报正常"。

    ## 2026-10-05（批 7）本条**语义翻转**，不是"改断言让它变绿"

    改前本条的断言是 `is None` —— 当年 `agent_decide` 的软返回出口写的是 `**cleared`
    （**显式清零**），因为那时"拦截"**只有** `tool_execute`（工具触发）那一种，
    入口节点的任务仅仅是"别让上轮的值串过来"。

    **批 7 新增的正是"入口节点自己就是拦截点"**（预算不够 ⇒ 根本走不到工具）
    ⇒ 本出口必须写**本轮的原因**，再写 `None` 就等于**把这次拦截藏起来**
    （而那正是本批要治的病）。裁定 ⇒ 本仓的决策记录（⛔ 不随产品仓提供）。

    ⇒ 守的东西没变（**上轮的值不许串过来**），判据从"是 `None`"改成"**不是上一轮那个值**"。
    """
    _patch_graph_leaves(monkeypatch, require_tool=False)
    monkeypatch.setattr(aga, "check_token_budget", lambda u, estimated_tokens=500: False)
    out = asyncio.run(aga.mcp_agent.ainvoke(
        _state("c1", budget_intercept="上一轮被拦了"),
        {"configurable": {"thread_id": f"{THREAD}-软返回串轮"}}))

    got = out.get("budget_intercept")
    assert got != "上一轮被拦了", (
        f"走软返回出口的那一轮把**上一轮的**标志留下来了：{got!r}"
    )
    assert got, (
        f"本轮明明被预算拦下了，出口却没留下原因 ⇒ 端点无从把它转成 429：{out}"
    )


# ==================== ④ 端点：非流式 429 ====================

class _FakeGraph:
    def __init__(self, result):
        self._result = result

    async def ainvoke(self, state, config=None):
        return self._result


def _patch_gates(monkeypatch):
    """三道门 + 追踪/留痕全短路 —— 它们各有自己的用例，碰 Redis/PG。"""
    monkeypatch.setattr(m, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "start_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "finish_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "check_budget_warning",
                        lambda *a, **k: {"warning": False, "message": ""})
    monkeypatch.setattr(m, "persist_turn", lambda *a, **k: None)


def _blocked_result():
    """图被拦之后的状态：末条是回应工具调用的 `ToolMessage` + state 上的标志。"""
    return {
        "messages": [ToolMessage(content=f"⚠️ 预算拦截：{REASON}", tool_call_id="c1",
                                 name="calculator")],
        "budget_intercept": REASON,
    }


def test_被拦时非流式端点回429(monkeypatch):
    """**本批要的就是这一条** —— 改前这里回 **200 + 一段"预算不够"的答案**。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "mcp_agent", _FakeGraph(_blocked_result()))
    with pytest.raises(AppException) as ei:
        asyncio.run(m.mcp_agent_chat(question="q", thread_id=THREAD, user_name="u"))
    assert ei.value.error_code is ErrorCode.QUOTA_EXCEEDED, ei.value.error_code
    assert ei.value.status_code == 429, ei.value.status_code
    assert REASON in (ei.value.message or ""), (
        f"429 的正文里没带上拦截原因，调用方看不出为什么：{ei.value.message!r}"
    )


def test_没被拦时非流式端点照常回答案(monkeypatch):
    """反向对照：注意这里的状态**根本没有 `budget_intercept` 这个键**
    （正常路径的图不会写它）⇒ 端点必须用 `.get`，不许 `result[...]`。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "mcp_agent",
                        _FakeGraph({"messages": [AIMessage(content="正常答案")]}))
    out = asyncio.run(m.mcp_agent_chat(question="q", thread_id=THREAD, user_name="u"))
    assert out["answer"] == "正常答案"
    assert out["thread_id"] == THREAD


# ==================== ④ 端点：流式 error 帧 ====================

class _StreamGraph:
    """最小假图：一条上游流 + 一份最终状态。"""

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


def _objs(frames):
    out = []
    for f in frames:
        body = f[len("data: "):].strip()
        out.append("__DONE__" if body == "[DONE]" else json.loads(body))
    return out


def test_被拦时流式端点发error帧而不是汇总帧(monkeypatch):
    """流式的响应头**已经发出去了**（HTTP 200 + `text/event-stream`）⇒ 改不了状态码
    —— 只能发一帧 `{"error": …}`（与 `/agent/plan_execute/stream` 同口径）。

    **不许发汇总帧**：那帧里有 `answer`，读起来就是"这轮正常答完了"
       —— 正是软拦截那个形态在流式这条路的重演。
    """
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "mcp_agent", _StreamGraph(_blocked_result()))
    resp = asyncio.run(m.mcp_agent_chat_stream(
        question="q", thread_id=THREAD, user_name="u"))
    objs = _objs(_collect_frames(resp))

    errors = [o for o in objs if isinstance(o, dict) and o.get("error")]
    assert errors, f"被拦了却没发 error 帧：{objs}"
    assert REASON in errors[0]["error"], errors[0]["error"]
    summaries = [o for o in objs if isinstance(o, dict) and "answer" in o]
    assert not summaries, f"被拦了还发了汇总帧（读起来像正常答完）：{summaries}"


def test_没被拦时流式端点照常发汇总帧(monkeypatch):
    """反向对照：别把正常路径也改成 error 帧。"""
    _patch_gates(monkeypatch)
    monkeypatch.setattr(m, "mcp_agent",
                        _StreamGraph({"messages": [AIMessage(content="正常答案")]}))
    resp = asyncio.run(m.mcp_agent_chat_stream(
        question="q", thread_id=THREAD, user_name="u"))
    objs = _objs(_collect_frames(resp))

    assert not [o for o in objs if isinstance(o, dict) and o.get("error")], objs
    summaries = [o for o in objs if isinstance(o, dict) and "answer" in o]
    assert summaries and summaries[0]["answer"] == "正常答案", objs
    assert objs[-1] == "__DONE__", objs[-1]
