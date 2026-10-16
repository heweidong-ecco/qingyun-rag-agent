"""`/agent/pending` 的**可见性**（§二·发现③）。

**改动前是什么样**：端点的唯一依赖是 `get_current_user_hybrid`，实现是 `rows = list_pending`，
   `pending_approvals.list_pending` **一处过滤都没有** ⇒ **任何登录用户都能看到所有人的
   待批会话**（含别人的 `tool_calls` 与 `raw_thread_id`）。
"""
import asyncio

import routing.api_v1_agent as m
import agent.pending_approvals as pa
from access.session_key import session_key


def _seed_both():
    pa.register(session_key("alice", "default"), "alice", [{"name": "a", "args": {}}],
                raw_thread_id="default")
    pa.register(session_key("bob", "default"), "bob", [{"name": "b", "args": {}}],
                raw_thread_id="default")


def _list(user_name):
    return asyncio.run(m.list_pending_approvals(user_name=user_name))


def test_clean_registry():
    """注册表是模块级内存 ⇒ 用例之间会互相污染，**而污染不报错**。"""
    pa.clear()


def test_a_user_sees_only_their_own():
    """本条就是那个洞。**改动前：alice 能看到 bob 那条。**"""
    pa.clear(); _seed_both()

    out = _list("alice")

    owners = {r["user_name"] for r in out["items"]}
    assert owners == {"alice"}, f"alice 看到了别人的待批会话：{owners}"
    assert out["count"] == 1


def test_admin_sees_everything():
    """硬门 D 要 admin **接管别人的** ⇒ 他必须看得见全量（别把 admin 也收窄了）。"""
    pa.clear(); _seed_both()

    out = _list("admin")

    assert {r["user_name"] for r in out["items"]} == {"alice", "bob"}
    assert out["count"] == 2


def test_admin_is_decided_by_role_not_by_string():
    """"谁是 admin"由 `permission.get_user_role` 定义 ⇒ 别在这里另写 `== "admin"`。"""
    from access.permission import UserRole, get_user_role
    assert get_user_role("admin") == UserRole.ADMIN
    assert get_user_role("alice") != UserRole.ADMIN
