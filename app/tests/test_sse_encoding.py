"""· **SSE 的编码口径全仓统一成一个**。
"""
import asyncio
import json
import pathlib
import re

import pytest
from langchain_core.messages import AIMessageChunk

from routing.schemas import QuestionRequest

ROUTING_DIR = pathlib.Path(__file__).resolve().parents[1] / "routing"

#: `ensure_ascii=<字面量>` —— ⚠️ **只认字面量**：真值算不出来的（比如变量）会被**放过**，
#:    这是本类扫描的已知盲区，跟 `test_sse_layer.py` 那条一样，**认不出来的一律不报**。
_ASSIGN_RE = re.compile(r"ensure_ascii\s*=\s*(\w+)")


def test_路由层的_ensure_ascii_一律是_False():
    """🔴  的**结构判据**：`app/routing/` 下的 `.py` 里，凡是显式传 `ensure_ascii=` 的，一律 `False`。

    ⚠️ **防空跑**：① 必须真扫到文件；② 必须真扫到**至少一处** `ensure_ascii=` ——
       全仓一处都不传时，"全为 False"是**空集合上的真**（本仓那条老账：
       空集合断言恒为真 ⇒ 必须先证尺子有读数）。
    """
    files = sorted(ROUTING_DIR.glob("*.py"))
    assert files, f"`{ROUTING_DIR}` 下一个 .py 都没有 ⇒ 本断言在测空集合"

    hits = []
    for f in files:
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = _ASSIGN_RE.search(line)
            if m:
                hits.append((f.name, lineno, m.group(1)))

    assert len(hits) >= 6, (
        f"只扫到 {len(hits)} 处 `ensure_ascii=` —— 判据**在测一个空集合**（或扫描正则失效了），"
        "⛔ 别当成通过"
    )
    # ⚠️ **只拦字面量 `True`** —— `sse.py` 里那三处是**透传自己的形参**（`ensure_ascii=ensure_ascii`），
    #    不是"选了某个值"；而"形参的默认值是多少"由
    #    `test_sse_layer.py::test_frame_default_keeps_chinese_readable` 看着。
    bad = [(n, i, v) for n, i, v in hits if v == "True"]
    assert not bad, (
        "🔴 有调用点没有跟全仓统一（：一律 `False` = 中文原样）：\n"
        + "\n".join(f"  {n}:{i}  ensure_ascii={v}" for n, i, v in bad)
    )


class _FakeStreamLLM:
    """吐两个**中文**正文块 —— 本文件要看的正是它们出帧时的编码。"""

    model_name = "fake-answer-model"

    async def astream(self, messages):
        yield AIMessageChunk(content="你好")
        yield AIMessageChunk(content="世界")


@pytest.fixture
def rag_stream(monkeypatch):
    """把 `/rag/stream_search` 的外部依赖短路（只留"发帧"这一件事在被测路径上）。"""
    import routing.api_v1_rag as rag_mod
    import billing.token_tracker as tt

    async def _no_retrieval(*_a, **_k):
        return []
    monkeypatch.setattr(rag_mod, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "get_chat_history", lambda *a, **k: [])
    monkeypatch.setattr(rag_mod, "persist_turn", lambda *a, **k: None)
    monkeypatch.setattr(rag_mod, "get_llm_stream", lambda: _FakeStreamLLM())
    monkeypatch.setattr(rag_mod, "_stream_retrieval", _no_retrieval)
    monkeypatch.setattr(tt, "record_usage", lambda *a, **k: None)
    return rag_mod


def test_流式端点吐出来的中文是原样的(rag_stream):
    """🔴  的**行为判据**：拿**端点真的吐出来的原始帧文本**看编码 —— ⛔ 不是看骨架的默认值。

    ⚠️ **必须看原始文本**：`json.loads` 之后两种编码**长得一模一样**
       （那正是"功能无感"的判据，也正是这条要用**字节**来测的原因）。
    """
    req = QuestionRequest(question="你好")

    async def _go():
        resp = await rag_stream.stream_search(req, thread_id="t-enc", user_name="alice")
        return [f async for f in resp.body_iterator]

    frames = asyncio.run(_go())
    raw = "".join(frames)

    assert frames, "一帧都没有 ⇒ 本断言在测空集合（空跑）"
    assert "你好" in raw, (
        "🔴 正文里的中文**没有原样出现**在帧文本里 —— 多半是被 `\\uXXXX` 转义了。\n"
        "  ⇒  裁的是**统一成 `False`**。\n"
        f"  实际帧（前 300 字）：{raw[:300]!r}"
    )
    assert "\\u4f60" not in raw, (
        "🔴 帧里出现了 `你` 的转义形式（`\\u4f60`）⇒ 这个端点还在用 `ensure_ascii=True`。\n"
        "  ⇒ 见 `app/routing/specs/sse.md` 里那条已改写的口径。"
    )
    # 正面控：帧是**合法 JSON**（别为了"中文原样"把编码弄坏）
    for f in frames:
        if f.startswith("data: ") and f.strip() != "data: [DONE]":
            json.loads(f[len("data: "):].strip())
