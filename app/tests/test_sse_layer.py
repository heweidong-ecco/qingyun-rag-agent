"""`app/routing/sse.py` 共享层自身的用例（剩余 4 条链 · 批 2）。

**本文件存在的理由**：`app/routing/sse.py` 收的**不是"重复代码"，是那 5 条实测出来的顺序约束**
（）。那几条错一次，**症状是"接口一切正常、而收尾一件都不跑"**
—— 只有**钉在骨架上**才测得出来；等到四条链各自接线再测，红的是端点，**看不出是骨架错了**。

**本文件只测骨架，不碰任何端点** —— 端点的等价性由批 3 的两份既有用例（`app/tests/test_agent_sse.py`
· `app/tests/test_cancel_propagation.py`）**必须全绿且不新加断言**来证（"没红没改 ⇒ 重构真的是等价的"）。
"""
import asyncio
import json

import routing.sse as sse


# ==================== 测试替身（不联网） ====================

class _FakeUpstream:
    """假上游：只是一串块 + 一个可记账的 `aclose`。

    **故意不复用图 / LLM 的替身** —— 本层（按设计）**对上游是什么一无所知**，
       拿真替身反而会把"骨架的契约"和"langgraph 的行为"搅在一起。
    """

    def __init__(self, items, events, exc=None):
        self._items = list(items)
        self._events = events
        self._exc = exc
        self._i = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        # 顺序是「**先把块吐完，再抛**」（要测的正是"吐了几块之后才炸"）。
        #    `exc` + `items=[]`（既有那三条用法）与旧行为**逐字相同** —— 第一次调用就抛。
        if self._i < len(self._items):
            item = self._items[self._i]
            self._i += 1
            return item
        if self._exc is not None:
            raise self._exc
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True
        self._events.append("aclose")


def _payload(frame):
    """从一帧 SSE 里取回 dict（别用字符串切片 —— 那会连带把"编码"也测进去）。"""
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    body = frame[len("data: "):-2]
    return body if body == "[DONE]" else json.loads(body)


async def _drain(agen):
    """把生成器抽干；返回 `(收到的帧, 抛出的异常或 None)`。"""
    frames, exc = [], None
    while True:
        try:
            frames.append(await agen.asend(None))
        except StopAsyncIteration:
            break
        except BaseException as e:      # noqa: BLE001 —— 取消/GeneratorExit 都要看得见
            exc = e
            break
    return frames, exc


# ==================== 1 · 帧与响应（叶子） ====================

def test_frame_default_keeps_chinese_readable():
    """默认 `ensure_ascii=False`：中文**原样**出（= `/agent/*` 四条链一直的字节）。"""
    frame = sse.sse_frame({"content": "你好"})
    assert frame == 'data: {"content": "你好"}\n\n'


def test_frame_ensure_ascii_true_preserves_legacy_rag_bytes():
    """`ensure_ascii=True`：中文变 `\\uXXXX`（= `/rag/stream_search` 一直的字节）。

    **这条用例守的是"不改线上字节"这件事** —— 抽公共层**不是**顺手统一编码的理由。
    """
    frame = sse.sse_frame({"content": "你好"}, ensure_ascii=True)
    assert frame == 'data: {"content": "\\u4f60\\u597d"}\n\n'
    # 两种编码 **JSON 解码后的值相同**（这正是"功能上无感"的判据）
    assert json.loads(frame[6:-2]) == json.loads(sse.sse_frame({"content": "你好"})[6:-2])


def test_response_headers_are_copied_not_shared(monkeypatch):
    """每个响应拿到的是**自己的** headers dict。

    别把模块级 `SSE_HEADERS` 直接传进 `StreamingResponse` —— 它持有并改写自己的 `headers`，
       共享同一个 dict 等于让所有响应**串台**（且要到"某天多了一条路由"才炸）。
    """
    async def _empty():
        if False:
            yield ""

    a = sse.sse_response(_empty())
    b = sse.sse_response(_empty())
    a.headers["X-Test-Only-A"] = "1"
    assert "X-Test-Only-A" not in b.headers
    assert "X-Test-Only-A" not in sse.SSE_HEADERS
    # 三头都在（`X-Accel-Buffering` 不能省：反代会把 SSE 攒着发）
    for k, v in sse.SSE_HEADERS.items():
        assert b.headers[k] == v


# ==================== 2 · extractor ====================

class _Chunk:
    def __init__(self, content):
        self.content = content


def _meta(node):
    return {"langgraph_node": node}


def test_graph_message_text_accepts_all_three_item_shapes():
    """`subgraphs` 开关会换掉 `item` 的形状（实测三种）—— extractor **负责归一化**。"""
    nodes = {"agent"}
    cases = [
        (_Chunk("hi"), _meta("agent")),                       # subgraphs=False
        (("react_dept:uuid",), (_Chunk("hi"), _meta("agent"))),  # subgraphs=True（本版本实测）
        (("react_dept:uuid",), _Chunk("hi"), _meta("agent")),    # 另一些版本的三元组
    ]
    for item in cases:
        assert sse.graph_message_text(item, nodes=nodes) == "hi", item


def test_graph_message_text_filters_by_node_and_empty_content():
    nodes = {"chat", "agent"}
    # 不在白名单 ⇒ 不发（`tools` 的 ToolMessage、`supervisor` 回显的用户提问都走这条）
    assert sse.graph_message_text((_Chunk("x"), _meta("supervisor")), nodes=nodes) is None
    # 空 content ⇒ 不发（`tool_call` 碎片）
    assert sse.graph_message_text((_Chunk(""), _meta("agent")), nodes=nodes) is None
    assert sse.graph_message_text((_Chunk(None), _meta("agent")), nodes=nodes) is None


def test_llm_chunk_text_skips_empty():
    assert sse.llm_chunk_text(_Chunk("a")) == "a"
    assert sse.llm_chunk_text(_Chunk("")) is None


# ==================== 3 · 正常路径 ====================

def test_each_chunk_becomes_one_frame_and_completion_tail_is_ours():
    """逐块一帧；收尾尾巴**由 `on_complete` 自己产**（骨架不自动补 `[DONE]`）。"""
    events = []
    up = _FakeUpstream(["a", "b", "c"], events)

    async def _complete(collected):
        assert collected == ["a", "b", "c"]     # `collected` 恰是**发出去的那些**
        yield sse.sse_frame({"answer": "".join(collected)})
        yield sse.DONE_FRAME

    async def main():
        agen = sse.sse_stream(lambda: up, endpoint="t",
                              extract=lambda x: x or None, on_complete=_complete)
        return await _drain(agen)

    frames, exc = asyncio.run(main())
    assert exc is None
    assert [_payload(f) for f in frames[:3]] == [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    assert _payload(frames[3]) == {"answer": "abc"}
    assert frames[4] == sse.DONE_FRAME
    assert up.closed is True
    assert "aclose" in events


def test_extract_none_passes_items_through_as_text():
    """`extract=None` 那一支 = `plan_execute`（不是图、只吐文本块）用的接口。"""
    events = []
    up = _FakeUpstream(["甲", "乙"], events)

    async def main():
        return await _drain(sse.sse_stream(lambda: up, endpoint="t"))

    frames, exc = asyncio.run(main())
    assert exc is None
    assert [_payload(f) for f in frames] == [{"content": "甲"}, {"content": "乙"}]


# ==================== 4 · 取消（约束①②③ 的正面判据） ====================

def test_sync_teardown_runs_before_closing_the_upstream(monkeypatch):
    """**本文件最重要的一条**：计数 → 日志 → `on_incomplete` **都排在 `aclose` 之前**。

    出处 （真服务实测）：`await stream.aclose` 会被**二次投递的取消**打断
    ⇒ 排在它后面的收尾**一件都不跑**，而**单测当时全绿**（假流的 `aclose` 不抛）。
    只在**晚切**（用户已经看到字再点停止）时才露出来 —— 所以这里必须**先发一块再取消**。
    """
    events = []
    counted = []
    monkeypatch.setattr(sse, "track_stream_cancel", lambda ep: (counted.append(ep), events.append("count")))
    up = _FakeUpstream(["a", "b"], events)

    async def main():
        agen = sse.sse_stream(lambda: up, endpoint="ep-x",
                              extract=lambda x: x or None,
                              on_incomplete=lambda collected, status: events.append(
                                  ("on_incomplete", list(collected), status)))
        first = await agen.asend(None)                      # ← 先真的发出去一块（"晚切"）
        assert _payload(first) == {"content": "a"}
        try:
            await agen.athrow(asyncio.CancelledError())
        except BaseException as e:                          # noqa: BLE001
            return e

    exc = asyncio.run(main())
    assert isinstance(exc, asyncio.CancelledError), "取消必须**上抛**（吞掉会让外层以为是正常结束）"
    assert counted == ["ep-x"], "取消要计数（判据③的观测对象）"
    hook_calls = [e for e in events if isinstance(e, tuple) and e[0] == "on_incomplete"]
    assert hook_calls == [("on_incomplete", ["a"], "cancelled")], \
        f"半截内容原样交出去，且**一次出口只调一次**、只按 'cancelled'：{hook_calls}"
    assert events.index("count") < events.index("aclose"), "同步收尾必须排在关流之前"
    assert next(i for i, e in enumerate(events) if isinstance(e, tuple)) < events.index("aclose")
    assert up.closed is True, "取消后**必须**关上上游（不关 = 上游继续生成、继续计费）"


def test_incomplete_hook_fires_on_error_before_the_error_frame(monkeypatch):
    """：**异常**那条出口也要留痕。

    旧实现只有一个"取消专用"的钩子 ⇒ **异常时半截一个字都不留**，
    而  的正文里 `/rag/stream_search` 那格写的是「丢」。

    **必须排在 error 帧之前**：留痕要**同步**做完（理由同约束① —— `yield` 也是 await 点，
       被二次投递的取消打断 ⇒ 后面那件事一件都不跑，而**错误帧照样发得出去**）。
    """
    events = []
    up = _FakeUpstream(["a"], events, exc=RuntimeError("炸了"))

    async def _err(exc, collected):
        # 在**帧被产出的那一刻**记一笔 —— 这样"留痕排在 error 帧之前"才是**可测**的，
        #    而不是靠读代码相信它。
        events.append(("error_frame", str(exc)))
        yield sse.sse_frame({"error": str(exc)})
        yield sse.DONE_FRAME

    async def main():
        return await _drain(sse.sse_stream(
            lambda: up, endpoint="ep-err", extract=lambda x: x or None,
            on_error=_err,
            on_incomplete=lambda collected, status: events.append(
                ("on_incomplete", list(collected), status)),
        ))

    frames, exc = asyncio.run(main())
    assert exc is None, "异常是 'error' 出口 —— 骨架收住它，⛔ 不上抛"
    hook_calls = [e for e in events if isinstance(e, tuple) and e[0] == "on_incomplete"]
    assert hook_calls == [("on_incomplete", ["a"], "error")], \
        f"半截内容原样交出去，且**一次出口只调一次**、只按 'error'：{hook_calls}"
    assert events.index(("on_incomplete", ["a"], "error")) < events.index(("error_frame", "炸了")), \
        "🔴 留痕必须**排在 error 帧之前**（同约束①：`yield` 也是 await 点）"
    assert [_payload(f) for f in frames] == [{"content": "a"}, {"error": "炸了"}, "[DONE]"]


def test_incomplete_hook_is_not_called_on_normal_completion(monkeypatch):
    """**反向**：正常跑完 ⇒ `on_incomplete` **一次都不调**（防"修过头"）。

    ⇒ 把"没走完"的钩子挂到"走完了"的路上，等于**每一次正常对话都记一遍假信号**。
    """
    hook_events = []
    up = _FakeUpstream(["a", "b"], [])

    async def _complete(collected):
        yield sse.DONE_FRAME

    async def main():
        return await _drain(sse.sse_stream(
            lambda: up, endpoint="ep-ok", extract=lambda x: x or None,
            on_complete=_complete,
            on_incomplete=lambda collected, status: hook_events.append(
                ("on_incomplete", list(collected), status)),
        ))

    frames, exc = asyncio.run(main())
    assert exc is None
    assert hook_events == [], f"正常收尾不许碰 on_incomplete：{hook_events}"
    assert _payload(frames[-1]) == "[DONE]"


def test_cancel_emits_no_frame_at_all():
    """取消路径**一帧都不发**（含 `[DONE]`）—— ：旧实现真发得出去一帧。"""
    events = []
    up = _FakeUpstream(["a", "b"], events)

    async def main():
        agen = sse.sse_stream(lambda: up, endpoint="t", extract=lambda x: x or None)
        await agen.asend(None)
        got = []
        try:
            while True:
                got.append(await agen.athrow(asyncio.CancelledError()))
        except BaseException:                               # noqa: BLE001
            return got

    assert asyncio.run(main()) == []


def test_generator_close_counts_as_cancel_too(monkeypatch):
    """`GeneratorExit` 那一支（Starlette 2.4 走的是它）**也要记到**。

    `GeneratorExit` **不是** `Exception` 的子类 ⇒ 它进不了 `except Exception`，
       只有 `finally` 收得住。这就是 `outcome` **默认按"取消"算**的原因。
    """
    events, counted = [], []
    monkeypatch.setattr(sse, "track_stream_cancel", lambda ep: counted.append(ep))
    up = _FakeUpstream(["a"], events)

    async def main():
        agen = sse.sse_stream(lambda: up, endpoint="t", extract=lambda x: x or None)
        await agen.asend(None)
        await agen.aclose()

    asyncio.run(main())
    assert counted == ["t"]
    assert up.closed is True


# ==================== 5 · 异常路径 ====================

def test_upstream_error_emits_error_frame_then_done():
    events = []
    up = _FakeUpstream([], events, exc=RuntimeError("上游炸了"))

    async def main():
        return await _drain(sse.sse_stream(lambda: up, endpoint="t"))

    frames, exc = asyncio.run(main())
    assert exc is None, "上游异常不该再抛出去 —— 那会变成'流自然结束'（前端看不到任何错误信号）"
    assert _payload(frames[0]) == {"error": "上游炸了"}
    assert frames[1] == sse.DONE_FRAME
    assert up.closed is True
    assert "t" not in events            # 异常路径**不算取消**


def test_error_frame_encoding_follows_ensure_ascii():
    events = []
    up = _FakeUpstream([], events, exc=RuntimeError("炸了"))

    async def main():
        return await _drain(sse.sse_stream(lambda: up, endpoint="t", ensure_ascii=True))

    frames, _ = asyncio.run(main())
    assert frames[0] == 'data: {"error": "\\u70b8\\u4e86"}\n\n'


def test_on_error_override_replaces_the_tail():
    """`/rag/stream_search` 靠它把旧行为**逐字留住**：错误路径**只有 error 帧、没有 `[DONE]`**。

    理由不是保守 —— **`[DONE]` 会被读成"正常收尾"**，RAG 今天靠"没有 `[DONE]`"分辨出错。
    """
    events = []
    up = _FakeUpstream([], events, exc=RuntimeError("boom"))

    async def _err(exc, collected):
        assert isinstance(exc, RuntimeError)
        assert collected == []
        yield sse.sse_frame({"error": str(exc)}, ensure_ascii=True)

    async def main():
        return await _drain(sse.sse_stream(lambda: up, endpoint="t", on_error=_err))

    frames, exc = asyncio.run(main())
    assert exc is None
    assert frames == ['data: {"error": "boom"}\n\n']
    assert sse.DONE_FRAME not in frames


def test_open_upstream_failure_is_also_reported_as_an_error_frame():
    """`open_upstream` 本身抛（配置错 / 图没编译）**也要变成 error 帧**，不是 500。

    它就是"必须在 `try` **里面**打开上游"的原因。
    """
    def _boom():
        raise RuntimeError("建流就炸了")

    async def main():
        return await _drain(sse.sse_stream(_boom, endpoint="t"))

    frames, exc = asyncio.run(main())
    assert exc is None
    assert _payload(frames[0]) == {"error": "建流就炸了"}
    assert frames[1] == sse.DONE_FRAME
