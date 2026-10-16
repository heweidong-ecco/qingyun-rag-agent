"""守卫：**每一个 `plan_task` 调用点都必须把 `user_name` 传下去**。
"""

import ast
from pathlib import Path

# `parents[1]` = 本仓的 `app/`（`app/tests/` 的上层）—— 与同目录其它守卫用例口径一致
API_DIR = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层

_TARGET = "plan_task"

# 只对**位置参数个数**设下界：`plan_task(goal, user_name)` 是 2 个。
#    第 3 个参数（`on_token`）可有可无（两个端点里一个传了一个没传），别把上界也钉死。
_MIN_POSITIONAL = 2


def _call_sites():
    """⇒ `[(文件名, 行号, 传给 plan_task 的实参, 那些实参上的关键字), ...]`"""
    sites = []
    for path in sorted(_product_py(API_DIR)):
        # 只扫**生产模块**（同 `test_bm25_cache_invalidation_wiring.py` 的口径）：
        #    测试里的探针自己负责传对，本门管的是产品代码。
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # ① 直接调用：`plan_task(...)`
            if isinstance(node.func, ast.Name) and node.func.id == _TARGET:
                sites.append((path.name, node.lineno, node.args, node.keywords))
                continue

            # ② 当【回调】传给别人：`plan_task` **之后**的那些实参才是它的。
            #    `asyncio.to_thread(func, /, *args, **kwargs)` —— func 后面的
            #    位置参数与关键字参数**都转发给 func** ⇒ 正好就是它的实参/关键字。
            for i, arg in enumerate(node.args):
                if isinstance(arg, ast.Name) and arg.id == _TARGET:
                    sites.append((path.name, node.lineno, node.args[i + 1:], node.keywords))
    return sites


def test_每个_plan_task_调用点都传了_user_name():
    sites = _call_sites()

    # 先钉住"扫描本身没瞎" —— 名字/目录一变就成空集，而**空集会让下面恒过**
    #    （本仓原话：「**从不命中」与「没人违规」在机器痕迹上完全一样**）
    assert sites, (
        "AST 扫描没找到任何 `plan_task` 调用点（2026-10-05 实测应为 3 处）——"
        "判据本身可能失效了。先查：① 调用点是不是被挪去别的目录了 ② 是不是改成别名调用了。"
    )

    bad = [
        f"{fname}:{lineno}（只给了 {len(args)} 个位置参数、也没有 `user_name=` 关键字）"
        for fname, lineno, args, keywords in sites
        if len(args) < _MIN_POSITIONAL and not any(kw.arg == "user_name" for kw in keywords)
    ]

    assert bad == [], (
        "以下 `plan_task` 调用点**没传 `user_name`** ⇒ 它会走默认 `\"unknown\"`：\n  - "
        + "\n  - ".join(bad)
        + "\n⇒ 这次调用的 token **既不受该用户的预算约束、也不算在他头上**。"
        "\n   ⚠️ 这个洞在本仓已经漏过**两次**（`dynamic_input` 那次 + 重规划那次），"
        "\n      而第 1 次修完写下的教训【没拦住第 2 次】—— 所以才有本文件。"
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
