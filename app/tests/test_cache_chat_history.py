"""`app/core/cache.py` 的 `chat_history` 落痕口径（N1）。

**本文件存在的理由**： 的全部意义是「**读的人能分辨**这一轮是**说完了**还是**被打断了**」。
判据只有一个 —— 存进去的那条**带不带 `status`、带的是什么**。
⇒ 这条判据**不钉在存储层**（唯一能同时看见三条出口的地方），就只能散到 6 条端点去证，
而那时红的是端点，**看不出是存储层漏了**。

**不碰真 redis**：`cache.redis_client` 换成 `conftest.FakeRedis`（`fake_redis` 夹具）。
   本机  复用**长期容器 `redis-rag`**，真写会攒 key 且跨用例互相污染。
"""
import json

import pytest

import core.cache as cache
from access.session_key import session_key

#  契约 C 起，历史键里多了 `thread_id`。本文件**全部**读写都走这一个名字 ——
#    不许在写入处写一个字面量、读取处另写一个：那两处一旦不一致，
#    `assert history(...) == []` 那类断言会**静默变绿**（读了个空桶，而不是"确实没写"）。
THREAD = "t1"


@pytest.fixture
def fake_store(monkeypatch, fake_redis):
    """把**叶子** `cache.redis_client` 换掉 —— 一个点盖住全部 6 条端点。"""
    monkeypatch.setattr(cache, "redis_client", fake_redis)
    return fake_redis


# ==================== ① `status` 是【必填】的关键字参数 ====================

def test_append_chat_history_writes_status(fake_store):
    cache.append_chat_history("u1", "assistant", "半截", thread_id=THREAD, status="cancelled")
    assert fake_store.history("u1", THREAD) == [
        {"role": "assistant", "content": "半截", "status": "cancelled"},
    ]


def test_append_chat_history_requires_status(fake_store):
    """漏传 `status` 必须**当场炸**，不许有默认值。

    有默认值 ⇒ 「忘了传」**静默**变成 `done` ⇒ 被打断的一轮会被读成"说完了"
    —— 那正是本 DEC 要防的那类假信号。

     契约 C 起**必须显式传 `thread_id`**：不传的话本用例会因为
       "缺 `thread_id`"而红 —— **照样是 `TypeError`，却不再是在量 `status`**
       （本仓 2026-10-05 收的【反证检验】：把 `status` 的必填拿掉，本用例还会红吗？）。
    """
    with pytest.raises(TypeError):
        cache.append_chat_history("u1", "user", "问题", thread_id=THREAD)


# ==================== ② 老条目（无 `status`）读回 `done` ====================

def test_get_chat_history_defaults_legacy_entries_to_done(fake_store):
    """ 之前写下的条目**没有 `status`** ⇒ 读回补 `"done"`。

    理由：那些条目**只可能是完整答案**（之前根本不存半截）。
    本条同时钉住 `FakeRedis.lrange` 的 **Redis 语义**（`-10/-1` **含**末条）——
       假 redis 若照抄成 Python 切片，**最后一条会凭空消失**，这里立刻红。
    """
    #  契约 C：写入端换键了，这条**裸写**必须跟着换 ——
    #    不改就是往一个没人读的键里塞数据（用例红得莫名其妙，或者干脆空转）。
    fake_store.rpush(
        f"chat_history:{session_key('u1', THREAD)}",
        json.dumps({"role": "user", "content": "老问题"}),   # ← 老格式：**没有 status 字段**
    )
    cache.append_chat_history("u1", "assistant", "半截", thread_id=THREAD, status="error")

    got = cache.get_chat_history("u1", thread_id=THREAD)

    assert [e["status"] for e in got] == ["done", "error"]
    assert got[0]["content"] == "老问题"


# ==================== ③ `persist_turn` —— 三条出口共用的一段 ====================

def test_persist_turn_marks_non_done_exit(fake_store):
    """非 `done` ⇒ **成对写** + 助手那条带 `INTERRUPTED_SUFFIX`。"""
    cache.persist_turn("u1", "问题", "  半截答案  ", thread_id=THREAD, status="error")
    assert fake_store.history("u1", THREAD) == [
        {"role": "user", "content": "问题", "status": "error"},
        {"role": "assistant", "content": "半截答案" + cache.INTERRUPTED_SUFFIX, "status": "error"},
    ]


def test_persist_turn_done_keeps_answer_verbatim(fake_store):
    """`done` ⇒ 答案**原样**存（不 strip、不带标记）。

    带了标记 ⇒ 下一轮会把**好答案**当成"被截断"的。
    """
    cache.persist_turn("u1", "问题", " 完整答案 ", thread_id=THREAD, status="done")
    assert fake_store.history("u1", THREAD) == [
        {"role": "user", "content": "问题", "status": "done"},
        {"role": "assistant", "content": " 完整答案 ", "status": "done"},
    ]


@pytest.mark.parametrize("status", ["done", "cancelled", "error"])
def test_persist_turn_skips_empty_answer(fake_store, status):
    """空答案 ⇒ **一条都不写**（**连提问也不写**）。

    只写提问 ⇒ 历史里留下一条**没有来由**的助手消息（决策 4）。
    """
    cache.persist_turn("u1", "问题", "", thread_id=THREAD, status=status)
    cache.persist_turn("u1", "问题", "   \n", thread_id=THREAD, status=status)
    assert fake_store.history("u1", THREAD) == []


# ==================== ④ 契约 C：历史按 (user, thread) 分 ====================

def test_the_three_functions_require_thread_id_as_a_keyword(fake_store):
    """`thread_id` **必填、关键字、无默认值** —— 同  对 `status` 的口径。

    漏传必须**当场 `TypeError`**，不是静默落回某个默认桶（那是 fail-open， 已裁）。
    反证检验：给三个函数各加个 `thread_id="default"` 默认值 ⇒ **本用例红**。
    """
    with pytest.raises(TypeError):
        cache.get_chat_history("u1")                                   # noqa
    with pytest.raises(TypeError):
        cache.append_chat_history("u1", "user", "问题", status="done")  # noqa
    with pytest.raises(TypeError):
        cache.persist_turn("u1", "问题", "答案", status="done")         # noqa


def test_two_threads_of_one_user_do_not_share_history(fake_store):
    """本契约的全部意义：同一个人的两个会话**互不串**。

    改前 `thread_id` **不切分历史**（只用于 B8 额度）⇒ 同一人开两个会话会互相喂上下文。
    """
    cache.persist_turn("u1", "问A", "答A", thread_id="t-a", status="done")
    cache.persist_turn("u1", "问B", "答B", thread_id="t-b", status="done")

    a = cache.get_chat_history("u1", thread_id="t-a")
    b = cache.get_chat_history("u1", thread_id="t-b")

    assert [m["content"] for m in a] == ["问A", "答A"]
    assert [m["content"] for m in b] == ["问B", "答B"]


def test_two_users_with_the_same_thread_id_do_not_share_history(fake_store):
    """同一个 `thread_id`（默认值就是 `"default"`）下的两个人也必须分开。

    这条钉住的是**长度前缀**那个形状（`session_key`）：`("alice","default")` 与
       `("bob","default")` 拼出来必须不同 —— 朴素拼法在这里就已经分得开，
       真正会撞的是 `("a","b:c")` vs `("a:b","c")`（见 `app/tests/test_session_key.py`）。
    """
    cache.persist_turn("alice", "问", "甲的答", thread_id="default", status="done")
    cache.persist_turn("bob", "问", "乙的答", thread_id="default", status="done")

    assert [m["content"] for m in cache.get_chat_history("alice", thread_id="default")] == ["问", "甲的答"]
    assert [m["content"] for m in cache.get_chat_history("bob", thread_id="default")] == ["问", "乙的答"]


def test_an_empty_thread_id_raises_instead_of_silently_bucketing(fake_store):
    """空串 ⇒ **抛错**（`session_key` 的 fail-closed），不是退回默认桶。

    抛的是 **`ValueError`**，不是 `TypeError`
       （§3.4 原文写的是 `TypeError`，这里是订正：`app/access/session_key.py:41`）。
    端点侧的对应处置见 `app/tests/test_thread_id_guard.py` —— 空串**根本进不来**（422），
       本用例钉的是"万一漏进来了，存储层也不许把它悄悄归到默认桶"。
    """
    with pytest.raises(ValueError):
        cache.persist_turn("u1", "问", "答", thread_id="", status="done")
