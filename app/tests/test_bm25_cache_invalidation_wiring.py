"""守卫：**凡写 `documents` 表的地方，都必须让 BM25 进程内缓存作废**。

## 为什么单开这道门（而不是把四个端点列个清单）

同一个坑**修过一次、没修全** —— 2026-09-11 的修复给了 `/rag/insert`（之前那次），
**漏了 `/rag/upload_document`** ⇒ 上传的新文档在 BM25 召回里"不存在"，直到别的写路径
清了缓存或进程重启。

⇒ **清单型守卫治不了这个病**：它只钉住"当时知道的那几个函数"，
下一个新写路径照样没人提醒。所以这里的写路径是**从代码里 AST 推出来的**，
不手工维护任何端点名单 —— 新增一条写路径而忘了清缓存，本文件立刻转红。

## 两条规矩

| # | 规矩 | 为什么 |
|---|---|---|
| **A** | 函数体内**直接**写 `documents` 的 SQL ⇒ 它**自己**必须调 `invalidate_bm25_cache` | 写就发生在这一层，没别的机会 |
| **B** | 函数调用了"写文档的 helper" ⇒ 要么**自己**调，要么**那个 helper** 调了 | 允许把不变量下沉到 helper（`db.insert_document`），但不许两边都不管 |

只扫**生产模块**（`app/*.py` 去掉 `test_*.py`）：测试里的探针自己负责清理
（`app/tests/test_isolation.py` 的 `_probe_cleanup` 就是干这个的）。
"""

import ast
import re
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层

# 「写 documents 表」的 SQL 形态
_WRITE_SQL = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+documents\b", re.IGNORECASE)

# 执行 SQL 的落点（`cur.execute(...)` / `execute_values(cur, ...)`）
_EXECUTE_NAMES = {"execute", "execute_values"}

_INVALIDATE_NAME = "invalidate_bm25_cache"


# --------------------------------------------------------------------------- #
# AST 工具
# --------------------------------------------------------------------------- #
def _iter_functions(tree):
    """顶层函数 + 类方法。

    **跳过嵌套函数** —— 否则外层函数会把内层的代码段一起算进来
       （`ast.get_source_segment(外层)` 包含内层的全部源码）。
    """
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(parent.get(node), (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield node


def _called_names(fn_node):
    """函数体里出现过的**被调用名**（`f(...)` 与 `obj.f(...)` 都算）。

    只按**名字**判，不做跨模块解析（`import` 别名 / 重导出统统不追）——
       本仓的落点全是直接名调用，够用；真出现别名调用时**本文件会漏**，
       那是**已知的判据边界**，不是"这里已经安全"。
    """
    names = set()
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _writes_documents(fn_node) -> bool:
    """函数体里有没有**直接**写 `documents` 的 SQL。

    判据取的是「**传给 `execute*` 的字符串常量**」，不是"源码段里出现过这个词"
       —— 后者会把**注释 / docstring 里提到 INSERT INTO documents** 也数进来。
    """
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in _EXECUTE_NAMES:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if _WRITE_SQL.search(arg.value):
                    return True
    return False


def _calls_invalidate(fn_node) -> bool:
    return _INVALIDATE_NAME in _called_names(fn_node)


def _scan():
    """⇒ (functions, writers)

    functions: `[(文件名, 函数名, AST 节点), ...]`（全部生产函数）
    writers:   `{函数名: (文件名, 函数名, AST 节点)}`（**直接**写 documents 的那些）
    """
    functions, writers = [], {}
    for path in sorted(_product_py(API_DIR)):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _iter_functions(tree):
            functions.append((path.name, node.name, node))
            if _writes_documents(node):
                writers[node.name] = (path.name, node.name, node)
    return functions, writers


# --------------------------------------------------------------------------- #
# 判据 A：直接写 documents 的函数，自己必须清缓存
# --------------------------------------------------------------------------- #
def test_每个直接写_documents_的函数都让缓存作废():
    _, writers = _scan()

    # 先钉住"扫描本身没瞎" —— 正则/路径一变就空集，而**空集会让下面恒过**
    assert writers, (
        "AST 扫描没找到任何写 documents 的函数 —— 判据本身失效了。"
        "先查：① app/*.py 是不是改名/换目录了 ② _WRITE_SQL 还认不认现在的 SQL 写法。"
    )

    missing = [f"{fname}::{name}" for fname, name, node in writers.values() if not _calls_invalidate(node)]
    assert missing == [], (
        "以下函数**直接写 `documents` 表**却没调 `invalidate_bm25_cache()`：\n  - "
        + "\n  - ".join(missing)
        + "\n⇒ 写完之后本进程内的 BM25 召回看不到变化（要么重启，要么等别的写路径顺手清掉）。"
        "\n   。"
    )


# --------------------------------------------------------------------------- #
# 判据 B：调用了"写文档 helper"的函数，两边至少有一边清了缓存
# --------------------------------------------------------------------------- #
def test_调用写文档helper的函数也清缓存():
    functions, writers = _scan()
    writer_names = set(writers)

    unprotected = []
    for fname, name, node in functions:
        if name in writer_names:
            continue  # 它自己由判据 A 管
        for called in _called_names(node):
            if called not in writer_names or called == name:
                continue
            if _calls_invalidate(node):
                continue  # 调用方自己清了
            if _calls_invalidate(writers[called][2]):
                continue  # helper 里清了（不变量下沉）
            unprotected.append(f"{fname}::{name} 调了 {called}()，两边都没清缓存")

    assert unprotected == [], (
        "以下调用点**写了文档却没让 BM25 缓存作废**：\n  - "
        + "\n  - ".join(unprotected)
        + "\n⇒ 插入后本进程内的 BM25 召回看不到新文档（记的就是这个："
        "\n   `/rag/upload_document` 漏了调用，而它调的 helper 当时也不清）。"
    )


# --------------------------------------------------------------------------- #
# 行为侧：AST 说"调了" ≠ 真清了 —— 真跑一次 `db.insert_document` 看桶
# --------------------------------------------------------------------------- #
class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        _FakeCursor.last_sql = sql


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass


def test_insert_document_跑一次真把缓存桶清空(monkeypatch):
    """不连库：把 `db.get_db` 换成假连接，只验**缓存这一件事**。"""
    import rag.bm25_index as bm25_index
    import core.db as db

    monkeypatch.setattr(db, "get_db", lambda: _FakeConn())

    bm25_index._bm25_cache["sentinel-user"] = {"bm25": None, "docs": []}
    try:
        db.insert_document("测试内容", "测试来源", [0.0] * 8, "sentinel-user")
        assert "sentinel-user" not in bm25_index._bm25_cache, (
            "`db.insert_document()` 之后缓存桶还在 ⇒ 新文档进不了 BM25 召回"
        )
        assert "INSERT INTO documents" in _FakeCursor.last_sql, (
            "假连接没收到插入语句 —— 这条用例没验到想验的东西"
        )
    finally:
        bm25_index._bm25_cache.clear()


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
