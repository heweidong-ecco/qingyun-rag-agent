"""会话隔离的**接线**判据 —— 端点有没有把身份拼进 checkpoint 键（丙段）。

**测的是什么**：不是模型答得好不好，是**两个用户带同一个 `thread_id` 时，
传给图的 `config` 是不是同一个**。今天（改动前）是同一个 ⇒ 共用一个桶。
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage

import routing.api_v1_agent as m


# ==================== 假图：只记录「被传了什么 config」 ====================

class _RecSync:
    """记下每次 `invoke` 收到的 `config`（同步图：`agent_graph` / `advanced_agent` / `checkpointer_agent`）。"""

    def __init__(self, result=None):
        self.configs = []
        self._result = result or {"messages": [AIMessage(content="ok")]}

    def invoke(self, state, config=None):
        self.configs.append(config)
        return self._result

    def keys(self):
        return [c["configurable"]["thread_id"] for c in self.configs]


class _RecAsync(_RecSync):
    """异步图：`mcp_agent` 走的是 `ainvoke`。"""

    async def ainvoke(self, state, config=None):
        self.configs.append(config)
        return self._result


@pytest.fixture
def gates(monkeypatch):
    """两道门放行 —— 它们不是本文件的被测对象（碰 Redis/PG）。"""
    monkeypatch.setattr(m, "check_session_token_budget", lambda u, t, **kw: (True, ""))
    monkeypatch.setattr(m, "circuit", lambda k: (True, ""))

    # `mcp_chat` 收尾还要写追踪 —— 同样打桩，避免碰库
    try:
        monkeypatch.setattr(m, "finish_trace", lambda *a, **kw: None)
    except AttributeError:
        pass


# ==================== ① 核心：同 thread_id、不同人 ⇒ 键必须不同 ====================

def _endpoints():
    """(名字, 协程, 假图, 额外形参) —— 一张表覆盖所有构造 config 的端点。"""
    return [
        ("langgraph_chat", m.langgraph_chat, _RecSync(), {}),
        ("advanced_chat", m.advanced_agent_chat, _RecSync({"final_output": "ok"}), {}),
        ("memory_chat", m.memory_chat, _RecSync(), {}),
        ("mcp_chat", m.mcp_agent_chat, _RecAsync(), {}),
    ]


@pytest.mark.parametrize("name", [e[0] for e in _endpoints()])
def test_same_thread_id_different_users_get_different_checkpoint_keys(name, gates, monkeypatch):
    """**丙段要修的就是这一条** —— 两个人用同一个 `thread_id` 不能共用一个桶。

    改动前：4 条端点传下去的都是**裸 `thread_id`** ⇒ alice 与 bob 的键**一模一样**
       ⇒ 共用 `MemorySaver` 的一个桶 ⇒ 消息（`Annotated[List, operator.add]`）**append 到一起**。
    """
    _, coro, fake, extra = next(e for e in _endpoints() if e[0] == name)
    graph_attr = {"langgraph_chat": "agent_graph",
                  "advanced_chat": "advanced_agent",
                  "memory_chat": "checkpointer_agent",
                  "mcp_chat": "mcp_agent"}[name]
    monkeypatch.setattr(m, graph_attr, fake)

    for user in ("alice", "bob"):
        asyncio.run(coro(question="q", thread_id="default", user_name=user, **extra))

    assert len(fake.configs) == 2, "两个用户各应打到图一次"
    alice_key, bob_key = fake.keys()
    assert alice_key != bob_key, (
        f"`{name}`：alice 与 bob 用同一个 `thread_id='default'` 拿到了**同一个** checkpoint 键 "
        f"（`{alice_key}`）⇒ 共用一个桶 ⇒ 对话记忆跨用户串号（丙段）"
    )


# ==================== ② 键必须【不是】裸 thread_id ====================

@pytest.mark.parametrize("name", [e[0] for e in _endpoints()])
def test_key_is_not_the_raw_thread_id(name, gates, monkeypatch):
    """键里必须**带人** —— 光看"两人不同"还不够（比如按调用顺序编个号也能"不同"）。

    ⇒ 直接钉住：键 ≠ 调用方传进来的原值。
    """
    from access.session_key import session_key

    _, coro, fake, extra = next(e for e in _endpoints() if e[0] == name)
    graph_attr = {"langgraph_chat": "agent_graph",
                  "advanced_chat": "advanced_agent",
                  "memory_chat": "checkpointer_agent",
                  "mcp_chat": "mcp_agent"}[name]
    monkeypatch.setattr(m, graph_attr, fake)

    asyncio.run(coro(question="q", thread_id="t-raw", user_name="alice", **extra))

    assert fake.keys() == [session_key("alice", "t-raw")], (
        f"`{name}` 传下去的键不是 `session_key(user_name, thread_id)` —— "
        f"实际 = {fake.keys()}（丙段：拼法唯一落点是 `app/access/session_key.py`）"
    )


# ==================== ③ 对外契约不变：响应里回显【原值】 ====================

def test_response_echoes_the_raw_thread_id(gates, monkeypatch):
    """拼出来的键是**内部 id** —— 调用方传什么、拿回来就还是什么。

    不许把 `11:isolation_a:default` 这种内部前缀吐给调用方：
       调用方后续还要拿它去 `/agent/approve`，契约一变**所有调用方都得改**。
    """
    fake = _RecSync()
    monkeypatch.setattr(m, "checkpointer_agent", fake)

    out = asyncio.run(m.memory_chat(question="q", thread_id="t-raw", user_name="alice"))

    assert out["thread_id"] == "t-raw", "响应必须回显调用方传进来的原值"
