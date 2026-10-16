"""**7 条页面路由**的门 —— 2026-10-12 新建（前端重做 ⑥）。

## 为什么要有这一份（不是"把原来那几条抄过来"）

换骨架之前，**每一条页面路由各自有一个文件**（`test_kb_page.py` / `test_ops_page.py` /
`test_lab_page.py` …），每条路由的前三条用例**逐字同构**（302 · 目标在盘 · 不进 openapi）。

**那个形状有个洞**：**新加一页时，没人会记得也新开一个文件** ——
于是新页的路由**一条门都没有**，而**没有任何东西会红**。
本次就是活例子：换完骨架后 `/rag` `/agent` `/guide` `/reference` **四条新路由一条守卫都没有**。

⇒ 收成**一份、参数化过 7 页**的表 —— 加一页 = 在下面那张表里加一行，
   不需要记得再开一个文件。

## 钉什么（每条路由三件）

1. `GET /x` ⇒ **302**（不是 301 —— 被永久缓存的 301 会把老用户钉在旧地址上）
2. 跳转目标**真在盘上**，且直接取它是 **200** + `text/html`
   （目标写错时 302 照样是 302 ⇒ **只有这一条**能发现）
3. `GET /x` **不在 openapi**（它是给人看的页面，不是对外承诺的 API）

## 两条纪律

- **不连库**：裸 `TestClient(app)`，**别写成 `with TestClient(app) as client:`**
  —— 那会触发 lifespan 的 startup（`init_pool` 真去连 Postgres）⇒ CI 没有库 ⇒ 直接红。
- **防空跑**：下面那张表要是空了，参数化用例**一条都不跑而且是绿的**
  单列一条 `test_the_table_is_not_vacuous`。

判据（可打印）：`venv/bin/python -m pytest app/tests/test_page_routes.py -q -p no:warnings`
"""
import os

import pytest
from fastapi.testclient import TestClient

from main import app, static_dir

#: 页面路由 ⇄ 落点文件。**加一页 = 在这里加一行**（不是再开一个测试文件）。
PAGE_ROUTES = {
    "/": "web/index.html",
    "/kb": "web/kb.html",
    "/rag": "web/rag.html",
    "/agent": "web/agent.html",
    "/ops": "web/ops.html",
    "/guide": "web/guide.html",
    "/reference": "web/reference.html",
    # 内页（不进那 7 格导航）—— 它在这里只为「302 + 目标在盘 + 不进 openapi」那三件；
    #    **互链门（test_web_pages）里没有它**，因为作者 2026-10-12 定它是**子页**。
    "/stream": "web/stream.html",
    # 🆕 2026-10-15（§七·1）：登录页。与 `/stream` 同一种"格外的页"
    #    —— 它是**所有页面的前置**，⛔ 不是第 8 个内容页（所以同样不在那 7 格导航里）。
    "/login": "web/login.html",
}


def test_the_table_is_not_vacuous():
    """**表不能是空的** —— 否则下面那三条参数化用例一条都不跑，而且全绿。"""
    assert len(PAGE_ROUTES) == 9, f"页面表里应当是 9 页，现在是 {len(PAGE_ROUTES)}"


@pytest.mark.parametrize("route", sorted(PAGE_ROUTES))
def test_page_route_is_a_302(route):
    """`GET <route>` ⇒ **302**。302 而不是 301：永久缓存会把老用户钉在旧地址上。"""
    r = TestClient(app).get(route, follow_redirects=False)
    assert r.status_code == 302, f"`{route}` 不是 302（{r.status_code}）—— 访客打不开这一页"
    assert r.headers["location"] == "/static/" + PAGE_ROUTES[route], (
        f"`{route}` 跳到了 {r.headers['location']}"
    )


@pytest.mark.parametrize("route", sorted(PAGE_ROUTES))
def test_page_route_target_really_exists(route):
    """跳转目标**真在盘上**、取它是 **200**、而且是 html。

    为什么必须三条一起：302 指到一个不存在的文件 ⇒ 访客看到 404 / 空白，
       而**上面那条 302 用例照样绿**（它只看 header）。
    """
    client = TestClient(app)
    loc = client.get(route, follow_redirects=False).headers["location"]
    assert loc.startswith("/static/"), loc

    # `loc` 是 URL 路径（以 `/` 开头），`static_dir` 已经落到 `…/app/static`
    #    ⇒ 必须**先剥掉 `/static/` 前缀**再 join（`static_dir` 是【字符串】，不是函数）。
    #    直接 `os.path.join(static_dir(), loc)` 是错的 —— posixpath 遇到以 `/` 开头的
    #    第二段会**丢掉第一段**，于是变成在文件系统根下找文件（2026-10-12 实测栽过）。
    rel = loc[len("/static/"):]
    assert os.path.isfile(os.path.join(static_dir, rel)), f"{loc} 不在盘上"

    r = client.get(loc)
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"].startswith("text/html"), r.headers["content-type"]


def test_login_route_keeps_the_query_string():
    """🔴 `/login?next=…` 那串**必须活到落点上** —— 否则「登完回原来那页」整个是死的。

    **这条是拿一个真缺陷换来的**（2026-10-15，在前端登录页那次）：
        `RedirectResponse` **默认丢掉查询串**，而这个**上面那三条参数化用例一条都发现不了**
        —— 它们只看 302 与 `location` 的**路径**，不看查询串。
        🔵 更值得记的是：**页面那半边是对的**（`login.js` 的 `safeNext` 有 13 条单测、变异自证过），
           **照样挡不住这一条** —— 两端各自都对，**接起来是断的**。
        ⚠️ 而且它**只有把页面真打开、在地址栏里带个 `?next=` 走一遍**才看得。

    **反证**：把 `login_page` 改回 `RedirectResponse(url="/static/web/login.html")` ⇒ 本条红。
    """
    r = TestClient(app).get("/login?next=%2Fops", follow_redirects=False)
    assert r.status_code == 302, r.status_code
    assert r.headers["location"] == "/static/web/login.html?next=%2Fops", (
        f"查询串在 302 那一步丢了（落到 {r.headers['location']}）"
        " ⇒ 登录页拿不到 `next`，登完只会回默认首页"
    )


@pytest.mark.parametrize("route", sorted(PAGE_ROUTES))
def test_page_route_is_not_in_openapi(route):
    """页面路由 **不许进 openapi** —— 它不是 API，混进去就成了一份对外承诺。

    判据是有意义的：只要有人顺手删掉 `include_in_schema=False`，这一页就成了"API"，
       之后改它会被当成破坏兼容 —— 而**没有别的东西会红**。
    """
    assert route not in app.openapi()["paths"], (
        f"`{route}` 混进 openapi 了 —— 它是给人看的页面，⛔ 不是 API（`include_in_schema=False`）"
    )
