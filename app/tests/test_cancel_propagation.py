""" —— **服务端 cancel 传播到上游**。

## 这个文件测的是「最难假完成」的那一件事

硬门 C 的三条判据里，前两条（前端停 / 日志有 cancel）**都能在"其实没停"的情况下成立** ——
前端停可能只是前端不再显示，而后端**还在烧钱**。
⇒ 所以这里**一条都不测前端**，只测**服务端与上游之间**发生了什么。

## 判据为什么钉在 ASGI 层（不是"自己造一个取消"）

**取消是谁发起的？** 不是我们的代码 —— 是 **Starlette 收到 `http.disconnect` 后
取消 `stream_response` 任务**。uvicorn 实测报 `spec_version: "2.3"` ⇒ 走的是
`create_collapsing_task_group` + `listen_for_disconnect` 那条路。

⇒ 如果测试自己 `task.cancel`，那测的是**我假想的取消**，不是**线上真会发生的那次取消**。
本文件直接把 `StreamingResponse` 当成 ASGI app 跑一遍（`await resp(scope, receive, send)`），
`receive` 在**第 N 帧发出之后**回 `http.disconnect` —— 与真服务器走的是**同一段代码**。

## 假上游为什么是「手动迭代器」而不是 async generator

写了 `async def` + `yield` 的假上游，**红不出来**：`asyncio.run` 收尾时会
`shutdown_asyncgens`，把没人关的异步生成器**顺手关掉** ⇒ `finally` 照跑
**"被我们关了"和"被事件循环收尾关了"长得一模一样**（实测：改前 5 条里有 3 条因此假绿）。

⇒ 假上游实现 `__aiter__` / `__anext__` / `aclose`，**故意不是 async generator**。
这样 `closed` 为真就**只能**是**我们的代码调了 `aclose`**。

## 两条链的观测对象不一样，别混

| 链 | 落在哪 | 数什么 |
|---|---|---|
| Prometheus | `app/core/metrics.py` | **本轮新加的 `stream_cancelled_total`** |
| PG（`token_usage_logs`） | `app/billing/token_tracker.py` | **流式生成阶段不记账** ⇒ 拿它当判据**看不到任何东西**（本任务踩过） |

裁定与理由 ⇒ 本仓的决策记录（⛔ 不随产品仓提供）
"""

import asyncio
import contextlib
import json

from langchain_core.messages import AIMessageChunk
from prometheus_client import REGISTRY

import routing.api_v1_agent as agent_mod
import routing.api_v1_rag as rag_mod
import core.cache as cache_mod
import core.db as db_mod
from conftest import FakeRedis
from routing.schemas import QuestionRequest

RAG_ENDPOINT = "rag_stream_search"
AGENT_ENDPOINT = "agent_langgraph_chat_stream"

#  契约 C 起，历史键里多了 `thread_id`（端点收到的那个）。
#    本文件**读写走同一个名字** —— 别在 `kw.setdefault` 处写一个字面量、
#    读取处另写一个：两处一旦不一致，`assert history(...) == []` 会**读空桶而静默变绿**。
THREAD = "t-cancel"

# 断开发生在**第几帧之后** —— 取 2 是为了留出一帧的余量，
# 免得把"正在途中的那一块"误判成"断开后还在拉"。
DISCONNECT_AFTER = 2
# 断开后最多还允许出现这么多次拉取（在途那一块）。
TOLERANCE = 2


# ==================== ASGI 层的"客户端断开" ====================


def _body_chunks(sent):
    """只数 `http.response.body` 的**非空**帧（`start` 与收尾的空帧不算）。"""
    return [m["body"] for m in sent
            if m["type"] == "http.response.body" and m.get("body")]


def _drive_asgi_until_disconnect(resp, *, after_chunks, expect_raise=False):
    """把 `StreamingResponse` 当 ASGI app 跑，并在**第 `after_chunks` 帧之后**断开客户端。

    `spec_version` 必须写 **"2.3"** —— 那是 uvicorn 实测报的版本。
       换成 "2.4" 会走 Starlette 另一条分支（`except OSError: raise ClientDisconnect`），
       **取消就不会送进生成器** ⇒ 测出来的东西和线上不是一回事。
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST",
        "path": "/under-test",
        "headers": [],
    }
    sent = []
    disconnected = asyncio.Event()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if len(_body_chunks(sent)) >= after_chunks:
            disconnected.set()

    async def go():
        try:
            await resp(scope, receive, send)
        except BaseException as e:      # noqa: BLE001 —— 只给"关流自己抛"那个用例用
            # 默认**照抛**（异常本身就是被测的行为）；只有 `expect_raise` 时才咽下，
            #    因为那个用例要断言的是"异常之前记账已经落了"，不是"有没有抛"。
            if not expect_raise:
                raise
            sent.append({"type": "test.expect_raise", "error": type(e).__name__})

    asyncio.run(go())
    return sent


# ==================== 假上游（手动迭代器 · 见文件头） ====================


class _AsyncSpyStream:
    """**故意不是 async generator** —— 否则会被 `shutdown_asyncgens()` 关掉，测不出真因。"""

    def __init__(self, owner, item_factory, first_delay=None):
        self.owner = owner
        self.item_factory = item_factory
        self.first_delay = first_delay
        self.i = 0
        # ：吐够 N 块之后**抛**（"生成到一半炸了"那条出口要用）——
        #    `None` ⇒ 从不抛，与改前**逐字相同**（`_SpyGraph` 没有这个属性 ⇒ 取到 `None`）。
        self.explode_after = getattr(owner, "explode_after", None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.i >= self.owner.cap:
            raise StopAsyncIteration
        # 判据是「**已经吐出去 N 块**」⇒ 在**取下一块时**抛（`i` 此刻正是已吐出的块数）。
        if self.explode_after is not None and self.i >= self.explode_after:
            raise RuntimeError("上游炸了")
        self.i += 1
        self.owner.pulled += 1
        # 第一块的等待时长可调： 要测「**一块都还没生成**就断了」，
        #    而那是**竞态** —— 首块等太短，断开还没送到，测试就变成掷骰子。
        delay = self.first_delay if self.i == 1 and self.first_delay else 0.01
        await asyncio.sleep(delay)  # 给取消一个**真的 await 点**（没有它就取消不进来）
        return self.item_factory()

    async def aclose(self):
        """**真服务实测（2026-10-03）**：这一句会被**二次投递的取消**打断 ⇒ 抛 `CancelledError`。

        旧版假流在这里**只是把 `closed` 置真、然后正常返回** —— 于是
           `finally` 里排在它后面的三件收尾（计数 / 日志 / 半截落盘）**照跑**，
           单测全绿而真服务全废。所以这里必须能**模拟被打断**。
        """
        self.owner.aclose_called = True
        if getattr(self.owner, "aclose_raises", False):
            # 关流**自己**就抛（例如上游连接早断了）—— 与 `aclose_interrupted` 不是一回事：
            # 那个是"外层来打断这次 await"，shield 护得住；这个是关流动作本身失败，shield 护不住，
            # ⇒ 只能靠**顺序**保住记账（收尾写在它前面）。这条正是顺序那一半的判据。
            raise RuntimeError("上游连接已经断了 ⇒ aclose 自己就抛")
        if getattr(self.owner, "aclose_interrupted", False):
            # **不能直接 `raise CancelledError`** —— 那连 shield 也护不住（异常是它自己抛的，
            #    与"外层把这次 await 打断"不是一回事）⇒ 测出来的是假的。
            #    真的机制是：这次 await **真的挂起了一次**，取消作用域就在这个挂起点上投递。
            #    ⇒ 于是"有没有 shield"**决定了**它能不能走完，与线上完全同构。
            await asyncio.sleep(0)
        self.owner.closed = True


class _SyncSpyStream:
    """旧的同步上游 —— **留在假对象里，只是为了让 RED 的理由是"没关流"而不是"没有 .stream"**。"""

    def __init__(self, owner, item_factory):
        self.owner = owner
        self.item_factory = item_factory
        self.i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.i >= self.owner.cap:
            raise StopIteration
        self.i += 1
        self.owner.pulled += 1
        return self.item_factory()

    def close(self):
        self.owner.closed = True


class _SpyRagLLM:
    """假的流式 LLM：**一直吐**（直到被关掉），记录被拉了几块、有没有被关、走的哪条路。

    `cap` 是**安全阀** —— 取消没生效时，测试应当**断言失败**，不是挂住。
    """

    def __init__(self, cap=40, first_delay=None, aclose_interrupted=False, aclose_raises=False,
                 explode_after=None):
        self.cap = cap
        self.first_delay = first_delay
        self.explode_after = explode_after           # ⇒ 吐够 N 块之后抛（的异常出口）
        self.pulled = 0
        self.closed = False
        self.aclose_called = False
        self.aclose_interrupted = aclose_interrupted   # ⇒ 模拟"关流被二次取消打断"（真服务实测）
        self.aclose_raises = aclose_raises             # ⇒ 模拟"关流动作本身失败"（护不住，只能靠顺序）
        self.astream_calls = []
        self.stream_calls = []  # 换异步之后必须保持为空

    def astream(self, messages):
        self.astream_calls.append(messages)
        return _AsyncSpyStream(
            self, lambda: AIMessageChunk(content="字"), first_delay=self.first_delay
        )

    def stream(self, messages):
        self.stream_calls.append(messages)
        return _SyncSpyStream(self, lambda: AIMessageChunk(content="字"))


class _SpyGraph:
    """假的图：与 `test_agent_sse._FakeGraph` 同款，**额外记「流有没有被关」**。"""

    def __init__(self, cap=40, aclose_interrupted=False, explode_after=None):
        self.cap = cap
        self.pulled = 0
        self.closed = False
        self.aclose_called = False
        self.aclose_interrupted = aclose_interrupted
        # ：吐够 N 块之后抛（"生成到一半炸了"）—— `_AsyncSpyStream` 从本对象上取它
        #    （同 `_SpyRagLLM` 的用法）。`None` ⇒ 从不抛，与改前**逐字相同**。
        self.explode_after = explode_after
        self.aget_state_calls = []

    def astream(self, payload, config, stream_mode):
        return _AsyncSpyStream(
            self, lambda: (AIMessageChunk(content="字"), {"langgraph_node": "agent"})
        )

    async def aget_state(self, config):
        self.aget_state_calls.append(config)
        return type("S", (), {"values": {"messages": []}})


# ==================== 驱动两条端点 ====================


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeCursor:
    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _call_rag_stream(monkeypatch, llm, history=None, get_history=None, **kw):
    """直接调端点函数，把**所有**外部依赖短路 —— 本文件测的是取消，不是检索/预算/认证。

    传 `history=<FakeRedis>` ⇒ 把**真落进存储的那些条目**留下来（的观测对象）；
       不传 ⇒ 换一个**一次性的**假存储 —— 其余用例也不许碰真 redis。
    传 `get_history=` ⇒ 覆盖"读回"；**默认真的从那个假存储读** ——
       这才把"落账"与"下一轮读到"**接起来**（端点 `:628` 只在前端没传历史时才读）。

    **桩打在【叶子】`cache.redis_client`，不是 `rag_mod.append_chat_history`。**
        把留痕搬进了 `cache.persist_turn`，它调的是 **`cache` 自己的模块全局** ——
       patch `rag_mod` 上那个名字**够不着它**，写入会**直接打到真 redis**。
       同一个病本仓刚栽过（`get_db` 有两个模块全局）⇒ 本仓的复盘记录（⛔ 不随产品仓提供）。
    """
    store = history if history is not None else FakeRedis()
    monkeypatch.setattr(cache_mod, "redis_client", store)
    # 端点 `:628` 调的是 **`api_v1_rag` 的模块全局** ⇒ 这个 patch 是必需的
    #    （与上面那个不是一个东西）。默认值 = 从上面那个假存储里**真读**。
    monkeypatch.setattr(rag_mod, "get_chat_history", get_history or cache_mod.get_chat_history)
    monkeypatch.setattr(rag_mod, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "get_embedding", lambda text, **k: [0.0] * 8)
    monkeypatch.setattr(rag_mod, "get_llm_stream", lambda: llm)
    monkeypatch.setattr(rag_mod, "get_db", lambda: _FakeConn())
    # 2026-10-03 补丁的**第二个** `get_db` —— 为什么一个不够（乙段）。
    #
    # 乙段把 `/rag/stream_search` 从「自己写 SQL」改成「调共享层 `db.search_similar`」。
    # 连接点**换了模块**：原先 `get_db()` 解析到 `api_v1_rag` 的模块全局（上一行能挡），
    # 现在解析到 **`db.py` 的模块全局** —— 上面那行 monkeypatch **够不着它**。
    #
    # **这个洞在本机是看不见的**：本机 postgres 真开着 ⇒ 真连上去、`fetchall` 回 `[]`
    #    ⇒ 12 条用例照样全绿。CI 没有 postgres ⇒ `Connection refused` ⇒ 12 条全红。
    #    ⇒ **判据不能是「本机跑绿」**，得跑 CI 等价物：
    #       `POSTGRES_PORT=59999 venv/bin/python -m pytest app/ -q -m "not integration and not needs_db"`
    #    复现与根因 ⇒ 本仓的复盘记录（⛔ 不随产品仓提供）
    monkeypatch.setattr(db_mod, "get_db", lambda: _FakeConn())
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("user_name", "tester")
    # `citations` 默认 `False` = **旧行为**（不是本次新增的行为）。
    #    开这个口子只为下面那条「帧序」用例 —— 不开引用模式 ⇒ `sources` 帧**根本不发**
    #    （发出条件是 `req.citations and sources_list`，`api_v1_rag.py:739`）。
    req = QuestionRequest(
        question=kw.pop("question", "你好"),
        citations=kw.pop("citations", False),
    )
    return asyncio.run(rag_mod.stream_search(req, **kw))


def _call_agent_stream(monkeypatch, graph, redis=None, **kw):
    monkeypatch.setattr(agent_mod, "agent_graph", graph)
    monkeypatch.setattr(agent_mod, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(agent_mod, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(agent_mod, "register", lambda *a, **k: None)
    monkeypatch.setattr(agent_mod, "resolve", lambda *a, **k: None)
    # ：端点会往 `chat_history` 写留痕 ⇒ 短路存储（否则打到真 redis）。
    monkeypatch.setattr(cache_mod, "redis_client", redis if redis is not None else FakeRedis())
    kw.setdefault("thread_id", THREAD)
    kw.setdefault("user_name", "tester")
    kw.setdefault("question", "你好")
    return asyncio.run(agent_mod.langgraph_chat_stream(**kw))


@contextlib.contextmanager
def _capture_logs():
    """抓 loguru 的日志 —— 判据第一条就是「**日志里有 cancel 事件**」，得能打印出来。"""
    from loguru import logger
    lines = []
    handler_id = logger.add(lambda msg: lines.append(str(msg)), level="INFO")
    try:
        yield lines
    finally:
        logger.remove(handler_id)


def _metric(endpoint):
    return REGISTRY.get_sample_value("stream_cancelled_total", {"endpoint": endpoint}) or 0.0


# ==================== RAG 端（`/rag/stream_search`） ====================


def test_rag_closes_upstream_when_client_disconnects(monkeypatch):
    """**本任务的核心断言**：客户端断开后，**上游 HTTP 流必须被真的关掉**。

    关不掉 = 上游继续生成、**继续计费** —— 而前端看起来一切正常（它只是不显示了）。
       这正是硬门 C 标"最容易假完成"的原因。
    """
    llm = _SpyRagLLM()
    resp = _call_rag_stream(monkeypatch, llm)
    _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert llm.pulled >= DISCONNECT_AFTER, f"根本没流起来（只拉了 {llm.pulled} 块）⇒ 本断言无意义"
    assert llm.closed, "客户端断开后上游流**没有被关** ⇒ 上游还在跑、还在烧钱"


def test_rag_stops_pulling_upstream_after_disconnect(monkeypatch):
    """断开之后**不再向上游要下一块** —— 关流是果，不再拉取才是"真的停了"。"""
    llm = _SpyRagLLM(cap=40)
    resp = _call_rag_stream(monkeypatch, llm)
    _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert llm.pulled >= DISCONNECT_AFTER, f"根本没流起来（只拉了 {llm.pulled} 块）⇒ 本断言无意义"
    assert llm.pulled <= DISCONNECT_AFTER + TOLERANCE, f"断开后仍拉了 {llm.pulled} 块 ⇒ 上游没停"


def test_rag_uses_async_upstream(monkeypatch):
    """上游必须是**异步**的（`astream`）—— 同步 `for` 会阻塞事件循环，取消只能等"下一块到达"。"""
    llm = _SpyRagLLM(cap=3)
    resp = _call_rag_stream(monkeypatch, llm)
    _drive_asgi_until_disconnect(resp, after_chunks=99)

    assert llm.astream_calls, "端点没有走异步上游"
    assert not llm.stream_calls, "端点仍在走同步 `stream()` ⇒ 事件循环会被阻塞"


def test_rag_sends_nothing_after_disconnect(monkeypatch):
    """**取消之后不许再发帧** —— 旧实现在 `except CancelledError` 里还 `yield "data: [DONE]"`。

    那不仅无意义（接收方已经走了），还会让"已取消"和"正常收尾"在**帧层面长得一样**。
    """
    llm = _SpyRagLLM()
    resp = _call_rag_stream(monkeypatch, llm)
    sent = _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    bodies = _body_chunks(sent)
    assert len(bodies) >= DISCONNECT_AFTER, f"根本没流出帧（只有 {len(bodies)} 帧）⇒ 本断言无意义"
    assert not any(b"[DONE]" in b for b in bodies), f"取消后还发了帧：{bodies}"
    assert len(bodies) <= DISCONNECT_AFTER + TOLERANCE, f"断开后仍在发：{len(bodies)} 帧"


def test_rag_records_cancel_event(monkeypatch):
    """判据①②：**日志里有 cancel 事件** + Prometheus 计数 +1（Grafana 里看得）。"""
    llm = _SpyRagLLM()
    before = _metric(RAG_ENDPOINT)
    resp = _call_rag_stream(monkeypatch, llm)
    with _capture_logs() as lines:
        _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert _metric(RAG_ENDPOINT) == before + 1, "取消没有计数 ⇒ Grafana 上看不到它"
    assert any("cancel" in line.lower() or "断开" in line for line in lines), f"日志里没有取消事件：{lines}"


def test_rag_normal_completion_is_not_counted_as_cancel(monkeypatch):
    """**反面**：正常跑完不算取消 —— 否则这个指标就是个恒增的假信号。"""
    llm = _SpyRagLLM(cap=3)
    before = _metric(RAG_ENDPOINT)
    resp = _call_rag_stream(monkeypatch, llm)
    sent = _drive_asgi_until_disconnect(resp, after_chunks=99)  # 永不触发断开

    bodies = _body_chunks(sent)
    assert bodies[-1].strip() == b"data: [DONE]", f"正常收尾的末帧不对：{bodies[-1]!r}"
    assert llm.closed, "正常跑完也要关流（否则连接泄漏）"
    assert _metric(RAG_ENDPOINT) == before, "正常收尾被记成了取消"


# ============ RAG 端 ·  ：中断时那半截答案怎么办 ============
#
# 核出来的事实（本仓的决策记录（⛔ 不随产品仓提供））：取消时 `collected_parts` 是**直接丢**的，
# 而且不是"决定"，是"碰巧" —— `append_chat_history` 写在循环之后，取消在它之前 `raise`。
# ⇒ 这里把「丢」改成「存」，并把**存的形态**钉死。


def _seen_content(sent):
    """客户端**真的收到**的内容 —— 用于核对"存进历史的就是这一段"（不是"存了点什么"）。"""
    parts = []
    for b in _body_chunks(sent):
        text = b.decode()
        if not text.startswith("data: {"):
            continue  # `data: [DONE]` 之类
        payload = json.loads(text[len("data: "):])
        # 只数**内容帧** —— `sources` / `error` 这些收尾帧没有 `content` 键
        #    （加的异常出口用例会走到这里）。
        if "content" in payload:
            parts.append(payload["content"])
    return "".join(parts)


def test_rag_persists_partial_answer_when_cancelled(monkeypatch):
    """ 核心：客户端断开后，**已经生成的那半截要进历史**（不是直接丢）。

    为什么非存不可：`api_v1_rag.py` 那行注释早就写了这个意图 ——
       「如果有停止当前消息先放历史……真停止按钮的调用（**使它支持历史补偿**）」。
       取消时丢掉 ⇒ 用户那问句**也一起丢**（它跟答案写在同一段代码里）。
    """
    llm = _SpyRagLLM()
    store = FakeRedis()
    resp = _call_rag_stream(monkeypatch, llm, history=store, question="中断也要留痕")
    sent = _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    seen = _seen_content(sent)
    assert seen, "根本没流出内容 ⇒ 本断言无意义"
    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"中断后历史应**成对**写入 user+assistant，实际 {entries}"
    )
    assert entries[0]["content"] == "中断也要留痕", f"用户那问句没存对：{entries[0]}"
    assert entries[1]["content"] == seen + rag_mod.INTERRUPTED_SUFFIX, (
        f"存进历史的不是客户端看到的那半截（或缺中断标记）：{entries[1]['content']!r}"
    )
    # ：光有"半截 + 标记"还不够 —— 读的人得能**机器可读**地分出这不是完整答案。
    assert [e["status"] for e in entries] == ["cancelled", "cancelled"], (
        f"取消那条出口的 status 不对（判决表那一格）：{entries}"
    )


def test_rag_bookkeeping_survives_interrupted_aclose(monkeypatch):
    """**2026-10-03 真服务实测抓到的**：`await stream.aclose` **会被二次投递的取消打断**。

    打断之后，`finally` 里排在它后面的三件收尾**一件都不会执行** ——
    计数不涨、日志没有、半截不落盘。**而单测当时全绿**（假流的 `aclose` 不抛）。

    这不是"理论上可能"：容器与宿主机**同一份代码**、同一个问题、同一次真切断，
       **早切（还没吐块）** 计数 1.0→2.0、**晚切（已经吐了字再断）** 2.0→2.0 ；
       插桩版本打印出 `DBG aclose 抛了: CancelledError`。
       卡在哪：早切时生成器**还没被推进过**，`aclose` 不必真收尾 ⇒ 不挂起 ⇒ 打不断。

    ⇒ 本用例把**"用户已经看到字了才点停止"**这个主场景钉死：三件收尾一件都不能少。
    """
    llm = _SpyRagLLM(aclose_interrupted=True)
    store = FakeRedis()
    before = _metric(RAG_ENDPOINT)
    resp = _call_rag_stream(monkeypatch, llm, history=store, question="关流被打断也要留痕")
    with _capture_logs() as lines:
        sent = _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert llm.aclose_called, "前提没成立：压根没去关上游"
    assert llm.closed, "关流被取消打断后**上游没真的关上** ⇒ 上游可能还在跑、还在烧钱"
    assert _metric(RAG_ENDPOINT) == before + 1, (
        "aclose 抛异常 ⇒ 取消没计数（真服务实测就是 2.0→2.0）"
    )
    assert any("断开" in line for line in lines), f"日志里没有取消事件：{lines}"
    seen = _seen_content(sent)
    assert seen, "根本没流出内容 ⇒ 本断言无意义"
    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], f"半截没成对落盘：{entries}"
    assert entries[1]["content"] == seen + rag_mod.INTERRUPTED_SUFFIX, (
        f"落盘的不是客户端看到的那半截：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "cancelled", f"status 没跟着走：{entries}"


def test_rag_bookkeeping_lands_even_when_aclose_itself_fails(monkeypatch):
    """**钉的是"顺序"那一半 —— shield 护不住这个**。

    上一个用例钉的是"关流被外层打断"（shield 能护）；本用例钉的是**关流动作自己失败**
    （上游连接早断了 ⇒ `aclose` 抛 `RuntimeError`）。这种情况 shield 一点用没有，
    ⇒ **唯一的保障是：三件收尾写在 `await` 之前**。

    为什么非要单独钉：修法落地后，`shield` 在，**顺序坏了上一个用例照样全绿**
       （护住的 aclose 正常返回，后面的收尾照跑）—— 于是有人把"关流"挪回最前面
       （很自然的"收尾动作要放前面才干净"），B3 的承诺就**静默**没了。
    """
    llm = _SpyRagLLM(aclose_raises=True)
    store = FakeRedis()
    before = _metric(RAG_ENDPOINT)
    resp = _call_rag_stream(monkeypatch, llm, history=store, question="关流自己抛也要留痕")
    with _capture_logs() as lines:
        sent = _drive_asgi_until_disconnect(
            resp, after_chunks=DISCONNECT_AFTER, expect_raise=True
        )

    assert llm.aclose_called, "前提没成立：压根没去关上游"
    assert not llm.closed, "假的 aclose 就没走到置位那一步 ⇒ 前提没成立"
    assert [m for m in sent if m["type"] == "test.expect_raise"], (
        "关流的异常没传出来 ⇒ 本用例前提没成立"
    )
    assert _metric(RAG_ENDPOINT) == before + 1, "关流抛 ⇒ 取消没计数（顺序坏了的典型症状）"
    assert any("断开" in line for line in lines), f"日志里没有取消事件：{lines}"
    seen = _seen_content(sent)
    assert seen, "根本没流出内容 ⇒ 本断言无意义"
    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], (
        f"关流抛 ⇒ 半截没落盘（B3 承诺失效）：{entries}"
    )
    assert entries[1]["content"] == seen + rag_mod.INTERRUPTED_SUFFIX, \
        f"落盘的半截不对：{entries[1]['content']!r}"
    assert entries[1]["status"] == "cancelled", f"status 没跟着走：{entries}"


def test_rag_next_turn_prompt_reads_the_interrupted_half_answer(monkeypatch):
    """ 的**真正目的**：那半截不是留个痕给人看的，是**下一轮 prompt 要读到**。

    前面几个用例只证到「**落进 Redis 了**」—— 而"落进去"离"下一轮真读到"还差一段：
       端点**只在前端没传历史时**才去读（`:628`），读了之后拼进 `messages`（`:694`）。
       本用例把这一段**接起来**：第一轮被切断 ⇒ 第二轮（不带历史）发问
       检查**真喂给 LLM 的 messages** 里有没有那半截 + 中断标记。
    """
    store = FakeRedis()                          # 假存储：两轮**共用同一个**
    llm1 = _SpyRagLLM(cap=40)
    resp = _call_rag_stream(monkeypatch, llm1, history=store, question="第一问：紫色河马协议")
    _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)
    assert [e["role"] for e in store.history("tester", THREAD)] == ["user", "assistant"], (
        f"前提没成立：第一轮被切断后历史没落账，后面对不上：{store.history('tester', THREAD)}"
    )

    # 第二轮：**不传 conversation_history** ⇒ 走 `:628` 从存储读回（`_call_rag_stream` 的默认读法）
    llm2 = _SpyRagLLM(cap=2)
    resp2 = _call_rag_stream(monkeypatch, llm2, history=store, question="接着上面说")
    _drive_asgi_until_disconnect(resp2, after_chunks=99)   # 正常跑完

    contents = [m["content"] for m in llm2.astream_calls[0]]
    assert any("第一问：紫色河马协议" in c for c in contents), (
        f"用户那句提问没进下一轮 prompt ⇒ 用户问'接着上面说'时模型不知道在接什么：{contents}"
    )
    assert any(rag_mod.INTERRUPTED_SUFFIX in c for c in contents), (
        f"半截答案没带中断标记进 prompt ⇒ 模型会把**被截断的回答**当成'上一轮说完了'，"
        f"行为跟着变（这正是 `INTERRUPTED_SUFFIX` 存在的理由）：{contents}"
    )


def test_rag_full_answer_is_saved_without_interrupt_marker(monkeypatch):
    """**反面**：正常跑完 ⇒ 存**完整**答案，**不许**带中断标记。

    否则下一轮 prompt 会把一个好好的回答当成"被截断的"，模型的行为跟着变。
    """
    llm = _SpyRagLLM(cap=3)
    store = FakeRedis()
    resp = _call_rag_stream(monkeypatch, llm, history=store, question="完整的一问")
    _drive_asgi_until_disconnect(resp, after_chunks=99)  # 永不触发断开

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], f"正常收尾的历史不对：{entries}"
    assert entries[1]["content"] == "字" * 3, f"正常收尾存的不是完整答案：{entries[1]['content']!r}"
    assert rag_mod.INTERRUPTED_SUFFIX not in entries[1]["content"], "完整答案被误标成了中断"
    assert [e["status"] for e in entries] == ["done", "done"], (
        f"🔴 ：正常走完那条出口的 status 必须是 'done'（读的人靠它分辨）：{entries}"
    )


def test_rag_cancel_before_any_chunk_saves_nothing(monkeypatch):
    """边界：**一块都没生成**就断了 ⇒ 什么都不写。

    写一条空的助手消息只会污染下一轮 prompt（模型看到"回答是空的"）。
    首块延迟调大是为了让"断开送到"与"首块产出"的**竞态**稳定倒向我们要测的那一边。
    """
    llm = _SpyRagLLM(first_delay=0.3)
    store = FakeRedis()
    resp = _call_rag_stream(monkeypatch, llm, history=store, question="还没开始就断了")
    _drive_asgi_until_disconnect(resp, after_chunks=0)

    assert store.history("tester", THREAD) == [], f"没生成任何内容却写了历史：{store.history('tester', THREAD)}"


def test_rag_persists_partial_answer_when_generation_raises(monkeypatch):
    """ 判据①：**异常**那条出口现在也要留痕（改前是"丢"）。

    改前的形状：骨架**只有一个"取消专用"的钩子**（`on_cancel`）⇒ 上游抛异常时，
       那半截**一个字都不留**（的普查表在 `/rag/stream_search` 的「异常」列写的就是「丢」）。
    与取消那条**存同一个形态**（半截 + 标记 + 成对），差别只在 `status` ——
       **机器可读的区分在 `status`，不另造第二个标记串**（§八·3）。
    """
    llm = _SpyRagLLM(explode_after=2)
    store = FakeRedis()
    resp = _call_rag_stream(monkeypatch, llm, history=store, question="生成到一半炸了")
    sent = _drive_asgi_until_disconnect(resp, after_chunks=99)   # 永不触发断开

    seen = _seen_content(sent)
    assert seen == "字" * 2, f"前提没成立：断前应已收到 2 块，实际 {seen!r}"
    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], f"异常后没成对落盘：{entries}"
    assert entries[0]["content"] == "生成到一半炸了"
    assert entries[1]["content"] == seen + rag_mod.INTERRUPTED_SUFFIX, (
        f"落盘的不是客户端看到的那半截：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "error", f"异常那条出口的 status 不对：{entries}"


def test_rag_error_path_still_emits_no_done_frame(monkeypatch):
    """**旧契约不许被这条新接线带歪**：错误路径**没有** `[DONE]`。

    本端点靠"**没有** `[DONE]`"分辨出错（`_on_error` 的 docstring）——
       加上它就等于把"出错"变成"正常结束"的样子。
    为什么值得单钉： 给错误出口**新接了一根线**（`on_incomplete`），
       接线时最自然的"顺手整一下"就是让尾巴统一 —— 而那条尾巴**是故意的**。
    """
    llm = _SpyRagLLM(explode_after=2)
    resp = _call_rag_stream(monkeypatch, llm, question="生成到一半炸了")
    sent = _drive_asgi_until_disconnect(resp, after_chunks=99)

    texts = [b.decode() for b in _body_chunks(sent)]
    assert any('"error"' in t for t in texts), f"压根没发 error 帧 ⇒ 本断言无意义：{texts}"
    assert not any(t.strip() == "data: [DONE]" for t in texts), (
        f"错误路径混进了 [DONE] ⇒ 前端会把'出错'读成'正常结束'：{texts}"
    )


def test_rag_emits_sources_frame_after_done(monkeypatch):
    """**帧序是线上契约，不是笔误**：`[DONE]` 必须排在 `sources` **之前**。

    为什么值得一条用例：常规顺序是「sources 在前」，所以将来任何人「顺手整理一下收尾顺序」
       都**看不出问题** —— 而前端是照**当前这个反直觉顺序**适配的。
       此前它的载体**只有** `app/routing/api_v1_rag.py:725-732` 那段注释
       （`grep '"sources"' app/test_*.py` **零命中**）⇒ 改坏了不会有任何东西红。
    **反证**（做过）：把 `_complete` 里两帧对调 ⇒ 本用例**必红**，报出 done/src 两个下标。
    本用例只跑**正常收尾**（`after_chunks=99` ⇒ 永不断开），与取消路径无关。
    """
    llm = _SpyRagLLM(cap=3)
    # 必须让检索**有结果**：`sources` 帧的发出条件是 `req.citations and sources_list`
    #    ⇒ 空结果这一帧根本不发，用例会**因为错误的理由**通过（`src_at is not None` 会先拦住它）。
    monkeypatch.setattr(
        rag_mod, "search_similar",
        lambda *a, **k: [(1, "上下文正文", "doc.md", 0.91)],
    )
    resp = _call_rag_stream(monkeypatch, llm, citations=True)
    sent = _drive_asgi_until_disconnect(resp, after_chunks=99)

    texts = [b.decode() for b in _body_chunks(sent)]
    done_at = next((i for i, t in enumerate(texts) if t.startswith("data: [DONE]")), None)
    src_at = next((i for i, t in enumerate(texts) if '"sources"' in t), None)

    assert src_at is not None, f"压根没发 sources 帧 ⇒ 本断言无意义：{texts}"
    assert done_at is not None, f"没发 [DONE] 帧：{texts}"
    assert src_at > done_at, (
        f"帧序变了：`sources` 必须在 `[DONE]` **之后**（前端按此适配）—— "
        f"现在 [DONE] 在第 {done_at} 帧、sources 在第 {src_at} 帧"
    )
    assert src_at == len(texts) - 1, f"`sources` 必须是最后一帧：{texts}"


# ==================== Agent 端（`/agent/langgraph_chat/stream`） ====================


def test_agent_closes_graph_stream_when_client_disconnects(monkeypatch):
    """Agent 端同一条要求 ——  自己点了「新加的流式路由也没有 cancel 处理」。"""
    graph = _SpyGraph()
    resp = _call_agent_stream(monkeypatch, graph)
    _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert graph.pulled >= DISCONNECT_AFTER, f"根本没流起来（只拉了 {graph.pulled} 块）⇒ 本断言无意义"
    assert graph.closed, "客户端断开后，图的流**没有被关** ⇒ 图会继续跑完"
    assert graph.pulled <= DISCONNECT_AFTER + TOLERANCE, f"断开后仍拉了 {graph.pulled} 块"


def test_agent_sends_nothing_after_disconnect(monkeypatch):
    graph = _SpyGraph()
    resp = _call_agent_stream(monkeypatch, graph)
    sent = _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    bodies = _body_chunks(sent)
    assert len(bodies) >= DISCONNECT_AFTER, f"根本没流出帧（只有 {len(bodies)} 帧）⇒ 本断言无意义"
    assert not any(b"[DONE]" in b for b in bodies), f"取消后还发了帧：{bodies}"


def test_agent_records_cancel_event(monkeypatch):
    graph = _SpyGraph()
    before = _metric(AGENT_ENDPOINT)
    resp = _call_agent_stream(monkeypatch, graph)
    with _capture_logs() as lines:
        _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert _metric(AGENT_ENDPOINT) == before + 1
    assert any("cancel" in line.lower() or "断开" in line for line in lines), f"日志里没有取消事件：{lines}"


def test_agent_records_cancel_event_when_aclose_interrupted(monkeypatch):
    """Agent 端**同一条**：关流的 `await` 被打断 ⇒ 计数与日志也必须照记（端点同一形状）。"""
    graph = _SpyGraph(aclose_interrupted=True)
    before = _metric(AGENT_ENDPOINT)
    resp = _call_agent_stream(monkeypatch, graph)
    with _capture_logs() as lines:
        _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    assert graph.aclose_called, "前提没成立：压根没去关图的流"
    assert graph.closed, "关流被取消打断后**图的流没真的关上** ⇒ 图会继续跑完"
    assert _metric(AGENT_ENDPOINT) == before + 1, "aclose 抛异常 ⇒ 取消没计数"
    assert any("断开" in line for line in lines), f"日志里没有取消事件：{lines}"


# -------- （2026-10-04）：Agent 端两条"没走完"的出口也要留痕 --------
#
# **判据（可打印）**：`grep -n "on_incomplete" app/routing/api_v1_agent.py` ⇒ 改前**零命中**
#    （5 条 Agent 链只传 `on_complete`）⇒ 取消/异常时**一个字都不留**。


def test_agent_persists_the_partial_answer_when_client_disconnects(monkeypatch):
    """：Agent 端取消 ⇒ **客户端看到的那半截 + 标记 + `status="cancelled"`**。

    半截长度是竞态（见 `_AsyncSpyStream`）⇒ 只断"至少一块、且全是 `字`"，
       不钉死具体块数 —— 真服务本来就断在第几块不定。
    """
    store = FakeRedis()
    graph = _SpyGraph()
    resp = _call_agent_stream(monkeypatch, graph, redis=store)
    _drive_asgi_until_disconnect(resp, after_chunks=DISCONNECT_AFTER)

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], f"取消后没成对留痕：{entries}"
    assert entries[0]["content"] == "你好", f"提问没留下：{entries[0]}"
    body = entries[1]["content"]
    assert body.endswith(cache_mod.INTERRUPTED_SUFFIX), f"半截没带中断标记：{body!r}"
    head = body[:-len(cache_mod.INTERRUPTED_SUFFIX)]
    assert head and set(head) == {"字"}, f"存的不是客户端看到的那半截：{head!r}"
    assert entries[1]["status"] == "cancelled", f"status 不对：{entries[1]}"


def test_agent_persists_the_partial_answer_when_generation_raises(monkeypatch):
    """ 判据①：Agent 端的**异常**出口也留痕 —— 改前  §一 那格写的是「丢」。"""
    store = FakeRedis()
    graph = _SpyGraph(cap=10, explode_after=2)
    resp = _call_agent_stream(monkeypatch, graph, redis=store)
    _drive_asgi_until_disconnect(resp, after_chunks=99)   # 跑到自然结束（错误帧收尾）

    entries = store.history("tester", THREAD)
    assert [e["role"] for e in entries] == ["user", "assistant"], f"异常后没成对留痕：{entries}"
    assert entries[0]["content"] == "你好", f"提问没留下：{entries[0]}"
    assert entries[1]["content"] == "字" * 2 + cache_mod.INTERRUPTED_SUFFIX, (
        f"异常时该存'已吐出的那两块 + 标记'：{entries[1]['content']!r}"
    )
    assert entries[1]["status"] == "error", f"status 不对：{entries[1]}"


def test_agent_normal_completion_is_not_counted_as_cancel(monkeypatch):
    """反面：正常跑完 ⇒ 末帧是汇总帧 + `[DONE]`，**且不计取消**。"""
    graph = _SpyGraph(cap=3)
    before = _metric(AGENT_ENDPOINT)
    resp = _call_agent_stream(monkeypatch, graph)
    sent = _drive_asgi_until_disconnect(resp, after_chunks=99)

    bodies = [b.decode() if isinstance(b, bytes) else b for b in _body_chunks(sent)]
    assert bodies[-1].strip() == "data: [DONE]", f"末帧不是 [DONE]：{bodies[-1]!r}"
    summary = json.loads(bodies[-2][len("data: "):])
    assert summary["status"] == "answered", f"汇总帧不对：{summary}"
    assert _metric(AGENT_ENDPOINT) == before, "正常收尾被记成了取消"
