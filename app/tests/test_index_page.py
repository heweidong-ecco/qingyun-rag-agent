"""首页（**总览**）的门 —— **整体重写**。
"""
import os
import re

EXPECTED_LOCATION = "/static/web/index.html"


def test_root_redirects_to_overview_page(client):
    """`GET /` ⇒ **302** 到总览首页。"""
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, f"`/` 不再是 302（{r.status_code}）—— 访客打开域名会看到别的东西"
    assert r.headers["location"] == EXPECTED_LOCATION, (
        f"`/` 跳到了 {r.headers['location']}，不再是 {EXPECTED_LOCATION}"
    )


def test_root_redirect_target_exists_on_disk(client):
    """跳转目标必须**真在盘上**、直接取它是 200、而且是 html。

    为什么必须三条一起：302 指到一个不存在的文件 ⇒ 访客看到 404，
       而**上面那条 302 用例照样绿**（它只看 header）。
    """
    loc = client.get("/", follow_redirects=False).headers["location"]
    assert loc.startswith("/static/"), loc
    # `loc` 是 URL 路径（以 `/` 开头），而 `static_dir` 已经落到 `…/app/static`
    #    ⇒ 必须**先剥掉 `/static/` 前缀**再 join。
    #    直接 `os.path.join(static_dir(), loc)` 是错的：posixpath 遇到以 `/` 开头的
    #    第二段会**丢掉第一段**，于是变成在文件系统根下找 `/static/web/index.html`
    rel = loc[len("/static/"):]
    assert os.path.isfile(os.path.join(static_dir(), rel)), f"{loc} 不在盘上"
    r = client.get(loc)
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"].startswith("text/html"), r.headers["content-type"]


def static_dir():
    from main import static_dir as d
    return d


def test_root_is_not_in_openapi():
    """`/` **不是 API** —— 它必须不出现在 openapi 里。"""
    from main import app

    assert "/" not in app.openapi()["paths"], "`/` 混进 openapi 了 —— 它是给人看的页面"


def test_overview_page_has_no_external_cdn(client):
    """首页 **不引外网 CDN**（本仓红线）。

    **防空跑**：先断言"确实抽到了 URL" —— 否则页面一个 `src=`/`href=` 都没有时，
       这条会**恒为真**（本仓最恨的空跑）。
    """
    html = client.get(EXPECTED_LOCATION).text
    urls = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert urls, "一个 src=/href= 都没抽到 ⇒ 扫描规则坏了，本用例会空跑"
    bad = [u for u in urls if u.startswith(("http://", "https://", "//"))]
    assert not bad, f"首页引了外网资源，离线/内网环境会加载不出来：{bad}"


def test_no_page_renders_a_credential_plaintext(client):
    """首页上**不许出现凭据明文**（`sk-…` 那种）。

    判据同本仓 `cred.js` 的三条约定①：**一个字节的凭据明文都不进 DOM**
       （只显示"有没有"和"还剩多少"）。
    """
    html = client.get(EXPECTED_LOCATION).text
    m = re.search(r"sk-[A-Za-z0-9_-]{16,}", html)
    assert not m, f"首页上印出了像凭据明文的东西：{m.group(0)[:20]}…"


# ══════════════：首页重做之后补的那几条 ══════════════

import os as _os

_PAGE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                      "static", "web", "index.html")


def _page_text():
    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


def _strip_comments(text):
    """剥掉 HTML 注释（页面里**有意**用注释记着沿革，不剥的话下面会报在它自己头上）。"""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


#: 7 条页面路由 —— **与 `app/tests/test_web_pages.py` 的 `_PAGE_ROUTES` 是同一张站点地图**。
#: 这里**故意**单独列一份而**不是 import** 那一份：那份是"导航栏里必须有哪几格"的判据，
#:    这一份是"能力地图里必须能点到哪几条"的判据 —— **两条判据分了家，就不该共用一份常量**
#:    （共用的话，改了一处会让另一条也悄悄变，而那正是"两处真相"）。
_CAPABILITY_ROUTES = ("/kb", "/rag", "/agent", "/stream", "/ops", "/guide", "/reference")


def test_the_capability_map_lists_every_page():
    """**能力地图必须覆盖【全部】7 条页面路由**。

    **为什么这条值钱**：这一版之前的首页只列了 **3 条链**（kb/rag/agent），
    另外 4 条（ops/stream/guide/reference）**只在导航栏里**。而首页是 95% 的人
    **唯一会看的那一页** ⇒ 他们看到的"这东西能干什么"**少了一半**。
    判据是"**这张表里能点到**"，不是"整页出现过这个 href"
       （那样导航栏那 7 格会让本条**恒真** —— 本仓栽过同型）。
    **反证**：从那张表里删掉任意一行 ⇒ 本条立刻红。
    """
    text = _page_text()
    m = re.search(r'<table class="tbl">.*?</table>', text, re.S)
    assert m, "首页上找不到那张能力地图（`<table class=\"tbl\">`）—— 本条的判据跟着失效"
    table = m.group(0)

    missing = [r for r in _CAPABILITY_ROUTES if f'href="{r}"' not in table]
    assert not missing, (
        f"能力地图里点不到这几条：{missing}\n"
        f"⇒ 首页是绝大多数人唯一会看的一页 ⇒ 少一条 = **他们以为你没做**"
        f"（最高判据：「显示了，才知道你有做」）。"
    )

    # 防空跑：盘上的页面数必须与上面那份清单**对得上**（否则加了页没人改这里）
    # 🔴 **两个 html 不算"能力"**（加第二个）：
    #    · `websocket_test.html` —— 调试页，⛔ 不面向访客（原就有）
    #      ⛔ **不是一个"能做什么"**。⚰️ 把它塞进能力地图 = 让首页多一条
    on_disk = sorted(f for f in _os.listdir(_os.path.join(_os.path.dirname(_PAGE)))
                     if f.endswith(".html") and f not in ("websocket_test.html", "login.html"))
    assert len(on_disk) - 1 == len(_CAPABILITY_ROUTES), (
        f"盘上有 {len(on_disk)} 个面客页面（含首页），而清单里列了 {len(_CAPABILITY_ROUTES)} 条 —— "
        f"页面增减了，把 `_CAPABILITY_ROUTES` 一起改。实扫到：{on_disk}"
    )


def test_the_capability_map_comes_before_the_boundary_block():
    """**能力地图必须排在边界【之前】**。

    **为什么**：这一版之前的首页，进 `<main>` 第一块就是**边界 5 条**（一堆**限制**）。
    对"看一眼你做了什么"的人来说，那是**负分**：他还没看到你能干什么，先读到了你不能干什么。
    ⇒ 首屏先答「**这是什么 · 有什么**」，边界是**限制**，该在能力**之后**。

    **反证**：把那一块 `<div class="pane">` 挪到表前面 ⇒ 本条立刻红。
    """
    text = _page_text()
    cap = text.find('<table class="tbl">')
    bound = text.find('这套服务的边界')
    assert cap != -1, "找不到能力地图 —— 本条的判据跟着失效"
    assert bound != -1, "找不到边界那一块 —— 本条的判据跟着失效"
    assert cap < bound, (
        "边界那一块排在能力地图**前面**了 ⇒ 访客先读到一堆限制，却还没看到能干什么。\n"
        "（2026-10-13 就是这么改过来的。）"
    )


def test_the_answers_box_is_not_left_stretched_by_the_notes_column():
    """**代码那一格不许被右栏拉成同高**（补 · 真浏览器上量出来的）。

    **为什么**：`.resp-body` 是 grid（默认 `align-items: stretch`），而右栏是 6 段标注 ——
    跑之前代码只有 14 行 ⇒ **左下方空出 344 px 的一块**（`background: var(--ink-100)`
    让它看着像"一块空面板"，不像"内容到底了"）。

    ⇒ 处置：给代码那一格 `align-self:start` —— **不拉伸就没有那块空白**（两栏各自到内容为止）。
    跑完之后，展开的 `docs[]` / `sources[]` 会把左栏自然撑到与右栏相当（实测空白 344 → **16 px**）。

    **反证**：把 `align-self:start` 去掉 / 改写成 `stretch` ⇒ 本条红。
    """
    text = _page_text()
    m = re.search(r'<div class="resp-code"[^>]*>', text)
    assert m, "找不到代码那一格（`class=\"resp-code\"`）—— 本条的判据跟着失效"
    assert "align-self:start" in m.group(0).replace(" ", ""), (
        "代码那一格没有 `align-self:start` ⇒ 会被右栏拉成同高，"
        "而它内容更短时**左下方就是一块空白面板**（跑之前实测 344 px）。"
    )


def test_the_boundary_count_is_not_hardcoded():
    """**边界条数不许写死** —— 由 `#n-bounds` 当场数。

    **为什么**：原先那个「5 条」被**写死在 8 个地方**（首页 pane 头 + 7 个页脚），
    而那 7 个页脚各写着「见首页那 5 条」。⇒ **加一条就 8 处全漂，而且没有任何门会红**
    （本仓 自验收 §16：「写死的计数不许有」）。
    ⇒ 现在条数由 js 从 `.rail` 的行数现数；页脚一律**不提数字**。

    **反证**：把 pane 头改回写死的「（6 条）」/ 把 `id="n-bounds"` 删掉 ⇒ 本条红。
    """
    live = _strip_comments(_page_text())
    assert 'id="n-bounds"' in live, (
        "边界那一块的条数不再由 js 现数了（`#n-bounds` 没了）⇒ 又变成要人记得改 8 个地方"
    )
    bad = re.findall(r"（\s*\d+\s*条\s*）", live)
    assert not bad, (
        f"页面上又出现了写死的条数：{bad}\n"
        f"⇒ 加减一条就漂，而**没有任何门会红**（本仓 自验收 §16）。改成当场数。"
    )
