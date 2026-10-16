"""追踪轴（`tool_visualizer`）的**归属与键**。
"""
import asyncio

import pytest

import routing.api_v1_agent as m
import tools.tool_visualizer as tv
from access.permission import UserRole, get_user_role

THREAD = "t-trace"


@pytest.fixture(autouse=True)
def _clean():
    """`_traces` 是**进程级字典** ⇒ 用例之间必须清干净，否则互为污染。"""
    tv._traces.clear()
    yield
    tv._traces.clear()


def _run(user_name: str, thread_id: str = THREAD, question: str = "q"):
    """造一条完整轨迹（开始 → 结束）。"""
    tv.start_trace(user_name, thread_id, question)
    tv.finish_trace(user_name, thread_id, "answer")


def _list(user_name: str):
    return asyncio.run(m.agent_trace_list(user_name))


def _detail(user_name: str, thread_id: str = THREAD):
    return asyncio.run(m.agent_trace_detail(thread_id, user_name))


# ==================== 一 · GET /agent/traces 只给自己的 ====================

def test_list_shows_only_own_traces():
    """本文件的主断言：别人的轨迹**不在**我的列表里。"""
    _run("alice", thread_id="t-alice", question="爱丽丝的问题")
    _run("bob", thread_id="t-bob", question="鲍勃的问题")

    names = [t["thread_id"] for t in _list("bob")["traces"]]

    assert names == ["t-bob"], f"鲍勃只该看到自己那条，实际 {names}"


def test_list_positive_control_owner_still_sees_own():
    """**正向控制** —— 没有它，一个"永远返回空列表"的实现也能过上面那条。"""
    _run("alice", thread_id="t-alice", question="爱丽丝的问题")

    traces = _list("alice")["traces"]

    assert [t["thread_id"] for t in traces] == ["t-alice"]
    assert traces[0]["user_query"] == "爱丽丝的问题"


def test_list_admin_sees_everyone():
    """admin 读侧例外（与  决策 2 / 决策 8-3 同一口径）。"""
    _run("alice", thread_id="t-alice", question="爱丽丝的问题")
    _run("bob", thread_id="t-bob", question="鲍勃的问题")

    assert get_user_role("admin") == UserRole.ADMIN
    names = sorted(t["thread_id"] for t in _list("admin")["traces"])

    assert names == ["t-alice", "t-bob"], f"admin 该看到两条，实际 {names}"


# ==================== 二 · GET /agent/trace/{thread_id} 要判属主 ====================

def test_detail_refuses_another_users_trace():
    """拿别人的 `thread_id` 查 ⇒ **查不到**（不是"查到了但标个红字"）。"""
    _run("alice", question="爱丽丝的问题")

    out = _detail("bob")

    assert "爱丽丝的问题" not in str(out), f"鲍勃读到了爱丽丝的提问原文：{out}"


def test_detail_positive_control_owner_can_read_own():
    """**正向控制** —— 防「谁都查不到」冒充修好了。"""
    _run("alice", question="爱丽丝的问题")

    out = _detail("alice")

    assert out["trace"]["user_query"] == "爱丽丝的问题"


def test_detail_admin_can_read_others():
    """admin 例外要**真的能用**（不是一句口号）。"""
    _run("alice", question="爱丽丝的问题")

    out = _detail("admin")

    assert out["trace"]["user_query"] == "爱丽丝的问题"


def test_detail_unknown_thread_still_says_not_found():
    """不存在的线程：口径不变（非属主走的也是这条路 ⇒ 不泄露"存在与否"）。"""
    out = _detail("bob", thread_id="never-existed")

    assert out.get("trace") is None
    assert "未找到" in out.get("error", "")


# ==================== 三 · 同一个 thread_id 不再共用一个槽 ====================

def test_two_users_with_same_thread_id_do_not_share_a_slot():
    """键碰撞：`thread_id` 默认就是 `"default"` ⇒ 不拼身份的话**后问的盖先问的**。"""
    _run("alice", thread_id="default", question="爱丽丝的问题")
    _run("bob", thread_id="default", question="鲍勃的问题")

    assert _detail("alice", thread_id="default")["trace"]["user_query"] == "爱丽丝的问题"
    assert _detail("bob", thread_id="default")["trace"]["user_query"] == "鲍勃的问题"


def test_tool_records_land_in_the_callers_bucket():
    """工具调用记在**发起人**那条轨迹上（不是"当前最后一条轨迹"）。"""
    _run("alice", question="爱丽丝的问题")
    tv.start_trace("bob", THREAD, "鲍勃的问题")
    tv.record_tool_start("web_search", {"q": "x"}, "bob", THREAD)
    tv.record_tool_end("web_search", "结果", "bob", THREAD)

    assert len(_detail("alice")["trace"]["tool_calls"]) == 0, "爱丽丝的轨迹被动过了"
    assert len(_detail("bob")["trace"]["tool_calls"]) == 1, "鲍勃的工具调用没记上"


# ==================== 四 · 身份是**必填**（fail-closed） ====================

@pytest.mark.parametrize("call", [
    lambda: tv.start_trace(THREAD, "q"),                       # 少了 user_name
    lambda: tv.start_trace(THREAD),                            # 少两个
    lambda: tv.record_tool_start("t", {}, THREAD),             # 少了 user_name
    lambda: tv.get_trace(THREAD),                              # 少了 user_name
])
def test_identity_is_required(call):
    """漏传身份 ⇒ `TypeError`，**不是**退回某个默认桶。

    与 `session_key`（决策 8-1）同一取向：
    **漏传必须是"崩"，不是"悄悄记到别人/公共的桶里"**。
    """
    with pytest.raises(TypeError):
        call()
