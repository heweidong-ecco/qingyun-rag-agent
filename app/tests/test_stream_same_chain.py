"""· **流式检索与同步检索【同一条链】**。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from routing.schemas import QuestionRequest

#: 向量那一路（形状 = `db.search_similar` 的行：`(id, content, source, similarity)`）
VECTOR_DOCS = [
    (11, "甲文（两路都命中）", "a.md", 0.90),
    (22, "乙文（只有向量命中）", "b.md", 0.80),
]
#: BM25 那一路（形状 = `bm25_index.bm25_search` 的行：`(id, content, source, score)`）
BM25_DOCS = [
    (33, "丙文（只有 BM25 命中）", "c.md", 5.00),
    (11, "甲文（两路都命中）", "a.md", 4.00),
]

#: 只有 BM25 找得到的那一条 —— 第 2 条用例就盯它。
#: ⚠️ 它**不是**随手编的样例：裸向量那条路上它**根本不出现**（见 `VECTOR_DOCS`）。
BM25_ONLY = "丙文（只有 BM25 命中）"


class _FakeStreamLLM:
    """流式桩：吐两个正文块。形状照 `test_frontend_contract` 那份（末帧另加 usage 也在那边）。"""

    model_name = "fake-answer-model"

    async def astream(self, messages):
        yield AIMessageChunk(content="答")
        yield AIMessageChunk(content="案")

    def invoke(self, messages):        # 非流式那条路（`/rag/search` 的答案生成）走它
        class _R:
            content = "答案"
        return _R()


@pytest.fixture
def same_chain(monkeypatch):
    """把**检索的输入侧**全换成固定的两路结果 —— 两条路共用同一份。

    ⚠️ 六个缝全都在 `rag_pipeline` 的模块级 `from X import Y` 上（见它的文件头）
       补丁目标是 `rag_pipeline.<name>`，⛔ 不是 `X.<name>`。
    ⚠️ **替身只负责"接得住任何调用"**（`**kw`），⛔ 不跟着真签名逐参数改
       —— 那是本仓  记着的病：真签名每加一个参数，这批用例就再烂一次。
    """
    import rag.rag_pipeline as rp
    import routing.api_v1_rag as rag_mod
    import billing.token_tracker as tt

    async def fake_similar(_embedding, top_k=3, *, user_id):
        return list(VECTOR_DOCS)

    async def fake_bm25(_query, top_k=10, *, user_id):
        return list(BM25_DOCS)

    monkeypatch.setattr(rp, "get_embedding", lambda text, model=None, **k: [0.0] * 1024)
    monkeypatch.setattr(rp, "search_similar_async", fake_similar)
    monkeypatch.setattr(rp, "bm25_search_async", fake_bm25)
    monkeypatch.setattr(rp, "rewrite_query", lambda q, h=None, *, user_name, **kw: q)
    monkeypatch.setattr(rp, "expand_query", lambda q, num_variants=3, *, user_name, **kw: [q])
    monkeypatch.setattr(rp, "make_llm", lambda *a, **k: _FakeStreamLLM())

    # 端点侧：只短路"与检索无关"的那些（闸 / 历史 / 落库 / 流式上游）
    monkeypatch.setattr(rag_mod, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "get_chat_history", lambda *a, **k: [])
    monkeypatch.setattr(rag_mod, "persist_turn", lambda *a, **k: None)
    monkeypatch.setattr(rag_mod, "get_llm_stream", lambda: _FakeStreamLLM())
    # 记账真跑、只拦"往库里写"那一步（与 `test_frontend_contract` 同款理由）
    monkeypatch.setattr(tt, "record_usage", lambda *a, **k: None)

    return SimpleNamespace(rag=rag_mod)


def _stream_sources(rag_mod, **kw) -> list:
    """跑一遍 `/rag/stream_search`，只取 `sources` 帧。"""
    req = QuestionRequest(question="你好", citations=True, **kw)
    resp = asyncio.run(rag_mod.stream_search(req, thread_id="t-chain", user_name="alice"))

    async def _drain():
        return [f async for f in resp.body_iterator]

    frames = asyncio.run(_drain())
    for f in frames:
        if not f.startswith("data: "):
            continue
        body = f[len("data: "):].strip()
        if body == "[DONE]":
            continue
        payload = json.loads(body)      # ⚠️ 帧体是 JSON，必须解码（之后中文是**原样**的）
        if isinstance(payload, dict) and "sources" in payload:
            return payload["sources"]
    return []


def _sync_sources(rag_mod) -> list:
    """跑一遍 `/rag/search` 的**默认模式**（`accurate_norerank`），取它的 `sources`。"""
    req = QuestionRequest(question="你好", citations=True, generate_answer=True)
    result = asyncio.run(rag_mod.unified_search(
        req, mode="accurate_norerank", thread_id="t-chain", user_name="alice",
    ))
    return result.get("sources") or []


def _identity(sources):
    """一条来源的**身份** = `(id, content, source)`。

    ⚠️ **不比 `index`**：它是**名次**、不是身份（两边各自从 1 开始编）。
    ⚠️ **不比 `similarity`**：BM25 独中的文档**诚实地没有分**（`None`）——
       那不是"不一致"，是那一路本来就没有。
    """
    return {(s.get("id"), s["content"], s.get("source")) for s in sources}


def test_两条路的_sources_是同一个集合(same_chain):
    """🔴 ** 的判据本身**（§二 的原话）。

    同一句问题、同一份检索输入 ⇒ `/rag/search`（默认模式）与 `/rag/stream_search`
    给出的来源**必须是同一批文档**。

    ⚠️ **防空跑**：两边都得非空 —— 空集合与空集合"相等"，那什么也没测到。
    """
    rag_mod = same_chain.rag
    stream_sources = _stream_sources(rag_mod)
    sync_sources = _sync_sources(rag_mod)

    assert stream_sources, "流式那边一条来源都没有 ⇒ 本断言在测空集合（空跑）"
    assert sync_sources, "同步那边一条来源都没有 ⇒ 本断言在测空集合（空跑）"

    a, b = _identity(stream_sources), _identity(sync_sources)
    assert a == b, (
        "🔴 同一句问题，两条路给出的来源【不是同一批文档】：\n"
        f"  只在流式有：{sorted(a - b)}\n  只在同步有：{sorted(b - a)}\n"
        "  ⇒ 这正是  那条欠账（§二 裁「接上同一条链」）。"
    )


def test_流式那条路真的会用只有_BM25_找得到的那一条(same_chain):
    """⚠️ **上一条单独是不够的** —— 它比的是"两边一致"，
    两条路**都退化成裸向量**时它**照样绿**。

    本用例给的那份输入里，`丙文` **只有 BM25 那一路**能找回来（向量那份没有它）。
    ⇒ 它出现在流式的 `sources` 里，就在说：**流式这条路上确实跑过 BM25 那一路**。

    ⚠️ 与 `test_rag_search.py` 的 `mode→pipeline` 用例**不重复**：
       那一条测的是**同步端点**的分派，本一条测的是**流式端点**有没有接上同一条链。
    """
    sources = _stream_sources(same_chain.rag)
    contents = {s["content"] for s in sources}
    assert contents, "一条来源都没有 ⇒ 本断言在测空集合（空跑）"
    assert BM25_ONLY in contents, (
        f"流式的 sources 里没有 `{BM25_ONLY}` —— 而它**只有 BM25 那一路**找得到\n"
        "  ⇒ 流式这条路上**没有跑 BM25**（多半是又退回裸向量了， 那条欠账复发）。\n"
        f"  实际拿到的：{sorted(contents)}"
    )
