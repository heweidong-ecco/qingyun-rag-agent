"""三个新工具的**外壳层**守卫。

这里测的是 **`@tool` 对象**（LLM 看到的那一层），不是 `*_impl` —— 后者在 `test_impl_modules.py`。
本文件**全离线**：不碰 Redis（TTL>0 的缓存路径由 `test_tool_cache.py` 用假 redis 覆盖）。
"""
import pytest


NEW_TOOL_NAMES = ["date_calc", "json_extract", "stats"]


@pytest.mark.parametrize("name", NEW_TOOL_NAMES)
def test_new_tool_is_a_langchain_tool_with_a_name_and_description(name):
    """三个名字都要是**真的 LangChain 工具**，且有**写给 LLM 的描述**。

    反证：`description` 为空 ⇒ LLM 只能靠名字猜这个工具干什么 ⇒ 本用例红。
    """
    import tools.simple_tools as simple_tools

    t = getattr(simple_tools, name)
    assert t.name == name, f"{name} 的工具名不对：{t.name!r}"
    assert t.description and len(t.description.strip()) > 10, f"{name} 没有可读的工具描述"


def test_date_calc_shell_delegates_to_impl():
    from tools.simple_tools import date_calc

    assert date_calc.invoke({"start_date": "2026-10-08", "days": 7}) == "2026-10-15（星期四）"
    assert "日期计算错误:" in date_calc.invoke({"start_date": "x", "days": 1})


def test_json_extract_shell_delegates_to_impl():
    from tools.simple_tools import json_extract

    assert json_extract.invoke({"json_text": '{"a": {"b": 2}}', "path": "a.b"}) == "2"
    assert "JSON 提取错误:" in json_extract.invoke({"json_text": "x", "path": "a"})


def test_stats_shell_delegates_to_impl():
    from tools.simple_tools import stats

    out = stats.invoke({"numbers": "1,2,3,4"})
    assert "个数=4" in out and "均值=2.5" in out
    assert "统计错误:" in stats.invoke({"numbers": "x"})


def test_simple_tools_list_names_all_five():
    """`SIMPLE_TOOLS` 这个**列表**要与本模块的 `@tool` 定义一致。

    它目前**全仓零调用**（死导出）—— 本用例**不是为了用它**，是为了**不让它变成假话**。
       删不删它 ⇒ 是另一件事（等需求方发话，见施工单「现状 4」）。
    """
    from tools.simple_tools import SIMPLE_TOOLS

    assert [t.name for t in SIMPLE_TOOLS] == [
        "calculator", "date_today", "date_calc", "json_extract", "stats",
    ]
