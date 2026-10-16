"""`/debug/*` 那 4 条的**鉴权口径**守卫。
"""
import re

from fastapi.testclient import TestClient

from main import app
from routing.deps import require_admin, get_current_user_hybrid

#: 4 条的路径 —— **不带** `{user_name}`（那正是  删掉的东西）。
DEBUG_PATHS = (
    "/api/v1/debug/count",
    "/api/v1/debug/cache_stats",
    "/api/v1/debug/quota",
    "/api/v1/debug/rate_limit",
)

#: 已删掉的两条旧路径 —— **一条都不许再出现**（出现 = 越权面回来了）。
REMOVED_PATHS = (
    "/api/v1/debug/quota/{user_name}",
    "/api/v1/debug/rate_limit/{user_name}",
)


def _walk(routes):
    """**`app.routes` 【不是】平的**（实测 `fastapi 0.141.1`）。

    `include_router` 之后挂上去的是 `_IncludedRouter`（惰性引用），
    它的子路由**不在 `app.routes` 顶层** —— 要顺着 `.original_router.routes` 往下走。

    **只看顶层会静默扫到 0 条 `/api/v1/*`** ⇒ 下面所有遍历型判据**恒为真**
    （本仓那族「空集合断言恒真」，见 `app/tests/CLAUDE.md` 规矩 1）。
    ⇒ `_api_routes` 那条防空跑断言就是为它设的。
    """
    for r in routes:
        sub = getattr(r, "original_router", None)
        if sub is not None:
            yield from _walk(sub.routes)
        else:
            yield r


def _api_routes():
    """**全部**路由里带 `dependant` 的那些（= 真的 `APIRoute`；中间件与 Mount 没有）。"""
    return [r for r in _walk(app.routes) if getattr(r, "dependant", None) is not None]


def _route(path):
    for r in _api_routes():
        if r.path == path:
            return r
    return None


def _dep_calls(path):
    """那条路由**实际装配**的依赖的可调用对象（不是扫源码文本 —— 扫文本测不出接线）。"""
    r = _route(path)
    assert r is not None, f"`{path}` 不在 app 的路由表里 —— 本文件的判据跟着失效"
    return [d.call for d in r.dependant.dependencies]


# ==================== 1 · 4 条都不再要管理员 ====================


def test_no_debug_route_requires_admin_anymore():
    """4 条**都不许**再挂 `require_admin`。

    反证：把任一条的 `Depends(get_current_user_hybrid)` 改回 `Depends(require_admin)` ⇒ 本条红。
    """
    for p in DEBUG_PATHS:
        calls = _dep_calls(p)
        assert require_admin not in calls, (
            f"`{p}` 又挂回 `require_admin` 了 ⇒ 普通 key 的访客会吃 403，"
            f"运维探针页对他就等于没做"
        )
        assert get_current_user_hybrid in calls, (
            f"`{p}` 上没有 `get_current_user_hybrid` —— 见下一条：放开 **⛔ 不是**匿名可打"
        )


def test_the_four_debug_routes_are_all_present():
    """防空跑：恰好 4 条。少了 ⇒ 上面那条是**遍历空集合**，恒为真。"""
    found = [p for p in DEBUG_PATHS if _route(p) is not None]
    assert len(found) == len(DEBUG_PATHS), (
        f"应有 {len(DEBUG_PATHS)} 条 `/debug/*`，实际扫到 {len(found)} 条：{found}\n"
        f"⇒ 路由改名/挪走之后，本文件其余判据会**静默恒真**"
    )


# ==================== 2 · 越权面：`{user_name}` 一条都不许在 ====================


def test_no_route_takes_a_user_name_path_param_anymore():
    """**本文件最要紧的一条** —— `{user_name}` 形式的路由**一条都不许存在**。

    为什么单独立一条： 当初加 `require_admin` 的**唯一理由**就是
    「这两条**能查任意用户、可枚举用户名**」。 之所以敢放开，
    是因为**那条路径被删掉了** —— 而"删没删干净"**只有这条能测**。

    反证：把 `@router.get("/debug/quota")` 改回 `"/debug/quota/{user_name}"` ⇒ 本条红。
    """
    all_paths = {r.path for r in _api_routes()}
    for p in REMOVED_PATHS:
        assert p not in all_paths, (
            f"`{p}` 又出现了 ⇒ **越权面回来了**（任何登录用户都能查别人）"
            f"⇒  放开这 4 条的前提**不再成立**"
        )
    # 再兜一层：**任何** `/debug/` 下的路由都不许带路径参数
    #    （防"换个名字再加一条"，比如 `/debug/quota/{name}`）
    offenders = sorted(p for p in all_paths if p.startswith("/api/v1/debug/") and "{" in p)
    assert offenders == [], (
        f"`/debug/` 下又出现了带路径参数的路由：{offenders}\n"
        f"⇒  的整个论证建立在「这 4 条读不出别人的数」之上"
    )


# ==================== 3 · 放开 ≠ 匿名可打 ====================


def test_anonymous_is_still_rejected_on_all_four():
    """**4 条不带任何凭据仍然必须 401**。

    为什么单独立一条（本仓那族：「**拿动作成功当结果正确**」）：
    上一条断言的是"**没有** `require_admin`"。而**把整个 `Depends(...)` 删掉**也能让它变绿 ——
    那 4 条就**匿名可打**了。只有本条能把这两种情形分开。

    判据落在**鉴权层**（它在 handler 之前跑）⇒ 本用例**不需要真库/真 Redis**。
    是 **401** 不是 403 —— 没带凭据 ⇒ `AUTH_MISSING`（见 `app/core/exceptions.py` 的映射）。
    """
    client = TestClient(app)
    for p in DEBUG_PATHS:
        r = client.get(p, follow_redirects=False)
        assert r.status_code == 401, (
            f"`{p}` 匿名打回的是 {r.status_code}，应 401 —— "
            f"**放开 ≠ 匿名可打**；把 `Depends(...)` 删掉也会让上一条变绿，本条是那条的对照"
        )
        body = r.json()
        assert body.get("code") == "AUTH_MISSING", f"`{p}` 的拒绝码应 AUTH_MISSING，实际 {body!r}"


# ==================== 4 · 别顺手放开那条真烧钱的 ====================


def test_benchmark_embedding_still_requires_admin():
    """`/rag/benchmark-embedding` **仍要 `require_admin`**。

    它**真烧钱**（`get_embedding` 真调 DashScope）—— 全仓唯一那样的一条。
     只放开了 `/debug/*` 那 4 条，不包含它。
    """
    calls = _dep_calls("/api/v1/rag/benchmark-embedding")
    assert require_admin in calls, (
        "`/rag/benchmark-embedding` 不再要管理员了 —— 它**真调 DashScope**，"
        "放开 = 重建  收掉的那个「匿名真烧钱」"
    )


# ==================== 5 · 源码侧的对照（不是主判据，是"文档别再写反"） ====================


def test_docstring_and_spec_no_longer_advertise_the_old_paths():
    """**辅助判据**：源码里出现 `{user_name}` 的地方**不许是路由装饰器**。

    这条**故意宽松** —— 它只把"**装饰器里又写回了形参**"这一种情形再拦一遍
    （真尺子是上面第 2 条：**路由表里不许有**）。别把它当成主判据。
    """
    import os
    os_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "routing", "api_v1.py")
    with open(os_path, encoding="utf-8") as f:
        src = f.read()
    # 剥掉注释与 docstring 再扫 —— 本文件里**大量**提到旧路径（那是沿革记录，不是代码）
    stripped = re.sub(r"#[^\n]*", "", src)
    hits = re.findall(r'"/debug/[a-z_]+/\{user_name\}"', stripped)
    assert hits == [], (
        f"源码的**装饰器字符串**里又出现了形参路由：{hits} ⇒ 见第 2 条（路由表才是真尺子）"
    )
