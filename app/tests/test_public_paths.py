"""公开路径名单的回归测试（对应 docs/重构计划-.md 的 N1）
"""
from main import app, MIDDLEWARE_EXEMPT_PATHS


def _all_route_paths() -> set:
    """收集 app 里所有真实路径。

    FastAPI 0.141 起 `include_router` 的结果被包成 `_IncludedRouter` 条目，
    `len(app.routes)` **不再等于路由总数** —— 权威来源是 `app.openapi["paths"]`。
    这里两者取并集，兼顾那些不进 OpenAPI 的路径（如 /metrics）。
    """
    paths = {getattr(r, "path", None) for r in app.routes}
    paths |= set(app.openapi()["paths"].keys())
    return {p for p in paths if p}


def test_public_paths_api_entries_exist_in_routes():
    """名单里所有 `/api/` 开头的路径，必须是 app 里**真实存在**的路由。

    断言的是"名单 ⊆ 真实路由"，而**不是**硬编码字符串 ——
    以后改路由前缀（比如加版本号），这条测试会自动抓到，不用人记得改。
    """
    routes = _all_route_paths()
    api_entries = [p for p in MIDDLEWARE_EXEMPT_PATHS if p.startswith("/api/")]

    # 防"空集通过"：旧名单里**一条 /api/ 路径都没有**，
    #    若只断言 `missing 为空`，旧名单会**恒过**（空集当然没有 missing）——
    #    那样这条测试就完全测不出它本该测的 bug。必须先要求非空。
    assert api_entries, (
        "公开名单里一条 /api/ 路径都没有 —— 这正是出过 bug 的那个形态"
        "（旧名单写的是 /auth/login，缺 /api/v1 前缀，永远不会匹配）"
    )

    missing = [p for p in api_entries if p not in routes]
    assert not missing, (
        f"公开名单里有**不存在的路由**（多半是前缀写漏了）: {missing}\n"
        f"真实路径样例: {sorted(p for p in routes if p.startswith('/api/v1/a'))}"
    )


def test_would_have_caught_the_original_bug():
    """**自证有效性**：把修复前的旧名单喂给本文件的判据，必须判它**不过**。

    没有这条，"上面那些断言到底能不能测出这个 bug"就只是我的声称。
    写这条的直接起因：**本文件的第一版就写错过** —— 它只断言"名单里的 /api/ 路径
    必须存在于真实路由"，而旧名单里**一条 /api/ 路径都没有** ⇒ 空集恒过，测不出任何东西。
    """
    old_list = ["/", "/docs", "/openapi.json", "/health", "/ready", "/metrics",
                "/auth/login", "/auth/refresh", "/admin/create_user"]
    routes = _all_route_paths()

    # ① 「非空」判据对旧名单【会通过】—— 所以光有它不够（= 我第一版的漏洞）
    assert [p for p in old_list if p.startswith("/api/")] == [], (
        "前提变了：旧名单里本不该有任何带 /api/ 前缀的路径"
    )

    # ② 实质判据：旧名单声称公开的路径，在真实路由里**一条 /api/ 都匹配不上** = bug 本体
    assert [p for p in old_list if p.startswith("/api/") and p in routes] == []

    # ③ 而「登录必须在名单里」这条判据对旧名单【会失败】—— 它才是真正抓住 bug 的那条
    assert "/api/v1/auth/login" not in old_list
    assert "/auth/login" in old_list, "旧名单写的就是这个 —— 缺 /api/v1 前缀，永不匹配"


def test_old_prefix_less_paths_must_not_come_back():
    """回归守卫：旧名单里那三个**缺 `/api/v1` 前缀**的路径不得再出现。

    它们**看着很像对的**（`/auth/login` 是常被当作规范写法的那种），
    因此是最容易在后续改动里被"改回去"的形态 —— 这条测试就是防这个。
    """
    for wrong in ("/auth/login", "/auth/refresh", "/admin/create_user"):
        assert wrong not in MIDDLEWARE_EXEMPT_PATHS, (
            f"{wrong} 缺 /api/v1 前缀、永远不会匹配真实路由 —— 这正是本测试要防的回归"
        )


def test_login_and_refresh_are_public():
    """登录/刷新必须在名单里 —— 否则会被挤进 `anonymous` 限流桶（3 次/秒共享）"""
    assert "/api/v1/auth/login" in MIDDLEWARE_EXEMPT_PATHS
    assert "/api/v1/auth/refresh" in MIDDLEWARE_EXEMPT_PATHS


def test_probe_endpoints_are_public():
    """健康探针必须豁免限流/配额，否则 K8s/Docker 探针可能收到 429 被误判为不健康"""
    for p in ("/health", "/ready", "/metrics"):
        assert p in MIDDLEWARE_EXEMPT_PATHS


def test_public_paths_is_shared_by_both_middlewares():
    """两个中间件必须引用**同一个**判定，而不是各留一份自己的列表。

    判据：`main` 模块里不应再出现旧的局部变量名 `public_paths`
    （它原先只存在于 QuotaMiddleware.dispatch 的局部作用域里）。

    **（待办）靶子改过一次** —— 原先钉的是
    `src.count("request.url.path in MIDDLEWARE_EXEMPT_PATHS") >= 2`。
    那一批把判定收口成了纯函数 `is_middleware_exempt(path)`
    （理由：`/static/*` 要前缀匹配，而这份名单是 `frozenset` + 精确匹配 ⇒ 塞进去等于没塞）
    ⇒ 那句内联写法**只剩 1 处**（在判定函数内部）⇒ 老靶子必然红。
    **不是把断言删掉，是换靶子**（本仓规矩）—— 这条**仍然**在管"两边是不是同一个来源"。
    """
    import main
    import inspect
    src = inspect.getsource(main)
    assert "public_paths = [" not in src, (
        "QuotaMiddleware 里又出现了局部的 public_paths 列表 —— 应该统一用模块级 MIDDLEWARE_EXEMPT_PATHS"
    )
    assert src.count("is_middleware_exempt(request.url.path)") >= 2, (
        "两个中间件都应调用同一个 is_middleware_exempt —— 只改一个的话，另一个照旧 429"
    )
