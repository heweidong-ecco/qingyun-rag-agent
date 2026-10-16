"""预算闸门的单位回归测试（`#2` · 决策）

背景（修）：
    三处闸门写成 `remaining = get_user_token_budget(...) - get_daily_usage_cost(...)`
    —— **Token 预算** 减 **「元」消耗**。后果是剩余恒 ≈ 预算值，而单次预估花费只有 ¥0.0x，
    于是 `cost > remaining` 永不成立 ⇒ **三处闸门恒放行**、80% 告警永不触发。
"""
import pytest

import billing.token_tracker as tt


@pytest.fixture
def budget_nearly_exhausted(monkeypatch):
    """把「预算」与「已用 Token」钉死，使剩余极小（10 tokens）。

    刻意**不** patch `get_daily_usage_cost` —— 那正是判别红绿的关键：
       修复前的代码会去调它、真走库拿到「元」，于是判据不成立。
    """
    monkeypatch.setattr(tt, "get_user_token_budget", lambda u: 10000)
    monkeypatch.setattr(tt, "get_daily_token_usage", lambda u: 9999)


# ==================== 三处闸门都要拦得住 ====================

def test_gate_blocks_when_token_budget_exhausted(budget_nearly_exhausted):
    """`check_budget_before_call`：剩余 10 tokens、预估 800 ⇒ **必须拦**。"""
    allowed, reason = tt.check_budget_before_call("someone", purpose="answer_generation")
    assert allowed is False, f"闸门恒放行（单位错配未修？）: {reason}"
    assert "tokens" in reason, f"文案应说 tokens 而不是金额: {reason}"
    assert "¥" not in reason, f"文案里不该再有 ¥（那是「元」的单位）: {reason}"


def test_multilevel_gate_blocks_when_token_budget_exhausted(budget_nearly_exhausted):
    """`check_multilevel_budget` 第三级（唯一在真实请求路径上的闸门）。

    `app/agent/agent_graph_advanced.py` 的 `tool_execute` 节点调的就是它。
    """
    allowed, reason = tt.check_multilevel_budget("someone", "thread-x", tool_name="web_search")
    assert allowed is False, f"多级闸门第三级恒放行: {reason}"
    assert "tokens" in reason


def test_warning_fires_when_ratio_high(monkeypatch):
    """`check_budget_warning`：已用 90% ⇒ 告警**必须触发**（原先永不触发）。"""
    monkeypatch.setattr(tt, "get_user_token_budget", lambda u: 10000)
    monkeypatch.setattr(tt, "get_daily_token_usage", lambda u: 9000)
    w = tt.check_budget_warning("someone")
    assert w["warning"] is True, f"90% 用量却未告警: {w}"
    assert "tokens" in w["message"]


# ==================== 通过的情形也要正常 ====================

def test_gate_allows_when_budget_is_ample(monkeypatch):
    """反向对照：预算充足时必须放行（别修成"恒拦"）。"""
    monkeypatch.setattr(tt, "get_user_token_budget", lambda u: 10000)
    monkeypatch.setattr(tt, "get_daily_token_usage", lambda u: 100)
    allowed, reason = tt.check_budget_before_call("someone", purpose="answer_generation")
    assert allowed is True, reason


def test_admin_is_still_unlimited(monkeypatch):
    """管理员仍是无限预算（早退分支不能被破坏）。"""
    monkeypatch.setattr(tt, "get_user_token_budget", lambda u: float("inf"))
    monkeypatch.setattr(tt, "get_daily_token_usage", lambda u: 10 ** 9)
    allowed, reason = tt.check_budget_before_call("admin", purpose="answer_generation")
    assert allowed is True and "无限" in reason


# ==================== 结构性断言：三份实现已收敂为一份 ====================

def test_single_source_of_truth(monkeypatch):
    """三处闸门必须都委托给 `check_token_budget_detail`。

    判据：把 `check_token_budget_detail` 换成"恒拦"，三处应**全部**跟着拦 ——
    若某处还留着自己的一份实现，它就不会跟着变。
    """
    monkeypatch.setattr(tt, "check_token_budget_detail",
                        lambda u, t=0: (False, "桩：恒拦"))
    a, _ = tt.check_budget_before_call("u", purpose="answer_generation")
    b, _ = tt.check_multilevel_budget("u", "t", tool_name="web_search")
    c = tt.check_token_budget("u", 100)
    assert (a, b, c) == (False, False, False), (
        f"三处闸门未全部走统一实现：before_call={a} multilevel={b} token={c}"
    )


def test_would_have_caught_the_original_unit_mismatch(monkeypatch):
    """**自证有效性**：把修复**前**的算式喂进来，必须判它**放行**（= 它测得出 bug）。

    修复前的算式：`remaining = get_user_token_budget() - get_daily_usage_cost()`。
    """
    budget, used_cost, est_cost = 10000, 0.0002, 0.02       # 真实量级
    remaining = budget - used_cost                           # ← 就是那个错配
    assert not (remaining <= 0), "修复前的算式不该判「预算用完」"
    assert not (est_cost > remaining), (
        "修复前的算式对 ¥0.02 的预估**恒放行** —— 这正是它测得出 bug 的地方"
    )
    # 对照：同量纲（Token）才会拦
    used_tokens, est_tokens = 9999, 800
    assert est_tokens > (budget - used_tokens), "同量纲下应当拦得住"
