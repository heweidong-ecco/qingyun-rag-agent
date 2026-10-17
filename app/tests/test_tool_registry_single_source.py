"""守卫：工具的【定义】只有一处事实源 —— 四个正本模块，别再手抄第 N 份。

**为什么用 AST 而不是 grep**：注释和 docstring 里也写着这些工具名，
   正则会把**说明文字**当成违规（本仓栽过 —— 见 `app/tests/test_tool_dispatch.py` 的表头）。
"""

import ast
import importlib
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层

#: 交给 LLM 的工具名（= `mcp_server.TOOLS` 里那些）。
#:：4 → **7**。
#: **新增工具时必须同时改这里** —— 本集合是 `_tool_definitions` 的**过滤条件**，
#:    名字不在里面 ⇒ 那条守卫对这个工具**是瞎的**（不是"少登记一条测试"，是"尺子量不到"）。
TOOL_NAMES = {
    "calculator", "date_today", "web_search", "execute_python",
    "date_calc", "json_extract", "stats",
}

#: **允许**【定义】这些工具的模块 —— 正本，每个工具**只有一处**。
#: 新增一个工具 ⇒ 在 `mcp_server.TOOLS` 加一行 + 在这里登记它的**定义处**
#:       （不是"到处都能定义"）。
#: **「加工具要加几处」的【完整】清单** ⇒  §六·1（**五处** + 两处此前零守卫的
DEFINITION_HOMES = {
    "tools/simple_tools.py": {"calculator", "date_today", "date_calc", "json_extract", "stats"},
    "tools/search_tools.py": {"web_search"},
    "tools/code_executor.py": {"execute_python"},
}


def _decorator_names(node: ast.AST) -> list:
    """把装饰器写成名字列表 —— `@tool` / `@tool()` / `@mod.tool` 都还原成 `tool`。"""
    out = []
    for d in getattr(node, "decorator_list", []):
        if isinstance(d, ast.Name):
            out.append(d.id)
        elif isinstance(d, ast.Attribute):
            out.append(d.attr)
        elif isinstance(d, ast.Call):
            f = d.func
            out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", "?"))
    return out


def _tool_definitions(path: Path):
    """产出 `(行号, 函数名, 装饰器们)` —— 只挑**被装饰的**、且名字是工具名的定义。

    只认**被装饰的**：`simple_tools_impl.calculator_impl` 那种裸 `def` 是**实现**，
       不是工具对象，不算重抄。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in TOOL_NAMES:
            continue
        decs = _decorator_names(node)
        if decs:
            yield node.lineno, node.name, decs


def test_only_home_modules_define_tools():
    """除正本外，**任何模块**都不该再 `@tool` 定义一个工具。

    反证：往任意模块塞一个 `@tool def calculator` ⇒ 本用例红并点名它。
    """
    offenders = {}
    for p in sorted(_product_py(API)):
        if p.name.startswith("test_"):
            continue
        allowed = DEFINITION_HOMES.get(p.relative_to(API).as_posix(), set())
        hits = [(ln, nm, decs) for ln, nm, decs in _tool_definitions(p) if nm not in allowed]
        if hits:
            offenders[p.relative_to(API).as_posix()] = hits

    assert offenders == {}, (
        "🔴 这些文件在**手抄工具定义**（正本见 DEFINITION_HOMES 表）：\n"
        + "\n".join(f"  {f}: {h}" for f, h in offenders.items())
    )


#: 运行时那条：`tools` 是**模块级**的这几个（`api_v1_rag` 的在函数体里 ⇒ 单独测不了）
DERIVED_MODULES = ["agent.agent_graph", "agent.agent_checkpointer", "agent.agent_graph_advanced_learning"]


@pytest.mark.parametrize("modname", DERIVED_MODULES)
def test_module_tools_are_derived_from_registry(modname):
    """每个模块交给 LLM 的 `tools` **必须是已注册工具的子集** —— 不许夹带私货。

    这是**行为面**那条：AST 只管"有没有多定义一份"，
       这条管"真正交出去的是不是同一批对象"。
    """
    import tools.mcp_server as mcp_server

    registered = {t["func"].name for t in mcp_server.TOOLS}
    mod = importlib.import_module(modname)
    names = {t.name for t in mod.tools}

    assert names <= registered, f"🔴 {modname}.tools 里有**未注册**的工具：{names - registered}"


def test_execute_python_is_in_every_graph_tool_table():
    """`execute_python` **要**在那两张图的工具表里。

    **本用例 翻过面**（原名 `..._not_yet_in_unisolated_graphs`，断言的是「**不在**」）。

    ## 为什么当初排除

    理由**两条**：
      ① 那两张图里它**没有容器隔离**（跑在宿主同进程树里，只有白名单 + 5 秒硬杀）
      ② 它**不在 `SENSITIVE_TOOLS`** 审批名单（默认只有 `web_search`）

    ## 为什么现在放开（作者 同意「甲」）

    · **①  解决了** —— 现在是**硬化容器**（只读根 · 无网 · 非 root · 无 cap · 5s/256MB/pids 限制）
    · **② 根本不是那两张图特有的** —— `execute_python` 从 `plan_execute`
      `agent_graph_advanced_learning` · `agent_graph_advanced`（经 MCP 动态取表）·
      `/agent/execute_code` **都拿得到**，而且**同样不在审批名单**。
      ⇒ **在两张图上抠掉它，并没有真的挡住什么**；它只买到"工具表在各图之间不一致"
      —— 而那正是 花一整批力气消除的东西（**一处事实源**）。

    **真正该管的地方是 `SENSITIVE_TOOLS`**（全局审批名单），不是"从两张图的派生表里抠掉"。
    「`execute_python` 要不要进审批名单」**已单独立为待裁项** —— 它是**独立裁定**，
       **不在本用例范围内**（本用例只管"工具表一致"）。

    **反证**：把 `_EXCLUDED_TOOLS` 加回任一模块 ⇒ 本用例红。
    """
    import agent.agent_graph as agent_graph
    import agent.agent_checkpointer as agent_checkpointer
    import tools.mcp_server as mcp_server

    registered = {t["func"].name for t in mcp_server.TOOLS}

    for mod in (agent_graph, agent_checkpointer):
        got = {t.name for t in mod.tools}
        assert "execute_python" in got, (
            f"🔴 {mod.__name__}.tools 里没有 execute_python —— 又把它排除回去了？"
        )
        # 顺手钉住「**没有任何别的排除**」 —— 别哪天又悄悄抠掉一个工具
        assert got == registered, (
            f"🔴 {mod.__name__}.tools 与 `mcp_server.TOOLS` **不一致**：差集={registered ^ got}"
        )


def test_all_graphs_share_the_same_tool_objects():
    """删掉三份副本之后，**它们拿到的是同一个对象** —— 这是「能力没丢」的判据。

    为什么单开一条：  把 `test_safe_math_wiring.py` 的守护面从 5 处收到 1 处，
    而那条规矩要求「**每删一个站点，就要能说出它原来守的能力现在由谁守**」。
    答案就是这一条 —— 用 `is`（**对象同一**）证明，**不是**"名字一样"。

    反证：把任意一张图改回自带一份 `@tool` ⇒ `test_only_home_modules_define_tools` 先红；
       若只把对象换成"另一个同名副本"，**本用例红**。
    """
    import agent.agent_graph as agent_graph
    import agent.agent_checkpointer as agent_checkpointer
    import agent.agent_graph_advanced_learning as agent_graph_advanced_learning
    import tools.mcp_server as mcp_server

    #: 唯一事实源 —— `mcp_server.TOOLS` 里那批**对象**
    shared = {t["func"].name: t["func"] for t in mcp_server.TOOLS}

    for mod in (agent_graph, agent_checkpointer, agent_graph_advanced_learning):
        got = {t.name: t for t in mod.tools}
        # 只钉这两个：`web_search` 三处本来就都导入同一个对象，
        #    而这两个原先各有一份**逐字等价但不同对象**的副本。
        for name in ("calculator", "date_today"):
            assert got[name] is shared[name], (
                f"🔴 {mod.__name__}.{name} **不是**共享对象（又抄了一份？）"
                f"\n   got ={got[name]!r}\n   want={shared[name]!r}"
            )


# ===========================================================================
# 单容器部署模式下不注册 execute_python
# ===========================================================================
def _registered_tools_in_subprocess(extra_env: dict):
    """在**子进程**里 import `mcp_server`，报回它注册的工具名。

    **必须走子进程**：`TOOLS` 是**模块级**建好的 ⇒ 同进程里改 env
       对已经 import 过的模块**无效**（本仓 `test_tool_dispatch.py` 踩过同一个坑，
       那里也是用子进程解决的）。别改成 `monkeypatch.setenv` —— 那会**静默无效**。
    """
    import json
    import os
    import subprocess
    import sys

    env = {**os.environ, **extra_env}
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json, tools.mcp_server; print(json.dumps([t['func'].name for t in tools.mcp_server.TOOLS]))"],
        capture_output=True, text=True, env=env, cwd=str(API),
    )
    assert proc.returncode == 0, f"子进程 import 失败：{proc.stderr[-400:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_execute_python_is_registered():
    """`execute_python` **必须注册着**（走独立的执行器容器）。

    **为什么保留它**：它原先是一条**正向对照** —— 当年还有一条
    「单容器部署模式下**不**注册它」（那个分支 已随那套部署一起删，见 `mcp_server.py`）。
    正向对照留着的理由在当年就写清楚了，现在照样成立：
    **没有它，一个"把 `TOOLS` 清空"的实现也能让别的断言绿** ——
    那正是本仓反复栽的「**尺子量不到它该量的事**」。

    判据是**子进程里重新 import 后的真值**，⛔ 不是"源码里有这一行"（`TOOLS` 是模块级建的）。
    """
    tools = _registered_tools_in_subprocess({})

    assert "execute_python" in tools, f"它该注册着，实际：{tools}"
    # 反向对照：别的工具**不许被顺手删掉**
    assert {"calculator", "date_today", "web_search"} <= set(tools), f"别的工具被误删了：{tools}"


# ===========================================================================
# ===========================================================================
# **为什么不能再用 `api_dir.glob("*.py")`**：
#   billing · agent · rag · tools）⇒ **根目录下再也扫不到它们** ⇒ `glob` 返回
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
