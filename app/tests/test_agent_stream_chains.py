"""· **剩余 4 条链**的真流式（``）。
"""

import ast
import asyncio
import importlib
import inspect
import json
import textwrap

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGenerationChunk

import agent.agent_checkpointer as ac
import agent.agent_graph_advanced as aga
import agent.agent_graph_advanced_learning as agl
import routing.api_v1_agent as m
import core.cache as cache_mod
from conftest import FakeRedis

# **复用既有夹具**（不复制粘贴第三份）—— 理由见文件头。
from test_agent_sse import (           # noqa: E402
    _FakeStreamingModel,
    _bind,
    _collect_frames,
)
from test_cancel_propagation import (  # noqa: E402
    DISCONNECT_AFTER,
    TOLERANCE,
    _AsyncSpyStream,
    _body_chunks,
    _capture_logs,
    _drive_asgi_until_disconnect,
    _metric,
)
from access.session_key import session_key    # noqa: E402

THREAD = "t-chain"


# ==================== 四条链的清单（判据都从这里取，不散落在用例里） ====================

#: 链 → (端点函数名, 图模块属性名, SSE 计数器标签, 图模块)
CHAINS = {
    "A": {
        "route": "/agent/advanced_chat/stream",
        "func": "advanced_agent_chat_stream",
        "graph_attr": "advanced_agent",
        "endpoint": "agent_advanced_chat_stream",
        "module": agl,
        "subgraphs": True,       # 链 A 独有：5 个部门全是子图
    },
    "B": {
        "route": "/agent/memory_chat/stream",
        "func": "memory_chat_stream",
        "graph_attr": "checkpointer_agent",
        "endpoint": "agent_memory_chat_stream",
        "module": ac,
        "subgraphs": False,
    },
    "C": {
        "route": "/agent/mcp_chat/stream",
        "func": "mcp_agent_chat_stream",
        "graph_attr": "mcp_agent",
        "endpoint": "agent_mcp_chat_stream",
        "module": aga,
        "subgraphs": False,
    },
    "D": {
        "route": "/agent/plan_execute/stream",
        "func": "agent_plan_execute_stream",
        "graph_attr": None,      # 链 D 没有图（`plan_execute.py` 的同步函数）
        "endpoint": "agent_plan_execute_stream",
        "module": None,
        "subgraphs": None,
    },
}


# ==================== ① 结构性：节点必须声明 `config` 并转发给**流式**调用 ====================

#: (模块, 节点函数名, 该节点该走的流式方法名)
STREAMING_NODES = [
    (ac, "agent_decide", "stream"),        # 链 B：同步节点 ⇒ 同步 `.stream()`
    (aga, "chat_node", "astream"),         # 链 C：async 节点 ⇒ 必须 `.astream()`
    (aga, "agent_decide", "astream"),      # 链 C
    (agl, "search_summarize", "stream"),   # 链 A：本文件节点**全是同步的**
    (agl, "translate_execute", "stream"),  # 链 A
    (agl, "agent_decide", "stream"),       # 链 A（react 子图内）
    (agl, "chat_node", "stream"),          # 链 A
]


def _find_node(tree, name):
    nodes = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(nodes) == 1, f"AST 里找到 {len(nodes)} 个名为 {name} 的函数（期望恰好 1 个）"
    return nodes[0]


@pytest.mark.parametrize("module,node_name,stream_method",
                         STREAMING_NODES,
                         ids=[f"{mod.__name__}::{n}" for mod, n, _ in STREAMING_NODES])
def test_streaming_node_declares_and_forwards_config(module, node_name, stream_method):
    """**真流式的必要条件** —— 声明 `config` **且**把它转发进**流式**调用。

    光声明不转发 ⇒ 等于没接（`config` 只是个没人用的形参）；
       调 `.invoke` 而不是 `.stream` ⇒ 模型一次性返回，`stream_mode="messages"`
       **只出 1 块整段** —— 而**前端看上去仍然是逐字**（那是前端在切）。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(module)))
    fn = _find_node(tree, node_name)

    params = [a.arg for a in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs]
    assert "config" in params, (
        f"{module.__name__}::{node_name} 没有 config 形参 ⇒ 拿不到 LangGraph 注入的回调 "
        f"⇒ stream_mode='messages' 只会吐 1 块整段（假流式，而接口看上去正常）"
    )

    streaming_calls = [
        c for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and any(k.arg == "config" for k in c.keywords)
        and getattr(c.func, "attr", None) == stream_method
    ]
    assert streaming_calls, (
        f"{module.__name__}::{node_name} 里没有 `…{stream_method}(…, config=config)` 调用 ⇒ "
        f"config 声明了却没转发（或仍在走 invoke / 走错了同步异步）"
    )


def test_async_nodes_are_not_mistaken_for_sync_ones():
    """**形态守卫**：链 C 的两个节点是 `async def`，链 A/B 的是同步。

    这条不是风格之争 —— 两个方向都会**当场炸**，但炸的地方不同：
      · 把**同步**节点改成 `async def` ⇒ 同步的 `graph.invoke` 抛
        `TypeError: No synchronous function provided to "agent"`（实测），
        而 `/agent/advanced_chat`、`/agent/memory_chat`、`api_v1.py` **都在用 invoke**；
      · 把**异步**节点写成同步 ⇒ 在事件循环里**阻塞**（链 C 是 async 图）。

    ⇒ 本用例把"谁该是 async"钉死，改错了在这里红，而不是在生产上。
    """
    def _is_async(module, name):
        tree = ast.parse(textwrap.dedent(inspect.getsource(module)))
        return isinstance(_find_node(tree, name), ast.AsyncFunctionDef)

    assert _is_async(aga, "chat_node") and _is_async(aga, "agent_decide"), (
        "`agent_graph_advanced`（链 C）的节点必须是 async —— 那张图是异步图"
    )
    assert not _is_async(ac, "agent_decide"), (
        "`agent_checkpointer::agent_decide` 一旦变成 async，`/agent/memory_chat` 的 "
        "`graph.invoke()` 会直接抛 TypeError"
    )
    assert not _is_async(agl, "chat_node"), (
        "`agent_graph_advanced_learning::chat_node` 必须是同步的 —— "
        "`/agent/advanced_chat` 走的是同步 `astream`/`invoke` 路径"
    )


# ==================== ②③④ 端点：假图驱动 ====================


class _ChainGraph:
    """四条链（A/B/C）共用的假图。

    `astream` 收 `**kw` —— 链 A 会传 `subgraphs=True`，而既有两份假图的签名都是
       位置参数写死的（故**不改**那两份：它们是已合入文件的夹具，要求"复用"）。
    上游流用 `test_cancel_propagation._AsyncSpyStream`（**手动迭代器**）——
       理由见那份文件头：写成 async generator 的话，取消用例会**假绿**。
    """

    def __init__(self, cap=None, items=None, final_values=None,
                 aclose_interrupted=False, aclose_raises=False, explode_after=None):
        # `cap` 不传时按 `items` 的长度**自然结束**；显式传 `cap=40` 才是"一直吐到断开"
        #    （取消用例要的正是后者 —— 上游必须还在吐，断开才有东西可断）。
        #：`explode_after=N` ⇒ 吐够 N 块之后抛 `RuntimeError`（"生成到一半炸了"）。
        #    `_AsyncSpyStream` 从 owner 上取这个属性（同 `_SpyGraph` / `_SpyRagLLM` 的用法）。
        self.cap = cap if cap is not None else (len(items) if items else 40)
        self.pulled = 0
        self.closed = False
        self.aclose_called = False
        self.aclose_interrupted = aclose_interrupted   # 模拟"关流被二次取消打断"（真服务实测）
        self.aclose_raises = aclose_raises             # 模拟"关流动作自己失败"（shield 护不住）
        self.explode_after = explode_after             # ⇒ 吐够 N 块之后抛（异常出口）
        self.items = items or []
        self._i = 0
        self._final_values = final_values
        self.astream_calls = []
        self.aget_state_calls = []

    # ---- 上游流 ----
    def astream(self, payload, config, stream_mode, **kw):
        self.astream_calls.append({
            "payload": payload, "config": config,
            "stream_mode": stream_mode, "kwargs": kw,
        })
        return _AsyncSpyStream(self, self._next_item)

    def _next_item(self):
        if self.items:
            item = self.items[self._i % len(self.items)]
            self._i += 1
            return item
        return AIMessageChunk(content="字"), {"langgraph_node": "agent"}

    # ---- 最终状态 ----
    async def aget_state(self, config):
        self.aget_state_calls.append(config)
        values = self._final_values
        if callable(values):
            values = values()
        return type("S", (), {"values": values if values is not None else {"messages": []}})


def _tokens(*texts, nodes=("agent",)):
    """造一条「每个 token 一块」的流 —— 用**图内层节点名**（`meta["langgraph_node"]`）。"""
    node = nodes[0] if len(nodes) == 1 else None
    out = []
    for i, t in enumerate(texts):
        name = node if node else nodes[i % len(nodes)]
        out.append((AIMessageChunk(content=t), {"langgraph_node": name}))
    return out


def _call(chain_key, monkeypatch, graph=None, patch_pending=True, redis=None, **kw):
    """直接调端点函数（不走 TestClient / 不建库）—— 拿到的是**未缓冲**的生成器。

    把 B8 / B11 短路掉：本文件测的是**流式接线**，不是预算判定
       （那两条各有自己的用例：`test_session_budget_wiring.py` / `test_breaker_wiring.py`）。
    链 C 还要短路**追踪 / 预算提醒**（`start_trace` / `finish_trace` /
       `check_budget_warning`）—— 它们会往追踪存储里写，与"流式接线"无关。
    `patch_pending=False` 留给链 B 那条**要断言登记内容**的用例（它自己装间谍）。
     起端点会往 `chat_history` 写留痕 ⇒ 必须短路存储（同 `_call_stream_route`）。
    """
    spec = CHAINS[chain_key]
    if graph is not None:
        monkeypatch.setattr(m, spec["graph_attr"], graph)
    monkeypatch.setattr(m, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "start_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "finish_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "check_budget_warning",
                        lambda *a, **k: {"warning": False, "message": ""})
    if patch_pending:
        monkeypatch.setattr(m, "register", lambda *a, **k: None)
        monkeypatch.setattr(m, "resolve", lambda *a, **k: None)
    # 桩打在【叶子】`cache.redis_client`（不是各端点的模块全局）—— 一个点盖住 6 条端点。
    monkeypatch.setattr(cache_mod, "redis_client", redis if redis is not None else FakeRedis())
    kw.setdefault("question", "你好")
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("user_name", "tester")
    return asyncio.run(getattr(m, spec["func"])(**kw))


def _frames_to_objs(frames):
    """把帧解析成对象；`[DONE]` 记为 `"__DONE__"`（保序，判得出"有没有攒着发"）。"""
    out = []
    for f in frames:
        body = f[len("data: "):].strip()
        out.append("__DONE__" if body == "[DONE]" else json.loads(body))
    return out


def _contents(objs):
    return [o["content"] for o in objs
            if isinstance(o, dict) and o.get("content") is not None]


# ---------------------------------------------------------------- ② 逐 token 出帧

@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_stream_route_emits_one_frame_per_token(chain_key, monkeypatch):
    """**后端不缓冲** —— 3 个 token ⇒ **至少 3 帧内容**，不是 1 帧整段。

    这正是弱测试抓不到的形态：只发「整段 + `[DONE]`」也是 2 个 `data:`。
    """
    items = _tokens("你", "好", "呀")
    if chain_key == "A":
        # 链 A 的 item 是**子图形状** `(namespace, (chunk, meta))` —— 因为端点必须传
        #    `subgraphs=True`（本图 5 个部门全是子图）。这条断言等于在守 `subgraphs`
        #    那个坑：形状不对 ⇒ `graph_message_text` 归化不了 ⇒ **一帧都不出**。
        items = [(("search_dept:abc",), it) for it in items]

    resp = _call(chain_key, monkeypatch, _ChainGraph(items=items))
    assert resp.media_type == "text/event-stream"
    assert resp.headers.get("X-Accel-Buffering") == "no", "少了禁缓冲头，Nginx 会攒着发"

    frames = _collect_frames(resp)
    objs = _frames_to_objs(frames)
    contents = _contents(objs)

    assert len(contents) >= 3, f"只发了 {len(contents)} 帧内容 ⇒ 后端**攒着发**了。帧：{frames}"
    assert "".join(contents) == "你好呀"
    assert objs[-1] == "__DONE__", f"末帧不是 [DONE]：{frames[-1]!r}"


@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_stream_route_forwards_thread_id_as_session_key(chain_key, monkeypatch):
    """接线守卫：进图的键**必须是 `session_key(user_name, thread_id)`**（丙段）。

    传丢了会让两个用户**共用一个 checkpoint 桶**（消息是 append 的 ⇒ 模型看得到别人的对话），
       而**接口返回一切正常**。
    链 A 独有：`subgraphs=True` **必须在**（§1.1 —— 它是链 A 出不出 token 的唯一开关）。
    """
    graph = _ChainGraph(items=_tokens("答"))
    resp = _call(chain_key, monkeypatch, graph)
    _collect_frames(resp)      # `StreamingResponse` 是**惰性**的：不消费就还没跑

    assert len(graph.astream_calls) == 1, f"图被调了 {len(graph.astream_calls)} 次"
    call = graph.astream_calls[0]
    assert call["config"]["configurable"]["thread_id"] == session_key("tester", THREAD), (
        f"进图的 checkpoint 键不对：{call['config']['configurable']['thread_id']!r}"
    )
    assert call["stream_mode"] == "messages", "⛔ 不是 messages 模式 ⇒ 拿不到 token 块"
    if CHAINS[chain_key]["subgraphs"]:
        assert call["kwargs"].get("subgraphs") is True, (
            "链 A 没传 `subgraphs=True` ⇒ 5 个部门子图一个都不出 token（假流式）"
        )
    else:
        assert "subgraphs" not in call["kwargs"], (
            f"链 {chain_key} 的图**没有子图** ⇒ 不该开 `subgraphs`（开了会改变 item 形状）"
        )


# ---------------------------------------------------------------- ③ 汇总取自 aget_state

_FINAL_BY_CHAIN = {
    # 三条链的最终态**形状**不同（各取各的键）—— 这正是"必须从图上取"的原因：
    #    攒流过的块**不可能**得回这些键里的任何一个。
    "A": {"final_output": "最终答案", "intent": "CHAT"},
    "B": {"messages": [AIMessage(content="最终答案")]},
    "C": {"messages": [AIMessage(content="最终答案")]},
}


@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_summary_comes_from_final_state_not_from_streamed_chunks(chain_key, monkeypatch):
    """汇总帧必须取自**图的最终状态**，不是"把流过的块攒起来"。

    **真服务上跑出来的缺陷**：端点当时用「把每轮的 `agent` 输出
    `+` 起来」⇒ 攒出的消息**带着上一轮的 `tool_calls`** ⇒ 判成 `pending_approval`
    ⇒ **接口报"在等人工审批"，而图早就跑完了**。

    ⇒ 钉法：让**流到的内容**与**最终态**故意不同，断言汇总帧取的是后者，且**真的问过图**。
    """
    graph = _ChainGraph(
        items=_tokens("半截"),
        final_values=_FINAL_BY_CHAIN[chain_key],
    )
    resp = _call(chain_key, monkeypatch, graph)
    objs = _frames_to_objs(_collect_frames(resp))

    assert _contents(objs) == ["半截"], f"流出来的内容不对：{objs}"
    summary = objs[-2] if objs[-1] == "__DONE__" else objs[-1]
    answer_key = "answer"
    assert summary.get(answer_key) == "最终答案", (
        f"汇总帧不是从图最终状态取的（攒块会把上一轮的 tool_calls 也带上）：{summary}"
    )
    assert graph.aget_state_calls, "端点没有读图的最终状态 ⇒ 它是在拿流式块猜"


@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_summary_frame_carries_requested_by(chain_key, monkeypatch):
    """汇总帧必须带 `requested_by` —— 三条链的**非流式兄弟一直都有它**
    （`api_v1_agent.py:469` / `:838` / `:1193`）。

    少一个字段是**最难发现**的那类差异：从 `/agent/advanced_chat` 切到
       `/agent/advanced_chat/stream`，两边其余字段长得**一模一样**，只有它静默没了。
    **另外两条端点的同一断言不在本用例里**：链 D 没有图（走 `_plan_factory`），
       断言加在 `test_plan_execute_summary_carries_plan_and_execution_result`；
       基线端点 `/agent/langgraph_chat/stream` 在 `app/tests/test_agent_sse.py`。
    """
    graph = _ChainGraph(
        items=_tokens("半截"),
        final_values=_FINAL_BY_CHAIN[chain_key],
    )
    resp = _call(chain_key, monkeypatch, graph)
    objs = _frames_to_objs(_collect_frames(resp))

    # 与上面那条同款取法：末帧是 `[DONE]`，汇总帧在它**前面**一帧。
    summary = objs[-2] if objs[-1] == "__DONE__" else objs[-1]
    assert summary.get("requested_by") == "tester", (
        f"{CHAINS[chain_key]['route']} 的汇总帧没带 requested_by ⇒ "
        f"与非流式兄弟的形状**静默不一致**：{summary}"
    )


# ---------------------------------------------------------------- ④ 取消传播

@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_closes_upstream_and_counts_cancel_on_disconnect(chain_key, monkeypatch):
    """**客户端断开 ⇒ 上游必须被真的关掉**，且**计数 +1**、**日志里有 `[cancel]`**。

    关不掉 = 上游继续生成、**继续计费** —— 而前端看起来一切正常（它只是不显示了）。
       这正是硬门 C 标"最容易假完成"的原因。
    顺带钉住"取消后**不再发帧**"：旧实现会在 `except CancelledError` 里补一帧 `[DONE]`
       （实测真发出去过）—— 那会让"已取消"与"正常收尾"在帧层面**长得一样**。
    """
    spec = CHAINS[chain_key]
    graph = _ChainGraph(cap=40, items=_tokens("字"))
    resp = _call(chain_key, monkeypatch, graph)
    before = _metric(spec["endpoint"])
    with _capture_logs() as lines:
        sent = _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert graph.pulled >= DISCONNECT_AFTER, f"根本没流起来（只拉了 {graph.pulled} 块）⇒ 本断言无意义"
    assert graph.closed, "客户端断开后上游流**没有被关** ⇒ 上游还在跑、还在烧钱"
    assert graph.pulled <= DISCONNECT_AFTER + TOLERANCE, f"断开后仍拉了 {graph.pulled} 块"
    assert _metric(spec["endpoint"]) == before + 1, "取消没有计数 ⇒ Grafana 上看不到它"
    assert any("cancel" in line.lower() or "断开" in line for line in lines), (
        f"日志里没有取消事件：{lines}"
    )
    bodies = _body_chunks(sent)
    assert not any(b"[DONE]" in b for b in bodies), f"取消后还发了帧：{bodies}"


@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_bookkeeping_lands_even_when_aclose_is_interrupted(chain_key, monkeypatch):
    """**`await stream.aclose` 会被二次投递的取消打断**（真服务实测）。

    打断之后，`finally` 里排在它**后面**的收尾**一件都不跑** —— 计数不涨、日志没有。
    ⇒ 唯一的保障是**顺序**（同步收尾写在任何 `await` 之前）+ `shield`。
      本用例用"关流那次 await 真的挂起一次"来同构它（不是直接 `raise CancelledError`，
      那连 `shield` 也护不住，测出来的是假的 —— 见 `_AsyncSpyStream.aclose` 的注释）。
    """
    spec = CHAINS[chain_key]
    graph = _ChainGraph(cap=40, items=_tokens("字"), aclose_interrupted=True)
    resp = _call(chain_key, monkeypatch, graph)
    before = _metric(spec["endpoint"])
    with _capture_logs() as lines:
        _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert graph.aclose_called, "前提没成立：压根没去关上游"
    assert graph.closed, "关流被取消打断后**上游没真的关上** ⇒ 上游可能还在跑、还在烧钱"
    assert _metric(spec["endpoint"]) == before + 1, "aclose 被打断 ⇒ 取消没计数（顺序坏了）"
    assert any("断开" in line for line in lines), f"日志里没有取消事件：{lines}"


@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_normal_completion_is_not_counted_as_cancel(chain_key, monkeypatch):
    """**反面**：正常跑完**不算取消**（否则这个指标就是个恒增的假信号）。"""
    spec = CHAINS[chain_key]
    graph = _ChainGraph(cap=3, items=_tokens("答"))
    resp = _call(chain_key, monkeypatch, graph)
    before = _metric(spec["endpoint"])
    sent = _drive_asgi_until_disconnect(resp, after_chunks=99)   # 永不触发断开

    bodies = [b.decode() if isinstance(b, bytes) else b for b in _body_chunks(sent)]
    assert bodies and bodies[-1].strip() == "data: [DONE]", f"正常收尾的末帧不对：{bodies[-1:]!r}"
    assert graph.closed, "正常跑完也要关上游（否则连接泄漏）"
    assert _metric(spec["endpoint"]) == before, "正常收尾被记成了取消"


# ==================== ⑤ 留痕（三条出口各写各的 status） ====================
# **判据（可打印）**：`grep -n "persist_turn\|append_chat_history" app/routing/api_v1_agent.py`
#    ⇒ 改前**零命中**（5 条 Agent 链一个钩子都没接）。

@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_completed_turn_is_persisted_with_done_status(chain_key, monkeypatch):
    """正常跑完 ⇒ **成对写** `chat_history`，`status == "done"`，答案是**图最终状态**里的那个。

    流内块是 `你/好/呀`，最终状态是 `最终答案` —— **故意不同**：
       拿 `collected` 攒的话这条会红（真服务撞过的同一个坑：
       链 A 的 `calc_execute` 分支一个字都不流，答案来自工具返回值）。
    """
    store = FakeRedis()
    graph = _ChainGraph(items=_tokens("你", "好", "呀"),
                        final_values=_FINAL_BY_CHAIN[chain_key])
    _collect_frames(_call(chain_key, monkeypatch, graph, redis=store, question="问题一"))

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"{CHAINS[chain_key]['route']} 正常跑完却没成对留痕：{entries}"
    )
    assert entries[0]["content"] == "问题一", f"提问没留下：{entries[0]}"
    assert entries[1]["content"] == "最终答案", (
        f"🔴 `done` 的答案取自**图最终状态**，⛔ 不是攒流过的块：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "done", f"status 不对：{entries[1]}"
    assert cache_mod.INTERRUPTED_SUFFIX not in entries[1]["content"], (
        "正常跑完的答案**不许**带中断标记 —— 带了会让下一轮把好答案当'被截断'的"
    )


@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_cancelled_turn_is_persisted_with_cancelled_status(chain_key, monkeypatch):
    """客户端断开 ⇒ 存**客户端已经看到的那半截 + 标记**，`status == "cancelled"`。

    半截长度是 `[DISCONNECT_AFTER, DISCONNECT_AFTER + TOLERANCE]`（竞态，见 `_AsyncSpyStream`）
       ⇒ 断"至少含前 2 块" + "块数在上界内"，不钉死具体数字。
    """
    store = FakeRedis()
    graph = _ChainGraph(cap=40, items=_tokens("字"))
    resp = _call(chain_key, monkeypatch, graph, redis=store, question="问题一")
    _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"{CHAINS[chain_key]['route']} 取消后没成对留痕：{entries}"
    )
    body = entries[1]["content"]
    assert body.endswith(cache_mod.INTERRUPTED_SUFFIX), f"半截没带中断标记：{body!r}"
    head = body[:-len(cache_mod.INTERRUPTED_SUFFIX)] if body.endswith(cache_mod.INTERRUPTED_SUFFIX) else ""
    assert head == "字" * len(head) and DISCONNECT_AFTER <= len(head) <= DISCONNECT_AFTER + TOLERANCE, (
        f"存的不是客户端看到的那半截（应为 2–4 个'字'）：{head!r}"
    )
    assert entries[1]["status"] == "cancelled", f"status 不对：{entries[1]}"


@pytest.mark.parametrize("chain_key", ["A", "B", "C"])
def test_failed_turn_is_persisted_with_error_status(chain_key, monkeypatch):
    """ 判据①：**异常**出口也留痕 —— 改前三条链一个钩子都没接（"丢"）。

    异常出**没有图的最终状态可查**（图正跑到一半）⇒ 这里只能用 `collected`，
       与上面那条（`done` 取自最终态）**不矛盾**。
    """
    store = FakeRedis()
    graph = _ChainGraph(cap=10, items=_tokens("字"), explode_after=2)
    resp = _call(chain_key, monkeypatch, graph, redis=store, question="问题一")
    _drive_asgi_until_disconnect(resp, after_chunks=99)   # 跑到自然结束（错误帧收尾）

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"{CHAINS[chain_key]['route']} 异常后没成对留痕：{entries}"
    )
    assert entries[1]["content"] == "字" * 2 + cache_mod.INTERRUPTED_SUFFIX, (
        f"异常时该存'已吐出的那两块 + 标记'：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "error", f"status 不对：{entries[1]}"


def test_pending_approval_turn_writes_nothing(monkeypatch):
    """停在审批点 ⇒ **本轮一条都不写**（规则 2）。

    不 gate 就会把「等审批的半截」写成 `status="done"` —— 而 `summarize_agent_result`
       在审批点给的 `answer` 是**模型已写的那半句（非空）** ⇒ `persist_turn` 的
       "空答案跳过"那道闸**拦不住它** ⇒ 这正是本 DEC 的 status 字段要防的**假信号**。
    """
    store = FakeRedis()
    pending = {"messages": [AIMessage(content="我来查一下", tool_calls=[
        {"name": "get_weather", "args": {}, "id": "c1"}
    ])]}
    resp = _call("B", monkeypatch,
                 _ChainGraph(items=_tokens("我来查一下"), final_values=pending),
                 patch_pending=False, redis=store, question="问题一")
    objs = _frames_to_objs(_collect_frames(resp))

    assert objs[-2]["status"] == "pending_approval", f"前提没成立：{objs[-2]}"
    assert store.history("tester", THREAD) == [], (
        f"停在审批点却写了历史 ⇒ 那半步会被当成 `done` 的完整答案：{store.history('tester', THREAD)}"
    )


# ==================== 链 A 的两条独有红线 ====================


def test_chain_a_filters_out_supervisor_and_calc_execute(monkeypatch):
    """**链 A 独有**：`supervisor` 的**路由词**与 `calc_execute` 的**表达式**都**不许漏**。

    这一条**不能**用"节点不产生块"来理解 —— 它们**真的会进到流里**：
      · `supervisor`（`agl:353`）调 LLM 输出路由词（`SEARCH` / `CALCULATOR` / …）；
      · `calc_execute`（`agl:164`）调 LLM **只提取表达式**（`6*7`），真答案 `42` 来自
        `calculator` **工具**（`agl:166`）。
    ⇒ 靠 `STREAMABLE_NODES` **白名单**挡掉。本用例直接喂这两种块，断言**一帧都不出**。
    """
    items = _tokens("计算器", "SEARCH", "6*7", "最终", "答案",
                    nodes=("supervisor", "calc_execute", "supervisor",
                           "chat", "chat"))
    graph = _ChainGraph(items=[(("ns",), it) for it in items])
    resp = _call("A", monkeypatch, graph)
    objs = _frames_to_objs(_collect_frames(resp))

    contents = _contents(objs)
    assert "".join(contents) == "最终答案", (
        f"路由词 / 表达式漏进流里了（用户会在答案前面先看到一个 `SEARCH`）：{contents}"
    )
    assert not any("SEARCH" in c or "6*7" in c for c in contents), f"白名单没挡住：{contents}"


def test_chain_a_whitelist_is_the_graph_module_s_own(monkeypatch):
    """白名单**只能有一个来源**（的教训）—— 端点不许自己抄一份字面量。

    抄一份就是**静默漂移**：改图的人加了节点，端点那份不知道 ⇒ 新节点**永远流不出来**，
       而**没有任何报错**（正是  记的病根）。
    """
    import routing.api_v1_agent as api_v1_agent
    src = textwrap.dedent(inspect.getsource(api_v1_agent))
    tree = ast.parse(src)

    # 端点里对白名单的唯一用法必须是 `…STREAMABLE_NODES` 属性访问
    used = [n for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "STREAMABLE_NODES"]
    assert used, "端点里找不到任何 `…STREAMABLE_NODES` 引用 ⇒ 白名单从哪来的？"
    # 链 ①（`/agent/langgraph_chat`，之前就有）走的是**裸名**
    #    `from agent_graph import agent_graph, STREAMABLE_NODES` ⇒ 它**不**出现在这里
    #    （它是 `ast.Name` 不是 `ast.Attribute`）。别据此把那条也算进来。
    qualifiers = {n.value.id for n in used if isinstance(n.value, ast.Name)}
    assert qualifiers == {
        "agent_checkpointer", "agent_graph_advanced", "agent_graph_advanced_learning",
    }, f"三条新链的白名单不是从**各自的图模块**取的：{qualifiers}"

    # 且白名单本身的定义**只在图模块里**，端点不定义
    defined_here = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", None) == "STREAMABLE_NODES" for t in n.targets)]
    assert not defined_here, "端点在**自己**定义 STREAMABLE_NODES ⇒ 又变成两处口径了"


# ==================== 白名单 × 图 对账（评审收口 · ）====================

#: (模块名, builder 名) —— 用字符串走 `importlib`（`agent_graph` 未在本文件 import）
_STREAMABLE_WHITELISTS = [
    ("agent.agent_graph", "build_agent_graph"),
    ("agent.agent_checkpointer", "build_checkpointer_agent"),
    ("agent.agent_graph_advanced", "build_mcp_agent"),
    ("agent.agent_graph_advanced_learning", "build_advanced_agent"),
]


@pytest.mark.parametrize("module_name,builder_name", _STREAMABLE_WHITELISTS)
def test_every_streamable_node_name_exists_in_its_graph(module_name, builder_name):
    """白名单里的**每个名字**都必须真的在它那张图里。

    **`xray=1` 是必须的**：链 A 的 5 个部门全是**子图**，不开 xray 只能看到 `react_dept`
       这层壳，看不到里面的 `agent` / `search_summarize` / `translate_execute`。
    按 `split(":")[-1]` 比**后缀** —— 与 `meta["langgraph_node"]` 报的名字**同口径**
       （它报的是**子图内层**名，见 `app/routing/sse.py:145-152`）。
    """
    module = importlib.import_module(module_name)
    graph = getattr(module, builder_name)()

    node_ids = set(graph.get_graph(xray=1).nodes)
    suffixes = {nid.split(":")[-1] for nid in node_ids}

    missing = set(module.STREAMABLE_NODES) - suffixes
    assert not missing, (
        f"{module_name}.STREAMABLE_NODES 里有名字【不在图中】⇒ 那段 token 会被静默丢掉：{sorted(missing)}\n"
        f"图中实际的节点名（后缀口径）：{sorted(suffixes)}"
    )


# ==================== 链 B 独有：审批登记不能被流式带坏 ====================


def test_chain_b_registers_pending_approval_with_graph_name(monkeypatch):
    """**B5 不能被流式带坏**：停在审批点时**待接管队列必须照样登记**，且**写明图名**。

    不登记 = `/agent/pending` 里查不到 ⇒ 没人知道有个会话卡住了（`MemorySaver` 没有
       "列出全部 thread" 的 API，这个登记是**唯一**入口）。
    图名不写 ⇒ `/agent/approve` 不知道该续跑哪张图 ⇒ 这个会话**永远放行不了**
       （比不加门还糟：门关了却没有钥匙 ——  丙段）。
    """
    registered, resolved = [], []
    monkeypatch.setattr(m, "register", lambda *a, **k: registered.append((a, k)))
    monkeypatch.setattr(m, "resolve", lambda *a, **k: resolved.append(a))

    pending = {"messages": [AIMessage(content="", tool_calls=[
        {"name": "web_search", "args": {"query": "x"}, "id": "c1", "type": "tool_call"},
    ])]}
    resp = _call("B", monkeypatch, _ChainGraph(items=_tokens("我来查一下"), final_values=pending),
                 patch_pending=False)
    objs = _frames_to_objs(_collect_frames(resp))

    assert registered, "停在审批点却没登记 ⇒ `/agent/pending` 里找不到它（硬门 D 判据③落空）"
    _, kwargs = registered[0]
    assert kwargs.get("graph") == "checkpointer_agent", (
        f"登记没写明图名 ⇒ `/agent/approve` 会去问另一张图：{kwargs}"
    )
    assert kwargs.get("raw_thread_id") == THREAD
    assert resolved == [], "在等审批却把队列清掉了"

    summary = objs[-2]
    assert summary["status"] == "pending_approval", (
        f"汇总帧没说清在等审批 ⇒ 前端只会看到一个戛然而止的半截答案：{summary}"
    )
    assert summary["thread_id"] == THREAD, "回显的应是**原值** thread_id（⛔ 不是拼过身份的键）"


# ==================== 链 D（plan_execute）：不是图，形态与 A/B/C 不同 ====================


class _LazyResponse:
    """把「`await` 端点函数」推迟到 ASGI/收帧的**那个**事件循环里执行。"""

    def __init__(self, factory):
        self._factory = factory

    async def _make(self):
        return await self._factory()

    async def __call__(self, scope, receive, send):
        resp = await self._make()
        await resp(scope, receive, send)


def _plan_factory(monkeypatch, *, plan_task=None, execute_plan=None, redis=None, **kw):
    """装好替身，返回一个「在跑它的那个 loop 里建响应」的工厂。"""
    monkeypatch.setattr(m, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "circuit", lambda *a, **k: (True, ""))
    #：链 D 也留痕（`on_complete` 走 `done`、`bridge.fail` 走 `error`）⇒ 短路存储。
    monkeypatch.setattr(cache_mod, "redis_client", redis if redis is not None else FakeRedis())
    if plan_task is not None:
        monkeypatch.setattr(m, "plan_task", plan_task)
    if execute_plan is not None:
        monkeypatch.setattr(m, "execute_plan", execute_plan)
    kw.setdefault("goal", "帮我算 6*7")
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("user_name", "tester")

    async def factory():
        return await m.agent_plan_execute_stream(**kw)

    return factory


def _collect_plan_frames(factory):
    async def go():
        resp = await factory()
        return [p.decode() if isinstance(p, (bytes, bytearray)) else p
                async for p in resp.body_iterator]
    return asyncio.run(go())


def test_plan_execute_streams_the_planning_segment(monkeypatch):
    """链 D：**规划段的每个 token 一帧** —— 这是「只流规划段」那条裁定的落点。

    流出去的是**正在生成的 JSON 片段**（`plan_task` 的提示词要求严格 JSON），
       **不是人读终稿**（作者 裁定"甲"）。本用例不断言 JSON 完整，
       只断言**逐块到达**且**顺序拼得回去**。
    """
    def fake_plan(goal, user_name="unknown", on_token=None):
        for t in ["[{", '"step"', ": 1}]"]:
            if on_token:
                on_token(t)
        return [{"step": 1, "action": "算", "tool": "calculator", "input": "6*7"}]

    factory = _plan_factory(monkeypatch, plan_task=fake_plan,
                            execute_plan=lambda plan, goal, user: "42")

    async def _check_headers():
        resp = await factory()
        return resp.media_type, resp.headers.get("X-Accel-Buffering")

    assert asyncio.run(_check_headers()) == ("text/event-stream", "no")

    objs = _frames_to_objs(_collect_plan_frames(factory))
    contents = _contents(objs)
    assert len(contents) >= 3, f"只发了 {len(contents)} 帧 ⇒ 规划段没有真流式：{objs}"
    assert "".join(contents) == '[{"step": 1}]', f"拼不回规划段原文：{contents}"
    assert objs[-1] == "__DONE__"


def test_plan_execute_summary_carries_plan_and_execution_result(monkeypatch):
    """汇总帧要带**规划 + 执行**两段结果 —— 执行段**不流**，只能在这里看到它。

    这正是"规划段之后是一长段静默"的另一面：用户等得久，但**末帧一定是完整的**。
    """
    plan = [{"step": 1, "action": "算", "tool": "calculator", "input": "6*7"}]

    def fake_plan(goal, user_name="unknown", on_token=None):
        if on_token:
            on_token("规划中")
        return plan

    factory = _plan_factory(monkeypatch, plan_task=fake_plan,
                            execute_plan=lambda p, g, u: "结果是 42")
    objs = _frames_to_objs(_collect_plan_frames(factory))
    summary = objs[-2]

    assert summary["plan"] == plan, f"汇总帧没带规划：{summary}"
    assert summary["execution_result"] == "结果是 42", f"汇总帧没带执行结果：{summary}"
    assert summary["goal"] == "帮我算 6*7"
    assert summary["thread_id"] == THREAD
    # 链 D 的 `requested_by` 是**原计划 §八 明文承诺过**的
    #    （末帧 = `{goal, plan, execution_result, requested_by}`）——
    #    承诺过、实现当时没落地，评审收口补上。
    assert summary["requested_by"] == "tester", (
        f"链 D 汇总帧没带 requested_by（原计划 §八 承诺过它有）：{summary}"
    )


def test_plan_execute_stream_survives_budget_exceeded_midway(monkeypatch):
    """流式带来的**固有代价**：预算耗尽从 4xx 变成 **error 帧**（不是 500）。

    非流式那条会把 `BudgetExceededError` 转成 `QUOTA_EXCEEDED`（4xx）。这里响应头**已经发出去了**
    （HTTP 200 + `text/event-stream`），**改不了状态码** ⇒ 只能发一帧 `{"error": …}`。
    ⇒ 这正是 B8 / B11 两道闸必须放在**进生成器之前**的原因：它们能拦的那部分是干净 4xx。
    """
    from agent.plan_execute import BudgetExceededError

    def fake_plan(goal, user_name="unknown", on_token=None):
        if on_token:
            on_token("开始规划")
        raise BudgetExceededError("今日额度用完")

    factory = _plan_factory(monkeypatch, plan_task=fake_plan,
                            execute_plan=lambda p, g, u: "不该跑到这里")
    objs = _frames_to_objs(_collect_plan_frames(factory))

    errors = [o for o in objs if isinstance(o, dict) and o.get("error")]
    assert errors, f"预算耗尽没有变成 error 帧（前端会看到流戛然而止）：{objs}"
    assert "额度用完" in errors[0]["error"]
    assert "开始规划" in _contents(objs), "已经流出去的块不该被吞掉"


def test_plan_execute_stream_closes_bridge_on_disconnect(monkeypatch):
    """取消：客户端断开 ⇒ 桥要**被关**（后台任务取消）、**计数 +1**、日志有 `[cancel]`。

    **规划线程本身停不下来**（Python 线程杀不掉）—— 桥能做到的只是"不再发"。
       这条限制写在 `_ThreadTokenBridge` 的 docstring 里，别读成"线程也停了"。
    假 `plan_task` **必须一块一块地吐、块间有等待**：桥没有节流 ⇒ 一口气把 200 块灌进队列时，
       消费侧**一次都不会挂起**（`Queue.get` 在队列非空时不 await），断开就送不进生成器
       本用例会**假绿**（真服务的 token 本来就是陆续到的，所以这样才同构）。
    """
    import time

    def paced_plan(goal, user_name="unknown", on_token=None):
        for i in range(100):
            if on_token:
                on_token(f"块{i}")
            time.sleep(0.005)
        return []

    spec = CHAINS["D"]
    factory = _plan_factory(monkeypatch, plan_task=paced_plan,
                            execute_plan=lambda p, g, u: "")
    before = _metric(spec["endpoint"])
    with _capture_logs() as lines:
        sent = _drive_asgi_until_disconnect(_LazyResponse(factory),
                                            after_chunks=DISCONNECT_AFTER)

    bodies = _body_chunks(sent)
    assert len(bodies) >= DISCONNECT_AFTER, f"根本没流出帧（{len(bodies)} 帧）⇒ 本断言无意义"
    assert _metric(spec["endpoint"]) == before + 1, "取消没有计数"
    assert any("断开" in line or "cancel" in line.lower() for line in lines), (
        f"日志里没有取消事件：{lines}"
    )
    assert not any(b"[DONE]" in b for b in bodies), f"取消后还发帧了：{bodies}"


def test_plan_execute_persists_the_turn_on_completion(monkeypatch):
    """ · 链 D 正常跑完 ⇒ 成对写历史，答案取**执行结果**（不是规划段的 JSON 片段）。"""
    store = FakeRedis()

    def fake_plan(goal, user_name="unknown", on_token=None):
        if on_token:
            on_token("规划中")
        return [{"step": 1, "action": "算", "tool": "calculator", "input": "6*7"}]

    factory = _plan_factory(monkeypatch, plan_task=fake_plan,
                            execute_plan=lambda p, g, u: "结果是 42", redis=store)
    _collect_plan_frames(factory)

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"链 D 正常跑完却没成对留痕：{entries}"
    )
    assert entries[0]["content"] == "帮我算 6*7", f"提问没留下：{entries[0]}"
    assert entries[1]["content"] == "结果是 42", (
        f"该存**执行结果**，⛔ 不是流出去的规划段 JSON 片段：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "done", f"status 不对：{entries[1]}"


def test_plan_execute_persists_the_turn_on_budget_exceeded(monkeypatch):
    """：链 D 的**异常**出口（预算中途触顶）也留痕 ⇒ 半截 + 标记 + `error`。"""
    from agent.plan_execute import BudgetExceededError

    store = FakeRedis()

    def fake_plan(goal, user_name="unknown", on_token=None):
        if on_token:
            on_token("开始规划")
        raise BudgetExceededError("今日额度用完")

    factory = _plan_factory(monkeypatch, plan_task=fake_plan,
                            execute_plan=lambda p, g, u: "不该跑到这里", redis=store)
    _collect_plan_frames(factory)

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"链 D 异常后没成对留痕：{entries}"
    )
    assert entries[0]["content"] == "帮我算 6*7", f"提问没留下：{entries[0]}"
    assert entries[1]["content"] == "开始规划" + cache_mod.INTERRUPTED_SUFFIX, (
        f"异常时该存'已流出的那截 + 标记'：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "error", f"status 不对：{entries[1]}"


def test_plan_execute_persists_the_turn_on_cancel(monkeypatch):
    """ · 链 D 取消 ⇒ 半截 + 标记 + `cancelled`。

    留痕里是**半截 JSON**（`{"step"` 这种）—— 链 D 的流本就不是人读终稿
       （`api_v1_agent.py` 明写"不是人读终稿"）⇒ **照实存**，不许为了好看去 `json.loads`。
    """
    import time

    store = FakeRedis()

    def paced_plan(goal, user_name="unknown", on_token=None):
        for i in range(100):
            if on_token:
                on_token(f"块{i}")
            time.sleep(0.005)
        return []

    factory = _plan_factory(monkeypatch, plan_task=paced_plan,
                            execute_plan=lambda p, g, u: "", redis=store)
    _drive_asgi_until_disconnect(_LazyResponse(factory), after_chunks=DISCONNECT_AFTER)

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"链 D 取消后没成对留痕：{entries}"
    )
    assert entries[0]["content"] == "帮我算 6*7", f"提问没留下：{entries[0]}"
    body = entries[1]["content"]
    assert body.endswith(cache_mod.INTERRUPTED_SUFFIX), f"半截没带中断标记：{body!r}"
    assert body.startswith("块0"), f"存的不是客户端看到的那半截：{body!r}"
    assert entries[1]["status"] == "cancelled", f"status 不对：{entries[1]}"


def test_plan_execute_stream_frames_match_the_other_three_chains(monkeypatch):
    """**不另发明一种「JSON 流」格式** —— 帧格式与 A/B/C 三条**同一套**。

    ⇒ 判据：每一帧都是 `data: ` 前缀；内容帧是 `{"content": …}`；末帧是 `data: [DONE]`。
      （作者 的裁定正是"照原样流 JSON"，指的是**内容**是 JSON 片段，
       **不是**帧格式要变成 JSON 流事件。）
    """
    def fake_plan(goal, user_name="unknown", on_token=None):
        if on_token:
            on_token('{"step"')
        return []

    factory = _plan_factory(monkeypatch, plan_task=fake_plan,
                            execute_plan=lambda p, g, u: "")
    frames = _collect_plan_frames(factory)

    assert all(f.startswith("data: ") for f in frames), f"有帧不是 SSE 格式：{frames}"
    assert frames[-1].strip() == "data: [DONE]"
    assert json.loads(frames[0][len("data: "):]) == {"content": '{"step"'}, (
        f"内容帧的形状与另外三条不一致：{frames[0]!r}"
    )


# ==================== 路由注册（4 条，一次问清） ====================


def test_all_four_stream_routes_are_registered():
    """路由真的挂上去了（别写成函数却忘了 `@router.post`）。"""
    paths = {r.path for r in m.router.routes}
    missing = [spec["route"] for spec in CHAINS.values()
               if f"/api/v1{spec['route']}" not in paths]
    assert not missing, f"这些流式路由没注册：{missing}。现有：{sorted(p for p in paths if 'stream' in p)}"


def test_all_four_stream_routes_keep_both_gates():
    """**两条前置闸必须同源保留**（B8 会话级 + B11 全站日级），且都在**进生成器之前**。

    少了它们 ⇒ 触顶会变成"HTTP 200 + 流到一半断掉"，调用方**看不出是被限额拒了**。
    判据用 **AST 的位置**（闸的调用行号 < `sse_stream(` 的行号），不用 grep ——
       "出现在函数里"与"出现在流开始之前"是两件事。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(m)))
    for chain_key, spec in CHAINS.items():
        fn = _find_node(tree, spec["func"])
        lines = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                lines.setdefault(name, node.lineno)
        assert "check_session_token_budget" in lines, f"链 {chain_key} 没有 B8 会话级闸"
        assert "circuit" in lines, f"链 {chain_key} 没有 B11 全站日级闸"
        assert "sse_stream" in lines, f"链 {chain_key} 没调共享层"
        assert lines["check_session_token_budget"] < lines["sse_stream"], (
            f"链 {chain_key}：B8 闸排在 `sse_stream()` **之后** ⇒ 触顶会变成流到一半断掉"
        )
        assert lines["circuit"] < lines["sse_stream"], f"链 {chain_key}：B11 闸排晚了"


# ==================== 真节点出块（不是"路由在"就算数） ====================


def _run_graph_stream(graph, payload, config, **kw):
    async def go():
        out = []
        async for item in graph.astream(payload, config=config, stream_mode="messages", **kw):
            out.append(item)
        return out
    return asyncio.run(go())


class _UsageCarryingModel(_FakeStreamingModel):
    """在**最后一块**（`content=""`）上带 `usage_metadata` —— 照抄真 provider 的形态。

    为什么必须这样喂：链 C 的节点写的是
       `if hasattr(response, "usage_metadata"): usage = response.usage_metadata` 然后
       **直接** `usage.get(...)`。`AIMessageChunk` 天然**有** `usage_metadata` 属性（默认 `None`）
       ⇒ 聚合结果没带用量时，那里会 `AttributeError: 'NoneType' object has no attribute 'get'`。
    **那是既有写法，本轮不动**（改了就是行为变更）；真 provider 会把 usage 挂在最后一块上，
       所以这里只是**同构**。⇒ 这条限制也解释了为什么上面那些端点用例走**假图**（绕开节点）。
    """

    def _stream(self, messages, stop=None, run_manager=None, **kw):
        yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kw)
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="",
            usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        ))


def test_real_chain_b_node_streams_one_chunk_per_token(monkeypatch):
    """**链 B 的真节点**（不是假图）—— 6 个 token ⇒ **6 块**。

    上面那些端点用例走的是假图 ⇒ 只证明**接线**对。真节点会不会真流，得拿真图跑
       （`test_agent_sse.py` 对 `agent_graph` 也是这么做的）。
    """
    monkeypatch.setattr(ac, "llm_with_tools", _bind(_FakeStreamingModel()))
    graph = ac.build_checkpointer_agent("memory")

    chunks = _run_graph_stream(
        graph, {"messages": [m.HumanMessage(content="你好")]},
        {"configurable": {"thread_id": "t-real-b"}},
    )
    texts = [c.content for c, _ in chunks if getattr(c, "content", "")]
    assert len(texts) > 1, f"只出了 {len(texts)} 块 ⇒ 链 B 的节点没在真流式：{chunks}"
    assert "".join(texts) == "你好，这是最终答案。"
    assert {meta["langgraph_node"] for _, meta in chunks} == {"agent"}
    assert ac.STREAMABLE_NODES >= {"agent"}, "白名单与节点名漂了 ⇒ 一块都过滤不出来"


def test_real_chain_a_node_streams_one_chunk_per_token(monkeypatch):
    """**链 A 的真节点**（`translate_execute`，单节点子图 ⇒ 建得起来、跑得快）。

    选翻译子图而不是搜索子图：搜索子图入口是 `search_execute`（**真联网**），
       而翻译子图入口**就是那个 LLM 节点** —— 只打假模型，不碰网络。
    """
    monkeypatch.setattr(agl, "llm", _bind(_FakeStreamingModel()))
    graph = agl.create_translate_subgraph()

    chunks = _run_graph_stream(
        graph, {"messages": [m.HumanMessage(content="你好")]},
        {"configurable": {"thread_id": "t-real-a"}},
    )
    texts = [c.content for c, _ in chunks if getattr(c, "content", "")]
    assert len(texts) > 1, f"只出了 {len(texts)} 块 ⇒ 链 A 的节点没在真流式：{chunks}"
    assert "".join(texts) == "你好，这是最终答案。"
    assert "translate_execute" in agl.STREAMABLE_NODES, (
        "翻译节点不在白名单里 ⇒ 端点会把它的译文**全部丢掉**（用户一个字都看不到）"
    )


def test_real_chain_c_nodes_stream_one_chunk_per_token(monkeypatch):
    """**链 C 的真节点** —— 这张图唯二的两个节点都是 `async`，走 `astream`。

    一条 `agent → chat`：`agent_decide` 先出块，`should_continue` 判无工具 ⇒ 走 `chat_node`
       再出块 ⇒ **两块都必须是逐 token 的**（`{"agent", "chat"}` 都在白名单里）。
    """
    async def _fake_get_llm_with_mcp_tools():
        return _bind(_UsageCarryingModel())

    monkeypatch.setattr(aga, "get_llm_with_mcp_tools", _fake_get_llm_with_mcp_tools)
    # 两个节点取模型的地方**不一样**：`agent_decide` 走 `get_llm_with_mcp_tools`（上面那个），
    #    `chat_node` 用**模块全局** `llm` ⇒ 两个都要换，否则第二个节点会去打真 API。
    monkeypatch.setattr(aga, "llm", _bind(_UsageCarryingModel()))
    monkeypatch.setattr(aga, "inject_memories_to_prompt", lambda prompt, state: prompt)
    monkeypatch.setattr(aga, "check_token_budget", lambda *a, **k: True)
    monkeypatch.setattr(aga, "record_usage", lambda **k: None)
    monkeypatch.setattr(aga, "record_agent_decision", lambda *a, **k: None)
    graph = aga.build_mcp_agent()

    chunks = _run_graph_stream(
        graph,
        {"messages": [m.HumanMessage(content="你好")], "user_name": "tester",
         "thread_id": "t-real-c", "memory_space": "default"},
        {"configurable": {"thread_id": "t-real-c"}},
    )
    texts = [c.content for c, _ in chunks if getattr(c, "content", "")]
    nodes = {meta["langgraph_node"] for _, meta in chunks}

    assert len(texts) > 1, f"只出了 {len(texts)} 块 ⇒ 链 C 的节点没在真流式：{chunks}"
    assert nodes == {"agent", "chat"}, f"出块的节点不是这两个：{nodes}"
    assert aga.STREAMABLE_NODES >= nodes, (
        f"白名单盖不住实际出块的节点 ⇒ 有一段答案会被丢掉：{nodes} - {set(aga.STREAMABLE_NODES)}"
    )


def test_real_chain_c_aggregates_fragmented_tool_calls(monkeypatch):
    """**链 C 的真节点**：碎片化的 `tool_calls` 必须被 `astream` 侧的 `+` 聚合还原。

    **为什么链 A / B 有守卫、链 C 也要有**：`+` 聚合是**保住 `tool_calls` 的唯一手段**。
       `app/agent/agent_graph_advanced.py` 自己写着后果 ——
       「工具**永远不会被执行**，而接口一切正常」（`:373-375`）。
       B0（`app/tests/test_agent_sse.py`）与 B（`app/tests/test_memory_chat_approval.py`）都有守卫，
       **A 与 C 此前没有**（A 的那条已补在 `app/tests/test_agent_repairs.py`）。
       代码本身是**对的**（全仓 7 个聚合点都写的是 `+` 全块聚合）⇒ 这是**测试债，不是现存缺陷**。

    走**真节点、真图**（不是假图）：`agent_decide` 判出 `tool_calls` ⇒ 路由去 `tools`
       ⇒ 回到 `agent` 拿终稿。**聚合一旦退化**（只留最后一块 / 只拼 `content`），
       `should_continue` 会判**没有** `tool_calls` ⇒ **直接跳去 `chat` 出最终答案**
       ⇒ 答案看着**完全正常**，而**工具一次都没跑**（本用例断言 `ToolMessage` 进没进 state）。
       判据**不能**是"接口返回正常" —— 那正是上面说的静默失效。

    **反证**（做过，非声称）：把本图两处聚合改成 `response = chunk` ⇒ **必红**
       （实测失败信息见本文件对应的提交）。
    """
    agent_model = _FragmentedThenAnswerModel()
    # `chat_node` 用的是**模块全局 `llm`**，与 `agent_decide` 那条路**不是同一个实例**
    #    ⇒ 它自己从第 1 次调用起就该吐终稿（`answer_from=1`），否则它会再吐一次 `tool_calls`。
    chat_model = _FragmentedThenAnswerModel(answer_from=1)

    async def _fake_get_llm_with_mcp_tools():
        # **必须每次返回同一个实例** —— 每次 `new` 会让 `calls` 从 0 重来
        #    ⇒ 第二轮又吐 `tool_calls` ⇒ `agent → tools → agent` **无限循环**。
        return _bind(agent_model)

    async def _fake_tool_execute(state):
        # 挡掉**真的**工具执行：真 `tool_execute` 会经 `call_mcp_tool` 起 MCP client
        #    子进程、真联网。本用例的判据是「**有没有路由到 `tools`**」，
        #    不是"工具抓没抓到网页"。
        return {"messages": [
            ToolMessage(content="stub", tool_call_id=tc["id"], name=tc["name"])
            for tc in state["messages"][-1].tool_calls
        ]}

    monkeypatch.setattr(aga, "get_llm_with_mcp_tools", _fake_get_llm_with_mcp_tools)
    monkeypatch.setattr(aga, "llm", _bind(chat_model))
    monkeypatch.setattr(aga, "inject_memories_to_prompt", lambda prompt, state: prompt)
    monkeypatch.setattr(aga, "check_token_budget", lambda *a, **k: True)
    monkeypatch.setattr(aga, "record_usage", lambda **k: None)
    monkeypatch.setattr(aga, "record_agent_decision", lambda *a, **k: None)
    monkeypatch.setattr(aga, "tool_execute", _fake_tool_execute)

    graph = aga.build_mcp_agent()

    async def _go():
        # **必须 `ainvoke`**（不是同步 `invoke`）—— 本图两个节点都是 `async def`。
        return await graph.ainvoke(
            {"messages": [m.HumanMessage(content="算一下 6*7")], "user_name": "tester",
             "thread_id": "t-frag-c", "memory_space": "default"},
            {"configurable": {"thread_id": "t-frag-c"}},
        )

    out = asyncio.run(_go())

    tool_msgs = [x for x in out["messages"] if isinstance(x, ToolMessage)]
    assert tool_msgs, (
        "聚合丢了碎片化的 tool_calls ⇒ should_continue 判不出 tools ⇒ 直接跳去 chat "
        f"出最终答案，而工具一次都没跑：{[type(x).__name__ for x in out['messages']]}"
    )
    # 下面两条**比"有 ToolMessage"更强**：`name` 与 `id` **都在【第一块碎片】上**，
    #    能对上 ⇒ 证明 `+` 真的**跨块合并**了，不是"某一块的 `tool_calls` 恰好还在"。
    assert tool_msgs[0].name == "calculator"
    assert tool_msgs[0].tool_call_id == "c1"
    assert out.get("final_output") == "答案来自工具后的总结。", (
        f"工具跑完后应回到 `agent` 拿终稿：{out.get('final_output')!r}"
    )


class _FragmentedThenAnswerModel(_FakeStreamingModel):
    """**异步**假模型：第 `answer_from` 次调用起吐终稿，之前吐**碎片化的 `tool_calls`**。

    碎片形态照抄真 provider（也与 `_FakeStreamingModel.with_tool` 一致）：
       `name` + `id` 在**第一块**，`args` 分两段续上，后两块的 `name` / `id` 都是 `None`。
       ⇒ **只有 `+` 聚合**才拼得出一个完整的 `tool_calls`（单看最后一块，`tool_calls` 是**空的**）。
    """

    tokens: list = []      # 不吐正文 token：本用例只看「有没有路由到 `tools`」
    calls: int = 0
    answer_from: int = 2

    def _stream(self, messages, stop=None, run_manager=None, **kw):
        self.calls += 1
        if self.calls < self.answer_from:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                {"name": "calculator", "args": "", "id": "c1", "index": 0},
            ]))
            for frag in ['{"expression"', ': "6*7"}']:
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                    {"name": None, "args": frag, "id": None, "index": 0},
                ]))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="答案来自工具后的总结。"))
        # **最后一块必须带 `usage_metadata`** —— 链 C 的两个节点写的是
        #    `if hasattr(response, "usage_metadata"): usage = response.usage_metadata`
        #    然后**直接** `usage.get(...)`；而 `AIMessageChunk` **天然有**该属性（默认 `None`）
        #    ⇒ 少了这块会 `AttributeError: 'NoneType' object has no attribute 'get'`。
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="",
            usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        ))
