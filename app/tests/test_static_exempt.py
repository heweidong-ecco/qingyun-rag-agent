"""—— **静态资源没被限流豁免**。
"""
import inspect


# ===========================================================================
# ① 主判据：真实静态资源路径必须豁免
# ===========================================================================

def test_real_static_asset_paths_are_exempt():
    """**本批要修的那条**：页面真正会去拉的那几个静态资源，必须判为豁免。

    路径逐条取自真实页面（`app/static/web/*.html` 里的 `<link>` / `<script>`），
    不是编的 —— 判据要在**真实**路径上成立，否则修了等于没修。
    """
    from main import is_middleware_exempt

    #    旧写的是 `/static/app.css` 与 `/static/js/index.js`，那两个**现在都不存在了**
    #    （`app.css` 随旧骨架删、`index.js` 更早就不在盘上）。
    #    本用例的原话就是「路径逐条取自真实页面 …… 不是编的 ——
    #       判据要在**真实**路径上成立，否则修了等于没修」⇒ 清单过期 = 这条用例失去意义。
    for p in (
        "/static/web/index.html",     # `/` 302 的落点 —— 就是它 429 时页面空白
        "/static/styles.css",         # 7 页引的唯一一张样式表
        "/static/js/session.js",      # 凭据层（7 页都引）
        "/static/js/cred.js",         # 页头那块凭据控件
        "/static/js/app-reveal.js",   # 滚动揭示（照 `/3` 搬过来的）
    ):
        assert is_middleware_exempt(p), f"{p} 没被豁免 —— 它还归限流管 ⇒ 刷两下就 429"


def test_static_mount_root_is_exempt():
    """`/static`（**不带尾斜杠**）也要豁免 —— StaticFiles 对它会回 307 到 `/static/`，
    那一下同样走中间件。漏了它只是多一个 429，但**看起来一模一样**，所以一并钉住。"""
    from main import is_middleware_exempt

    assert is_middleware_exempt("/static")


# ===========================================================================
# ② 负控（**这条才是防"改宽了"的**）
# ===========================================================================

def test_prefix_match_has_a_boundary():
    """**反证 / 负控**：前缀匹配必须**有边界**，不是 `startswith("/static")`。

    没有这条时，一个图省事的实现（`path.startswith("/static")`）会**照样全绿**
    —— 而它会把 `/staticfoo` 这类**根本不存在的兄弟路径**也放过去。
    本仓立场：**判据要能证伪** —— 先问「这条命令的输出会变吗」。
    这里：把边界去掉 ⇒ **本条必须红**。
    """
    from main import is_middleware_exempt

    for p in ("/staticfoo", "/static2/app.css", "/statics", "/static-evil"):
        assert not is_middleware_exempt(p), (
            f"{p} 被判成豁免了 —— 说明前缀匹配**没有边界**，谁都能造一个 /static 开头的路径绕开限流"
        )


def test_protected_paths_are_still_limited():
    """绝大多数路径**照旧受管** —— 豁免名单是白名单，别改成一个"放行一切"的口子。"""
    from main import is_middleware_exempt

    for p in (
        "/api/v1/rag/search",
        "/api/v1/agent/token/budget",
        "/api/v1/agent/langgraph_chat",
        "/chat",
    ):
        assert not is_middleware_exempt(p), f"{p} 被判成豁免了 —— 豁免名单被改宽了"


# ===========================================================================
# ③ 老的精确条目不许被这次改动弄丢
# ===========================================================================

def test_existing_exact_entries_still_exempt():
    """原有的每一条**逐字**仍在名单里，且仍判为豁免 —— 加了前缀匹配不许顺手动坏它们。"""
    from main import MIDDLEWARE_EXEMPT_PATHS, is_middleware_exempt

    assert MIDDLEWARE_EXEMPT_PATHS, "防空跑：名单空了"
    for p in sorted(MIDDLEWARE_EXEMPT_PATHS):
        assert is_middleware_exempt(p), f"{p} 本来在名单里，现在不豁免了"


# ===========================================================================
# ④ 接线：**两个中间件都得用这个函数**（别只改一个）
# ===========================================================================

def test_both_middlewares_call_the_same_helper():
    """判据抄 `test_public_paths.py` 那条同款纪律：**两个中间件必须走同一个判定**。

    **靶子已随本批改动换过** —— 它原先钉的是源码里 `request.url.path in
    MIDDLEWARE_EXEMPT_PATHS` 出现 >= 2 次；现在这句**只剩 1 处**（在判定函数内部）。
    ⇒ 不许为了让门绿把它删掉，要**改靶子**（本仓规矩）。
    """
    import main

    src = inspect.getsource(main)
    assert src.count("is_middleware_exempt(request.url.path)") >= 2, (
        "限流中间件与配额中间件应**都**调用 is_middleware_exempt —— "
        "只改一个的话，另一个照旧 429"
    )


def test_the_stale_inline_check_is_gone():
    """改完之后，两个中间件里不许再有内联的 `request.url.path in MIDDLEWARE_EXEMPT_PATHS`。

    与上一条**成对**：上一条管"用上了新函数"，这条管"旧的写法真撤了"。
    少任何一条，都可能出现「两处都留着，只是新函数没人用」而门全绿。
    """
    import main

    src = inspect.getsource(main)
    assert "request.url.path in MIDDLEWARE_EXEMPT_PATHS" not in src, (
        "还有中间件在用内联的精确匹配 —— 它匹配不到 /static/... 这类带后缀的路径"
    )


# ===========================================================================
# ⑤ 自证：**「把 /static 塞进精确匹配的集合」为什么不够**
# ===========================================================================

def test_naive_fix_would_not_have_worked():
    """**自证有效性**：把 `/static/` 直接加进**精确匹配**的集合里，对真实路径**无效**。

    没有这条，「为什么这次要引入前缀匹配」就只是我的声称。
    实现在改前就是**精确匹配**（`request.url.path in MIDDLEWARE_EXEMPT_PATHS`）
    ⇒ 本批的成因正是它。
    """
    from main import MIDDLEWARE_EXEMPT_PATHS

    naive = set(MIDDLEWARE_EXEMPT_PATHS) | {"/static/"}
    assert "/static/web/index.html" not in naive, (
        "前提变了：精确匹配居然能匹配上带后缀的路径 —— 那本批的成因就不是精确匹配了"
    )
    assert "/static/app.css" not in naive
