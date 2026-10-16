"""成本可见（`①b`）· **需要真 Postgres** 的那一半。

与 `test_cost_visibility.py` 的分工：本文件**只放"必须真读写库"的**。
"""
import pytest

pytestmark = pytest.mark.needs_db


# 本文件专用前缀，便于事后精确清理（别用 t1 / test 这种会撞上别人的名字）
_PREFIX = "b13-cost-"


def _simulate_restart():
    """清空三个**进程内存**汇总 —— 这在语义上**等价于重启进程**。

    `_user_summary` / `_purpose_summary` / `_thread_summary` 只在 `record_usage`
    里累加、**从不回读 DB**（`token_tracker.py` 顶部三个 defaultdict）。
    ⇒ 清掉它们之后还答得出来的数，只能来自库。
    """
    import billing.token_tracker as token_tracker

    with token_tracker._lock:
        token_tracker._user_summary.clear()
        token_tracker._purpose_summary.clear()
        token_tracker._thread_summary.clear()


def test_totals_survive_a_restart():
    """**本文件存在的理由**：总览必须**扛得住重启**。

    步骤 = 真写一行 → **清空进程内存**（= 重启）→ 再取总览。

    改动前（`/agent/cost/overview` 读 `get_user_summary`）这一条会答 **0**，
       而**不报任何错** —— 实测 admin 在库里有 4216 tokens、端点答 0。
    """
    import billing.token_tracker as token_tracker

    user = _PREFIX + "restart"
    token_tracker.record_usage(
        model="qwen-turbo", prompt_tokens=1234, completion_tokens=0,
        purpose="test", user_name=user, thread_id=_PREFIX + "restart-t",
    )

    _simulate_restart()

    ov = token_tracker.get_user_overview(user)

    # 自证（别删）：**同一步骤**下，改动前用的那条路**确实答 0** ——
    #    证明本测试测的是真问题（"两边都为真"式的空测试不成立）。
    assert token_tracker.get_user_summary(user).get("total_tokens", 0) == 0, (
        "清空内存后 `get_user_summary` 居然不为 0 ⇒ 本条的前提（清空 = 重启）不成立，"
        "先回来核对 `_simulate_restart`。"
    )

    assert ov["total_tokens"] >= 1234, (
        f"清空进程内存后总览只剩 {ov['total_tokens']} ⇒ 它读的还是内存，不是库。\n"
        "  ⚠️ 症状：重启后『花了多少钱』答 0，且不报错。"
    )
    assert ov["calls"] >= 1, f"调用次数也要扛得住重启，实得 {ov['calls']}"


def test_overview_window_is_all_time_not_today():
    """口径 = **全时累计**，不是"今天"。

    这条与  的 `test_yesterdays_usage_does_not_count` **方向正好相反** ——
       那边**必须排除**昨天（额度要跨天自愈），这边**必须包含**昨天（"一共花了多少"）。

    ⇒ 若有人把 `get_user_overview` 的 SQL 加上 `created_at >= CURRENT_DATE`，
      它会**静默退化成"今天"**（数值偏小、不报错）—— 本条就是钉这个。
    """
    import billing.token_tracker as token_tracker
    from core.db import get_db

    user = _PREFIX + "alltime"

    # 直接写库：只有这样才控制得了 created_at（record_usage 恒为 now()）
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO token_usage_logs
                   (user_name, thread_id, model, purpose,
                    prompt_tokens, completion_tokens, total_tokens, cost, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                           CURRENT_DATE - INTERVAL '3 day')""",
                (user, _PREFIX + "alltime-t", "qwen-turbo", "test",
                 777, 0, 777, 0),
            )
            conn.commit()

    ov = token_tracker.get_user_overview(user)

    assert ov["total_tokens"] >= 777, (
        f"塞了一条【3 天前】的 777 tokens，总览只看到 {ov['total_tokens']} ⇒ "
        "窗口被收窄成「今天」了（数值偏小、不报错）。\n"
        "  ⚠️ 检查 get_user_overview 的 SQL 里有没有混进 `created_at >= CURRENT_DATE`。"
    )


def test_by_purpose_breakdown_is_scoped_to_this_user():
    """`by_purpose` 必须是**本人**的 —— 这是本次修掉的第二个错。

    改前它读 `get_purpose_summary`，那个函数**不收 `user_name`** ⇒ 返回的是
    **全站**的按用途拆分，与同一份响应里的 `total_*`（本人）**根本不是一套口径**。
    """
    import billing.token_tracker as token_tracker

    a, b = _PREFIX + "pa", _PREFIX + "pb"
    tag_a, tag_b = _PREFIX + "purpose-a", _PREFIX + "purpose-b"

    token_tracker.record_usage(
        model="qwen-turbo", prompt_tokens=11, completion_tokens=0,
        purpose=tag_a, user_name=a, thread_id=_PREFIX + "t",
    )
    token_tracker.record_usage(
        model="qwen-turbo", prompt_tokens=22, completion_tokens=0,
        purpose=tag_b, user_name=b, thread_id=_PREFIX + "t",
    )

    by_a = token_tracker.get_user_overview(a)["by_purpose"]

    # 自证（别删）：**改动前的数据源** `get_purpose_summary` 里确实躺着**别人**的用途
    #    ⇒ 当年那份响应真的把 tag_b 混进了 a 的 by_purpose（证明本条测的是真问题）。
    assert tag_b in token_tracker.get_purpose_summary(), (
        "全站口径里看不到另一个用户的用途 ⇒ 本条的前提不成立（先核对 record_usage 写没写进去）"
    )

    assert tag_a in by_a, f"自己写的用途 {tag_a} 没出现在自己的总览里：{list(by_a)}"
    assert tag_b not in by_a, (
        f"别人的用途 {tag_b} 出现在自己的总览里 ⇒ by_purpose 又退回【全站】口径了。\n"
        "  ⚠️ 症状：同一份响应里 total_* 是本人、by_purpose 是全站，两个口径串在一起。"
    )
