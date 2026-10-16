"""`/agent/approve` 的**归属校验**（丙段）。

**改动前是什么样**：`/agent/approve` **没有任何归属校验** ——
   任何登录用户拿一个 `thread_id` 就能**批准并续跑**那个会话。
   更糟的是：它用**调用方自己**的键去取 checkpoint ⇒ 拿到的是**别人的**图状态。
"""
import asyncio
import types

import pytest
from langchain_core.messages import AIMessage

import routing.api_v1_agent as m
import agent.pending_approvals as pa
from access.permission import UserRole, get_user_role
from access.session_key import session_key

THREAD = "default"


# 卡在审批点的那条消息（真货形状：**只有 `tool_calls`、没有文字**）。
_STUCK = AIMessage(content="", tool_calls=[
    {"name": "web_search", "args": {}, "id": "call_1"}])


class _FakeGraph:
    """记下 approve 对图做过的**每一个动作**（含没做动作 ⇒ `trace == []`）。

    **`values["messages"]` 必须给**：`/agent/approve` 现在要读**卡住的那条 `tool_calls`**，
       去回填配对的 `ToolMessage`（硬门 D 修复）。给空 dict 会直接 `KeyError` ——
       而那是**假图的缺陷**，不是被测代码的（真 `StateSnapshot` 一定有 `.values["messages"]`）。
    **`next_after`**：**放行之后** `get_state.next`。默认 ``（走完了）。
       approve 现在读两次 state（放行前判"停没停"、放行后判"该不该注销登记"）——
       若两处共用一个恒定值，**"放行后还停在审批点"这条岔路就永远测不到**。
    """

    def __init__(self, next_=("approval",), next_after=()):
        self.trace = []          # [(动作名, config), …] —— **按调用顺序**
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
        # 吞掉 `as_node` 等关键字（真货会收）；回填的**形状**由
        #    `test_approval_resume.py` §⑤ 的真图用例钉，这里只记"动过图"。
        self.trace.append(("update_state", config))
        return config

    def invoke(self, state, config=None):
        self.trace.append(("invoke", config))
        self._invoked = True
        return {"messages": [AIMessage(content="done")]}

    def configs(self):
        return [c for _, c in self.trace]


@pytest.fixture
def graph(monkeypatch):
    fake = _FakeGraph()
    monkeypatch.setattr(m, "agent_graph", fake)
    return fake


@pytest.fixture(autouse=True)
def _clean_registry():
    """注册表是**模块级内存** ⇒ 用例之间会互相污染，**而污染不报错**。"""
    pa.clear()
    yield
    pa.clear()


def _alice_pending(thread=THREAD):
    """alice 卡在审批 —— 注册时键要**拼身份**（丙段之后的真实形状）。"""
    pa.register(session_key("alice", thread), "alice", [{"name": "x", "args": {}}],
                raw_thread_id=thread)


def _approve(**kw):
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("approved", True)
    kw.setdefault("edited_answer", None)
    kw.setdefault("user_name", "alice")
    return asyncio.run(m.approve_agent_action(**kw))


# ==================== ① 核心：别人的会话，不许批 ====================

def test_non_owner_cannot_approve(graph):
    """丙段要修的就是这一条 —— `bob` 拿 `thread_id` 去批 alice 的会话。

    改动前：**放行**（而且续跑的是 alice 的 checkpoint）。
    """
    _alice_pending()

    out = _approve(user_name="bob")

    assert out["status"] == "error", f"bob 不该批得动 alice 的会话，实际返回 {out}"
    assert graph.trace == [], (
        "拒绝必须在**碰图之前**发生 —— 一旦先 `get_state`/`invoke`，"
        "就等于已经「接手」了别人的会话（哪怕随后报错）"
    )


# ==================== ② 本人可以批，且键按【属主】拼 ====================

def test_owner_can_approve_and_key_is_namespaced_by_owner(graph):
    _alice_pending()

    out = _approve(user_name="alice")

    assert out["status"] == "approved"
    assert graph.configs(), "应当动过图"
    assert all(c["configurable"]["thread_id"] == session_key("alice", THREAD)
               for c in graph.configs()), (
        f"approve 拼的键必须是 `session_key(属主, thread_id)`，实际 {graph.configs()}"
    )


# ==================== ③ 硬门 D：admin 必须能批【别人】的 ====================

def test_admin_can_approve_someone_elses_session(graph):
    """这条是"别把 admin 关在门外"的守卫 —— 少了它，跨用户队列（`/agent/pending`）就没用了。

    关键不在"admin 能批"，而在**它拼的是谁的键**：
       必须是**属主 alice 的**，不是 admin 自己的 `session_key("admin", …)`。
       （按调用方拼 ⇒ admin 拼出的桶根本不存在 ⇒ 永远批不了 —— 这就是那个坑。）
    """
    _alice_pending()

    out = _approve(user_name="admin")

    assert out["status"] == "approved"
    assert all(c["configurable"]["thread_id"] == session_key("alice", THREAD)
               for c in graph.configs()), (
        f"admin 批准时也必须用**属主**的键，实际 {graph.configs()}"
    )


def test_admin_is_the_premium_role_not_a_hardcoded_string():
    """"谁是 admin"由 `permission.get_user_role` 定义——
    别在这条链路上另写一套 `== "admin"` 判断（那就又多了一处口径）。"""
    assert get_user_role("admin") == UserRole.ADMIN
    assert get_user_role("alice") != UserRole.ADMIN


# ==================== ④ 队列里没有 ⇒ 如实说"没有"，别去碰图 ====================

def test_unknown_thread_reports_no_pending_task(graph):
    out = _approve(user_name="alice")       # 队列空

    assert out["status"] == "error"
    assert "审批" in out["message"]
    assert graph.trace == [], "队列里没有 ⇒ ⛔ 不许去碰图"


# ==================== ⑤ 裁定 6：可选 `owner` 收窄 ====================

def test_owner_param_narrows_candidates(graph):
    """两个人都用 `thread_id="default"` ⇒ 歧义；给了 `owner` ⇒ 批的是**他指的那本**。"""
    pa.clear()
    pa.register(session_key("alice", THREAD), "alice", [{"name": "x", "args": {}}],
                raw_thread_id=THREAD)
    pa.register(session_key("bob", THREAD), "bob", [{"name": "x", "args": {}}],
                raw_thread_id=THREAD)

    out = _approve(user_name="admin", owner="bob")

    assert out["status"] == "approved"
    assert all(c["configurable"]["thread_id"] == session_key("bob", THREAD)
               for c in graph.configs()), f"批错了会话：{graph.configs()}"
