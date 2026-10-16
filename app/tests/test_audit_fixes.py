"""全仓审计的 A / B 两条修复的回归测试（2026-09-20）

| # | 位置 | 问题 |
|---|---|---|
| **A** | `app/main.py:440` | **`/ready` 在 DB/Redis 挂掉时返回 500 而非 503** —— `health_check` 不健康时 `return JSONResponse(...)`，而 `/ready` 对它调 `health.get("status")` ⇒ **`JSONResponse` 没有 `.get`** ⇒ `AttributeError` ⇒ 全局处理器返回 500 |
| **B** | `app/routing/api_v1.py:136,144` · `app/routing/schemas.py:86` | **公开 `/docs` 上的安全声明与代码相反** —— 原文三句「还没有加入初始管理员」「当前版本暂未强制校验管理员身份」「管理接口…暂不加权限控制」，而代码里 `api_v1.py:156` = `Depends(require_admin)`、`main.py:466` 启动即 `ensure_admin_exists` |

这两条都是**"声明与事实不符"**型 —— 靠跑一遍端点**发现不了 B**（它只出现在 `/docs` 与 Postman 导入里），
   所以 B 的用例直接查 **OpenAPI 文档对象**，而不是发请求。
"""
import ast
import contextlib
import pathlib


# ===========================================================================
# A · /ready 健康检查不通过时必须是 503，不是 500
# ===========================================================================
def test_ready_returns_503_not_500_when_unhealthy(monkeypatch):
    """/ready 在底层不可用时必须回 **503**（"暂时不可用"），**不能是 500**（"服务内部错误"）。

    实测根因（2026-09-20）：`health_check` 有**两种返回类型** ——
       健康时返回 `dict`，不健康时 `return JSONResponse(status_code=503, ...)`。
       而 `/ready` 无条件对它调 `.get("status")`
       ⇒ `JSONResponse` 没有 `.get` ⇒ `AttributeError: 'JSONResponse' object has no attribute 'get'`
        ⇒ 被全局异常处理器兜住 ⇒ **500**。

    对 **K8s / 负载均衡** 来说这两个码是**不同语义**：
       503 = "别把流量给我，等会儿再试"；500 = "我坏了"。
       把"依赖挂了"报成"我坏了"，正是就绪探针最不该犯的错。

    ⇒ 本用例**真调 `/ready`**，把 DB 与 Redis 两条探针都打挂。
    """
    import main as M
    from fastapi.testclient import TestClient

    # 越过 /ready 的「启动后 10 秒内一律 503」那层门，否则测不到后面的逻辑
    monkeypatch.setattr(M, "APP_START_TIME", 0)

    def _boom(*_a, **_kw):
        raise RuntimeError("模拟依赖不可用")

    @contextlib.contextmanager
    def _dead_db():
        raise RuntimeError("模拟数据库不可用")
        yield  # pragma: no cover

    monkeypatch.setattr(M, "get_db", _dead_db)
    monkeypatch.setattr(M.redis_client, "ping", _boom)

    # `raise_server_exceptions=False` —— **本用例要断言的是真实 HTTP 状态码**，
    #    而不是"抛了个异常"。默认的 True 会让异常直接冒到测试里，
    #    那样就**测不出**"生产上到底是 500 还是 503"（生产环境有全局处理器兜住）。
    client = TestClient(M.app, raise_server_exceptions=False)
    resp = client.get("/ready")

    assert resp.status_code == 503, (
        f"/ready 在依赖不可用时返回了 **{resp.status_code}**（期望 **503**）。"
        "500 的语义是'服务内部错误'，会让 K8s/LB 误判 —— "
        "这两条依赖挂了应当报 503（暂时不可用）。"
        f"实际响应体：{resp.text[:200]}"
    )


# ===========================================================================
# B · 公开 /docs 上的安全声明不能与代码相反
# ===========================================================================
# 这三句是原文里**与代码相反**的断言（改后的描述不该再出现它们）
_CONTRADICTORY_CLAIMS = (
    "还没有加入初始管理员",
    "暂未强制校验管理员身份",
    "暂不加权限控制",
)


def test_openapi_descriptions_do_not_deny_admin_enforcement():
    """`/docs`（= `app.openapi`）里不能出现"未加管理员/未鉴权"这类**与代码相反**的声明。

    实测（2026-09-20）：`/api/v1/admin/create_user` 的 `description` 里有三句
       「这个系统内**还没有加入初始管理员**」「当前版本**暂未强制校验管理员身份**」
       「管理接口（仅供管理员使用，**暂不加权限控制**）」——
       而代码事实是：`app/routing/api_v1.py:156` = `Depends(require_admin)`（非 ADMIN 直接 403）、
       `app/main.py:466` 启动时 `ensure_admin_exists(logger)`（初始管理员是有的）。

    **这些字符串是 OpenAPI `description`，会原样渲染进公开的 `/docs`，并被 Postman 导入**
       ⇒ 面向**外部读者**的**安全声明**，说反了比不说更糟（会让人以为管理接口是敞开的）。
    它**只出现在文档对象里**，发请求测不出来 ⇒ 所以本用例查 `app.openapi`。
    """
    import main as M

    spec = M.app.openapi()
    hits = []
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in (ops or {}).items():
            if not isinstance(op, dict):
                continue
            text = " ".join(str(op.get(k, "")) for k in ("summary", "description", "response_description"))
            for claim in _CONTRADICTORY_CLAIMS:
                if claim in text:
                    hits.append(f"{method.upper()} {path} :: 「{claim}」")

    assert not hits, (
        "OpenAPI 描述里仍有**与代码相反**的安全声明（会渲染进公开 /docs）：\n  "
        + "\n  ".join(hits)
        + "\n代码事实：`admin/create_user` 用的是 `Depends(require_admin)`；启动时 `ensure_admin_exists`。"
    )


# ===========================================================================
# §二 · 一个类里不能有【同名方法定义两次】
# ===========================================================================
def test_no_duplicate_method_definitions_in_preprocessor():
    """`document_preprocessor.py` 的类里，**同名方法不能定义两次**。

    实测（2026-09-20）：`remove_noise_markers` 被定义了**两次** ——
       `:85-86` 是**只有一句 docstring 的空壳**，`:88-103` 才是真实现。
       Python **后者胜出** ⇒ 当前行为是对的，**但改 `:85` 那份不会生效** ——
       下一个人照着上面那份改，改完"没反应"，且**没有任何报错**。

    **为什么不用"跑一下看输出"来测**：两条定义**在行为上恰好相同**
       （一条是空的、一条是真的，但真正生效的始终是后面的）
       ⇒ 无论修没修，跑 `remove_noise_markers` **都通过**。
       ⇒ 这是**结构缺陷**，只能用**结构判据**守。

    与 `CLAUDE.md` 修复记录 **#7 同型** —— `db.py` 的 `get_db` 曾被重复定义
       **写入不提交**（那次是行为真的错了）。本仓已两次踩同一个坑。
    """
    src = (pathlib.Path(__file__).resolve().parents[1] / "rag" / "document_preprocessor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        seen = {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen.setdefault(item.name, []).append(item.lineno)
        for name, linenos in seen.items():
            if len(linenos) > 1:
                problems.append(f"{node.name}.{name} 定义了 {len(linenos)} 次，行号：{linenos}")

    assert not problems, (
        "同一个类里有【同名方法定义多次】—— 先定义的那份**永远不会生效**，"
        "改它不会报错、也不会有任何效果：\n  " + "\n  ".join(problems)
    )


# ===========================================================================
# §三·A9/A10 · 函数体里不能有【孤立的 docstring】（不是首句的裸字符串表达式）
# ===========================================================================
def test_no_stray_docstrings_in_function_bodies():
    """`app/` 下**不允许**出现"孤立的 docstring" —— 函数体里【不是首句】的裸字符串表达式。

    实测（2026-09-20）：全仓有 **8 处**这种写法（审计只列了 6 处，`ast` 检查又抓出 2 处 ——
       `api_v1_rag.py:261` 的 `upload_document`、`main.py:503` 的 `graceful_shutdown`）。
       它们**全是空操作**（Python 求值后丢弃），典型来历是**docstring 被后插入的代码挤开**：

           def f:
               log_something          # ← 后插进来的
               <那句本该是 docstring 的字符串>   # ← 于是成了空操作，f 实际上没有 docstring

       ⇒ **函数看起来"有文档"，但 `f.__doc__` 是 None**，`help` / IDE 提示 / 自动文档**全拿不到**。

    本段注释里**故意不写出三引号** —— 写的话会把本 docstring 提前闭合（我第一版就这么错的）。

    **为什么不能用"跑一下看行为"来测**：它是**纯空操作**，跑任何调用都通过
       ⇒ 只能用**结构判据**（`ast`）守。

    **与 `CLAUDE.md` 修复记录 #7、以及本文件里那条"重复方法定义"用例同型**：
       都是"**代码与它看起来的样子不一致**"，且都只能靠结构判据发现。
    """
    roots = pathlib.Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层
    hits = []
    for f in sorted(_product_py(roots)):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for i, stmt in enumerate(node.body):
                if i == 0:
                    continue          # 首句是正常 docstring
                if (isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)):
                    hits.append(f"{f.name}:{stmt.lineno}  在 {node.name}() 里（函数体第 {i+1} 句）")

    assert not hits, (
        "函数体里有【孤立的 docstring】（空操作 ⇒ 该函数实际没有 docstring）：\n  "
        + "\n  ".join(hits)
        + "\n修法：上移到 `def` 正下方（若函数已有 docstring，则把那句合并进去并删掉）。"
    )


# ===========================================================================
# §三·C5 · 缓存装饰器"抢不到锁"时【不能无限递归】
# ===========================================================================
def test_cached_tool_does_not_recurse_forever_when_lock_never_acquired(monkeypatch):
    """`cached_tool` 在**始终抢不到锁**时，必须**有上限**，不能无限递归。

    实测（2026-09-20）：原实现在 `else` 分支里
           time.sleep(0.1); return wrapper(*args, **kwargs)
       ⇒ 如果锁**一直**拿不到（持有者崩了没删锁、或一直被别的请求续上），
         **递归没有上限** ⇒ 栈溢出 / 无限等待。

    这是**并发正确性**问题，不是风格问题 —— 它只在高并发下暴露，
       而一旦暴露就是**进程级**故障（RecursionError 把整个请求打死）。

    期望行为：等一小段（有上限）之后**降级为直接执行**（这次不写缓存），
       而不是继续递归 —— 缓存是优化，**不该因为它拿不到就拒服务**。
    """
    import tools.tool_cache as T

    # 永远抢不到锁
    monkeypatch.setattr(T.redis_client, "set", lambda *a, **k: False)
    monkeypatch.setattr(T.redis_client, "get", lambda *a, **k: None)
    monkeypatch.setattr(T.redis_client, "delete", lambda *a, **k: None)
    # 把等待缩短，否则用例会慢（原实现 0.1s × 上千次递归）
    monkeypatch.setattr(T.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(T, "_LOCK_WAIT_SECONDS", 0.2, raising=False)

    calls = []

    @T.cached_tool(expire_seconds=1)
    def _probe(x):
        calls.append(x)
        return x * 2

    result = _probe(3)          # 老实现会在这里 RecursionError

    assert result == 6, "抢不到锁时应当【降级为直接执行】，把结果正常返回"
    assert calls == [3], "且只应真正执行一次（不能重复调用工具函数）"


# ===========================================================================
# §三·3.7 / N14 · markdown 图片规则【不是死代码】—— 别删
# ===========================================================================
def test_markdown_image_rule_is_not_dead():
    """`document_preprocessor.remove_noise_markers` 里那条 markdown 图片规则，**不是死代码**。

    **登记文件原写「这条规则从未生效」—— 实测证明那是错的**（2026-09-21）：
       它在 `legal` / `medical` 两个域**正在干活**（那两个域的配置里【没有】图片规则）
       ⇒ **删掉它，那两个域的图片处理会坏。**

    **为什么要有这条用例**：本仓规矩是「**有结构才执行，只有文字就漏**」——
       光在那行代码上写注释"别删"，下一个会话照样会删。

    判据（三个域各跑一次）：
      · `legal` / `medical` —— 靠**硬编码这一条**把 `![alt](url)` 收敛成 `alt`
      · `default` —— 配置里有一条**逐字等价**的规则**先跑** ⇒ 这一条多余；
        但 `default` 的 URL 规则**排在图片规则之前**，把 `![alt](http://…)` 打成 `![alt](`
        ⇒ **两条图片规则都不匹配**（**真问题在这里**）
    """
    from rag.document_preprocessor import DocumentPreprocessor

    # ① legal / medical 域：硬编码那条【在干活】—— 两个域都必须收敛成功
    for dom in ("legal", "medical"):
        out = DocumentPreprocessor(domain=dom).remove_noise_markers("看图 ![图](http://x/y.png) 结束")
        assert out == "看图 图 结束", (
            f"domain={dom} 的图片收敛坏了（得到 {out!r}）⇒ "
            "`document_preprocessor.remove_noise_markers` 里那条 markdown 图片规则"
            "被删了/失效了。⚠️ 它不是死代码：legal/medical 两个域的配置里【没有】图片规则，"
            "靠的就是它。"
        )

    # ② default 域：相对路径也要收敛（这一条是配置里那条等价规则在做）
    d = DocumentPreprocessor(domain="default")
    assert d.remove_noise_markers("看图 ![图](local.png) 结束") == "看图 图 结束"

    # ③ **故意锁住当前已知的缺陷** —— `default` 域 + http 图片仍然残废。
    #    这条断言红了 = 有人把 default 域的规则顺序修好了 ⇒ 请去更新登记与代码注释，
    #    而不是让文档悄悄过期（与 N17 那条用例同一个设计）。
    out = d.remove_noise_markers("看图 ![图](http://x/y.png) 结束")
    assert out == "看图 ![图](  结束", (
        f"`default` 域的 http 图片行为变了（得到 {out!r}）。\n"
        "  · 若这是【修好了】（变成 '看图 图 结束'）⇒ 请更新 "
        "`docs/待办登记-2026-09-20-全仓审计与方向更正.md` §三·3.7 "
        "与 `document_preprocessor.py` 里那段 N14 注释。\n"
        "  · 若只是【变了但没修好】⇒ 请查清原因，这可能是回归。"
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
