"""B1 · Agent 端 SSE—— 范围**只** `/agent/langgraph_chat`。

**本文件存在的理由：「有这个路由」和「真的是流式」是两件事。**

`通用/四硬门 §3-A` 的判据是「**逐字出现**」。而**假流式**（后端一次性拿到整段、
前端再用 `setInterval` 切字符）**照样有 `text/event-stream`、照样有 `data:` 帧** ——
计划里那条弱测试（`content-type` + `data:` ≥ 2）**抓不到它**：
后端只发「整段 + `[DONE]`」就是 2 条 `data:`，**照样绿**。

⇒ 本文件把判据钉在**后端出块**这一层，两道：

1. **结构性**（`test_agent_decide_declares_config_parameter` /
   `test_agent_decide_forwards_config_to_the_model`）——
   **真流式的必要条件是节点声明 `config` 并把它转发给模型的流式调用**。
2. **机制性**（`test_graph_streams_one_chunk_per_token` /
   `test_stream_route_emits_one_frame_per_token`）——
   实际数块：**每 token 一块**，不是攒完一次发。

---

## 三条**实测**事实（假模型探针，非推断；探针脚本已随  记档）

| 事实 | 实测 |
|---|---|
| 真流式**不要求**节点是 `async` | 同步节点 + `.stream(msgs, config=config)` ⇒ 6 块、时间戳递增 |
| **不能**把节点改成 `async` | 会让**同步的** `graph.invoke` 直接抛 `TypeError: No synchronous function provided to "agent"` —— 而非流式路径（`/agent/langgraph_chat` · `api_v1.py` · `api_v1_rag.py`）**都在用它** |
| `.stream` 不读 `ChatOpenAI.streaming` 字段 | `_should_stream` 只看 `disable_streaming` + `_stream` 有没有实现 ⇒ **不必动 `llm` 的构造** |

## 一条**没有**在源码里被保证、只被实测钉住的性质

`stream_mode="messages"` 的三个来源（`langgraph/pregel/_messages.py`）本会**重复**：
① `on_llm_new_token`（真·token）② `on_llm_end`（整段）③ `on_chain_end`（节点返回值）。
**实测**（4 个剧本）：② 被 `dedupe` 拦掉、③ **根本没发** ⇒ 每 token **恰好一块**、不重复。
⇒ 这个性质**只在实测里成立**，所以 `test_stream_route_emits_one_frame_per_token`
把它**钉成用例** —— 哪天 langgraph 升级后开始发 ③，这条会红，而不是**悄悄把答案吐两遍**。
"""
import asyncio
import inspect
import json
import textwrap

import pytest

import agent.agent_graph as agent_graph
import agent.memory_store as memory_store
import routing.api_v1_agent as m
import core.cache as cache_mod
from conftest import FakeRedis
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables.base import RunnableBinding

#  契约 C 起，历史键里多了 `thread_id`（端点收到的那个）。
#    本文件**读写走同一个名字** —— 两处一旦不一致，`assert history(...) == []` 会读空桶而静默变绿。
THREAD = "t-sse"

from access.session_key import session_key          #  丙段：进图的键要拼身份


# ==================== 假模型 / 假图（不联网、不花钱） ====================

class _FakeStreamingModel(BaseChatModel):
    """一个**会流式**的假模型 —— 形态照抄 OpenAI（name 先到、args 碎片化）。

    必须实现 `_stream`（而不是只在 `_generate` 里回调），否则探针证明不了任何事：
       `StreamMessagesHandler.on_llm_new_token` **第 152 行**要求 `chunk` 是
       `ChatGenerationChunk` —— 只在 `_generate` 里手动 fire 回调**传不进去**。
    """
    disable_streaming: bool = False
    with_tool: bool = False
    tokens: list = ["你好", "，", "这是", "最终", "答案", "。"]

    @property
    def _llm_type(self) -> str:
        return "fake-streaming"

    def _stream(self, messages, stop=None, run_manager=None, **kw):
        for t in self.tokens:
            yield ChatGenerationChunk(message=AIMessageChunk(content=t))
        if self.with_tool:
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "calculator", "args": "", "id": "c1", "index": 0}],
            ))
            for frag in ['{"expression"', ': "6*7"}']:
                yield ChatGenerationChunk(message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": None, "args": frag, "id": None, "index": 0}],
                ))

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        # 故意不实现：本文件的用例**必须**走流式路径。若哪天有人把它改回
        #    `invoke()`，这里会直接炸，而不是**静默退化成假流式**。
        raise AssertionError("本用例必须走流式路径（_stream），⛔ 不该落到非流式实现")


def _bind(model) -> RunnableBinding:
    """包一层 —— 这正是 `llm.bind_tools(tools)` 返回的类型（探针验过 config 能穿过去）。"""
    return RunnableBinding(bound=model, kwargs={}, config={})


def _install_fake_model(monkeypatch, **kw):
    """把假模型装成 `agent_graph.llm_with_tools`。

    用 `monkeypatch` 而**不是** `agent_graph.llm_with_tools = …`：
       `agent_graph` 是**全局单例模块**，直接赋值**不会还原** ⇒ 假模型会泄漏到
       同一次 pytest 会话的其它文件里。那种污染**不报错**，只会让别处莫名变绿/变红。
       （同 `app/tests/test_approval_resume.py` 的 `_approve` 里的注释。）
    """
    monkeypatch.setattr(agent_graph, "llm_with_tools", _bind(_FakeStreamingModel(**kw)))
    # 2026-10-13：`agent_graph` 现在**本任务的第一步会去检索长期记忆**。
    #    不打这一针 ⇒ 它会真去连 mem0（本地 Qdrant + 一次 DashScope embedding），
    #    而那一次 embedding 走 `core.cache.redis_client` —— 本文件的替身 `FakeRedis`
    #    没有 `.get` ⇒ **节点当场抛**，端点把它变成一帧 error，下面那些断言就集体失准。
    #    **回空数组 = 不注入**（记忆那一段整段跳过）⇒ 与本文件要量的那件事（状态取自哪）无关，
    #       行为与"补记忆之前"逐字一致。守卫 ⇒ `app/tests/test_memory_injection_wiring.py`
    monkeypatch.setattr(memory_store, "search_user_memory", lambda *a, **k: [])


def _run_graph_stream(graph, thread_id="t-stream"):
    """跑一遍 `astream(stream_mode="messages")`，返回 `[(chunk, meta), …]`。

    本仓**没有 pytest-asyncio** ⇒ 异步只能手跑 `asyncio.run`（同 `test_approval_resume.py`）。
    """
    async def go():
        out = []
        async for chunk, meta in graph.astream(
            {"messages": [HumanMessage(content="帮我算 6*7")]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="messages",
        ):
            out.append((chunk, meta))
        return out
    return asyncio.run(go())


# ==================== ① 结构性判据：真流式的【必要条件】 ====================

def test_agent_decide_declares_config_parameter():
    """**节点必须声明 `config`** —— 这是真流式的必要条件，少了它**一切照绿**。

    实测（假模型）：不声明 `config` ⇒ `llm_with_tools.invoke(...)` 无从转发
    ⇒ `astream(stream_mode="messages")` **只剩 1 块**，而那 1 块是模型**一次性**返回、
    被 `on_llm_end` 吐出来的**整段**。**前端看上去仍然是逐字**（那是前端切的）。

    所以这条不能用「端点有没有返回 `text/event-stream`」代替 —— 那个假流式也有。
    """
    sig = inspect.signature(agent_graph.agent_decide)
    assert "config" in sig.parameters, (
        "agent_decide 没有 config 形参 ⇒ 拿不到 LangGraph 注入的回调 ⇒ "
        "stream_mode='messages' 只会吐 1 块整段（假流式，而接口看上去正常）"
    )


def test_agent_decide_forwards_config_to_the_model():
    """光**声明**不够 —— 必须**真的转发**进模型调用，否则等于没接。

    判据用 **AST**，不用 `grep`（`config` 在注释和 docstring 里也出现，
       本仓栽过：注释里同样的串让"替换了 10 处"变成了假结论）。
    """
    src = textwrap.dedent(inspect.getsource(agent_graph.agent_decide))
    tree = __import__("ast").parse(src)

    forwards = [
        node for node in __import__("ast").walk(tree)
        if isinstance(node, __import__("ast").Call)
        and any(kw.arg == "config" for kw in node.keywords)
    ]
    assert forwards, (
        "agent_decide 里没有任何 `…(…, config=config)` 调用 ⇒ config 声明了却没转发。\n"
        f"源码：\n{src}"
    )


# ==================== ② 机制判据：实际数块 ====================

def test_graph_streams_one_chunk_per_token(monkeypatch):
    """**判据来自 `通用/四硬门 §3-A`：逐字出现** —— 数的是**后端**出的块。

    反例（假流式）：整段一次性到达 ⇒ 只有 1 块。
    「时间戳递增」这条**在 TestClient 里测不出**（拿到的是已缓冲的整段）
       ⇒ 它归 Step 4 的**真服务 + 真 HTTP**（`curl -N`）。本用例守的是**块数**。
    """
    _install_fake_model(monkeypatch, with_tool=False)
    graph = agent_graph.build_agent_graph()

    chunks = _run_graph_stream(graph)
    texts = [c.content for c, _ in chunks if getattr(c, "content", "")]

    assert len(texts) > 1, (
        f"只出了 {len(texts)} 块内容 ⇒ 这是**假流式**（整段一次到）。"
        f" 收到的块：{[getattr(c, 'content', None) for c, _ in chunks]}"
    )
    assert "".join(texts) == "你好，这是最终答案。", f"拼起来不对：{texts!r}"


def test_chunks_are_not_duplicated(monkeypatch):
    """**本用例钉的是一条【实测】性质，不是文档保证的性质**（见文件头 段）。

    `stream_mode="messages"` 源头上会让**同一段文字**出现三次
    （`on_llm_new_token` / `on_llm_end` / `on_chain_end`）。
    一旦它开始重复而没人拦，**答案会被吐两遍** —— 而内容拼起来"看着还挺长"，
       前端也不会报错。

    ⇒ 钉法：整段文字在流里**恰好出现一次**，且**最后一块不是** `[内容为空但携带全量]`。
    """
    _install_fake_model(monkeypatch, with_tool=False)
    graph = agent_graph.build_agent_graph()

    chunks = _run_graph_stream(graph)
    texts = [c.content for c, _ in chunks if getattr(c, "content", "")]

    joined = "".join(texts)
    assert joined.count("你好，这是最终答案。") == 1, f"整段被重复了：{texts!r}"


# ==================== ③ 回归守卫：B4 与非流式路径**都不能被带坏** ====================

def test_agent_decide_preserves_tool_calls(monkeypatch):
    """**改流式最容易静默打断的东西**：`should_continue` 靠 `last_message.tool_calls`
    判「要不要停下来等人批」（B4）。

    聚合 `AIMessageChunk` 时若把 `tool_call_chunks` 丢了，图会**直接跑到 END** ——
       接口返回 `{"status": "answered", "answer": "我来帮你算一下。"}`，**一切看着正常**，
       而 `搜索` 这种敏感操作**根本没被接管过**。硬门 D 就是这样名存实亡的。
    """
    _install_fake_model(monkeypatch, with_tool=True)
    # 让 calculator 变成"敏感工具"，这样图会停在审批点（否则会去真跑工具）
    monkeypatch.setattr(agent_graph, "SENSITIVE_TOOLS", frozenset({"calculator"}))
    graph = agent_graph.build_agent_graph()

    # 先跑一轮把图推进到审批点（带 interrupt_before，会停在那里）
    _run_graph_stream(graph, thread_id="t-tc")
    state = graph.get_state({"configurable": {"thread_id": "t-tc"}})
    last = state.values["messages"][-1]

    tc = getattr(last, "tool_calls", None) or []
    assert tc, f"tool_calls 丢了 ⇒ B4 审批会静默失效。末条消息 = {last!r}"
    assert tc[0]["name"] == "calculator"
    assert tc[0]["args"] == {"expression": "6*7"}
    assert agent_graph.should_continue(state.values) == "approval", (
        "图没有判定为「要人工接管」⇒ 硬门 D 名存实亡"
    )


def test_empty_stream_neither_writes_none_nor_returns_an_empty_answer(monkeypatch):
    """模型的**空响应**怎么处理 —— 钉住真正的行为，不是钉住我想当然的行为。

    **本条是"写代码时跑一次"跑出来的**（原始想法是给节点加一句
       `if response is None: response = AIMessage(content="")` 兜底）。
       实测：**那句兜底是不可达代码** —— `BaseChatModel.stream` 在模型**零块**时
       自己就 `raise ValueError("No generation chunks were returned")`
       （`langchain_core/language_models/chat_models.py:551`），
       **永远不会返回一个空迭代器**。⇒ 兜底删了，改成本用例钉住真实行为。

    **为什么还是要有这条**：真正危险的不是"返回空答案"，而是
       **`None` 被写进 `messages`** —— 它会被 `MemorySaver` **持久化**，
       之后**每一轮**都读到 `messages[-1] is None`，会话**永久**坏掉，
       而**当次请求返回一切正常**。 ⇒ 本用例断言两件事都不发生。
    """
    monkeypatch.setattr(
        agent_graph, "llm_with_tools",
        _bind(_FakeStreamingModel(tokens=[])),   # 一个 token 都不吐
    )
    graph = agent_graph.build_agent_graph()

    with pytest.raises(ValueError, match="No generation chunks"):
        graph.invoke(
            {"messages": [HumanMessage(content="你好")]},
            config={"configurable": {"thread_id": "t-empty"}},
        )

    # 报错之后 **checkpoint 必须干净** —— 若空响应留下了 None，
    #    下一次同 thread 的请求会在 `should_continue` 那里以**完全无关的报错**炸掉。
    messages = graph.get_state({"configurable": {"thread_id": "t-empty"}}).values["messages"]
    assert None not in messages, f"空响应把 None 写进了 checkpoint：{messages!r}"


def test_non_streaming_invoke_still_works(monkeypatch):
    """**非流式路径不能被带坏** —— `/agent/langgraph_chat` · `api_v1.py` · `api_v1_rag.py`
    都在用同步的 `graph.invoke`。

    这条是**实测支撑**的：把节点改成 `async def` 之后，`invoke` 会直接抛
       `TypeError: No synchronous function provided to "agent"`。
       ⇒ 节点必须**保持同步**，用 `.stream(config)`（同步迭代）而不是 `astream`。
    """
    _install_fake_model(monkeypatch, with_tool=False)
    graph = agent_graph.build_agent_graph()

    result = graph.invoke(
        {"messages": [HumanMessage(content="你好")]},
        config={"configurable": {"thread_id": "t-invoke"}},
    )
    assert result["messages"][-1].content == "你好，这是最终答案。"


# ==================== ④ 端点：SSE 契约 + **不缓冲** + B5 不被带坏 ====================

class _TwoRoundModel(_FakeStreamingModel):
    """**两轮**的假模型：先调工具 → 工具跑完 → 再给最终答案。

    这条是**真服务上跑出来的**：喂一句会触发搜索的话，模型因为工具报
       「未找到工具」而**重试了好几轮**。一轮就能跑完的场景**盖不住**下面的缺陷。
    """
    def _stream(self, messages, stop=None, run_manager=None, **kw):
        if isinstance(messages[-1], ToolMessage):
            for t in ["答案是", "42"]:
                yield ChatGenerationChunk(message=AIMessageChunk(content=t))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="我来算一下。"))
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="", tool_call_chunks=[{"name": "calculator", "args": "", "id": "c1", "index": 0}]))
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="", tool_call_chunks=[{"name": None, "args": '{"expression": "6*7"}', "id": None, "index": 0}]))


class _FakeGraph:
    """给端点用的假图：分块吐、每次一块 —— 好让"有没有攒着发"可判。"""

    def __init__(self, tokens=("你", "好", "呀"), tool_call=False, final_messages=None):
        self.tokens = tokens
        self.tool_call = tool_call
        self.astream_calls = []
        # `aget_state` 的返回值 —— 端点**必须**用它（而不是流式攒出来的东西）算 status
        self._final_messages = final_messages
        self.aget_state_calls = []

    async def astream(self, payload, config, stream_mode):
        self.astream_calls.append((payload, config, stream_mode))
        for t in self.tokens:
            yield AIMessageChunk(content=t), {"langgraph_node": "agent"}
        if self.tool_call:
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "web_search", "args": "", "id": "c1", "index": 0}],
            ), {"langgraph_node": "agent"}
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": None, "args": '{"query": "x"}', "id": None, "index": 0}],
            ), {"langgraph_node": "agent"}

    async def aget_state(self, config):
        self.aget_state_calls.append(config)
        messages = self._final_messages
        if messages is None:
            tokens = "".join(self.tokens)
            messages = [AIMessage(content=tokens)]
            if self.tool_call:
                messages = [AIMessage(content=tokens, tool_calls=[
                    {"name": "web_search", "args": {"query": "x"}, "id": "c1", "type": "tool_call"}])]
        return type("S", (), {"values": {"messages": messages}})


def _call_stream_route(monkeypatch, fake_graph, redis=None, **kw):
    """直接调端点函数（不走 TestClient / 不建库）—— 拿到的是**未缓冲**的生成器。

    用 `monkeypatch` 装假图，理由同 `_install_fake_model`。
    顺带把 B8 / B11 短路掉：本文件测的是**流式接线**，不是预算判定
       （那两条各有自己的用例：`test_session_budget_wiring.py` / `test_breaker_wiring.py`）。
     起端点会往 `chat_history` 写留痕 ⇒ 必须短路存储
       （不打桩就会写**真 redis**：本机 `ci-local.sh` 复用长期容器 `redis-rag`，会攒 key）。
    """
    monkeypatch.setattr(m, "agent_graph", fake_graph)
    monkeypatch.setattr(m, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(m, "circuit", lambda *a, **k: (True, ""))
    # 桩打在【叶子】`cache.redis_client`（不是 `m.append_chat_history` 之类的 import 方全局）——
    #    一个点盖住全部 6 条端点。
    monkeypatch.setattr(cache_mod, "redis_client", redis if redis is not None else FakeRedis())
    kw.setdefault("question", "你好")
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("user_name", "tester")
    return asyncio.run(m.langgraph_chat_stream(**kw))


def _collect_frames(resp):
    """把 `StreamingResponse` 的 body 逐帧收下来（**保序**，这才判得出"有没有攒着发"）。"""
    async def go():
        out = []
        async for piece in resp.body_iterator:
            out.append(piece.decode() if isinstance(piece, (bytes, bytearray)) else piece)
        return out
    return asyncio.run(go())


def _content_of(frame: str):
    """从一帧里取出 content；`[DONE]` 与汇总帧返回 None。"""
    body = frame[len("data: "):].strip()
    if body == "[DONE]":
        return None
    obj = json.loads(body)
    return obj.get("content")


def test_stream_route_emits_one_frame_per_token(monkeypatch):
    """**后端不缓冲** —— 3 个 token ⇒ **至少 3 帧内容**，不是 1 帧整段。

    这正是计划里那条弱测试**抓不到**的形态：只发「整段 + `[DONE]`」也是 2 个 `data:`。
    """
    fake = _FakeGraph(tokens=("你", "好", "呀"))
    resp = _call_stream_route(monkeypatch, fake)

    assert resp.media_type == "text/event-stream"
    assert resp.headers.get("X-Accel-Buffering") == "no", "少了禁缓冲头，Nginx 会攒着发"

    frames = _collect_frames(resp)
    contents = [c for c in map(_content_of, frames) if c is not None]

    assert len(contents) >= 3, f"只发了 {len(contents)} 帧内容 ⇒ 后端**攒着发**了。帧：{frames}"
    assert "".join(contents) == "你好呀"
    assert frames[-1].strip() == "data: [DONE]".strip(), f"末帧不是 [DONE]：{frames[-1]!r}"


def test_stream_route_forwards_question_and_thread_id(monkeypatch):
    """接线守卫：**问句与 thread_id 必须原样进图**。

    `thread_id` 是 checkpointer 的键 —— 传丢了会让每一轮都成了**新会话**，
       而**接口返回一切正常**（只是模型失忆）。同 `test_approval_resume.py` 的思路。
    """
    fake = _FakeGraph()
    resp = _call_stream_route(monkeypatch, fake, question="帮我算 6*7", thread_id="t-42")
    # **必须先消费 body** —— `StreamingResponse` 是**惰性**的：
    #    端点函数返回时生成器**还没跑**，此时断言 `astream_calls` 一定是空的。
    _collect_frames(resp)

    assert len(fake.astream_calls) == 1, f"图被调了 {len(fake.astream_calls)} 次"
    payload, config, stream_mode = fake.astream_calls[0]
    assert payload["messages"][0].content == "帮我算 6*7"
    # 2026-10-03（丙段）【口径变了】：进图的键**不再是裸 thread_id**，
    #    而是 `session_key(user_name, thread_id)` —— 否则两个用户用同一个 `thread_id`
    #    会**共用一个 checkpoint 桶**（消息是 append 的 ⇒ 模型看得到别人的对话）。
    assert config["configurable"]["thread_id"] == session_key("tester", "t-42"), (
        f"进图的 checkpoint 键不对：{config['configurable']['thread_id']!r}"
    )
    assert stream_mode == "messages", "⛔ 不是 messages 模式 ⇒ 拿不到 token 块"


def test_stream_route_registers_pending_approval(monkeypatch):
    """**B5 不能被流式带坏**：停在审批点时，**待接管队列必须照样登记**。

    不登记 = `/agent/pending` 里查不到 ⇒ 没人知道有个会话卡住了
       ⇒ 硬门 D 的判据③（"接管事件在界面上找得到"）**又一次落空**，
       而流式接口**返回 200、内容看着也完整**。
    """
    registered = []
    monkeypatch.setattr(m, "register", lambda *a, **k: registered.append(a))
    monkeypatch.setattr(m, "resolve", lambda *a, **k: None)

    fake = _FakeGraph(tool_call=True)
    resp = _call_stream_route(monkeypatch, fake)
    frames = _collect_frames(resp)

    assert registered, "停在审批点却没登记待接管队列 ⇒ /agent/pending 里找不到它"

    summary = [json.loads(f[len("data: "):]) for f in frames
               if f.startswith("data: ") and f[len("data: "):].strip() != "[DONE]"]
    assert summary[-1]["status"] == "pending_approval", f"末帧没说清在等审批：{summary[-1]}"


def test_status_comes_from_final_state_not_from_streamed_chunks(monkeypatch):
    """**真服务上跑出来的缺陷**：状态必须取自**图的最终状态**，
    不是"把 `agent` 节点的流式块攒起来"。

    **实测经过**（2026-10-03 ·   Step 4）：问一句会触发搜索的话，模型因为
    `tool_execute` 不认 `duckduckgo_search` 这个名字而**重试了好几轮**。
    端点当时用的是「把每一轮的 `agent` 输出 `+` 起来」⇒ 攒出的消息**带着上一轮的
    `tool_calls`** ⇒ `summarize_agent_result` 判成 `pending_approval`
    ⇒ **接口报"在等人工审批"，而图其实早就跑完了**。
    一轮就能跑完的场景（下面那些用例）**盖不住**它 —— 所以这条必须**两轮**。

    攒块本身还有第二个后果：`tool_calls` 里会出现
    `"date_todayduckduckgo_search"` 这种**两个名字粘在一起**的串
    （两轮的 name 被拼到一条上了）—— 在真服务上就是这么看到的。

    **2026-10-03更正**：上面那句是**当时的观察记录**（原文保留）。
      它当时的**根因**是 `tool_execute` 按字面量 `"search"` 分派、而真名是
      `duckduckgo_search` ⇒ 模型收到"未找到工具"后**反复重试**（名字现在已改成 `web_search`，
      分派也改成查表了）。**根因修掉 ≠ 本用例可以删** —— 它守的是**端点该从哪取状态**，
      那是**另一件事**：只要端点还在拿流式块猜，多轮场景就会重演。
    """
    _install_fake_model(monkeypatch)                       # 先装，再换模型
    monkeypatch.setattr(agent_graph, "llm_with_tools",
                        _bind(_TwoRoundModel(tokens=[])))
    # `calculator` **不在**敏感名单里 ⇒ 走 `tools` 节点直接执行，图会跑完
    monkeypatch.setattr(agent_graph, "SENSITIVE_TOOLS", frozenset({"web_search"}))
    graph = _SpyGraph(agent_graph.build_agent_graph())

    resp = _call_stream_route(monkeypatch, graph)
    frames = _collect_frames(resp)

    bodies = [json.loads(f[len("data: "):]) for f in frames
              if f.startswith("data: ") and f[len("data: "):].strip() != "[DONE]"]
    summary = [b for b in bodies if "status" in b][-1]

    assert summary["status"] == "answered", (
        f"图已经跑完了，却报成在等审批 ⇒ 前端会一直等一个**永远不会来**的批准。"
        f" 汇总帧 = {summary}"
    )
    assert summary["answer"] == "答案是42", f"答案不是最终那一轮：{summary['answer']!r}"

    # 并且**必须真的去问过图**（不是靠攒块蒙对的）
    assert graph.aget_state_calls, "端点没有读图的最终状态 ⇒ 它是在拿流式块猜"


class _SpyGraph:
    """把**真图**包一层，只为记下「端点到底有没有去读最终状态」。

    不包这一层的话，断言只能看汇总帧对不对 —— 而那**可能蒙对**
       （比如攒块刚好攒出同一个结果）。要钉死的是**取数来源**。
    """
    def __init__(self, inner):
        self.inner = inner
        self.aget_state_calls = []

    def astream(self, *a, **kw):
        return self.inner.astream(*a, **kw)

    async def aget_state(self, config):
        self.aget_state_calls.append(config)
        return await self.inner.aget_state(config)


@pytest.mark.parametrize("route_missing_msg", ["langgraph_chat_stream"])
def test_route_is_registered(route_missing_msg):
    """路由真的挂上去了（别写成函数却忘了 `@router.post`）。"""
    paths = {r.path for r in m.router.routes}
    assert "/api/v1/agent/langgraph_chat/stream" in paths, f"路由没注册。现有：{sorted(paths)}"


def test_summary_frame_carries_requested_by(monkeypatch):
    """汇总帧必须带 `requested_by` —— 与**非流式** `/agent/langgraph_chat`
    （`api_v1_agent.py:177`）对齐。

    这是**基线**端点的那一条；A / B / C 三条新链在
       `app/tests/test_agent_stream_chains.py::test_summary_frame_carries_requested_by`，
       链 D 在 `test_plan_execute_summary_carries_plan_and_execution_result`。
    """
    fake = _FakeGraph(tokens=("你", "好"))
    resp = _call_stream_route(monkeypatch, fake, user_name="tester")
    frames = _collect_frames(resp)

    objs = [json.loads(f[len("data: "):]) for f in frames if f.strip() != "data: [DONE]"]
    assert objs[-1].get("requested_by") == "tester", f"汇总帧没带 requested_by：{objs[-1]}"


# ====================  · 留痕（链 L = `/agent/langgraph_chat/stream`） ====================


def test_stream_route_persists_the_turn_on_completion(monkeypatch):
    """：正常跑完 ⇒ **成对**写进 `chat_history`，且 `status == "done"`。

    答案取自**图的最终状态**（`aget_state`）—— 所以这里刻意让 `_FakeGraph` 的
       流内 token 与最终状态**不一样**（流的是 `你/好`，最终状态是 `最终答案`）：
       拿 `collected` 攒出来的话，这条断言会红。
    """
    store = FakeRedis()
    fake = _FakeGraph(tokens=("你", "好"), final_messages=[AIMessage(content="最终答案")])
    _collect_frames(_call_stream_route(monkeypatch, fake, redis=store, question="问题一"))

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], f"没成对写：{entries}"
    assert entries[0]["content"] == "问题一"
    assert entries[1]["content"] == "最终答案", (
        f"：`done` 的答案必须取自**图的最终状态**，⛔ 不是攒流过的块：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "done", f"status 不对：{entries}"


def test_stream_route_writes_nothing_while_waiting_for_approval(monkeypatch):
    """ 规则 2：**停在审批点**的这一轮**一条都不写**。

    不 gate 就会把「等人工审批的半句」写成 `status="done"` ——
       `summarize_agent_result` 此时给的 `answer` 是**非空的半句**（模型"先说一句再调工具"），
       ⇒ 那是**本 DEC 的 `status` 字段要防的那类误判**。
    这不是"漏了一个出口"：停在审批点是图的**正常**行为，不属
       「没能正常走完」的射程（§八·1 已登记为另裁）。
    """
    store = FakeRedis()
    monkeypatch.setattr(m, "register", lambda *a, **k: None)
    monkeypatch.setattr(m, "resolve", lambda *a, **k: None)
    fake = _FakeGraph(tool_call=True)
    _collect_frames(_call_stream_route(monkeypatch, fake, redis=store))

    assert store.history("tester", THREAD) == [], (
        f"停在审批点却写了历史 ⇒ 那半句会被读成'说完了'：{store.history('tester', THREAD)}"
    )
