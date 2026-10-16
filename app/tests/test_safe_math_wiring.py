"""接线守卫：`eval(expression)` 那 5 处**真的**收口到 `safe_math` 了吗？

**为什么单开一个文件**（本仓的老毛病）："写好了" ≠ "接上了"。
`safe_math.py` 本身测得再绿，只要有一个调用点还留着 `eval`，那条路照样是 RCE。
同族守卫 ⇒ `app/tests/test_approval_trigger.py` 的姊妹篇 `app/tests/test_pending_approvals_wiring.py`

**两道，覆盖面不同**（不是重复）：

| 闸 | 覆盖 | 挡什么 |
|---|---|---|
| **行为验证** | 1 处（能离线调起来的） | 最强的一道：**喂真实恶意表达式，验副作用没发生** —— 断言"返回了错误字符串"**不够**，旧代码也返回错误字符串 |
| **AST 静态** | 2 处（含 `tools_with_cache`） | "还留着 `eval` 调用" / "没把入口指向 `calculate`" |

## 2026-10-08：站点面**从 5 处收到 1 处**

改前 `calculator` 被**抄了 5 份**； 删掉 `agent_graph` / `agent_checkpointer` /
`agent_graph_advanced_learning` 三份， 再删 `tools_with_cache` 那份
⇒ **只剩 `simple_tools_impl.calculator_impl` 一处**。

**删站点 = 守卫覆盖面缩小**，所以每删一处都得能回答「**它原来守的能力现在由谁守**」：

| 删掉的站点 | 现在由谁守 |
|---|---|
| `agent_graph.calculator` | **不再是"另一份实现"** —— 该图的 `calculator` **就是** `simple_tools` 那个对象（`simple_tools_impl.calculator_impl`）。⇒ 守 `simple_tools_impl` **= 守它**。<br>**这句话有判据，不是嘴上说说**：`test_tool_registry_single_source.py::test_all_graphs_share_the_same_calculator_object` 断言**对象同一**（`is`），不是"名字一样"。 |
| `agent_checkpointer.calculator` | 同上（同一张对象表） |
| `agent_graph_advanced_learning.calculator` | 同上 |

⇒ 所以 `OFFLINE_SITES` 收缩成一条**不是"放宽断言"**，是**实现真的只剩一份**了。

**2026-10-08 ·  ：`app/tools_with_cache.py` 整个模块删掉了。**

它此前是【第 6 份 `calculator` 拷贝】，靠本文件那条**静态**闸守着
（它被 `@cached_tool` 包着 ⇒ 调一次就要连 Redis，故一向只进静态那道、不进行为那道）。

**需求方 2026-10-08 原话：「`tools_with_cache.py`，删除，这是定好的事了，这个是重复的，没用了」**
—— ⇒ 它**不再是**"挂起"（原先的 ⏸ 理由是「缓存机制不是废物，是没接上」；
现在 已经把缓存接在**工具函数体**上，它就成了纯重复）。

**删它之前的两条判据**（都跑过）：
· `grep -rn --include='*.py' 'tools_with_cache' app/` ⇒ **只剩注释**，没有任何 import
· `grep -rn 'get_weather' app/` ⇒ 只剩 `api_v1.py` 的两处**注释**（那是个 mock 天气工具）

**删掉一个站点 = 守卫面缩小**，所以本文件**同时**收窄了 `ALL_SITES`
（`tools_with_cache` 那条一并删）—— 两件事**必须同一个 commit**，
否则"删文件"会悄悄把守卫的面缩了、而没人记得说过。
"""

import ast
import importlib
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层


# ===========================================================================
# 零、**唯一的**检测函数 —— 两条闸都调它，不许各写一份（那正是漂移的来源）
# ===========================================================================
def eval_name_offenders(tree: ast.AST) -> list[int]:
    """返回**所有「裸名 `eval`」**出现的行号 —— 不是"所有 `eval(...)` 调用"。

    **2026-10-04 修：原判据只抓 `ast.Call` 且 `func` 是裸名 `eval`**，
    于是这一行**两条闸都看不见**：

        result = await asyncio.to_thread(eval, expression)      # api_v1_rag.py:801

    `eval` 是**实参**，不是被调用的目标 —— `ast.Call` 的 `func` 是 `asyncio.to_thread`
    ⇒ 旧判据返回**空**，而**命令真的跑了**（实测：返回值 `'0'`，`touch` 的文件真被创建）。

    ⇒ **改成「只要这个名字被读出来就报」**，与它的**调用形状无关**：
      · `eval(x)` 抓到（`ast.Name` 一定在）
      · `to_thread(eval, x)` 抓到 —— **旧判据漏的就是这个**
      · `f = eval` / `getattr(m, "eval")` 之类 一并抓到

    **不会误伤 `redis_client.eval(...)`**（`app/access/rate_limiter.py` 真家伙）——
       它的 `eval` 是 `ast.Attribute.attr`，**不是** `ast.Name`。

    **改判据时要看它能不能红**（本仓两条教训）：这次改完**先跑**，
       红在 `api_v1_rag.py:801` 才算数 —— 判据纪律见 `app/agent/agent_graph.py` 同名注释 /。
    """
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "eval"
    )


# ===========================================================================
# 一、行为验证：喂恶意表达式，验"没被执行"
# ===========================================================================
#: (模块名, 入口名) —— 入口名不同是因为 `simple_tools_impl` 那份叫 `calculator_impl`。
#:
#: 2026-10-08：原先这里有 **4** 条（三张图各自那份 + 本处）。
#:    那三份**已被删除** —— 它们与 `simple_tools` 那份逐字等价，属"重复定义"。
#:    ⇒ 现在**只剩一处实现**，见文件头那张「删掉谁、现在由谁守」的表。
#: **别再往这里加回模块名** —— 那意味着又有人抄了第 N 份，
#:       `test_tool_registry_single_source.py` 会先红。
OFFLINE_SITES = [
    ("tools.simple_tools_impl", "calculator_impl"),
]


def _entry_point(module_name: str, attr: str):
    """取到**底下那个真函数**。

    langchain 的 `@tool` 会把函数换成 `StructuredTool` ⇒ 不 `.func` 的话，
       拿到的是工具对象，`fn("1+1")` 会走成"把字符串当 config"，报一个看不出原因的错。
    """
    obj = getattr(importlib.import_module(module_name), attr)
    return getattr(obj, "func", obj)


@pytest.mark.parametrize(
    "module_name,attr", OFFLINE_SITES, ids=[f"{m}.{a}" for m, a in OFFLINE_SITES]
)
def test_site_still_does_arithmetic(module_name, attr):
    """收口不许把功能一起收掉：正常算式结果不变。"""
    fn = _entry_point(module_name, attr)

    assert fn("6*7") == "42"
    assert fn("7/2") == "3.5"
    assert fn("1/0").startswith("计算错误:")


@pytest.mark.parametrize(
    "module_name,attr", OFFLINE_SITES, ids=[f"{m}.{a}" for m, a in OFFLINE_SITES]
)
def test_site_does_not_execute_the_expression(module_name, attr, tmp_path):
    """**本文件最要紧的一条**：`eval` 没了没有，就看**命令有没有真的跑**。

    只断言返回值是不够的 —— **改前实测：它返回的是 `'0'`**（`system` 的返回值被
       `str` 成了 `"0"`）⇒ 命令**执行成功**，而**模型收到的是一条正常的"答案是 0"**。
       ⇒ 唯一的判据是**副作用**：那个文件**必须没被创建**。
    """
    marker = tmp_path / "pwned_by_eval"
    fn = _entry_point(module_name, attr)

    out = fn(f"__import__('os').system('touch {marker}')")

    assert out.startswith("计算错误:"), f"应当被拒，实际：{out!r}"
    assert not marker.exists(), (
        f"🔴 {module_name}.{attr} 把表达式**执行**了 —— `eval` 还留在里面"
    )


@pytest.mark.parametrize(
    "module_name,attr", OFFLINE_SITES, ids=[f"{m}.{a}" for m, a in OFFLINE_SITES]
)
def test_site_cannot_read_a_file(module_name, attr, tmp_path):
    """换一种逃逸形状再验一遍（别只堵一种 payload 就以为堵住了）。

    **文件必须真的存在** —— 否则 `open` 抛 `FileNotFoundError`，改前的 `eval`
       也会返回 `计算错误: …` ⇒ 这条就变成"改前就绿"的假测试了。
       ⇒ 判据是**内容没有被读出来**：结果里不许出现 `TOP_SECRET`。
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP_SECRET_abc123", encoding="utf-8")
    fn = _entry_point(module_name, attr)

    out = fn(f"open({str(secret)!r}).read()")

    assert out.startswith("计算错误:"), f"应当被拒，实际：{out!r}"
    assert "TOP_SECRET_abc123" not in out, f"🔴 文件内容被读出来了：{out!r}"


# ===========================================================================
# 二、AST 静态：这些站点都不许再出现 `eval(...)`，且都要指向 `calculate`
# ===========================================================================
#: 全部站点 —— **这份名单是"收口的定义"**：将来再复制一份 `calculator`，
#:    要把它加进来（别让它成为漏网的）。
#: 2026-10-08：**5 → 1**。三张图那三份由  删、`tools_with_cache` 那份由  删
#:    ⇒ 现在**只剩 `simple_tools_impl` 一处实现**，故 `ALL_SITES == OFFLINE_SITES`。
#: 别把这一行删掉、也别改回字面量列表 —— 留着是为了"加站点时只有一个地方要改"。
ALL_SITES = list(OFFLINE_SITES)


def _site_files():
    return [(f"app/{name.replace(chr(46), chr(47))}.py",
             API_DIR / (name.replace(".", "/") + ".py")) for name, _ in ALL_SITES]


@pytest.mark.parametrize("label,path", _site_files(), ids=[p for p, _ in _site_files()])
def test_no_eval_call_remains(label, path):
    """用 AST 而**不是 grep**：注释里也写着 `eval(expression)` 这几个字，
    正则会把说明文字的命中当成违规（本仓栽过：见 本仓的复盘记录（⛔ 不随产品仓提供））。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = eval_name_offenders(tree)

    assert not offenders, f"🔴 {label} 里还有裸 `eval` 的引用，行号：{offenders}"


@pytest.mark.parametrize("label,path", _site_files(), ids=[p for p, _ in _site_files()])
def test_site_delegates_to_safe_math(label, path):
    """正向那条：光"没有 eval"还不够 —— 得**确实调用了** `calculate`。

    只查"调了 `calculate`"，不规定 `from … import calculate` 还是 `safe_math.calculate(...)`
       —— 那是实现细节，钉死只会让下次重构白改一遍测试。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    delegates = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "calculate")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "calculate")
        )
    ]

    assert delegates, f"🔴 {label} 没有调用 `safe_math.calculate` —— 入口没指过去"


# ===========================================================================
# 三、名单自身别放过期了
# ===========================================================================
def test_no_other_module_defines_an_eval_based_calculator():
    """防止"第 n 份拷贝"：全仓扫一遍，谁引用裸名 `eval` 就当违规。

    扫的是**整个 `app/`**（不只是上面那 5 个文件）—— 上面那 5 个是"已知的"，
       这条管的是"**将来又冒出来的**"。本仓的拷贝文化是真的：这一份就被复制了 5 次。

    **2026-10-04：这条闸原先【有形盲区】** —— 判据是 `ast.Call` 且 `func` 是裸名
       `eval` ⇒ `asyncio.to_thread(eval, expression)`（`api_v1_rag.py:801`）**它抓不到**，
       而那一处**真的在执行任意代码**（匿名 WS `/ws/agent` 可达）。⇒ 改用 `eval_name_offenders`。
    """
    offenders = []
    for path in sorted(_product_py(API_DIR)):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.name}:{n}" for n in eval_name_offenders(tree)]

    assert not offenders, (
        "🔴 全仓还有裸名 `eval` —— 每多一处，就是一个未收口的任意代码执行面："
        f"{offenders}"
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
