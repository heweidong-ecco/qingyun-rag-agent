"""· 两条历史链的**记忆注入 fail-open** —— 接线判据。
"""
import asyncio
import importlib

import pytest
from langchain_core.messages import HumanMessage

_AGA = "agent.agent_graph_advanced"
_AGL = "agent.agent_graph_advanced_learning"

#: 假模型回的意图 —— 内容不重要，本文件量的是**喂进去的 prompt**。
_INTENT = "CHAT"


def _bomb(*_a, **_k):
    """模拟「embedding 取不到」—— CI 里的占位 key 就是这个形状（DashScope 回 401）。"""
    raise RuntimeError("DashScope 401: invalid api key")


def _state():
    return {
        "messages": [HumanMessage(content="在保设备上门收不收工时费")],
        "user_name": "u1",
        "memory_space": "default",
        "thread_id": "t1",
    }


def _call(fn, state):
    """直调节点。`supervisor` 在本仓是**同步**函数，但节点也可能被换成异步的 ⇒ 两种都接。"""
    return asyncio.run(fn(state)) if asyncio.iscoroutinefunction(fn) else fn(state)


# ═══════════════════════════════════════════════════════════════════
# 一 · 两处 `inject_memories_to_prompt`
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("modname", [_AGA, _AGL], ids=["mcp_chat 那条图", "advanced_chat 那条图"])
def test_注入函数_检索失败时回原样(monkeypatch, modname):
    """`mcp_chat` 与 `advanced_chat` 各有一份 `inject_memories_to_prompt`，两份都要 fail-open。"""
    mod = importlib.import_module(modname)

    # ① 正控：有记忆 ⇒ **必须**注入（否则 ② 恒真、证明不了任何东西）
    monkeypatch.setattr(mod, "search_user_memory", lambda uid, q: ["用户偏好：用表格回答"])
    out = mod.inject_memories_to_prompt("原始提示", _state())
    assert "用户偏好：用表格回答" in out, (
        f"正控失败：`{modname}` 的注入**本身没生效** ⇒ 下面那条恒真，证明不了任何东西"
    )
    assert "原始提示" in out, "注入时把原 prompt 弄丢了"

    # ② 反证：检索炸了 ⇒ 回**原样**，不抛
    monkeypatch.setattr(mod, "search_user_memory", _bomb)
    assert mod.inject_memories_to_prompt("原始提示", _state()) == "原始提示", (
        f"`{modname}`：检索失败时没有降级 ⇒ 这条链会整条 500"
    )


# ═══════════════════════════════════════════════════════════════════
# 二 · 入口节点 `supervisor`（**直调**那一处）
# ═══════════════════════════════════════════════════════════════════


class _FakeLLM:
    """假模型：**记下**每一次 `invoke()` 收到的 prompt，回一个意图。"""

    def __init__(self):
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages[-1].content)
        return type("_R", (), {"content": _INTENT})()


def _supervisor_fn(monkeypatch, agl, search):
    """把 `supervisor` 周围那三件外部件打桩，再**从编译好的图里**把节点函数取回来。

    三件必须全打 —— 否则会**真连库 / 真花钱**（`check_token_budget` 读 PG）。
    取不到节点时**必须炸**（不许 `getattr(..., None)`）—— 那会让本条退化成
       "调了个空函数"，而它**照样绿**。同 `test_budget_soft_return._node_fn` 的理由。
    """
    monkeypatch.setattr(agl, "search_user_memory", search)
    monkeypatch.setattr(agl, "check_token_budget", lambda *a, **k: True)
    monkeypatch.setattr(agl, "record_from_response", lambda *a, **k: None)
    fake = _FakeLLM()
    monkeypatch.setattr(agl, "llm", fake)

    graph = agl.build_advanced_agent()
    bound = graph.nodes["supervisor"].bound
    fn = bound.func or bound.afunc
    assert fn is not None, "从图里取不到 `supervisor` 的函数 ⇒ 本用例的判据失效"
    return fn, fake


def test_入口节点_检索失败时照常判意图(monkeypatch):
    """**这一处是  原先漏掉的那一处** —— 它不走 `inject_memories_to_prompt`。"""
    from agent import agent_graph_advanced_learning as agl

    # ① 正控：有记忆 ⇒ 那段**真的**进了 `classify_prompt`
    fn, fake = _supervisor_fn(monkeypatch, agl, lambda uid, q: ["用户偏好：用表格回答"])
    _call(fn, _state())
    assert "用户偏好：用表格回答" in fake.prompts[-1], (
        "正控失败：`supervisor` 这一处的注入**本身没生效** ⇒ 下面那条恒真"
    )

    # ② 反证：检索炸了 ⇒ 不抛，照常拿到 intent，且**没有**拼记忆段
    fn2, fake2 = _supervisor_fn(monkeypatch, agl, _bomb)
    out = _call(fn2, _state())
    assert out["intent"] == _INTENT, "检索失败把整条链打挂了 ⇒ 这正是  的病灶"
    assert "用户相关记忆" not in fake2.prompts[-1], (
        "检索失败了却仍然拼了记忆段 ⇒ 那条 fail-open 的分支没生效"
    )


# ═══════════════════════════════════════════════════════════════════
# 三 · 探针不许被 fail-open 吞掉（本文件真正的"门"）
# ═══════════════════════════════════════════════════════════════════


def test_探针不会被_fail_open_吞掉(monkeypatch):
    """`test_budget_soft_return._no_memory` 的探针报的是 `AssertionError`，
    而它是 `Exception` 的子类 ⇒ 新加的 `except Exception` **会把它吞掉**。

    本用例把那条**看不见的失效**变成可打印的判据：
    探针必须**照样穿出来**。

    **反证**（要自己跑一遍）：把三处守卫里的 `except AssertionError: raise` 删掉
    ⇒ 本用例**当场红**；`test_budget_soft_return` 却**仍然绿**。
    """
    from agent import agent_graph_advanced_learning as agl

    def _probe(*_a, **_k):
        raise AssertionError("预算已被拒，却仍然去取长期记忆 ⇒ 钱花在闸之前")

    # ① 两处 `inject_memories_to_prompt`：探针是**整个替掉函数**的 ⇒ 本来就不会被吞。
    #    这里钉的是**另一种可能**：将来谁把守卫加在**调用方**（而不是函数体内），
    #    探针就会被吞 ⇒ 这一条把它变成红的。
    for modname in (_AGA, _AGL):
        mod = importlib.import_module(modname)
        monkeypatch.setattr(mod, "search_user_memory", _probe)
        with pytest.raises(AssertionError):
            mod.inject_memories_to_prompt("原始提示", _state())

    # ② 入口节点那一处：探针装在 `search_user_memory` 上，**必须**照样穿出来。
    fn, _fake = _supervisor_fn(monkeypatch, agl, _probe)
    with pytest.raises(AssertionError):
        _call(fn, _state())
