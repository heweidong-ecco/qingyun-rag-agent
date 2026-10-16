"""守卫：**接受 `limit` 的端点，必须【说出来自己截断了】** —— `frontend/README.md` §六 红线②。
"""
import ast
import re
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层
REPO = API_DIR.parent
EXEMPT_FILE = REPO / "scripts" / "truncation-exempt.txt"

#: 「有 `limit` 的端点」至少该有这么多 —— **不是凑数**：扫不到时它是唯一会红的东西。
MIN_ENDPOINTS = 3

#: 签名里出现它就当"这个端点是分页/截断型"
_LIMIT_PARAM = "limit"

#: 响应里认这两个键（**任一即可**）
_DECLARED = ("truncated", "has_more")

#: 页面路由这类装饰器（被 `@router.<method>` 装饰的函数才算端点）
_ROUTE_DECORATOR = re.compile(r"^router\.(get|post|put|delete|patch)$")


def _iter_endpoints(tree):
    """产出 `(函数名, 参数名集合, 节点)` —— 只认被 `@router.<method>` 装饰的。"""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_route = any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and _ROUTE_DECORATOR.match(f"router.{d.func.attr}")
            for d in node.decorator_list
        )
        if not is_route:
            continue
        args = [a.arg for a in node.args.args + node.args.kwonlyargs]
        yield node.name, set(args), node


def _returned_keys(fn_node) -> set:
    """函数里**所有** `return {…}` 的字面量键（含 `{**x, "k": v}` 的 `**x` 忽略）。

    只看**字典字面量** —— 返回一个变量（`return data`）时**看不见键**
    那种情况一律**当"没声明"**（宁可贵一次，不要静默放过）。
    """
    keys = set()
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        v = node.value
        if isinstance(v, ast.Dict):
            for k in v.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def _load_exempt() -> dict:
    """ —— 每行 `<端点名>  <理由>`；`#` 开头是注释。"""
    if not EXEMPT_FILE.exists():
        return {}
    out = {}
    for line in EXEMPT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        out[parts[0]] = parts[1] if len(parts) > 1 else ""
    return out


def _scan():
    found, offenders = [], []
    exempt = _load_exempt()
    for py in sorted(_product_py(API_DIR)):
        if py.name.startswith("test_"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for name, args, node in _iter_endpoints(tree):
            if _LIMIT_PARAM not in args:
                continue
            found.append(name)
            if _returned_keys(node) & set(_DECLARED):
                continue
            if name in exempt:
                continue
            offenders.append((py.name, name))
    return found, offenders


def test_scan_is_not_empty():
    """**防空跑** —— 扫到 0 个端点时，下面的用例**一条都不会红**，而那是假的绿。

    （本仓前科：`pre-commit-gates.py` 空跑 · `test_web_pages.py` 的 glob 写错。）
    """
    found, _ = _scan()
    assert len(found) >= MIN_ENDPOINTS, (
        f"只扫到 {len(found)} 个带 `limit` 的端点（预期 ≥ {MIN_ENDPOINTS}）：{found}\n"
        "  ⇒ ⚠️ 要么本文件的 AST 规则写歪了（装饰器名 / 参数名变了），"
        "要么端点真的变少了 —— 两种都不该静默通过。"
    )


def test_every_limit_endpoint_declares_truncation():
    """**本文件的正身**：收了 `limit`，就必须回 `truncated` 或 `has_more`。

    **不是"必须做 offset 分页"** —— 见模块 docstring：那条不一定对。
    本文件只钉「**必须说出来**」这一半。
    """
    found, offenders = _scan()
    assert not offenders, (
        "这些端点收了 `limit` 却【不告诉调用方自己被截了】：\n"
        + "\n".join(f"  · {f}::{n}" for f, n in offenders)
        + "\n⇒ 违的是 `frontend/README.md` §六 红线②「被截断必须说出来」。\n"
        "   ⚠️ 它**不报错**，所以除了本文件**没有任何门会红**。\n"
        "   ✅ 两种改法都行：① 回 `truncated`（+ 服务端算合计，照 `/agent/trace/{id}/cost` 的先例）\n"
        "                    ② 回 `has_more`（+ `offset`，照 `/agent/approvals/history` 的先例）\n"
        "   ⚠️ 真要豁免 ⇒ 登记进  并**写明理由**（那个文件是给人审阅的）。"
    )


def test_exempt_file_entries_are_real():
    """豁免清单里的名字必须**真的对得上一个端点** —— 否则它是一条**永不生效**的豁免。

    （同族：`doc-links-ignore.txt` 里过期的那几行 —— 那族本仓收过口。）
    """
    found, _ = _scan()
    stale = [k for k in _load_exempt() if k not in found]
    assert not stale, (
        f"豁免清单里这些名字不对应任何带 `limit` 的端点：{stale}\n"
        "  ⇒ 要么端点改名/删了（该删这一行），要么名字打错了（那这条豁免从来没生效过）。"
    )


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
