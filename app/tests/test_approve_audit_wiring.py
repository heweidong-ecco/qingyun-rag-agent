"""留痕**接进 `/agent/approve`**（§3.2 + 本单具体化 C）。

本文件钉的不是"模块能不能写库"（那是 `test_approval_events.py`），而是
   **四个出口里哪些记、哪些不记**：

| 出口 | 记不记 | 为什么 |
|---|---|---|
| 没有可待批任务 | 不记 | 那不是一次裁决 |
| 歧义（多条） | 不记 | 同上 |
| 无权限 | 不记 | 同上（**越权尝试也不记**， §六·3） |
| **登记陈了**（图没停在审批点） | 不记 | **本单具体化 C**：它在归属校验**之后**才发生 ⇒ 留痕必须写在它**后面**，否则会记下一次**根本没落到图上**的裁决 |
| 批准 / 拒绝 / 触顶强制收尾 / 又停在审批点 | 记 | 真的做出并落到图上了 |

判据（可打印）：`venv/bin/python -m pytest app/tests/test_approve_audit_wiring.py -q -p no:warnings`
"""
import asyncio
import types

import pytest
from langchain_core.messages import AIMessage

import routing.api_v1_agent as m
import agent.approval_audit as aa
import agent.pending_approvals as pa
from access.session_key import session_key

THREAD = "default"

_STUCK = AIMessage(content="", tool_calls=[
    {"name": "web_search", "args": {"q": "x"}, "id": "call_1"}])


class _FakeGraph:
    def __init__(self, next_=("approval",), next_after=()):
        self._before, self._after = next_, next_after
        self._invoked = False

    def get_state(self, config):
        return types.SimpleNamespace(
            next=self._after if self._invoked else self._before,
            values={"messages": [AIMessage(content="Q1"), _STUCK]})

    def update_state(self, config, values=None, **kw):
        return config

    def invoke(self, state, config=None):
        self._invoked = True
        return {"messages": [AIMessage(content="done")]}


@pytest.fixture
def calls(monkeypatch):
    """把留痕拦在内存里 —— 顺便**钉住调用形状**（关键字参数名写错会当场 `TypeError`）。"""
    got = []
    monkeypatch.setattr(aa, "record_decision",
                        lambda **kw: got.append(kw))
    monkeypatch.setattr(m, "record_decision", lambda **kw: got.append(kw), raising=False)
    pa.clear()
    yield got
    pa.clear()


@pytest.fixture(autouse=True)
def _fake_graphs(monkeypatch):
    monkeypatch.setattr(m, "agent_graph", _FakeGraph())
    monkeypatch.setattr(m, "checkpointer_agent", _FakeGraph())


def _alice_pending():
    pa.register(session_key("alice", THREAD), "alice", [{"name": "web_search", "args": {}}],
                raw_thread_id=THREAD, graph="agent_graph")


def _approve(**kw):
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("approved", True)
    kw.setdefault("edited_answer", None)
    kw.setdefault("owner", None)
    kw.setdefault("user_name", "alice")
    return asyncio.run(m.approve_agent_action(**kw))


# ==================== ① 记：一次真的裁决 ====================

def test_approve_records_owner_actor_and_reason(calls):
    """核心：**owner 是 alice，actor 是 admin** —— 两个身份，不是一个。"""
    _alice_pending()

    out = _approve(user_name="admin")

    assert out["status"] == "approved"
    assert len(calls) == 1, f"一次裁决应当只记一条，实际 {len(calls)} 条"
    assert calls[0]["owner"] == "alice", "owner 必须是【会话是谁的】"
    assert calls[0]["actor"] == "admin", "actor 必须是【谁做的裁决】"
    assert calls[0]["decision"] == "approved"
    assert calls[0]["edited"] is False
    assert calls[0]["reason"] == "web_search", "「为什么」= 待批的 tool_calls 摘要"


def test_reject_records_rejected(calls):
    _alice_pending()
    _approve(approved=False)
    assert [c["decision"] for c in calls] == ["rejected"]


def test_edited_answer_sets_edited_true(calls):
    """`edited=True` 的语义是「**代替模型给出了这次工具调用的结果**」，见 spec。"""
    _alice_pending()
    _approve(approved=True, edited_answer="答案是 42")
    assert calls[0]["edited"] is True


def test_rejected_ignores_edited_answer(calls):
    """拒绝时给了改写 ⇒ 后端本来就会忽略它 ⇒ 留痕里别记成"改写过"。"""
    _alice_pending()
    _approve(approved=False, edited_answer="随便写点")
    assert calls[0]["edited"] is False


# ==================== ② 不记：四条"根本没批成" ====================

def test_no_pending_task_records_nothing(calls):
    pa.clear()
    out = _approve()
    assert out["status"] == "error" and calls == []


def test_ambiguity_records_nothing(calls):
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [], raw_thread_id=THREAD)
    pa.register(session_key("bob", THREAD), "bob", [], raw_thread_id=THREAD)
    out = _approve(user_name="admin")
    assert out["status"] == "error" and calls == []


def test_no_permission_records_nothing(calls):
    """**越权尝试也不记**（§六·3：那是**认了**的代价）。"""
    _alice_pending()
    out = _approve(user_name="bob")
    assert out["status"] == "error" and calls == []


def test_stale_registry_records_nothing(calls, monkeypatch):
    """**本单具体化 C 的那一条**：图**没**停在审批点 ⇒ 登记是陈的 ⇒ 记了就是假留痕。

    它在归属校验**之后**才发生 —— 所以留痕必须写在 `current_state.next != ("approval",)`
       那道守卫**后面**。把留痕挪到守卫之前 ⇒ 本条立刻红。
    """
    _alice_pending()
    monkeypatch.setattr(m, "agent_graph", _FakeGraph(next_=("tools",)))

    out = _approve()

    assert out["status"] == "error" and calls == []


# ==================== ③ 留痕坏了 不许把审批带崩 ====================

def test_audit_failure_does_not_break_approval(monkeypatch):
    """留痕是**旁路**：`record_decision` 抛了，审批也必须照常返回 `approved`。"""
    pa.clear(); _alice_pending()

    def _boom(**_kw):
        raise RuntimeError("模拟留痕写库炸了")

    monkeypatch.setattr(m, "record_decision", _boom)

    out = _approve()

    assert out["status"] == "approved", f"留痕失败把审批带崩了：{out}"


# ==================== ④ 裁定 6 的反面：owner 收窄歧义 ====================

def test_owner_resolves_ambiguity_on_approve(calls):
    """与 `/agent/pending/context` 同款：给了 `owner`，歧义消失，批的是**他指的那本**。"""
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [{"name": "x", "args": {}}],
                raw_thread_id=THREAD, graph="agent_graph")
    pa.register(session_key("bob", THREAD), "bob", [{"name": "x", "args": {}}],
                raw_thread_id=THREAD, graph="agent_graph")

    out = _approve(user_name="admin", owner="bob")

    assert out["status"] == "approved"
    assert calls[0]["owner"] == "bob", f"批错了会话：{calls[0]['owner']}"


def test_owner_is_not_authorization(calls):
    """`owner` **只是收窄候选**，不是授权 —— bob 传 `owner="alice"` 照样被拒。"""
    pa.clear(); _alice_pending()

    out = _approve(user_name="bob", owner="alice")

    assert out["status"] == "error" and "无权" in out["message"] and calls == []


def test_owner_defaults_to_none_and_keeps_old_behavior(calls):
    """**不许把 `owner` 变成必填** —— 不传 ⇒ 行为与改动前**逐字一致**。"""
    _alice_pending()
    assert _approve()["status"] == "approved"
