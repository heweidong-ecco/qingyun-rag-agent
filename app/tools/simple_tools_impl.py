"""简单工具的**纯 stdlib 内核**（无 langchain 依赖）。

从 `simple_tools.py` 切出（重构计划 ⑥ 切开点 3）。目的：让这两个函数的逻辑
可在**只有标准库**的环境里被导入和测试 —— 此前它们住在 `simple_tools.py` 里，
而那个文件在**模块层** `from langchain_core.tools import tool`。
"""
import json
import re
import statistics
from datetime import datetime, timedelta

from tools.safe_math import calculate  #  `eval`（理由见 `app/tools/safe_math.py`）


def calculator_impl(expression: str) -> str:
    """计算一个数学表达式（纯逻辑，无 langchain 依赖）。"""
    # 返回值形状**与改前逐字一致**：成功 `str(结果)`；失败 `计算错误: {原因}`。
    #    `test_impl_modules.py` 断言了 `1/0` 那条要含 `division by zero` —— 已保留。
    return calculate(expression)


def date_today_impl() -> str:
    """返回今天的日期与星期几（纯逻辑，无 langchain 依赖）。"""
    now = datetime.now()
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    weekday_str = weekdays[now.weekday()]
    return f"今天是{now.year}年{now.month}月{now.day}日，星期{weekday_str}"


# ====================：三个新工具的纯逻辑内核 ====================
# 与 `calculator_impl` 同款三条约定：返回 `str` · 失败也返回字符串（不抛） · 只用标准库。
# 三个都是【纯函数】：同一组入参结果永远一样 ⇒ 连失败结果也是**稳定的**，
#    所以缓存它无害（对比 `web_search`：它的"搜不到"是**瞬时**的 ⇒ 那边必须挂 `should_cache`）。
_WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def date_calc_impl(start_date: str, days: int) -> str:
    """在 `start_date` 上加减 `days` 天，返回 `YYYY-MM-DD（星期X）`。

    **`start_date` 必填，故意不默认"今天"** —— 一默认，"（今天, 1）"这组入参
       在**跨天**时会给出不同答案，而缓存键**只含入参** ⇒ 只能靠"不默认"消除，
       不是靠把 TTL 设成 0。要今天几号 ⇒ 先调 `date_today`。
    """
    try:
        base = datetime.strptime(str(start_date).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return f"日期计算错误: start_date 必须是 YYYY-MM-DD，实际收到 {start_date!r}"
    try:
        n = int(days)
    except (TypeError, ValueError):
        return f"日期计算错误: days 必须是整数，实际收到 {days!r}"
    target = base + timedelta(days=n)
    return f"{target.isoformat()}（星期{_WEEKDAYS[target.weekday()]}）"


#: 合法路径 = `a.b` 或 `a[0]` 交替。
#: **先整串校验再拆** —— 直接用 `re.findall` 扫 `a..b` 会**悄悄当成 `a.b`**
#:    （拿"能跑"当"对了"）。本仓立场：坏输入要**说出来**，不静默兜住。
_JSON_PATH_RE = re.compile(r"^[^.\[\]]+(?:\.[^.\[\]]+|\[\d+\])*$")


def _split_json_path(path: str) -> list:
    """把 `a.b[0].c` 拆成 `["a", "b", 0, "c"]`；坏路径抛 `ValueError`。"""
    s = str(path).strip()
    if not s or not _JSON_PATH_RE.match(s):
        raise ValueError(f"路径格式不支持：{path!r}（只支持 a.b 与 a[0] 两种写法交替）")
    out = []
    for name, idx in re.findall(r"([^.\[\]]+)|\[(\d+)\]", s):
        out.append(int(idx) if idx else name)
    return out


def json_extract_impl(json_text: str, path: str) -> str:
    """从 JSON 文本里按 `path` 取一个值。

    返回值：**字符串原样**返回；其它类型返回 **JSON 编码**（`ensure_ascii=False`，中文不转义）。
    """
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, TypeError) as exc:
        return f"JSON 提取错误: 输入不是合法 JSON（{exc}）"

    try:
        tokens = _split_json_path(path)
    except ValueError as exc:
        return f"JSON 提取错误: {exc}"

    cur = data
    for tok in tokens:
        try:
            cur = cur[tok]
        except (KeyError, IndexError, TypeError):
            return f"JSON 提取错误: 路径 {path!r} 在数据里走不通（卡在 {tok!r}）"

    return cur if isinstance(cur, str) else json.dumps(cur, ensure_ascii=False)


def _fmt_num(x) -> str:
    """整数不显示小数点（`4.0` ⇒ `4`），小数最多 6 位有效数字。"""
    return str(int(x)) if float(x).is_integer() else f"{x:.6g}"


def stats_impl(numbers: str) -> str:
    """对一串数字做基础统计：个数 / 和 / 均值 / 最小 / 最大 / 中位数。

    入参是**字符串**（逗号 / 空格 / 换行分隔），不是 JSON 数组 ——
       与 `calculator(expression: str)` 同一种形状：MCP 工厂只搬运 `type`/`description`，
       字符串入参在**所有**路径上形状最稳。逗号**中英文都认**（LLM 常写全角）。
    """
    raw = str(numbers).replace("，", ",")
    parts = [p for p in re.split(r"[,\s]+", raw.strip()) if p]
    if not parts:
        return "统计错误: 没有可统计的数字"
    try:
        values = [float(p) for p in parts]
    except ValueError as exc:
        return f"统计错误: 输入里含非数字（{exc}）"
    return (
        f"个数={len(values)} 和={_fmt_num(sum(values))} "
        f"均值={_fmt_num(statistics.mean(values))} "
        f"最小={_fmt_num(min(values))} 最大={_fmt_num(max(values))} "
        f"中位数={_fmt_num(statistics.median(values))}"
    )
