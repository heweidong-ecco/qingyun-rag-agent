"""
简单工具集：不依赖外部服务的独立工具（**面向 LLM 的工具外壳**）。

（纯 stdlib，可脱离 langchain 单测）。本文件只剩一层 `@tool` 外壳。
`import simple_tools` 仍会拉 **langchain**；**只想用逻辑就 import `simple_tools_impl`**。
"""
from langchain_core.tools import tool

from tools.simple_tools_impl import (
    calculator_impl,
    date_calc_impl,
    date_today_impl,
    json_extract_impl,
    stats_impl,
)
from tools.tool_cache import cached_tool

__all__ = [
    "calculator",
    "date_today",
    "date_calc",
    "json_extract",
    "stats",
    "SIMPLE_TOOLS",
]

# **名字必须显式给 `name=`** —— `cached_tool` 缺省用 `func.__name__`
#    （= `calculator_impl`），而 `TTL_BY_TOOL` 的键是**工具名**（`calculator`）≠ 函数名。
# **`tool_cache` 绝不许 import 进 `simple_tools_impl`** ——
#    那个文件的不变量是「**只 import 标准库**」（写在它文件头）；缓存是**外壳**的事。
_cached_calculator = cached_tool(name="calculator")(calculator_impl)
_cached_date_today = cached_tool(name="date_today")(date_today_impl)


@tool
def calculator(expression: str) -> str:
    """
    计算一个数学表达式，例如 3*4-5/6。
    输入的必须是纯数学表达式。
    """
    # 上面的 docstring 是**给 LLM 读的工具描述**，故留在本层。
    return _cached_calculator(expression)


@tool
def date_today(query: str = "") -> str:
    """
    查询今天的日期、星期几。
    忽略查询参数。
    """
    # 上面的 docstring 是**给 LLM 读的工具描述**，故留在本层。
    return _cached_date_today()


# ====================：三个新工具 ====================
# 缓存层与上面两条同款 —— `name=` **必须显式给**（缺省会用 `*_impl` 那个**函数名**，
#    与 `TTL_BY_TOOL` 的键对不上 ⇒ `get_ttl` 抛 `KeyError`）。
# **三个都不挂 `should_cache`**：它们是**纯函数** ⇒ 失败结果也是**稳定的**
_cached_date_calc = cached_tool(name="date_calc")(date_calc_impl)
_cached_json_extract = cached_tool(name="json_extract")(json_extract_impl)
_cached_stats = cached_tool(name="stats")(stats_impl)


@tool
def date_calc(start_date: str, days: int) -> str:
    """
    在指定日期上加减若干天，返回结果日期与星期几。
    例：start_date="2026-10-08", days=7 → "2026-10-15（星期四）"。
    start_date 必须是 YYYY-MM-DD 格式；days 正数往后、负数往前、0 就是当天。
    不知道今天几号 ⇒ 先调 date_today。
    """
    # 上面这段是**给 LLM 读的工具描述**，故留在本层。
    return _cached_date_calc(start_date, days)


@tool
def json_extract(json_text: str, path: str) -> str:
    """
    从一段 JSON 文本里按路径取出一个字段。
    路径只支持点号取字段与数字下标，例：user.name ／ items[0].id ／ data.list[2].title。
    取不到会返回一句以"JSON 提取错误:"开头的说明，不会崩。
    """
    # 上面这段是**给 LLM 读的工具描述**，故留在本层。
    return _cached_json_extract(json_text, path)


@tool
def stats(numbers: str) -> str:
    """
    对一串数字做基础统计：个数 / 和 / 均值 / 最小 / 最大 / 中位数。
    输入是一段**字符串**，用逗号或空格分隔，例："1,2,3,4"。
    """
    # 上面这段是**给 LLM 读的工具描述**，故留在本层。
    return _cached_stats(numbers)


# 工具列表（实测：**全仓零调用** —— `mcp_server` 是**逐个名字**导入的，
#   本行不是它的来源。原注释写「方便 MCP Server 批量导入」，**那是假的**，已改准。
#   删不删它 ⇒ 另一件事（等作者发话）；本行只保证**内容与上面的定义一致**。）
SIMPLE_TOOLS = [calculator, date_today, date_calc, json_extract, stats]
