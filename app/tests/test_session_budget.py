"""会话级 token 上限· **需要真 Postgres** 的那一半。

与 `test_session_budget_offline.py` 的分工见那个文件的文件头。
   本文件**只放"必须真读写库"的**：真 `INSERT INTO token_usage_logs`，再真去查。

**跑之前必须带库名隔离**：`POSTGRES_DB=rag_test`
   （本仓有前科：R2 冒烟忘带库名，`record_usage` 往**真库**写了 4 行 ——
   本仓的复盘记录（⛔ 不随产品仓提供））。
   凡调用 `record_*` / `insert_*` / `create_*` 的验证，**必须带库名隔离**。

**本文件不进 CI**（CI 无 postgres service，见 `app/pytest.ini`）
   —— 所以  的**核心判据不在本文件**，在离线那份里。
"""
import pytest

pytestmark = pytest.mark.needs_db


# 本文件专用前缀，便于事后精确清理（别用 t1 / test 这种会撞上别人的名字）
_PREFIX = "b8-session-budget-"


def test_session_usage_comes_from_the_table():
    """真写一行 `token_usage_logs` ⇒ 取数函数必须读到它。

    本条的**反面**由离线文件里的 `test_memory_is_not_the_source` 守着
       （那边证明"只写内存读不到"）。两条合起来才说明数据源是表。
    """
    import billing.token_tracker as token_tracker

    tid = _PREFIX + "readback"
    token_tracker.record_usage(
        model="qwen-turbo", prompt_tokens=1234, completion_tokens=0,
        purpose="test", user_name="test_user", thread_id=tid,
    )

    assert token_tracker.get_session_token_usage("test_user", tid) >= 1234


def test_budget_rejects_after_real_write():
    """真写库、真超限 ⇒ 真拒，且原因是「会话」级。"""
    import billing.token_tracker as token_tracker
    from billing.token_config import SESSION_TOKEN_LIMIT

    tid = _PREFIX + "over"
    token_tracker.record_usage(
        model="qwen-turbo", prompt_tokens=int(SESSION_TOKEN_LIMIT), completion_tokens=0,
        purpose="test", user_name="test_user", thread_id=tid,
    )

    ok, why = token_tracker.check_session_token_budget("test_user", tid)

    assert ok is False, (
        f"已写入 {SESSION_TOKEN_LIMIT} tokens = 会话上限，却没有被拒。"
        "（判据是 `remaining <= 0` ⇒ 恰好等于上限就该拒）"
    )
    assert "会话" in why


def test_other_thread_is_not_affected():
    """**会话隔离**：A 会话超限，不能把 B 会话一起拦掉。

    若有人把 SQL 里的 `thread_id` 过滤删了（或拼错参数顺序），
       本仓会变成「一个会话用满 ⇒ **同一用户的其他会话全不能聊**」，
       而且**看起来像正常限流**。
    """
    import billing.token_tracker as token_tracker
    from billing.token_config import SESSION_TOKEN_LIMIT

    hot = _PREFIX + "hot"
    cold = _PREFIX + "cold"

    token_tracker.record_usage(
        model="qwen-turbo", prompt_tokens=int(SESSION_TOKEN_LIMIT), completion_tokens=0,
        purpose="test", user_name="test_user", thread_id=hot,
    )

    ok, why = token_tracker.check_session_token_budget("test_user", cold)

    assert ok is True, f"另一个会话被 A 会话的超限带走了：{why!r}"


def test_other_user_is_not_affected():
    """**用户隔离**（决策二）：A 用户把会话用满，不能拦掉 B 用户的**同名会话**。

    这条**专门守 `WHERE user_name = %s`**：删掉它，两个用户只要 `thread_id` 一样
       （默认值都是 `"default"`！）就会互相踩 —— 而那正是决策二要解决的问题。
    """
    import billing.token_tracker as token_tracker
    from billing.token_config import SESSION_TOKEN_LIMIT

    shared_tid = _PREFIX + "shared-thread"

    token_tracker.record_usage(
        model="qwen-turbo", prompt_tokens=int(SESSION_TOKEN_LIMIT), completion_tokens=0,
        purpose="test", user_name="user-a", thread_id=shared_tid,
    )

    ok, why = token_tracker.check_session_token_budget("user-b", shared_tid)

    assert ok is True, (
        f"用户 A 把 `{shared_tid}` 用满后，用户 B 的**同名会话**也被拦了：{why!r}\n"
        "  ⇒ SQL 里的 `WHERE user_name = %s` 丢了（决策二）"
    )
