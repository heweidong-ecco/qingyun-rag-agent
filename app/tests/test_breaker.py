"""通用按 key 断路器（`R2`）· **离线可跑的那一半**。
"""
import pytest

import billing.breaker as breaker
import billing.token_tracker as token_tracker


def test_global_key_blocks_when_daily_budget_is_exhausted(monkeypatch):
    """`global:` 这条 key，必须【真的】接到  的全站日级判定上。

    最省事的错法是 `circuit` 自己写一个 `return True`（或自己另查一次库）——
       那样它**永远放行**、或与 `check_global_daily_budget` 的口径**悄悄分叉**。
    """
    from billing.token_config import GLOBAL_DAILY_TOKEN_LIMIT

    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage",
                        lambda: GLOBAL_DAILY_TOKEN_LIMIT + 1)

    ok, why = breaker.circuit("global:2026-10-02")

    assert ok is False, "全站额度已用尽，断路器却放行了"
    assert "全站" in why, f"拒绝原因看不出是【全站】级：{why!r}"


@pytest.mark.parametrize("key", [
    "model:qwen-turbo:2026-10-02",   # L2 将来要用的前缀 —— 现在还没实现
    "global",                        # 少冒号（拼错的形态之一）
    "",                              # 空 key
    "GLOBAL:2026-10-02",             # 大小写错了
])
def test_unknown_key_fails_open(key):
    """认不出的 key **必须放行**，不许改成 `return False`。

    这是最容易"顺手加固"坏掉的一处：看着像"未知情况应当谨慎"，
       但额度是**成本控制**、不是安全边界 ⇒ **fail-closed 会让一个拼错的 key
       把全站请求打成 429**，而原因（"未知 key"）谁都看不懂。
    """
    ok, why = breaker.circuit(key)

    assert ok is True, f"未知 key {key!r} 被当成熔断了 —— 拼错一个字符就打挂全站"
    assert "未知" in why, f"放行理由没说清是【key 认不出】：{why!r}"


def test_estimated_tokens_reaches_the_global_budget(monkeypatch):
    """`estimated_tokens` 必须**透传**给  的判定。

    参数**收下却不用**是很容易漏的一处（签名看着对、行为是死的）——
       本条专治它：现在全站用 0，但预估本身就超了上限 ⇒ 必须拦。
       若 `circuit` 忘了把 `estimated_tokens` 传下去，它会放行 ⇒ 本条红。
    """
    from billing.token_config import GLOBAL_DAILY_TOKEN_LIMIT

    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage", lambda: 0)

    ok, why = breaker.circuit("global:2026-10-02",
                              estimated_tokens=GLOBAL_DAILY_TOKEN_LIMIT + 1)

    assert ok is False, "全站用量为 0、但本次预估已超上限 —— 却放行了（estimated_tokens 没传下去？）"
    assert "预估" in why, f"拒绝原因里看不出是【预估】触发的：{why!r}"


def test_under_limit_is_allowed(monkeypatch):
    """额度充足 + 没有预估 ⇒ 放行。**这是常态路径**，别忘了它也得对。"""
    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage", lambda: 1)

    ok, why = breaker.circuit("global:2026-10-02")

    assert ok is True, f"额度充足却拦了：{why!r}"


def test_global_key_is_todays_key():
    """`global_key()` 必须带**今天**的日期 —— 它正是「跨天恢复」的载体。"""
    from datetime import date

    assert breaker.global_key() == f"global:{date.today().isoformat()}"


def test_global_key_is_actually_recognized_by_circuit(monkeypatch):
    """**两条合起来才安全**：`global_key` 产出的 key，`circuit` 必须**认得**。

    这是最容易静默失效的一种组合错：
        helper 生成 `global:`，而 `circuit` 认的是 `global/`
        ⇒ **前缀对不上 ⇒ 走 fail-open ⇒ 永远放行**，而**两条测试各自都是绿的**
        （一条只查格式、一条只喂字面量）。⇒ 本条把两边**接起来**验。
    """
    from billing.token_config import GLOBAL_DAILY_TOKEN_LIMIT

    monkeypatch.setattr(token_tracker, "get_global_daily_token_usage",
                        lambda: GLOBAL_DAILY_TOKEN_LIMIT + 1)

    ok, why = breaker.circuit(breaker.global_key())

    assert ok is False, (
        f"额度已用尽，但用 global_key() 产出的 key 却被放行了 —— "
        f"多半是 circuit 认不出这个 key 走了 fail-open。key={breaker.global_key()!r}, why={why!r}"
    )
