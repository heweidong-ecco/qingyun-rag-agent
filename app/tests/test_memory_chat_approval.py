"""`/agent/memory_chat` 的**审批门** + `/agent/approve` 的**按图路由**（丙段）。

**两条现状**：
   1. `agent_checkpointer.py`（`/agent/memory_chat` 走它）**没有审批门**
      —— 无 `interrupt_before` / 无 `SENSITIVE_TOOLS`（§遗留·2）
      **同一个仓里，一条路停下等人批，另一条直接执行。**
   2. `/agent/approve` 把 **`agent_graph` 写死**了 ⇒ 光给 ① 加门而不改它，
      **会话会停在审批点、永远没人能放行**（比不加固还糟）。
   ⇒ 作者 2026-10-03 裁：**按登记表里的图路由**（一个入口，两张图都认）。

判据（可打印）：
    `venv/bin/python -m pytest app/tests/test_memory_chat_approval.py -q -p no:warnings`
"""
import asyncio
import types

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

import agent.agent_checkpointer as ac
import agent.agent_graph as ag
import routing.api_v1_agent as m
import agent.pending_approvals as pa
from access.session_key import session_key

THREAD = "default"


# ==================== ① 门真的会把图停住 ====================

class _StubTool:
    """只在**门没生效**（RED）时才跑得到 —— 挡住真联网，让红灯是"断言失败"而不是"卡住"."""

    def invoke(self, args):
        return "stub"


class _ScriptedLLM:
    """第一次返回**敏感工具调用**，之后返回普通答案。

    必须有"之后"：门**没生效**时，假模型每次都回 tool_calls
       `agent → tools → agent …` **转到递归上限**，红灯变成 `GraphRecursionError`
       （吵、且看不出是哪条断言坏的）。

    2026-10-04：`agent_decide` 已从 `.invoke` 改成 `.stream`（真流式）
       ⇒ 替身必须跟上，否则 `AttributeError: '_ScriptedLLM' object has no attribute 'stream'`。
    敏感那一次**故意用碎片化的 `tool_call_chunks`**（而不是组装好的 `tool_calls`）——
       这正是流式到达的**真实形态**，于是本用例**顺带守住**了「节点的 `+` 聚合
       能不能把碎片还原成 `tool_calls`」：还原不出来 ⇒ `should_continue` 判不出审批
       ⇒ 图**不会停在审批点**，而下面那条断言正好就是它的判据。
    """

    def __init__(self):
        self.calls = 0

    def _next_reply(self):
        self.calls += 1
        if self.calls == 1:
            # 必须 `AIMessageChunk`（不是 `AIMessage`）：节点用 `+` 聚合，
            #    `AIMessage` **没有 `__add__`** ⇒ `TypeError`。
            return AIMessageChunk(content="", tool_call_chunks=[
                {"name": "web_search", "args": '{"query": "x"}', "id": "c1", "index": 0},
            ])
        return AIMessageChunk(content="done")

    def invoke(self, messages):
        return self._next_reply()

    def stream(self, messages, config=None):
        yield self._next_reply()


def test_memory_chat_graph_stops_at_approval_for_sensitive_tools(monkeypatch):
    """丙段的门 —— 含**敏感**工具时，图必须**停在审批点**（`get_state.next == ("approval",)`）。

    这是**端到端**的：真编图、真走 `should_continue`，只有 LLM 是假的（不联网）。
    """
    monkeypatch.setattr(ac, "llm_with_tools", _ScriptedLLM())
    monkeypatch.setattr(ac, "TOOLS_BY_NAME", {"web_search": _StubTool()})

    g = ac.build_checkpointer_agent("memory")
    cfg = {"configurable": {"thread_id": "t-gate"}}
    g.invoke({"messages": [HumanMessage(content="q")]}, config=cfg)

    assert g.get_state(cfg).next == ("approval",), (
        "敏感工具没能把图停在审批点 —— 说明 `checkpointer_agent` 上**没有** "
        "`interrupt_before=['approval']`（§遗留·2 /  丙段）"
    )


def test_gate_shares_one_whitelist_with_agent_graph():
    """**白名单只能有一份** —— 别在 `agent_checkpointer` 里再抄一遍 `SENSITIVE_TOOLS`。

    抄一份就是  记的那个病根：同一个判断两处实现，**改了一边另一边不知道**，
    而两处不一致时**不报错**（审批静默地永不触发 / 静默地对错的工具触发）。
    """
    assert ac.SENSITIVE_TOOLS is ag.SENSITIVE_TOOLS, (
        "`agent_checkpointer` 的敏感名单不是 `agent_graph` 那一份 ⇒ 又变成两处口径了"
    )


# ==================== ② 停住之后，队列里要记【是哪张图】 ====================

def test_memory_chat_registers_pending_with_graph_name(monkeypatch):
    """停在审批点 ⇒ 登记时**必须写明图名**，否则 `/agent/approve` 不知道该续跑哪张。"""
    pending_result = {"messages": [AIMessage(content="", tool_calls=[
        {"name": "web_search", "args": {"query": "x"}, "id": "c1"},
    ])]}
    fake = types.SimpleNamespace(invoke=lambda state, config=None: pending_result)
    monkeypatch.setattr(m, "checkpointer_agent", fake)
    monkeypatch.setattr(m, "check_session_token_budget", lambda u, t, **kw: (True, ""))
    monkeypatch.setattr(m, "circuit", lambda k: (True, ""))
    pa.clear()

    out = asyncio.run(m.memory_chat(question="q", thread_id=THREAD, user_name="alice"))

    assert out["status"] == "pending_approval", (
        "`memory_chat` 必须**告诉调用方**它在等审批（⛔ 别返回 200 + 空答案）"
    )
    rows = pa.list_pending()
    assert len(rows) == 1
    assert rows[0]["graph"] == "checkpointer_agent", (
        f"登记表里没写明是 `/agent/memory_chat` 那张图（实际 {rows[0].get('graph')!r}）"
    )
    assert rows[0]["raw_thread_id"] == THREAD
    assert rows[0]["thread_id"] == session_key("alice", THREAD)


# ==================== ③ approve 必须按【登记的图】路由 ====================

# 卡在审批点的那条消息（真货形状：**只有 `tool_calls`、没有文字**）。
_STUCK = AIMessage(content="", tool_calls=[
    {"name": "web_search", "args": {}, "id": "call_1"}])


class _FakeGraph:
    """本桩的两处形状是**被 approve 读出来的**，别删：
      · `values["messages"]` —— approve 要读卡住的 `tool_calls` 去回填配对的 `ToolMessage`；
      · `next_after` —— approve 放行后**再读一次** state（判"该不该注销登记"），
        与放行前那个 `next` **不是同一个值**。
    改前只给 `values={}` + 一个恒定 `next`，是因为**改前的 approve 只读 `.next`** ——
    `KeyError` 暴露的是桩不完整，不是被测代码错。
    """

    def __init__(self, next_=("approval",), next_after=()):
        self.trace = []
        self._before = next_
        self._after = next_after
        self._invoked = False

    def get_state(self, config):
        self.trace.append(("get_state", config))
        return types.SimpleNamespace(
            next=self._after if self._invoked else self._before,
            values={"messages": [AIMessage(content="Q1"), _STUCK]},
        )

    def update_state(self, config, values=None, **kw):
        self.trace.append(("update_state", config))
        return config

    def invoke(self, state, config=None):
        self.trace.append(("invoke", config))
        self._invoked = True
        return {"messages": [AIMessage(content="done")]}


def _approve(**kw):
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("approved", True)
    kw.setdefault("edited_answer", None)
    kw.setdefault("user_name", "alice")
    return asyncio.run(m.approve_agent_action(**kw))


def test_approve_routes_to_checkpointer_graph(monkeypatch):
    """`memory_chat` 卡住的会话，`/agent/approve` 必须去找 **`checkpointer_agent`**。

    改动前：approve 把 `agent_graph` 写死 ⇒ 它会去问**另一张图**"你有没有停在审批点"
       答"没有" ⇒ 这个会话**永远放行不了**（比不加门还糟：门关了却没有钥匙）。
    """
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [{"name": "web_search", "args": {}}],
                raw_thread_id=THREAD, graph="checkpointer_agent")
    ckpt = _FakeGraph()
    plain = _FakeGraph()
    monkeypatch.setattr(m, "checkpointer_agent", ckpt)
    monkeypatch.setattr(m, "agent_graph", plain)

    out = _approve(user_name="alice")

    assert out["status"] == "approved"
    assert ckpt.trace, "必须续跑 `checkpointer_agent`"
    assert plain.trace == [], "⛔ 不许去碰 `agent_graph` —— 那不是这个会话的图"


def test_approve_routes_to_agent_graph_by_default(monkeypatch):
    """回归：`/agent/langgraph_chat` 那条路（`graph` 默认值）仍走 `agent_graph`。"""
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [{"name": "web_search", "args": {}}],
                raw_thread_id=THREAD)
    ckpt = _FakeGraph()
    plain = _FakeGraph()
    monkeypatch.setattr(m, "checkpointer_agent", ckpt)
    monkeypatch.setattr(m, "agent_graph", plain)

    assert _approve(user_name="alice")["status"] == "approved"
    assert plain.trace and ckpt.trace == []


def test_approve_refuses_unknown_graph_name(monkeypatch):
    """图名不认识 ⇒ **如实报错**，别"猜一个" —— 猜错就是往别人会话上写状态。"""
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [], raw_thread_id=THREAD, graph="no_such_graph")
    plain = _FakeGraph()
    monkeypatch.setattr(m, "agent_graph", plain)

    out = _approve(user_name="alice")

    assert out["status"] == "error"
    assert plain.trace == []
