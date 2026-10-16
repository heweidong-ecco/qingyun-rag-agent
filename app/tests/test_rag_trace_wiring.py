"""检索链必须【建轨迹】**并且【收尾】** —— 演示路径 `/chat` 走的就是这条链。

## 为什么需要它（§六·1）

 建 `/trace` 页时实测：全仓只有 **2 处**调 `start_trace`，**都在 Agent 链**
（`mcp_agent_chat` / `mcp_agent_chat_stream`）。
⇒ 演示路径（`/chat` → `POST /api/v1/rag/stream_search`）**从没建过轨迹**
⇒ 打开 `/trace`，上半页**是空的**（**不是坏，是"从没建过"**）。

当时**有意没做**（「改后端链」与「建一个页面」是两件事，且会把判据面从"读"扩到"写"）⇒ 登记为债。
本文件钉住它**现在建了**。

## 判据只钉三件（别往"记了几个工具"上扩）

1. **建了**（`start_trace`）
2. **收尾了**（`finish_trace`）—— 只建不收 ⇒ 页面上是一条**没有耗时**的记录，
   比没有还容易误读（"它跑过、但用时未知"）。
3. **写在【这个人 × 这个线程】下** —— 键写错了 ⇒ 本人反而看不到自己的。

检索链**本来就不调工具** ⇒ 轨迹里 `tool_calls` 为空**是正确的**，**别把它当失败**。
"""
import asyncio

import pytest
from langchain_core.messages import AIMessageChunk

import routing.api_v1_rag as rag_mod
import tools.tool_visualizer as tv
from core.exceptions import AppException

USER = "trace-probe-user"
THREAD = "trace-probe-thread"

# 本文件要钉的那个数（别抄成常量断言 —— 别处也会加调用点，那时该红的是**别处**）：
#    `grep -rn '^[[:space:]]*start_trace(' app/ --include='*.py' | grep -v '^app/test_'`


class _FakeStreamLLM:
    """够用的假流式模型：吐两块正文 + 一帧 `content=''` 但**带 usage**（本仓 provider 的真形状）。"""

    def __init__(self, texts=("你", "好")):
        self._texts = texts

    async def astream(self, messages):
        for t in self._texts:
            yield AIMessageChunk(content=t)
        yield AIMessageChunk(
            content="",
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )


@pytest.fixture
def clean_traces():
    """`_traces` 是**进程级字典** ⇒ 用例之间必须清干净，否则互为污染。

    同 `app/tests/test_trace_isolation.py` 的做法（理由一致）。
    """
    tv._traces.clear()
    yield
    tv._traces.clear()


def _stub_deps(monkeypatch, llm):
    """把 `stream_search` 的外部依赖**全部**短路 —— 本文件只问"轨迹建没建"，不碰 DB / 模型 / Redis。"""
    import core.cache as cache_mod
    import core.db as db_mod
    from conftest import FakeRedis

    monkeypatch.setattr(cache_mod, "redis_client", FakeRedis())
    monkeypatch.setattr(rag_mod, "get_chat_history", lambda *a, **k: [])
    monkeypatch.setattr(rag_mod, "check_session_token_budget", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "circuit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(rag_mod, "get_embedding", lambda text, **k: [0.0] * 8)
    monkeypatch.setattr(rag_mod, "search_similar", lambda *a, **k: [])
    monkeypatch.setattr(rag_mod, "get_llm_stream", lambda: llm)
    monkeypatch.setattr(rag_mod, "record_from_response", lambda *a, **k: True)
    # 第二个 `get_db`：`search_similar` 走的是 **`db.py`** 的模块全局
    #    （乙段把检索搬进共享层）⇒ 只 patch `rag_mod` 那个**够不着它**。
    #    那次的完整教训
    monkeypatch.setattr(db_mod, "get_db", lambda: None)


def _drive(monkeypatch, llm):
    """调端点并把流**拉完**。

    **必须拉完** —— `finish_trace` 在 `_complete` 里，而 `_complete` **只有流正常跑完才进**
       ⇒ 只调端点不消费，验得到 `start_trace`、**验不到收尾**（那会让本文件只剩一半判据）。
    """
    from routing.schemas import QuestionRequest

    _stub_deps(monkeypatch, llm)

    async def go():
        resp = await rag_mod.stream_search(
            QuestionRequest(question="轨迹建了吗"),
            thread_id=THREAD,
            user_name=USER,
        )
        return [f async for f in resp.body_iterator]

    return asyncio.run(go())


# ══════════════════════════════════════════════════════════════════
# ① 建了 ② 收尾了 ③ 键写对了
# ══════════════════════════════════════════════════════════════════

def test_stream_search_starts_and_finishes_a_trace(clean_traces, monkeypatch):
    frames = _drive(monkeypatch, _FakeStreamLLM())
    # 先证"尺子有读数"：一个帧都没吐 ⇒ 夹具坏了，下面两条断言**不算数**。
    assert frames, "流一个帧都没吐出来 —— 夹具坏了，后面的断言不算数"

    trace = tv.get_trace(USER, THREAD)
    assert trace is not None, (
        "检索链没建轨迹 ⇒ `/trace` 上半页在演示时还是空的。"
        "  判据：grep -rn '^[[:space:]]*start_trace(' app/ --include='*.py' | grep -v '^app/test_'"
    )
    assert trace["user_query"] == "轨迹建了吗", f"轨迹记的不是本次提问：{trace['user_query']!r}"
    assert trace["duration_ms"] is not None, (
        "轨迹【开了没收】⇒ 页面上是一条没有耗时的记录，比没有还容易误读。"
        "  ⇒ `finish_trace` 也要在 `_complete` 里调（要的是 start + finish 两处）"
    )


def test_trace_is_keyed_by_user_and_thread(clean_traces, monkeypatch):
    """写在【这个人 × 这个线程】下 —— 用别人的身份读不到（证明键没写错）。"""
    _drive(monkeypatch, _FakeStreamLLM())
    # **先证"尺子有读数"** —— 少了这一行，下面两条断言在"**一条轨迹都没建**"时
    #    **照样会通过**（`get_trace` 对不存在的键也回 `None`）⇒ 那就是**空跑假通过**
    #    （本仓：拿空集合断言会一路绿着放行）。本文件第一版**真的**是那样绿的。
    assert tv.get_trace(USER, THREAD) is not None, "夹具没建出轨迹 ⇒ 下面两条断言不算数"
    assert tv.get_trace("someone-else", THREAD) is None, (
        "别人读到了 ⇒ 身份或线程没传对（`tool_visualizer` 的键是 `session_key(user_name, thread_id)`）"
    )
    assert tv.get_trace(USER, "some-other-thread") is None, "换线程还能读到 ⇒ 线程没传对"


def test_rejected_request_still_leaves_a_trace(clean_traces, monkeypatch):
    """**超限被拒也要留痕** —— 这正是 `start_trace` 排在 B8 **之前**的理由。

    先例（同一句话）⇒ `app/routing/api_v1_agent.py:1646`：
    「放在 `start_trace` **之后**：超限被拒时，追踪里仍留得下这次尝试的痕迹。」
    别"顺手"把 `start_trace` 挪到两道闸之后 —— 那会让**被拦下的请求**在页面上**消失**，
    而"被拦下的请求"恰恰是排查时最想看到的那一类。
    """
    from routing.schemas import QuestionRequest

    _stub_deps(monkeypatch, _FakeStreamLLM())
    monkeypatch.setattr(rag_mod, "check_session_token_budget", lambda *a, **k: (False, "会话额度用完了"))

    async def go():
        with pytest.raises(AppException):
            await rag_mod.stream_search(
                QuestionRequest(question="会被拦下的一次"),
                thread_id=THREAD,
                user_name=USER,
            )

    asyncio.run(go())

    trace = tv.get_trace(USER, THREAD)
    assert trace is not None, (
        "被拦下的请求没留痕 ⇒ `start_trace` 被挪到闸后面了（本条的靶子就是那个顺序）"
    )
    assert trace["user_query"] == "会被拦下的一次"
