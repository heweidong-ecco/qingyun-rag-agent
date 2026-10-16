"""接线守卫：工具分派必须按【名字】走，不是按硬编码的字面量。

**为什么单开一个文件**（本仓的老毛病）："改对了一处" ≠ "没有第二处"。
本仓的拷贝文化是真的 —— `calculator` 就被复制了 5 次（`test_safe_math_wiring.py`），
而 **`tool_execute` 这个分派被复制了 2 次**，两处**同时**带着同一个 bug（2026-10-03 实测）。

## 被守的两条既有 bug（不是本次引入的）

| # | 症状 | 为什么它能活到今天 |
|---|---|---|
| ① | `if tool_name == "search"`，而真实工具名是 `duckduckgo_search` ⇒ **永远落 `else`** | 落 `else` 时**如实返回** `未找到工具: xxx` ⇒ 模型以为"搜过了没结果"，**不报错** |
| ② | `SENSITIVE_TOOLS` 默认值是**变量名** `"search_tool"`，不是工具名 ⇒ 交集恒空 | `validate_approval_config` **只查"非空"，不查"名字存在"** ⇒ 空转但**不报错** |

⇒ 两条都是「**静默失效**」：接口一切正常，只有真去数才看得。

## 两道闸，覆盖面不同（不是重复）

| 闸 | 挡什么 |
|---|---|
| **AST 静态** | "分派里又出现了字符串字面量比较" —— **不是 grep**：注释/文档里也写着 `"search"` 这几个字，正则会把说明文字的命中当成违规（本仓栽过，） |
| **行为 / 子进程** | "出厂默认下，白名单与真实工具名有没有交集" + "写了不存在的名字，服务到底肯不肯启动" |

`SENSITIVE_TOOLS` **默认值**那两条走**子进程**，不走同进程 —— 它在**模块级**读一次 env，
同进程里改 env 对已 import 的模块无效（本仓既有测试已经踩过这个，只能 `monkeypatch.setattr` 绕）。
子进程是**唯一**能真的验"启动那一刻发生什么"的写法。
"""

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层

#: 分派的**全部**现场 —— **这份名单就是"分派的定义"**：
#: 将来再复制一份 `tool_execute`，要把它加进来（别让它成为第 3 个漏网的）。
DISPATCH_SITES = ["agent.agent_graph", "agent.agent_checkpointer"]


def _function_source(path: Path, func_name: str):
    """取 `path` 里名为 `func_name` 的函数节点；找不到返回 `None`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    return None


def _literal_comparisons(func_node):
    """函数体里所有「拿字符串字面量做比较」的位置（行号）。

    只看**比较**（`==` / `in` 之类）里的字符串常量。
       `ToolMessage(content=str(result), name=tool_name)` 那种**不是比较**，别误伤。
       将来若真的要按名字写映射表（`{"web_search": fn}`），那会是一次 `Dict` 字面量、
       **不是 `Compare`** ⇒ 也不会误伤。
    """
    offenders = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if any(
            isinstance(op, ast.Constant) and isinstance(op.value, str) for op in operands
        ):
            offenders.append(node.lineno)
    return offenders


# ===========================================================================
# 一、AST 静态：分派里不许再有字符串字面量比较
# ===========================================================================
@pytest.mark.parametrize("module_name", DISPATCH_SITES)
def test_dispatch_does_not_compare_against_a_string_literal(module_name):
    """本文件最要紧的一条：`tool_name == "..."` 这种写法**一律不许再出现**。

    **为什么**：它把工具名**抄了第二份**。抄的那份一旦和 `tools` 里注册的名字对不上，
       后果是 `else` 分支 —— 而 `else` 是**如实报错**（`未找到工具: xxx`），
       **不是崩溃** ⇒ 看起来一切正常，实际那个工具**从来没被执行过**。
    """
    path = API_DIR / (module_name.replace(".", "/") + ".py")   # "agent.agent_graph" → app/agent/agent_graph.py
    func = _function_source(path, "tool_execute")

    assert func is not None, f"🔴 {module_name}.py 里找不到 `tool_execute` —— 分派被挪走了？"

    offenders = _literal_comparisons(func)

    assert not offenders, (
        f"🔴 {module_name}.tool_execute 里还有「拿字符串字面量比较」—— 行号 {offenders}。"
        " 工具名要**从 `tools` 派生**（`{t.name: t for t in tools}`），⛔ 不要再抄一份。"
    )


def test_no_undiscovered_third_copy_of_the_dispatch():
    """防止"第 3 份拷贝"：全仓扫一遍，谁再写同型的字面量分派就当违规。

    扫的是**整个 `app/`**（不只是上面那 2 个文件）—— 上面那 2 个是"已知的"，
       这条管的是"**将来又冒出来的**"。`api_v1_rag.py` 里那份用的是 `ToolNode` /
       不同的形状，不在本判据的射程内（见  遗留·1）。
    """
    offenders = []
    for path in sorted(_product_py(API_DIR)):
        if path.name.startswith("test_"):
            continue
        func = _function_source(path, "tool_execute")
        if func is None:
            continue
        for lineno in _literal_comparisons(func):
            offenders.append(f"{path.name}:{lineno}")

    assert not offenders, (
        "🔴 全仓还有「按字符串字面量分派工具」的地方 —— 每一处都是一次静默失效："
        f"{offenders}"
    )


# ===========================================================================
# 二、溯源：分派表必须是**从 `tools` 派生**的，且覆盖全部工具
# ===========================================================================
@pytest.mark.parametrize("module_name", DISPATCH_SITES)
def test_dispatch_table_covers_every_registered_tool(module_name):
    """正向那条：光"没有字面量比较"还不够 —— 得**确实**能按名字查到工具。

    这条同时钉住了"新加工具忘了分派"：`tools` 里有的，表里**必须**有。
    """
    module = importlib.import_module(module_name)
    table = getattr(module, "TOOLS_BY_NAME", None)

    assert table is not None, (
        f"🔴 {module_name} 没有 `TOOLS_BY_NAME` —— 分派没有唯一的名字来源"
    )
    assert set(table) == {t.name for t in module.tools}, (
        f"🔴 {module_name} 的分派表与 `tools` 对不上："
        f"表={sorted(table)} · tools={sorted(t.name for t in module.tools)}"
    )


@pytest.mark.parametrize("module_name", DISPATCH_SITES)
def test_unknown_tool_name_falls_back_to_an_honest_message(module_name, monkeypatch):
    """查不到 ≠ 抛 `KeyError` —— 要**如实**告诉模型"没这个工具"。

    这是**行为**契约（`未找到工具: xxx` 是模型能看懂并能改主意的信号）；
       换成 `KeyError` 会把一次"模型调错名字"升级成"整条请求 500"。
       旧代码的 `else` 分支就是这个语义，别在重构里弄丢。
    """
    from langchain_core.messages import AIMessage

    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "TOOLS_BY_NAME", {})

    message = AIMessage(
        content="",
        tool_calls=[{"name": "no_such_tool", "args": {}, "id": "c1", "type": "tool_call"}],
    )
    out = module.tool_execute({"messages": [message]})

    assert out["messages"][0].content == "未找到工具: no_such_tool"


# ===========================================================================
# 三、启动那一刻：默认名单必须命中真工具；写了不存在的名字，服务不许起来
# ===========================================================================
def _run_python(code: str, sensitive_tools: str | None):
    """在**子进程**里跑 `code`，env 里的 `SENSITIVE_TOOLS` 按参数定。

    必须子进程：该常量是**模块级**读的，同进程改 env 对已 import 的模块无效。
    """
    env = {k: v for k, v in os.environ.items() if k != "SENSITIVE_TOOLS"}
    if sensitive_tools is not None:
        env["SENSITIVE_TOOLS"] = sensitive_tools
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_shipped_default_whitelist_matches_a_real_tool():
    """BUG② 的反向判据：**不设 `.env`** 时，白名单与真实工具名**必须**有交集。

    这就是 2026-10-03 那条一行判据的测试化版本：
    `{t.name for t in tools} & SENSITIVE_TOOLS` —— 修复前是 `set`（= 审批永不触发），
    修复后是 `{'web_search'}`。

    修复前本用例**必须变红** —— 否则说明它没测到真东西。
    """
    result = _run_python(
        "import agent.agent_graph as g\n"
        "print(sorted({t.name for t in g.tools} & g.SENSITIVE_TOOLS))\n",
        sensitive_tools=None,
    )

    assert result.returncode == 0, f"出厂配置下 import 就失败了：\n{result.stderr}"
    assert result.stdout.strip() != "[]", (
        "🔴 出厂默认下白名单与真实工具名【交集为空】⇒ 审批永不触发（硬门 D 名存实亡）。"
        f" 实际输出：{result.stdout.strip()}"
    )


def test_unknown_name_in_the_whitelist_refuses_to_start():
    """BUG② 的正面防线：名单里有**不存在的工具名** ⇒ **服务不许启动**。

    这是  那条哲学的延伸 —— 它只硬拦了「空名单」，
       于是「名字全都对不上」这个**同一个失败**换了个形状绕过了它自己设的闸。
       ⇒ 判据从「非空」升级为「**非空且名字真的存在**」。

    代价（明说）：在 `.env` 里写了错名字的部署**会直接起不来**。
       这是有意的 —— 「部署那一刻失败」好过「验收时才发现接管从来没发生过」。
    """
    result = _run_python("import agent.agent_graph\n", sensitive_tools="no_such_tool")

    assert result.returncode != 0, (
        "🔴 白名单里写了个不存在的工具名，服务却**照常启动**了 —— 审批会静默失效。\n"
        f"stdout={result.stdout!r}"
    )
    assert "EnvironmentError" in result.stderr or "no_such_tool" in result.stderr, (
        f"报错信息里应当点名那个不认识的名字，实际：\n{result.stderr}"
    )


# ===========================================================================
# 2026-10-09（模块化重构）：**产品模块的枚举口径**
# ===========================================================================
# **为什么不能再用 `api_dir.glob("*.py")`**：
#   2026-10-09 把 60 个模块按组收进了 `app/<组>/`（core · routing · access
#   billing · agent · rag · tools）⇒ **根目录下再也扫不到它们** ⇒ `glob` 返回
#   **空列表** ⇒ 下面那些「对每个模块…」的断言**全部为真**。
#   ⇒ 本仓原话：「**空跑 = 静默假通过**」（`pre-commit-gates.py` / `test_web_pages.py` 都栽过）。
#   ⇒ 所以这里改成**递归**，并且**递归之后必须排掉两处**：
#       · `tests/`  —— 测试不是产品模块
#       · `alembic/` —— 迁移脚本，**重构前就扫不到**（原来 `glob("*.py")` 只看 app/ 根）
#   ⇒ 并且带一条**防空跑断言**：真扫到 0 个 ⇒ 当场红，不许静默变绿。
def _product_py(api_dir):
    """`app/` 下的**产品 .py**（`app/` 根 + 七个模块组）。"""
    ps = [p for p in api_dir.rglob("*.py")
          if "__pycache__" not in p.parts
          and "tests" not in p.parts
          and "alembic" not in p.parts
          and not p.name.startswith("test_")]
    assert ps, f"🔴 防空跑：{api_dir} 下扫到 0 个产品模块 —— 枚举口径又变了"
    return sorted(ps)


def _mod_name(api_dir, path):
    """模块的**可导入名** —— 相对 `app/` 的点分路径（如 `agent.agent_graph`）。

    用它是因为 `importlib.import_module("agent_graph")` 在重构后**会 ModuleNotFoundError**
       （真模块名已是 `agent.agent_graph`）。
    """
    return path.relative_to(api_dir).with_suffix("").as_posix().replace("/", ".")
