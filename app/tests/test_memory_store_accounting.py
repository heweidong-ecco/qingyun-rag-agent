"""mem0 那一路的**记账归属**（2026-10-11）。

## 改前是什么样

`app/agent/memory_store.py` 让 mem0 用**它自己的** LLM 与 embedder
⇒ 那条路的 token **一个都没记**。实测：`/agent/memory/add` 回 200，
而**LLM 用量、embedding 用量都没动**、`token_usage_logs` 里**0 行**。

**影响不止那两个端点** —— `advanced_chat` / `mcp_chat` 的 `inject_memories_to_prompt`
**每一轮**都调一次 mem0 检索。

## 改后是什么样

mem0 的 LLM 与 embedder **都换成 `langchain` provider**，塞进去的是**我们自己的**对象：
· embedder ⇒ 走 `rag.embedding_client.get_embedding`（与 RAG 侧同一份实现 + 同一份缓存 + 同一份记账）
· LLM ⇒ 我们 `make_llm` 造的那个 + 一个 `on_llm_end` 回调记账

**「算谁的」靠一个 `ContextVar`** —— mem0 内部调它们时**不把 user 传进来**。
那条路走得通是**实测过的**：mem0 **就在调用方的执行上下文里**调它们，不是另起线程。

## 本文件钉什么

| # | 钉什么 | 不钉会怎样 |
|---|---|---|
| 1 | embedder 拿到的是**当前用户** | 又回到"记在假人头上" |
| 2 | LLM 回调把用量记成 `memory_extract` + 当前用户 | mem0 的 LLM 白花 |
| 3 | **两个入口函数都设了/复原了** ContextVar | 不设 ⇒ 全记 `system`；不复原 ⇒ **并发串账** |
| 4 | 端点里**不算库**的那条路不编数 | 印没有数据源的数 |

本文件**不连库、不连网、不调真模型**。
"""
import pytest


# ═══════════════════════════════════════════════════════════════════
# 一 · `_user_of`：从 `user_id` 里取出用户
# ═══════════════════════════════════════════════════════════════════

def test_user_of_splits_on_the_first_colon():
    from agent.memory_store import _user_of

    assert _user_of("alice:default") == "alice"
    assert _user_of("u-abc:工作") == "u-abc"
    # `user_name` 自己带冒号时**只切一刀** —— `split(":")[0]` 在这里也对，
    #    但 `split(":", 1)` 把意图写死了（名字里带冒号的那一半不许被吃掉）
    assert _user_of("a:b:c") == "a"


def test_user_of_falls_back_to_system():
    from agent.memory_store import _user_of

    assert _user_of("") == "system"
    assert _user_of(None) == "system"
    assert _user_of(":default") == "system", "用户名为空 ⇒ 回落，⛔ 不是拿空串当用户名"


# ═══════════════════════════════════════════════════════════════════
# 二 · embedder：走我们自己的 `get_embedding`，带当前用户
# ═══════════════════════════════════════════════════════════════════

def test_embedder_passes_the_current_user(monkeypatch):
    import rag.embedding_client as ec
    from agent import memory_store as ms

    seen = []
    monkeypatch.setattr(ec, "get_embedding", lambda text, **kw: seen.append((text, kw)) or [0.0, 0.0])

    tok = ms._CURRENT_USER.set("alice")
    try:
        ms.RecordingEmbeddings().embed_query("他住在哪")
    finally:
        ms._CURRENT_USER.reset(tok)

    assert seen == [("他住在哪", {"user_name": "alice"})], (
        f"embedder 没把当前用户传下去：{seen} ⇒ 那一笔又会记在 system 头上"
    )


def test_embedder_handles_documents_too(monkeypatch):
    """mem0 存记忆时走的是 `embed_documents`（**另一条路**）—— 一起钉住。"""
    import rag.embedding_client as ec
    from agent import memory_store as ms

    seen = []
    monkeypatch.setattr(ec, "get_embedding", lambda text, **kw: seen.append(kw.get("user_name")) or [0.0])

    tok = ms._CURRENT_USER.set("bob")
    try:
        ms.RecordingEmbeddings().embed_documents(["a", "b"])
    finally:
        ms._CURRENT_USER.reset(tok)

    assert seen == ["bob", "bob"]


def test_embedder_defaults_to_system_without_a_user(monkeypatch):
    """没人设 ⇒ `"system"` —— 那是**默认值存在的理由**（不是漏传）。"""
    import rag.embedding_client as ec
    from agent import memory_store as ms

    seen = []
    monkeypatch.setattr(ec, "get_embedding", lambda text, **kw: seen.append(kw.get("user_name")) or [0.0])
    ms.RecordingEmbeddings().embed_query("x")

    assert seen == ["system"]


# ═══════════════════════════════════════════════════════════════════
# 三 · LLM 回调：把 mem0 抽事实那次记到账上
# ═══════════════════════════════════════════════════════════════════

def _resp(prompt=None, completion=None):
    usage = {}
    if prompt is not None:
        usage["prompt_tokens"] = prompt
    if completion is not None:
        usage["completion_tokens"] = completion
    return type("R", (), {"llm_output": {"token_usage": usage}})()


def test_llm_callback_records_usage(monkeypatch):
    """**本条是改前完全空白的那一块**：mem0 内部那次 LLM 的 token。"""
    from agent import memory_store as ms

    got = []
    monkeypatch.setattr(ms, "record_usage", lambda **kw: got.append(kw))

    tok = ms._CURRENT_USER.set("alice")
    try:
        ms._UsageRecorder().on_llm_end(_resp(prompt=300, completion=80))
    finally:
        ms._CURRENT_USER.reset(tok)

    assert len(got) == 1, "mem0 那次 LLM 没被记账"
    kw = got[0]
    assert kw["user_name"] == "alice", f"记到了 {kw['user_name']!r} 头上"
    assert kw["purpose"] == "memory_extract"
    assert (kw["prompt_tokens"], kw["completion_tokens"]) == (300, 80)


def test_llm_callback_does_not_invent_numbers(monkeypatch):
    """端点**没回用量** ⇒ 一个数都不许编（本仓：「不许印没有数据源的数」）。"""
    from agent import memory_store as ms

    got = []
    monkeypatch.setattr(ms, "record_usage", lambda **kw: got.append(kw))

    ms._UsageRecorder().on_llm_end(_resp())            # 空 usage
    ms._UsageRecorder().on_llm_end(type("R", (), {"llm_output": None})())

    assert got == [], f"端点没回用量却记了账：{got}"


def test_llm_callback_never_breaks_the_memory_call(monkeypatch):
    """**记账失败不许把记忆功能带崩** —— 回调里抛异常会冒到 mem0 那边去。"""
    from agent import memory_store as ms

    def boom(**kw):
        raise RuntimeError("记账后端挂了")

    monkeypatch.setattr(ms, "record_usage", boom)
    ms._UsageRecorder().on_llm_end(_resp(prompt=1, completion=1))   # 不许抛


# ═══════════════════════════════════════════════════════════════════
# 四 · 两个入口函数：设了 + **复原了**
# ═══════════════════════════════════════════════════════════════════

class _FakeMem0:
    """假的 mem0 客户端：只记下**被调到那一刻** ContextVar 是什么。"""

    def __init__(self):
        self.seen_add, self.seen_search = [], []

    def add(self, content, user_id=None):
        from agent import memory_store as ms
        self.seen_add.append(ms._CURRENT_USER.get())

    def search(self, query, filters=None, top_k=None):
        from agent import memory_store as ms
        self.seen_search.append(ms._CURRENT_USER.get())
        return {"results": []}


def test_both_entry_points_set_the_user(monkeypatch):
    """**两个入口都设** —— 少一个，那条路就**静默**回到 `system`。"""
    from agent import memory_store as ms

    fake = _FakeMem0()
    monkeypatch.setattr(ms, "mem0_client", fake)

    ms.add_user_memory("alice:default", "我叫张三")
    ms.search_user_memory("bob:工作", "他住哪")

    assert fake.seen_add == ["alice"], f"add 那条没设对：{fake.seen_add}"
    assert fake.seen_search == ["bob"], f"search 那条没设对：{fake.seen_search}"


@pytest.mark.parametrize("call", [
    lambda ms: ms.add_user_memory("alice:default", "x"),
    lambda ms: ms.search_user_memory("alice:default", "y"),
])
def test_the_context_is_restored_afterwards(monkeypatch, call):
    """**用完必须复原** —— 不复原的话，**下一个请求**会顶着别人的名字记账
    （并发下尤其致命，而它**不报错**）。"""
    from agent import memory_store as ms

    monkeypatch.setattr(ms, "mem0_client", _FakeMem0())

    before = ms._CURRENT_USER.get()
    call(ms)
    assert ms._CURRENT_USER.get() == before, (
        f"调完之后 ContextVar 还停在 {ms._CURRENT_USER.get()!r} ⇒ 会把下一个请求记到这个人头上"
    )


def test_the_context_is_restored_even_when_mem0_raises(monkeypatch):
    """mem0 抛了也要复原 —— 否则**一次失败会把后续请求全污染**。"""
    from agent import memory_store as ms

    class Boom(_FakeMem0):
        def add(self, content, user_id=None):
            raise RuntimeError("mem0 挂了")

    monkeypatch.setattr(ms, "mem0_client", Boom())
    before = ms._CURRENT_USER.get()
    with pytest.raises(RuntimeError):
        ms.add_user_memory("alice:default", "x")
    assert ms._CURRENT_USER.get() == before, "抛异常之后 ContextVar 没复原"


# ═══════════════════════════════════════════════════════════════════
# 五 · 接线：mem0 真的换成了我们这两个对象
# ═══════════════════════════════════════════════════════════════════

def test_mem0_is_wired_to_our_adapters():
    """**结构判据**：mem0 的 llm / embedder **必须**是我们塞进去的那两个。

    少了这条，上面那些单测**全绿也没用** —— 它们测的是适配器本身，
    而"mem0 到底用没用它们"是另一件事（本仓「写了但没接上」栽过多次）。
    """
    from agent import memory_store as ms

    assert type(ms.mem0_client.llm).__name__ == "LangchainLLM", (
        "mem0 的 LLM 不是 langchain 适配器 ⇒ 它又用回自己那个**不记账**的了"
    )
    assert type(ms.mem0_client.embedding_model).__name__ == "LangchainEmbedding", (
        "mem0 的 embedder 不是 langchain 适配器 ⇒ 那条路的 token 又不记账了"
    )
    assert isinstance(ms.mem0_client.llm.langchain_model.callbacks[0], ms._UsageRecorder), (
        "我们的记账回调没挂到那个模型上 ⇒ LLM 那半等于没修"
    )
    assert isinstance(ms.mem0_client.embedding_model.langchain_model, ms.RecordingEmbeddings)
