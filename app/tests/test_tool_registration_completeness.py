"""工具登记【完整性】守卫 —— 补上两处**此前完全没守卫**的登记面。
"""
import pytest


def _registered_tool_names() -> list:
    from tools.mcp_server import TOOLS

    return sorted(t["func"].name for t in TOOLS)


@pytest.mark.parametrize("tool_name", _registered_tool_names())
def test_tool_has_health_check_args(tool_name):
    """每个已注册工具都要有健康检查参数 —— 否则它**永远不体检**。

    反证（实测做过）：把 `date_calc` 从 `TEST_ARGS_MAP` 里删掉 ⇒ **本用例红**。
       改前它**不存在** —— 那正是"哑掉"的定义。
    """
    from tools.tool_health import TEST_ARGS_MAP

    assert tool_name in TEST_ARGS_MAP, (
        f"🔴 `{tool_name}` 没有健康检查参数 ⇒ 它永远不会被体检（静默）。"
        f" 请在 app/tools/tool_health.py 的 TEST_ARGS_MAP 里补一条。"
    )


@pytest.mark.parametrize("tool_name", _registered_tool_names())
def test_tool_has_token_estimates_in_both_tables(tool_name):
    """两张成本预估表**都要**有它 —— 本仓明文：两套并存、不可互相替代。

    忘了登记的后果是**静默**的：`estimate_tool_tokens` 兜底
       `DEFAULT_ESTIMATED_TOKENS = 500` ⇒ 一个本地**免费**工具被当**花 500 token** 算
       ⇒ 可能误触预算门（判据在 `app/billing/token_tracker.py` 的 `estimate_tool_tokens`）。
    """
    from billing.token_tracker import TOOL_ESTIMATED_COST, TOOL_ESTIMATED_TOKENS

    assert tool_name in TOOL_ESTIMATED_TOKENS, (
        f"🔴 `{tool_name}` 不在 TOOL_ESTIMATED_TOKENS ⇒ 预算判定会按默认 500 token 估它。"
    )
    assert tool_name in TOOL_ESTIMATED_COST, (
        f"🔴 `{tool_name}` 不在 TOOL_ESTIMATED_COST ⇒ 对外展示的预估会漏掉它。"
    )


def test_the_guard_itself_is_not_vacuous():
    """**反向对照**：本文件必须真的扫到了工具，不是一个空集合在跑。

    没有这一条，一个"`mcp_server.TOOLS` 变空"的世界里，上面两组用例会**全绿** ——
       那正是本仓反复栽的「**尺子量不到它该量的事**」。
    """
    names = _registered_tool_names()
    assert len(names) >= 7, f"已注册工具数不该少于 7，实际：{names}"
    for expected in ("calculator", "date_today", "web_search",
                     "execute_python", "date_calc", "json_extract", "stats"):
        assert expected in names, f"🔴 `{expected}` 不在已注册工具里：{names}"
