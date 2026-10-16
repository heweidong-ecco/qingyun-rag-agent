"""硬门 D 的触发条件（B4）。

改前的现状：**只要产生任意 tool_calls 就进审批** ⇒ 问一句"今天几号"也会停下来等人批。
   那条路在验收上是**过不去**的 —— 硬门 D 要的是「**该被接管时被接管**」，不是「全都接管」。

不碰 DB / Redis / 网络 —— 只测路由函数的返回值。

裁定 ⇒ 本仓的施工单（⛔ 不随产品仓提供） 的 B4 · `裁` 栏
   （白名单第一版 = `{web_search}` · 写在 `.env` 的 `SENSITIVE_TOOLS`）

**2026-10-03勘误**：上面那个名字**原来写的是 `search_tool`** ——
   那是**变量名**，不是工具名（真名是 `duckduckgo_search`，后换成 `web_search`）。
   ⇒ 交集恒空 ⇒ **审批永不触发**，而这份文件**全程没红**（它自己就钉着那个错名字）。
   别再写变量名 —— `SENSITIVE_TOOLS` 装的是**工具名**，判据在 `app/tests/test_tool_dispatch.py`。
"""
import os

os.environ.setdefault("SENSITIVE_TOOLS", "web_search")

import pytest                                                       # noqa: E402

from agent.agent_graph import needs_approval, should_continue             # noqa: E402


def _calls(*names):
    return [{"name": n, "args": {}} for n in names]


def test_local_only_tools_do_not_need_approval():
    """本条对应"问个日期也进审批"那个现状 —— 它必须**不**触发。"""
    assert needs_approval(_calls("date_today")) is False
    assert needs_approval(_calls("calculator")) is False


def test_external_side_effect_tool_needs_approval():
    """`web_search` 会把问题发到第三方 ⇒ 是敏感操作。"""
    assert needs_approval(_calls("web_search")) is True


def test_mixed_calls_need_approval():
    """只要**有任何一个**敏感 ⇒ 整体审批（不能"挑着执行"）。"""
    assert needs_approval(_calls("date_today", "web_search")) is True


def test_no_tool_calls_ends_the_graph():
    """没有 tool_calls ⇒ 结束，不是"去审批"。"""
    from langchain_core.messages import AIMessage
    from langgraph.graph import END

    assert should_continue({"messages": [AIMessage(content="答案是 42")]}) == END


def test_unknown_tool_is_not_sensitive_by_default():
    """白名单是**白名单** —— 没登记的工具**不**进审批。

    这条是**故意的取舍**：默认"不敏感"意味着**新加的工具默认不过审批**。
       要它过，就得改 `SENSITIVE_TOOLS`（env）或改本文件。
       若哪天裁定改成"默认敏感"，**改这一条 + 写明理由**，别悄悄改。
    """
    assert needs_approval(_calls("some_new_tool")) is False


def test_empty_whitelist_is_rejected_at_startup(monkeypatch):
    """白名单**不许悄悄为空** —— 空 ⇒ 审批永不触发 ⇒ 硬门 D 名存实亡。

    这条是**有意加在计划之外**的：计划  Step 4 要求加 `validate_approval_config`，
       但没给它配测试。而这条校验**恰恰是"空白名单"唯一的防线** ——
       没有测试守着，它就只是一段"看起来在防"的代码。

    它防的是"**没有任何报错**"的那种失败：白名单空 ⇒ 一切照跑 ⇒ 验收时才发现
       接管从来不触发。⇒ 让它**启动就报**。
    """
    import agent.agent_graph as agent_graph

    monkeypatch.setattr(agent_graph, "SENSITIVE_TOOLS", frozenset())

    with pytest.raises(EnvironmentError) as exc:
        agent_graph.validate_approval_config()

    assert "SENSITIVE_TOOLS" in str(exc.value)


def test_unknown_name_in_whitelist_is_rejected_at_startup(monkeypatch):
    """**名单里有不存在的工具名 ⇒ 也不许启动**（第二段硬拦）。

    这一条是**上一条（空名单）的孪生兄弟**，而且它是**更常见的那种**：
       空名单要"显式清空"才会发生，而**写错名字**是手滑就会发生的 ——
       本仓自己就发生了（默认值写成了变量名 `search_tool`），且**活了整整三天**：
       `validate_approval_config` 当时只查"非空"，于是它**照常启动**、审批**从不触发**。

    判据是**集合差**，不是"名单非空"：名字必须能在 `tools` 里找到。
    """
    import agent.agent_graph as agent_graph

    monkeypatch.setattr(agent_graph, "SENSITIVE_TOOLS", frozenset({"no_such_tool"}))

    with pytest.raises(EnvironmentError) as exc:
        agent_graph.validate_approval_config()

    assert "no_such_tool" in str(exc.value), "报错要点名那个不认识的名字"


def test_validate_approval_config_passes_with_the_shipped_default():
    """反向：**出厂配置必须是通的** —— 否则服务根本起不来。

    与上面两条配成一对：只测"错了会炸" ⇒ **可能误报**（比如判据写反、恒炸）。

    **2026-10-03加强**：原来只断言"不抛" —— 而**错名字当年也不抛**，
       于是这条用例**在 bug 存在时照样是绿的**。⇒ 补一条"默认值必须真的命中工具"。
    """
    import agent.agent_graph as agent_graph
    from agent.agent_graph import validate_approval_config

    validate_approval_config()      # 不抛即通过

    assert agent_graph.SENSITIVE_TOOLS & {t.name for t in agent_graph.tools}, (
        "🔴 出厂默认的白名单与真实工具名**没有交集** ⇒ 审批永不触发。"
        f" 名单={sorted(agent_graph.SENSITIVE_TOOLS)} · "
        f"工具={sorted(t.name for t in agent_graph.tools)}"
    )
