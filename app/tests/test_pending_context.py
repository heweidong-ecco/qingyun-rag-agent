"""`GET /agent/pending/context` —— 硬门 D 演示里「点开能看到**完整上下文**」的来源（§3.1）。

**为什么必须新开一个端点**：`/agent/pending` 返回的只有登记表那 7 个字段，
   **没有 messages、没有用户问的原话、没有已生成的部分**；而全仓**没有任何端点**
   暴露某会话的图状态（`aget_state` 只在端点内部被用过）。
"""
import asyncio
import types

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import routing.api_v1_agent as m
import agent.pending_approvals as pa
from access.session_key import session_key

THREAD = "default"

_STUCK = AIMessage(content="", tool_calls=[
    {"name": "web_search", "args": {"q": "x"}, "id": "call_1"}])


class _FakeGraph:
    """**异步**假图 —— 上下文端点走 `aget_state`（对照 `:362` 那条真用法）。"""

    def __init__(self, next_=("approval",), messages=None):
        self._next = next_
        self._messages = list(messages) if messages is not None else [
            HumanMessage(content="帮我查一下"), _STUCK]
        self.calls = []

    async def aget_state(self, config):
        self.calls.append(config)
        return types.SimpleNamespace(next=self._next,
                                     values={"messages": self._messages})


def _ctx(graph, **kw):
    monkeypatch_kw = dict(thread_id=THREAD, owner=None, user_name="alice")
    monkeypatch_kw.update(kw)
    return asyncio.run(m.pending_approval_context(**monkeypatch_kw))


def _fake(monkeypatch, graph):
    monkeypatch.setattr(m, "agent_graph", graph)
    monkeypatch.setattr(m, "checkpointer_agent", graph)
    return graph


def _alice_pending(graph="agent_graph"):
    pa.register(session_key("alice", THREAD), "alice", [{"name": "web_search", "args": {}}],
                raw_thread_id=THREAD, graph=graph)


def test_clean():
    pa.clear()


# ==================== ① happy path：原样给出消息序列 ====================

def test_returns_full_message_sequence(monkeypatch):
    """裁定 2：**图里的 messages 序列原样给**（Human / AI / Tool 全要，工具返回也是全文）。

    "完整"只有**原样序列**能证 —— 摘要漏一段，操作员就是在不知道前因的情况下放行。
    """
    g = _fake(monkeypatch, _FakeGraph(messages=[
        HumanMessage(content="帮我查一下"),
        _STUCK,
        ToolMessage(content="搜索结果全文：……", tool_call_id="call_1", name="web_search"),
    ]))
    _alice_pending()

    out = _ctx(g)

    assert out["status"] == "ok"
    assert [msg["type"] for msg in out["messages"]] == ["human", "ai", "tool"]
    assert out["messages"][2]["content"] == "搜索结果全文：……", "工具返回必须是全文"
    assert out["messages"][1]["tool_calls"] == [
        {"name": "web_search", "args": {"q": "x"}, "id": "call_1"}]
    assert out["next"] == ["approval"] and out["owner"] == "alice"
    assert out["graph"] == "agent_graph" and out["rounds"] == 1


# ==================== ② 0 条 ⇒ 200 + status=error（不是 404） ====================

def test_no_pending_task_is_200_with_error_status(monkeypatch):
    """与 `/agent/approve` 一致 —— 不改成 404（§3.1）。"""
    pa.clear()
    out = _ctx(_fake(monkeypatch, _FakeGraph()))
    assert out["status"] == "error" and "审批" in out["message"]


# ==================== ③ 歧义 ⇒ 拒绝；给 owner ⇒ 不再歧义（裁定 6 的正面） ====================

def test_ambiguity_is_rejected(monkeypatch):
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [], raw_thread_id=THREAD)
    pa.register(session_key("bob", THREAD), "bob", [], raw_thread_id=THREAD)

    out = _ctx(_fake(monkeypatch, _FakeGraph()), user_name="admin")

    assert out["status"] == "error" and "多条" in out["message"]


def test_owner_narrows_it_down(monkeypatch):
    """裁定 6 的正面：`owner` 一给，歧义消失，且**取的是那一本**。"""
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [], raw_thread_id=THREAD)
    pa.register(session_key("bob", THREAD), "bob", [], raw_thread_id=THREAD)
    g = _fake(monkeypatch, _FakeGraph())

    out = _ctx(g, user_name="admin", owner="bob")

    assert out["status"] == "ok" and out["owner"] == "bob"
    assert g.calls == [{"configurable": {"thread_id": session_key("bob", THREAD)}}], (
        f"取错了会话的键：{g.calls}"
    )


def test_owner_that_matches_nobody_falls_into_zero(monkeypatch):
    """给了 `owner` 却一条都不匹配 ⇒ 落在「0 条」那支（不是 500）。"""
    pa.clear(); _alice_pending()
    out = _ctx(_fake(monkeypatch, _FakeGraph()), user_name="admin", owner="carol")
    assert out["status"] == "error" and "审批" in out["message"]


# ==================== ④ 归属：非属主且非 admin ⇒ 拒绝，且【不许碰图】 ====================

def test_non_owner_cannot_read_context(monkeypatch):
    """`owner` 只是**收窄候选**，**不是授权** —— 传了它照样走归属校验。"""
    pa.clear(); _alice_pending()
    g = _fake(monkeypatch, _FakeGraph())

    out = _ctx(g, user_name="bob", owner="alice")

    assert out["status"] == "error" and "无权" in out["message"]
    assert g.calls == [], "拒绝必须在【碰图之前】发生 —— 否则已经读到别人的会话了"


def test_admin_can_read_someone_elses_context(monkeypatch):
    """硬门 D 演示的**就是**这条路（admin 接管）⇒ 它必须通。"""
    pa.clear(); _alice_pending()
    g = _fake(monkeypatch, _FakeGraph())

    out = _ctx(g, user_name="admin")

    assert out["status"] == "ok" and out["owner"] == "alice"
    assert g.calls == [{"configurable": {"thread_id": session_key("alice", THREAD)}}]


# ==================== ⑤ 防御：content 可能是 list（多模态） ====================

def test_multimodal_content_is_serialized_not_crashed(monkeypatch):
    """`content` **可能是 list**（多模态 parts）⇒ 别假定是 `str`。

    这不是 happy path：真实的多模态消息**就是这么长的**，而一个 `jsonable_encoder`
       之外的假定会让整个端点 500 —— 而失败点离"看不到上下文"很远，难查。
    """
    pa.clear(); _alice_pending()
    out = _ctx(_fake(monkeypatch, _FakeGraph(messages=[
        HumanMessage(content=[{"type": "text", "text": "看图"},
                              {"type": "image_url", "image_url": {"url": "http://x/1.png"}}])])))

    assert out["status"] == "ok"
    assert isinstance(out["messages"][0]["content"], list), (
        "多模态 content 应原样带上（前端自己归一），⛔ 不是被 `str()` 糊成一坨"
    )


# ==================== ⑥ 登记的图不认识 ⇒ 如实说，别猜一个 ====================

def test_unknown_graph_is_reported_not_guessed(monkeypatch):
    pa.clear(); _alice_pending(graph="nope_graph")
    out = _ctx(_fake(monkeypatch, _FakeGraph()))
    assert out["status"] == "error" and "nope_graph" in out["message"]
