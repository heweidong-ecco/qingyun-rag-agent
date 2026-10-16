"""全站页面脚本里 URL 字面量的守卫 —— **页面写死的 URL 必须带 `/api/v1`**。

起因是一个**真事故**（§四）：`app/static/web/approvals.html` 把
`/agent/pending` 写死，而真实路由是 `/api/v1/agent/pending`
⇒ **那个页面在浏览器里 404，从合进去那天起就是坏的**，而**没有任何用例红**：

| 层 | 为什么漏 |
|---|---|
| `app/tests/test_approvals_page.py`（3 条） | 只看 `GET /approvals` 的 **302 与跳转目标**，不看页面脚本里的 URL |
| `app/static/js/approvals.test.js`（14 条） | 全是**纯函数** —— `buildContextQuery` 只回 query string，前缀根本不经过它 |
|  | 管的是「后端有没有多余的无鉴权路由」，与页面打什么 URL 无关 |

⇒ **URL 前缀硬编码在那一行 HTML 里，而那一行没有任何判据。**

本文件**不连库**（裸 `TestClient(app)`）—— ：
   别写成 `with TestClient(app) as client:`，那会触发 lifespan 的 startup
   （`init_pool` 真去连 Postgres）⇒ **CI 没有库 ⇒ 直接红**。

## 为什么**扫目录**而不是逐个列页面名

「逐个列名」是本仓反复踩的形态：**新增一个页面/用例，忘了在门里加一行
它永远不受管，而且没有任何东西会红**（同一形态：`.github/workflows/ci.yml` 的
`node --test` 逐行列文件名 ）。
⇒ 这里**扫 `app/static/` 下所有 `.html`**，页面一多**自动**进网（结构上的，
不是靠人记得加行）。

**配套那条"至少发现 N 个页面"的用例是必需的**，不是凑数 ——
   glob 写错时 `PAGES` 会是空列表，**参数化用例一条都不跑，而且是绿的**
   （本仓最恨的「空跑 = 静默假通过」；前科：`pre-commit-gates.py` 空跑）。

## 看代码会误判的地方

* **某个页面若改用 JS helper 拼路径，本用例对它是【空过】的。**
  `web/trace.html` 就是这种 —— 它不写字面量，走 `RagTrace.buildPath` /
  `buildCostPath`。那时前缀的保护来自**那个 helper 自己的 node 用例**
  （`app/static/js/trace.test.js` 的「`buildPath` / `buildCostPath` 会编码 thread_id」）。
  ⇒ **别把"扫不到字面量"读成"这个页面验过了"**。
* 只查**调用点里的字面量**（`getJSON('…')` / `fetch('…')`），
  **不做全文件子串扫描** —— 那会连注释一起命中（本仓  的原话）。
* `app/static/` 根下的页面也在扫描面内。**2026-10-07起只剩
  `websocket_test.html` 一个** —— 原先那两个（`stream_test.html`：调**不存在**的
  `/api/v1/user/chat_history`；`trace_viewer.html`：`fetch` **不带认证头** ⇒ 必然 401）
  **已经删掉**。⇒ **本文件的用例数会跟着页数自动变**（当时 7 ⇒ 现在 5），
  **别把"少了两条"读成"守卫变松了"** —— 少的是**两个不存在的页面**。

判据（可打印）：`venv/bin/python -m pytest app/tests/test_web_pages.py -q -p no:warnings`
"""
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser

import pytest

from main import static_dir

# 必须含有的页面 —— 只有这些都发现得到，才说明"扫描真的在工作"。
# 加进这里 = "这个页面删了是件该被看见的事"，不是随手 Grep。
#: 必须含有的页面 —— 只有这些都发现得到，才说明"扫描真的在工作"。
#: 加进这里 = "这个页面删了是件该被看见的事"，不是随手 Grep。
#: 2026-10-12（前端重做 ⑥）：**整批换成新 7 页** ——
#:    旧那 10 页（`approvals` `chat` `cost` `lab` `system` `tools` `trace` …）
#:    随旧骨架一起删了（它们的内容已进新页的嵌套层，`编排 §10.4` 逐条裁过）。
_REQUIRED = ("web/agent.html", "web/guide.html", "web/index.html", "web/kb.html",
             "web/login.html", "web/ops.html", "web/rag.html", "web/reference.html")

# 调用点里的字符串字面量：单引号 / 双引号 / 反引号都收。
# 反引号收进来是有意的：`fetch(`/agent/${id}`)` **同样是坏的**，别放过。
# 🔴 2026-10-15（§七·1）：**`apiFetch` 也收** —— 同 `test_response_model_declared.py` 那条，
#    理由是同一个：页面取数改走 `RagAuth.apiFetch(` 之后，只认 `fetch(` 会让这一道门**静默空掉**
#    （而它正是「页面里写死的 URL 必须带 `/api/v1`」的**唯一**判据）。
#    ⚠️ `fetch\(` 匹配不到 `apiFetch(`（那个 F 是大写）—— 2026-10-15 当场栽过一次，见两条门里的注释。
_URL_LITERAL = re.compile(r"(?:getJSON|fetch|apiFetch)\(\s*['\"`]([^'\"`]*)['\"`]")


def _discover_pages():
    pages = []
    for root, _dirs, files in os.walk(static_dir):
        for name in files:
            if name.endswith(".html"):
                pages.append(os.path.relpath(os.path.join(root, name), static_dir))
    return sorted(pages)


PAGES = _discover_pages()


def test_page_scan_is_not_vacuous():
    """**扫描本身必须有货** —— 否则下面那条参数化用例一条都不跑，而且是绿的。

    这条**故意写死三个页面名**：删页面是件该被看见的事
       （要么改这条，要么说明为什么那个页面没了），不是"顺手 Grep 一下"。
    """
    assert PAGES, (
        f"在 {static_dir} 下一个 `.html` 都没扫到 ⇒ 下面的参数化用例【空跑】。"
        f"先查 `os.walk` 的起点与后缀对不对。"
    )
    missing = [p for p in _REQUIRED if p not in PAGES]
    assert not missing, (
        f"这些页面没被扫到：{missing}（实扫到 {len(PAGES)} 个：{PAGES}）。"
        f"⇒ 要么它们被改名/删了，要么扫描规则漏了它们。"
    )


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_page_url_literals_carry_the_api_prefix(page):
    """页面脚本里**写死的 URL 字面量必须带 `/api/v1`**（§四）。

    反证检验：把 `web/approvals.html` 里的 `/api/v1/agent/pending` 改回
    `/agent/pending` ⇒ 本条立刻红（**这条是实测过的**，见  §五）。
    """
    with open(os.path.join(static_dir, page), encoding="utf-8") as f:
        text = f.read()

    found = _URL_LITERAL.findall(text)
    bad = [
        u for u in found
        # 绝对地址（外部 CDN 之类）不归这条门管；相对路径必须落在 /api/v1 下。
        if not u.startswith(("/api/v1", "http://", "https://"))
    ]
    assert not bad, (
        f"`{page}` 里这些 URL 字面量没带 `/api/v1` 前缀，浏览器里会 404：{bad}\n"
        f"（真实路由前缀是 `/api/v1` —— 见 `app/routing/api_v1_agent.py` 的 `APIRouter(prefix=…)`。"
        f"⚠️ **服务端不会报任何错**，页面只是空白/失效。）"
    )


# ══════════════ 页面**内联**的 JS 必须真能跑（2026-10-10 立 · 补一个结构缺口）══════════════
#
# **这道门是拿一个真事故换来的**，事故长这样：
#   8 个能力页**全部是死的**（一行内联脚本都不执行），而
#   **982 条 pytest + 283 条 node 用例全绿**。
#
# 为什么一条都没红：
#
# | 层 | 为什么够不到 |
# |---|---|
# | `app/tests/test_*_page.py`（各页守卫） | 它们读的是**页面文本**（正则找标记、比先后）—— **不解析 JS** |
# | `app/static/js/*.test.js`（node 用例） | 只测 **`js/*.js` 自己**，从不加载页面里那段内联脚本 |
# | 静态检查（ruff 棘轮） | 只管 `.py` |
#
# ⇒ **页面里那段内联 JS 没有任何判据** —— 它写错了，全世界的门都是绿的，
#   只有**把页面真打开**才看得。这正是本仓那句「**门挂在别处，就等于没有门**」。
#
# 具体那次错在**全局重名**：共用脚本 `session.js` 顶层写了 `function key()`，
# 而经典脚本的顶层 `function` **是全局的** ⇒ 与 8 个页面各自的 `const key` 撞上
# `SyntaxError: Identifier 'key' has already been declared` ⇒ **整段内联脚本作废**。
#
# 所以这道门钉的是**最外圈、也最便宜**的那一层：**它能解析吗**。
#   ⇒ 它**不管**逻辑对不对（那是各页自己的用例 + `js/*.test.js` 的事）；
#      但"解析不了"这一类**从今夜起再也过不去了**。
#   复盘：

def _inline_scripts(text):
    """把页面里**内联**的 `<script>` 正文抽出来（带 `src=` 的是外部文件，不算）。

    **为什么用 `html.parser` 而不是正则**（2026-10-11 · **CodeQL 连着抓了两次**）：

    | 第几次 | 正则漏了什么 | 后果 |
    |---|---|---|
    | 1 | **大写** `<SCRIPT>` | 那一页**扫不到** ⇒ **静默跳过** |
    | 2 | `</script >`（**带空格**） | 同上（或把两段脚本**吞成一段**） |

    **两次都是同一个毛病**：**漏扫 ≠ 通过** —— 正则没覆盖到某种写法，
    那个页面就被**静默跳过**，**而门照样绿**。这正是这道门存在的理由，也是它最不能犯的错。
    而且第 2 次我**事先把那条限制写在注释里当成了"有意不收"**，理由是"本仓页面一处都没有这种写法"
    —— **那正是本仓明令不许的那种推理**（拿「本仓现在没有」当「不会发生」）。
    ⇒ 换成 stdlib 的**真解析器**：**"某种写法没覆盖到"这一类洞，一次都没有了**。

    换之前做过的核对：拿它和旧正则跑过 `app/static/` 下**全部 12 个 `.html`**
    ⇒ **逐页逐段完全一致**（含 `eval.html` / `eval_gate.html` 那两个"一段都没有"的）。
    ⇒ 所以这是一次**行为不变、鲁棒性变强**的替换。
    """
    p = _InlineScripts()
    p.feed(text)
    return ["".join(b) for b in p.blocks]


class _InlineScripts(HTMLParser):
    """收集**内联** `<script>` 的正文。带 `src=` 的不算（那是外部文件，另有门管）。

    `handle_comment` 那道补丁不是多余的：老式脚本里 `<!--` 是**脚本内容**，
    而 `html.parser` 可能把它当注释 ⇒ 不补回去就会**少一段**（而门照样绿）。
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return
        if any((k or "").lower() == "src" for k, _v in attrs):
            return                       # 外部文件 ⇒ 不归这道门管
        self._depth += 1
        self.blocks.append([])

    def handle_data(self, data):
        if self._depth and self.blocks:
            self.blocks[-1].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._depth:
            self._depth -= 1

    def handle_comment(self, data):
        if self._depth and self.blocks:
            self.blocks[-1].append("<!--" + data + "-->")


def test_inline_script_scan_never_silently_skips_a_page():
    """**下面那道门的【正控】** —— 认不出某种写法 = 那个页面被**静默跳过**（而门照样绿）。

    **为什么单立一条**：这道门一开始是**正则**写的，而 **CodeQL 连着抓了它两次**
    （`Bad HTML filtering regexp` · HIGH）：

    | 第几次 | 漏了什么 | 后果 |
    |---|---|---|
    | 1 | **大写** `<SCRIPT>` | 那一页扫不到 ⇒ **静默跳过** |
    | 2 | `</script >`（**带空格**） | 同上（或把两段脚本**吞成一段**） |

    ⇒ 两次都是"**漏扫 ≠ 通过**"，而这正是本仓最恨的形态（「空跑 = 静默假通过」）。
    ⇒ 现在换成了 stdlib 的**真解析器**（`html.parser`），这一类洞**一次都没有了**；
      本条就是钉住它的**正控** —— 下面每一种写法都**必须**被认出来。
    **反证**：把 `_inline_scripts` 换回「只认小写、不认标签里空格」的老正则 ⇒ 本条立刻红。
    """
    for src, why in [
        ("<script>var a=1;</script>", "最普通那种"),
        ("<SCRIPT>var a=1;</SCRIPT>", "🔴 大写 ⇒ 第 1 次 CodeQL 报的就是它"),
        ("<script >var a=1;</script >", "🔴 标签里带空格 ⇒ 第 2 次报的就是它"),
        ('<script type="text/javascript">var a=1;</script>', "带别的属性"),
        ("<script>\nvar a=1;\n</script>", "跨行"),
        ("<script>var a=1;</script><script>var b=2;</script>", "两段脚本（⛔ 不许吞成一段）"),
    ]:
        got = [s.strip() for s in _inline_scripts(src)]
        want = ["var a=1;"] if "b=2" not in src else ["var a=1;", "var b=2;"]
        assert got == want, f"认不出「{why}」⇒ 那个页面会被静默跳过（拿到的是 {got}）"

    for src, why in [
        ('<script src="/x.js"></script>', "带 `src=` 的是外部文件"),
        ('<SCRIPT SRC="/x.js"></SCRIPT>', "同上，大写"),
    ]:
        assert _inline_scripts(src) == [], f"{why} ⇒ ⛔ 别把它算成内联脚本（那是别的门管的事）"


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_inline_page_script_parses(page):
    """**页面内联的 JS 必须能被解析** —— 解析不了 ⇒ 那一整段一行都不执行，页面就是死的。

    判据用的是 **`node --check`**（真解析器），不是"用正则数括号"那种自造的近似物。
    反证检验：在任一页面的内联脚本里加一句 `const key = 1;`（与全局 `key` 撞名）
    或写一个语法错 ⇒ 本条立刻红。
    """
    with open(os.path.join(static_dir, page), encoding="utf-8") as f:
        blocks = _inline_scripts(f.read())
    if not blocks:
        pytest.skip(f"{page} 本来就没有内联脚本 —— ⛔ 这不是「这条门放过了它」")

    node = shutil.which("node")
    assert node, (
        "找不到 `node` ⇒ **这道门根本没法跑**。\n"
        "⚠️ ⛔ 别把它当通过 —— 这正是本仓「**取不到真值 ≠ 通过**」那条"
        "（同型：`check_remote_sync.sh` 的退出码 3）。\n"
        "⇒ 本仓的离线测试 job 本来就跑 `node --test`（`ci.yml`），所以 node 是**前置条件**。"
    )

    bad = []
    for i, src in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
            tf.write(src)
            tmp = tf.name
        try:
            r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(f"第 {i + 1} 段：{r.stderr.strip().splitlines()[0] if r.stderr else r.stdout}")
        finally:
            os.unlink(tmp)
    assert not bad, (
        f"`{page}` 里有内联脚本**解析不过** ⇒ 浏览器里那一整段**一行都不执行**，页面是死的：\n"
        + "\n".join(bad)
        + "\n⇒ 最常见的一种是**全局重名**：共用脚本的顶层 `function`/`const` 是**全局的**，"
          "会与页面自己的顶层绑定撞上（2026-10-10 就是这么栽的）。"
    )



# ==================== 2026-10-12（前端重做 ⑥）：**页间互链** ====================
#
# **换掉了旧骨架那两条**（`test_every_page_has_the_top_nav` · `test_topnav_lists_every_page`）。
#    旧骨架的跨页通道是 `topnav.js` **用 JS 生成**的顶部条；新骨架（照 `/3`）是
#    **每页 HTML 里写死的 7 格导航**（：**旧骨架随旧骨架一起废，换钉新骨架的新门**）。
#
# **要守的东西一个字没变** —— 旧门存在的理由是：
#    **「漏一页，那一页就变成孤岛 —— 而它看起来完全正常（页面能打开、内容都在）」**。
#    新骨架下这条同样成立：某一页的导航少写一格 ⇒ **从那一格进不去那一页**，
#    而那一页自己完全正常（能打开、内容都在）。
#
# 判据钉在 **`<nav class="nav">` 那一块里面** —— 不是"整页出现过这个 href"：
#    那样品牌那块 `<a class="brand" href="/">` 会让"首页"这一格**永远算通过**，
#    量到的就不是"导航里有这一格"了。

#: 7 页 ⇄ 7 条路由。**改这里 = 改站点地图**（加页/改名都要同步）。
_PAGE_ROUTES = {
    "index.html": "/",
    "kb.html": "/kb",
    "rag.html": "/rag",
    "agent.html": "/agent",
    "ops.html": "/ops",
    "guide.html": "/guide",
    "reference.html": "/reference",
}


@pytest.mark.parametrize("page", sorted(_PAGE_ROUTES), ids=sorted(_PAGE_ROUTES))
def test_every_page_nav_links_to_all_seven(page):
    """7 页**互链完整** —— 每一页的导航里都要有**全部 7 格**（含它自己）。

    **反证**：把任意一页导航里那行 `<a href="/ops">系统观测</a>` 删掉 ⇒ **本条红**
       （删的是"导航里那一格"，而那一页自身仍然完全正常 —— 这正是要拦的形态）。
    """
    path = os.path.join(static_dir, "web", page)
    assert os.path.isfile(path), f"{page} 不在盘上"
    text = open(path, encoding="utf-8").read()

    m = re.search(r'<nav class="nav"[^>]*>(.*?)</nav>', text, re.S)
    assert m, f"`{page}` 里找不到主导航（`<nav class=\"nav\">`）—— 7 格导航是唯一的跨页通道"
    nav = m.group(1)

    missing = [f"{name}（{route}）" for name, route in sorted(_PAGE_ROUTES.items())
               if f'href="{route}"' not in nav]
    assert not missing, (
        f"`{page}` 的导航里缺这些入口：{missing}\n"
        f"⚠️ 缺一格的后果：从那一条路进不去那一页，而**那一页看起来完全正常**"
        f"（能打开、内容都在）—— 这正是本门要拦的形态。"
    )



# ==================== 2026-10-12（前端重做 ⑥）：**页面上不许印 markdown 记号** ====================
#
# **从 `test_approvals_page.py` 搬过来的**（那个文件随旧骨架删了）——
#    它当时只扫 `approvals.html` 一页，本仓已在 `tools` / `system` / `ops` **三页**上各记过一次同族缺陷。
#    ⇒ 搬过来时**顺手升成全站扫描**（本文件本来就是"扫目录"的那一份）：
#      从此**新页自动进网**，不必记得给新页再开一条。
#
# **根因**：页面往 DOM 里塞文案一律 `textContent` ⇒ **markdown 的 `**` 会原样印出来**
#    （2026-10-10 实测：`**完整上下文**` 那四个星号**真的印在页面上**）。
#    这类缺陷**只在把页面真打开时看得见** —— 所以必须有门。


def _strip_comments(text):
    """剥掉 HTML 注释与 JS 注释。

    **必须剥**：本仓的页面在**顶部注释块**里大量用 `**` 和路径（那是给读代码的人看的沿革），
       不剥的话下面那条会**报在它自己头上**。
       同族纪律 ⇒ 仓库根 `CLAUDE.md`：「**要提那串，就拆开写或只描述它**」（本仓已栽 5 次）。
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//[^\n]*", r"\1", text, flags=re.M)
    return text


def _visible_text(text):
    """剥掉**不该**被算作"页面上的字"的那些块。

    顺序有意义：先剥注释、再剥 `<script>`/`<style>`，最后 **`<code>` 也要剥** ——
       `<code>` 里写反引号是**故意的**（那是在讲代码），不算 markdown 记号。
    """
    text = _strip_comments(text)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<code[^>]*>.*?</code>", "", text, flags=re.S)
    return text


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_page_does_not_print_markdown_marks(page):
    """页面上的字 **不许带 markdown 记号**（`**` · **反引号** —— 它们会**原样印在页面上**）。

    **反证**：往任意一页加一行 `<p>**完整上下文**</p>` ⇒ **本条红**。

    **2026-10-12 扩了一条**：**反引号也收进来了** —— 原来只查 `**`，
       而当天在**全部 7 页**上都扫出了反引号（大多是「页脚那句内部话」与"我的工作笔记"），
       它们**真的会原样印出来**（`\`/3\`` 就那样印在页脚上）。
       ⇒ 同族纪律（仓库根 `CLAUDE.md`）：「**页面上不许有 markdown 记号（`**` / `# 标题` / md 链接）**」——
         **反引号是同一族**，当初漏了它。
    """
    with open(os.path.join(static_dir, page), encoding="utf-8") as f:
        live = _visible_text(f.read())
    bad = []
    for line in live.split("\n"):
        marks = [m for m, ch in (("**", "**"), ("反引号", "`")) if ch in line]
        if marks:
            # **报内容，不只报行号** —— `live` 是【剥过块】的文本，
            #    它的行号与文件里的行号**对不上**（2026-10-12 实测：门报第 212 行，
            #    而那在文件里是 `</div>`）。拿那个号去查会**白跑一趟**。
            bad.append({"记号": marks, "那一行": line.strip()[:110]})
    assert bad == [], (
        f"`{page}` 这些行（已剥注释 / script / style / code）里有 markdown 记号 ⇒ "
        f"页面上会**原样印出来**：\n  " +
        "\n  ".join(f"{b['记号']} → {b['那一行']}" for b in bad) +
        "\n⚠️ 想强调就用 `<b>`，想讲代码就包 `<code>`。"
    )


# ==================== 2026-10-13：**已接线的页面不许再挂着"形态"牌子** ====================
#
# **这道门是拿三个实例换来的**（同一天晚上一次性扫出来的）：
#    接完「外部检索与工具Agent编排」那一页之后顺手全站扫了一遍，发现
#    · `rag.html` 与 `stream.html`（**2026-10-12 就接了**）页头还挂着「形态演示」
#    · `kb.html`（**同一天接的**）除了牌子，正文还写着「形态稿：按钮尚未接线 ——
#      真调用在 ⑥ 实施那一步接上」、页脚写着「入库 · 未接线」
#    ⇒ 三处**都是假的**，而**没有任何门会红**（它们只是页面上的字）。
#
# **为什么这是要拦的一类**：访客（与作者）**看的就是那几行字**
#    （最高判据：「95% 的人不会去看代码」）。**页面说自己没接线**，而它其实会真调后端 ——
#    与「后端有、页面上点不到」是**同一件事的两面**：**页面说的与它做的不一致**。
#
# **判据为什么是「有没有它自己那份 js」**：本仓一个页面**接了线**就一定有自己的
#    `app/static/js/<页名>.js`（纯逻辑 + node 用例）—— 那是 2026-10-12 起的前端结构约定。
#    **不是**"扫一眼 fetch"：`rag` / `kb` / `agent` 三页都走 helper 拼路径，
#    页面里**一个字面量都没有**（那道 `test_response_model_declared` 也写着这条）。
#
# **不是"哪些词不许出现"**：反过来 —— **形态页挂着那块牌子是【对的】**
#    （`index` / `ops` / `guide` / `reference` 现在就没接线，它们该挂）。
#    所以下面的表是按"它有没有 js"筛出来的，不是一张手写的页面名单。

#: 那两块"我还是个稿子"的牌子（页面上原样印出来的字）。
_NOT_WIRED_CLAIMS = ("形态演示", "未接线")


def test_the_wired_page_guard_is_not_vacuous():
    """**防空跑**：至少要有一个页面**有自己那份 js** —— 否则下面那条一条都不跑，而且是绿的。"""
    wired = [p for p in PAGES
             if os.path.isfile(os.path.join(static_dir, "js",
                                            os.path.basename(p).replace(".html", ".js")))]
    assert wired, (
        "一个「有自己那份 js」的页面都没扫到 ⇒ 下面那条**空过**。"
        "⚠️ 最可能的成因：页面改了名、或者 js 的命名约定变了（两者都该同步这里）。"
    )


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_a_wired_page_does_not_claim_to_be_a_mockup(page):
    """**有自己那份 js 的页面 = 已接线 ⇒ 页面上不许再挂着「形态演示」/「未接线」**。

    **反证**：给 `rag.html` 的页头加回 `<span>形态演示</span>` ⇒ **本条红**；
       把 `app/static/js/rag.js` 删掉 ⇒ 本条**变绿**（那是"它又变回形态页了"，也该被看见 ——
       而被看见的方式是 `test_response_model_declared` 那边少一条前端调用）。
    """
    js = os.path.join(static_dir, "js", os.path.basename(page).replace(".html", ".js"))
    if not os.path.isfile(js):
        pytest.skip(f"{page} 没有自己那份 js（`{os.path.relpath(js, static_dir)}` 不在）"
                    " —— 它还是个形态页，那两块牌子是**真的**，⛔ 不归本条管")

    with open(os.path.join(static_dir, page), encoding="utf-8") as f:
        live = _visible_text(f.read())
    hits = [line.strip()[:110] for line in live.split("\n")
            if any(k in line for k in _NOT_WIRED_CLAIMS)]
    assert not hits, (
        f"`{page}` **有自己那份 js（= 已接线）**，而页面上还印着这几句：\n  "
        + "\n  ".join(hits)
        + "\n⇒ 访客看的就是这几句话（最高判据：**95% 的人不会去看代码**）。"
          "\n⇒ 处置：把牌子换成**如实**的那句（页脚状态写「已接线」，正文写它会真调什么）。"
          "\n⚠️ ⛔ 别反过来把接线拆掉去迁就这行字。"
    )


# ==================== 2026-10-13：**切换列表那一块的头一句话是定死的** ====================
#
# **作者 2026-10-13 当场指出做错了一次**：那一块（「本页能打哪些接口 / 有哪些链」）
#    我写成了页首**一行 `.tools`**（`本页用到的接口 | 链 3 条 | 伴随的收在下面…`），
#    他说：**应当是【块】**，而且结构与措辞照内部RAG 页那一块 ——
#    原话：「**修改成块，参考 内部RAG 业务 的「「链」展示列表：点击行首的切换按钮，
#    下方左右两个面板将同步更新。」这个块 一样的结构和内容。你写在是下面 块「链 点行首的键
#    左右两块跟着换」：这样做是错的。**」
#
# ⇒ **两块两处**：① 标题那句是**定死的**（别自己改写）② 它是 `.pane` 块、**一行一个整行可点的
#    `button.rail`**（`ri` 序号 + `rn` 一句），不是 `.tools` 一行、不是一排 chips。
# **为什么值得立门**：还剩 4 页没做（`ops` `/guide` `/index` `/reference`），
#    每一页都会有一个这样的列表 ⇒ 不立门就是"下一个人再错一次"。

#: 切换列表那些挂点 id（新增一页用别的 id 时要同步这里）。
_SWITCHER_IDS = ("eps", "chains")

#: **那句定死的标题** —— 两页逐字一致（改它 = 改作者定的口径）。
_SWITCHER_HEADER = "展示列表：点击行首的切换按钮，下方左右两个面板将同步更新。"


def test_switcher_header_scan_is_not_vacuous():
    """**防空跑**：至少要扫到一个切换列表 —— 否则下面那条**空过**。"""
    found = [p for p in PAGES
             if any(f'id="{i}"' in open(os.path.join(static_dir, p), encoding="utf-8").read()
                    for i in _SWITCHER_IDS)]
    assert found, (
        f"一个带 {list(_SWITCHER_IDS)} 挂点的页面都没扫到 ⇒ 下面那条空过。"
        "⚠️ 最可能：换了挂点 id（那就同步 `_SWITCHER_IDS`），或者那两块被删了。"
    )


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_switcher_block_uses_the_agreed_header(page):
    """**切换列表必须是一块 `.pane`，且头一句就是那句定死的话**（作者 2026-10-13 定的）。

    **反证**：把 `agent.html` 那句标题改成别的 ⇒ 本条红；
       把它退回页首那一行 `.tools` ⇒ 本条也红（那时 `pane-h + pane-b#chains` 这对结构就不在了）。
    """
    text = open(os.path.join(static_dir, page), encoding="utf-8").read()
    if not any(f'id="{i}"' in text for i in _SWITCHER_IDS):
        pytest.skip(f"{page} 没有切换列表（无 {'/'.join(_SWITCHER_IDS)} 挂点）—— ⛔ 不归本条管")

    m = re.search(
        r'<div class="pane-h"><span>([^<]*)</span></div>\s*<div class="pane-b"[^>]*id="(?:%s)"'
        % "|".join(_SWITCHER_IDS), text)
    assert m, (
        f"`{page}` 里那个切换列表**不是**「`.pane-h` 紧挨着 `.pane-b#id`」这个结构 ⇒ "
        f"它多半被写成了别的东西（页首一行 `.tools`？一排 chips？）。\n"
        f"⇒ 要的是：**一个块**，块头那句是定死的那一句（见 `_SWITCHER_HEADER`）。"
    )
    assert _SWITCHER_HEADER in m.group(1), (
        f"`{page}` 那个切换列表块的标题不是定死的那句：\n  实得：{m.group(1)!r}\n"
        f"  要求：含「{_SWITCHER_HEADER}」\n"
        f"⚠️ 这句是作者 2026-10-13 定的（附内部RAG 页那个块作样板），⛔ 别自己改写。"
    )


# ==================== 2026-10-15（§七·1）：页头【登录态】那两块【要么都在，要么都不在】 ====================
#
# **它替掉了原来的两段**（`cred-auto` / `cred-manual` / `cred-full` 那一族）——
#    那三块是「访客凭据」时代的：领到了 / 自己填 / 名额满。作者 2026-10-15 裁「改成**正规登录**」
#    ⇒ 换成两块；**同一天晚些时候又改了形态** ⇒ 现在是：
#      · `auth-btn`   —— 页头那颗按钮（**两个状态都在，只换字**：登录 ⇄ 已登录）
#      · `auth-strip` —— **页头【下面】那一行**（额度 + 退出）
#
# **为什么要留这道门（理由一字没变）**：某个页面**漏了**其中一块 ——
#    那时 `authbar.js` 会 `setHidden(...)` 到一个**不存在**的元素上（**静默跳过**，不报错），
#    表现是：**没登录的人在那一页上根本看不到登录入口** —— 他不知道要登录、也不知道去哪登。
#    ⇒ **漏一块 = 那一页在最该说人话的时候什么都不说**，而**没有任何东西会红**。
#
# 与 `authbar.test.js` 分工不同：那边测**逻辑**（没登录就不跑 `onReady`），这边测**页面真有那块**。

#: 那两块的挂点（缺一块就会出现上面那种"什么都不说"）。
_AUTH_ROW_IDS = ("auth-btn", "auth-strip")


def test_the_auth_row_scan_is_not_vacuous():
    """**防空跑**：至少要有页面带这一组挂点 —— 否则下面那条参数化用例一条都不跑，而且是绿的。"""
    found = [p for p in PAGES
             if all(f'id="{i}"' in open(os.path.join(static_dir, p), encoding="utf-8").read()
                    for i in _AUTH_ROW_IDS)]
    assert found, (
        f"没有任何页面同时带 {list(_AUTH_ROW_IDS)} ⇒ 下面那条**空过**。"
        "⚠️ 最可能：挂点改名了（那就同步 `_AUTH_ROW_IDS`），或者那一块被删了。"
    )


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_a_page_with_the_auth_row_has_all_of_its_blocks(page):
    """**有登录态那两块的页面，两块必须齐全**（§七·1）。

    **反证**：从 `rag.html` 里删掉 `<a id="auth-btn" …>` ⇒ **本条红**；
       而删掉之后**其余全世界的门都是绿的**（`authbar.js` 对不存在元素静默跳过）。

    判据是"**有 `auth-btn` 才要求 `auth-strip`**" ——
       **不是**"每个页面都必须有这两块"：`login.html` / `guide.html` / `reference.html`
       与 `websocket_test.html` **本来就不该有**（前三个是纯文档或前置页，
       第四个不面向访客）—— 它们**不归本条管**。
    """
    text = open(os.path.join(static_dir, page), encoding="utf-8").read()
    if 'id="auth-btn"' not in text:
        pytest.skip(f"{page} 没有登录态那两块（⛔ 不是内容页 / 不面向访客的不归本条管）")

    missing = [i for i in _AUTH_ROW_IDS if f'id="{i}"' not in text]
    assert not missing, (
        f"`{page}` 有登录态那两块，却缺这几块：{missing}\n"
        f"⇒ `authbar.js` 会 `setHidden(...)` 到**不存在**的元素上 —— **静默跳过，不报错**。\n"
        f"⇒ 表现：**没登录的人**在这一页根本**看不到登录入口** —— 他不知道要登录、也不知道去哪登。\n"
        f"⇒ 处置：把缺的那块补上（照 `app/static/web/rag.html` 页头那几行 + 页头下面那一行）。"
    )


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_the_auth_strip_sits_outside_the_header(page):
    """🔴 **登录之后那一行（额度 + 退出）必须在 `<header>`【外面】**（作者 2026-10-15 定）。

    原话：「**你不能…单独一行，清空内容，里面只放…不要和页头放在一个块里**」。
    ⇒ 它要是待在 `<header>` 里，两件坏事一起发生：
      ① 跟着页头一起**吸顶**（作者要的是"页头下面一行"，不是"页头的一部分"）；
      ② **把页头撑高** —— 而 `--header-h` 一旦没跟上，`.side` / `.aside` / `.form`
         那几个吸顶块就会**钻到页头底下**（`authbar.js` 的 `syncHeaderHeight()` 正是治这个的）。

    **反证**：把那一块挪到 `</header>` **之前** ⇒ **本条红**。
    """
    text = open(os.path.join(static_dir, page), encoding="utf-8").read()
    if 'id="auth-strip"' not in text:
        pytest.skip(f"{page} 没有登录态那一行 —— ⛔ 不归本条管")
    assert "</header>" in text, f"`{page}` 里没有 `</header>` —— 本条的判据没法成立，先查页面结构"

    assert text.index('id="auth-strip"') > text.index("</header>"), (
        f"`{page}` 的 `#auth-strip` 落在 `<header>` **里面**了\n"
        f"⇒ 它会跟着页头一起**吸顶**，还会**把页头撑高**（`--header-h` 没跟上时吸顶块会钻到页头底下）。\n"
        f"⇒ 作者 2026-10-15 原话：「**单独一行…不要和页头放在一个块里**」。"
    )


# ═══════════════ 2026-10-15（§七·1）：有登录态行 ⇒ 必须真去装配它 ═══════════════
#
# **这道门的由来没变**（那次是在真浏览器上撞出来的）：
#    `index.html` 有那几块控件、却**从来没调 `mount()`**
#    ① 页头那一行**永远空白**（几块全 `hidden`，因为没人调 `applyMode`）；
#    ② 而页面上那颗「跑一次」按钮的判据是「有没有令牌」⇒ **恒为无** ⇒ **永远按不动**，
#       提示还写着「先在页头那块填一把」—— **而页头那块根本没露出来**。
#    界面**不报错**、控制台**不报错**、本文件也**全绿** ⇒ 只有把页面真打开才看得。
#
# ⚠️ 换的只是"装配入口的名字"（`RagCred.mount` ⇒ `RagAuthBar.mount`），**要守的东西一个字没变**。


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_a_page_with_the_auth_row_actually_mounts(page):
    """**有登录态那两块的页面，必须真的调 `RagAuthBar.mount`**（§七·1）。

    **`mount` 是唯一一处"按登录态决定露哪一块、以及要不要把页面起起来"**（`authbar.js`）——
    少了它：下面那一行**永远 hidden**（没人调 `applyMode`）· 页头那颗按钮**永远写着「登录」**
    · 页面**永远不会自己起**。

    **反证**：把任意一页的 `RagAuthBar.mount(` 那一句删掉 ⇒ **本条立刻红**
       （真发生过：`index.html` 就是这么漏的）。
    **`login` / `guide` / `reference` 是【有意】没有那两块控件的** ⇒ 它们**不归本条管**，
       ⛔ 别给它们补 `mount`。
    """
    text = open(os.path.join(static_dir, page), encoding="utf-8").read()
    if 'id="auth-btn"' not in text:
        pytest.skip(f"{page} 没有登录态控件 —— ⛔ 不归本条管")

    assert "RagAuthBar.mount" in text, (
        f"`{page}` 有登录态控件，却**没有调 `RagAuthBar.mount()`**\n"
        f"⇒ 那两块会**永远 hidden**（没人调 `applyMode`）· 页面**永远不会自己起**。\n"
        f"⇒ 处置：整页包一层 `window.RagAuthBar.mount(function () {{ … }});`（照 `rag.html` 末尾）。\n"
        f"⚠️ 若这一页**本来就不该有登录态行**（纯文档页 / 登录页本身）⇒ **把那一整块控件删掉**，⛔ 别留着。"
    )


# ═════════ 行内标记配平（🔴 2026-10-15 加）═════════
#
# **为什么加这一道**：2026-10-15 用一次性脚本（`tmp/apply_copy.py`）把作者裁定的
#   52 条文案回写进 8 个页面时，脚本是拿【文本节点拼出来的整段】去替换的
#   **区间里夹着的 `<b>` / `<code>` / `<span class="mono">` 连同它们的闭合标签一起被吃掉**。
#   实测 **8 页 / 33 处**，两种症状：
#     · `<span class="mono">doc_id` 开着不关 ⇒ **等宽字体漏到整个段尾**；
#     · 正文里留下一个**孤立的 `</b>`** ⇒ 那一句的**加粗整段没了**（作者自己先看出来的）。
#
# 🔴 **当时那三把尺子一把都没红** —— 这才是补门的原因，⛔ 不是"下次注意"：
#   | 尺子 | 为什么漏 |
#   |---|---|
#   | 「52/52 文字在不在页面上」 | 查的是**子串**，标签被吃掉**照样命中** |
#   | `pytest -k "page or web or route"`（155 条） | 一条都**不查标签配平** |
#   | `node --test`（161 条） | 全是**纯函数**，不碰 HTML |
#
# ⚠️ **判据本身也要防"空过"** —— 见下面那条 not_vacuous：
#   正则写歪 ⇒ `_unbalanced_tags` 恒返回空 ⇒ **所有页面一律绿**（本仓最恨的形态）。
#
# 判据（可打印）：`venv/bin/python -m pytest app/tests/test_web_pages.py -q -k tag_balance`
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*)>")


def _unbalanced_tags(text):
    """逐个标签配平；返回 `[(标签名, 行号, 说明), …]`，**空列表 = 配平**。

    三类**挖成等长空白**再扫（⚠️ 挖空白、⛔ 不是删掉 —— **行号要留住**，报错时要能指到行）：
      · `<script>` / `<style>` 整块 —— 里面是 JS/CSS，`<` 比较符与模板串**不是标签**；
      · **HTML 注释 `<!-- … -->`** —— 注释里**本来就会写标签当例子**
        （本仓页面大量用注释写沿革，`<!-- 🔴 它不在 <header> 里 -->` 这种一抓一把）。
    **void 元素**（`br` / `input` / `img` …）与**自闭合写法**（`… />`）跳过。

    🔴 **注释那一类是本条自己栽出来的**：2026-10-15 加这道门当天，
       我往 6 个页面的注释里写了一句「它**不在** `<header>` 里」⇒ **6 页当场全红**，
       报的是「`<header>` 开着没关」—— **报在它自己头上**（同族纪律：本仓 `CLAUDE.md`
       「**要提那串，就拆开写或只描述它**」）。⇒ 修的是**尺子**：注释里那串不算标签。
    """
    def _blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))

    text = re.sub(r"<script\b[^>]*>.*?</script>", _blank, text, flags=re.S | re.I)
    text = re.sub(r"<style\b[^>]*>.*?</style>", _blank, text, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", _blank, text, flags=re.S)

    stack, problems = [], []
    for m in _TAG_RE.finditer(text):
        closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3)
        line = text.count("\n", 0, m.start()) + 1
        if name in _VOID_TAGS or attrs.rstrip().endswith("/"):
            continue
        if not closing:
            stack.append((name, line))
            continue
        for i in range(len(stack) - 1, -1, -1):
            if stack[i][0] == name:
                for inner_name, inner_line in stack[i + 1:]:
                    problems.append((inner_name, inner_line,
                                     f"`<{inner_name}>` 开着没关（它套在 `</{name}>` 里面）"))
                del stack[i:]
                break
        else:
            problems.append((name, line, f"孤立的 `</{name}>` —— 找不到对应的开标签"))
    for name, line in stack:
        problems.append((name, line, f"`<{name}>` 从头到尾没闭合"))
    return problems


def test_tag_balance_scanner_is_not_vacuous():
    """**扫描器必须能测出自己会红**（本仓明文：没有自测的门 = 装饰）。

    两件事一起断：① 喂**已知坏掉**的 HTML 进去**必须报**；② **正常的不能误报**
       （会误报的门最后一定被人关掉 —— 那等于没有门）。
    """
    missing_close = _unbalanced_tags('<p><span class="mono">doc_id还没有入库过</div>')
    assert missing_close, "喂了一段**已知漏掉 `</span>`** 的 HTML，却报「配平」⇒ 扫描器是装饰"
    assert any(name == "span" for name, _line, _why in missing_close), missing_close

    stray_close = _unbalanced_tags("<p>正文</b></p>")
    assert stray_close, "**孤立闭合标签**（吞噬加粗的那一种）没被抓到"

    clean = '<p><b>粗</b> · <code>x</code> · <span class="mono">y</span><br></p>'
    assert _unbalanced_tags(clean) == [], f"正常的 HTML 被误报 ⇒ 门会被当成噪音关掉：{clean}"

    # 🔴 注释里那串**不算标签** —— 不剥注释 ⇒ 页面注释里写一句「它不在 `<header>` 里」
    #    就会**六页全红**，而报错说的是**它自己**（2026-10-15 加这道门当天真栽过）。
    assert _unbalanced_tags("<p>正文</p>\n<!-- 🔴 它不在 <header> 里 -->\n<p>尾巴</p>") == [], (
        "把【注释里】的标签当成了真标签 ⇒ 页面一写注释就假红"
    )
    assert _unbalanced_tags("<p><!-- </b> -->正文</p>") == [], "注释里的孤立闭合标签被当真了"

    total = 0
    for page in PAGES:
        total += len(_TAG_RE.findall(open(os.path.join(static_dir, page), encoding="utf-8").read()))
    assert total > 500, (
        f"{len(PAGES)} 页一共只数出 {total} 个标签 ⇒ 正则没在工作，下面那条参数化用例是【空过】的"
    )


@pytest.mark.parametrize("page", PAGES, ids=PAGES)
def test_page_inline_tags_are_balanced(page):
    """**每一页的行内标记必须配平** —— 吃掉一个闭合标签**没有任何东西会红**（§四同族）。

    症状（2026-10-15 实测 33 处）：`<span class="mono">` 不关 ⇒ **等宽字体漏到段尾**；
       留下孤立的 `</b>` ⇒ **那一句的加粗整段没了**。

    **反证**：把任意一页里的一个 `</span>` 删掉 ⇒ **本条当场红**（已用上面那条 not_vacuous 自证）。
    """
    path = os.path.join(static_dir, page)
    problems = _unbalanced_tags(open(path, encoding="utf-8").read())
    assert not problems, (
        f"`{page}` 的行内标记没配平（{len(problems)} 处）：\n"
        + "\n".join(f"    第 {line} 行：{why}" for _name, line, why in problems)
        + "\n⇒ 多半是**脚本按文本替换**时把闭合标签一起吃掉了（本仓栽过：`tmp/apply_copy.py` 回写 52 条文案）。\n"
        "⇒ 处置：**逐条**把标记补回去，⛔ 别用脚本批量补 —— 上一次就是脚本干的。"
    )


# ═════════ 2026-10-15：「我的库」那一行的【删除】—— 两条"错了也没人知道"的规矩 ═════════
#
# 那一行删的是**整篇**（表里一篇 = 很多块），后端**撤不回来**。两件事必须钉住：
#   ① **先问一次** —— 少了 `confirm` 就是"点一下就没了一篇"，而**没有任何门会红**；
#   ② **只在「我的库」** —— 挂到「公用库」上，点下去**必被后端 403**（那是语料方交的），
#      而页面上"长了一颗按不动的按钮"同样是没人会红的那种坏。
#
# ⚠️ **这条是扫源码文本，⛔ 不是真跑**（pytest 里没有浏览器）——
#    它挡的是"有人把这两样删了/挪走了"，⛔ **不挡"逻辑写错了"**。
#    真链路那一次的判据在 `CHANGELOG.md`（2026-10-15 那条）：真浏览器里
#    点删除 ⇒ 弹确认（原文抓到了）⇒ 发出 `DELETE /api/v1/rag/documents/202` ⇒ **自动重列**。


def test_kb_row_delete_asks_first_and_only_in_my_library():
    """「我的库」每行的删除：**必须先问一次**，且**⛔ 不许出现在「公用库」**。"""
    path = os.path.join(static_dir, "web", "kb.html")
    text = open(path, encoding="utf-8").read()

    assert "function delDoc(" in text, (
        "`delDoc` 不见了 —— 这条判据的锚点没了。先看删除那一段还在不在，"
        "再决定是改这条，还是它被误删了。"
    )
    assert "window.confirm(" in text, (
        "删一篇**必须先用 `window.confirm` 问一次**（删的是整篇、⛔ 撤不回来）。\n"
        "⇒ 少了它 = 点一下就没了一篇，而**没有任何东西会红**。"
    )
    assert "scope === 'mine'" in text, (
        "删除按钮**只许挂「我的库」**（`scope === 'mine'`）——\n"
        "「公用库」是语料方交的那批，点删除**必被后端 403**，那就成了一颗「按不动的按钮」。"
    )


# ═════════════ 2026-10-16：静态资源引用必须【盖着内容哈希】+【引得到】 ═════════════
#
# 🔴 **这条是怎么来的（一次真事故）**：2026-10-15 发布之后，作者在**线上**撞到
#    「**登录之后按钮没变成「已登录」、额度那一行也不见了**」。
#    查下来**不是发布错了**：服务器发的文件与本地**逐字节一致**（34/34）；
#    是**浏览器缓存了旧的 `authbar.js`、而 HTML 是新的** —— 旧 JS 找的是旧挂点
#    （`#auth-in`/`#auth-out`），新版已改名（`#auth-btn`/`#auth-strip`）
#    ⇒ `setHidden()` **对不存在的元素静默跳过** ⇒ **按钮不换字、那一行不露，且不报错**。
#
# 🔴 **为什么本仓躲不掉**：`/static/` 原先**只有 ETag/Last-Modified、没有 `Cache-Control`**
#    ⇒ 浏览器走**启发式缓存** ⇒ **每次发布都可能有人拿到「新 HTML + 旧 JS」**。
#
# ⇒ 治法是**把内容哈希写进 URL**（`?v=…`），配套 `app/main.py` 给 `.js`/`.css` 发长缓存。
#    版本号由  **从文件内容算**（⛔ 不是手写 —— 手写必忘），
#    而**这条门就是"忘了"的兜底**。
#
# ⚠️ **这条门第一次跑就抓到了另一件事**：`guide.html` 引着 `session.js` 与 `cred.js`
#    —— 那两个文件 **2026-10-15 就删了**（§七·1）⇒ 那一页**每次加载打两个 404**，
#    而**全仓没有任何门会红**（"引了一个不存在的文件"这一族，此前无人守）。
#    ⇒ 所以这条门**查两件事**：① 引得到吗　② 版本号是不是当前内容的哈希。


def _stamp_script():
    """把  当模块拿进来 —— ⛔ **别在这儿再抄一份正则**。

    ⚠️ 抄一份 = 两处真相 ⇒ 脚本刷的和门查的**迟早会不一致**（本仓反复栽过的那一族）。
    同款先例：`探针-能力清单` 直接 import。
    """
    import importlib.util
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "stamp_static_version", repo / "scripts" / "stamp_static_version.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_static_asset_references_are_versioned_and_real():
    """页面引的 `.js` / `.css`：**必须引得到**，且**带着当前内容的哈希**（见上）。

    **反证**：随便改一个 `.js` 的内容而不重跑那个脚本 ⇒ **本条红**（版本号对不上了）。
    """
    m = _stamp_script()
    total, bad_all = 0, []
    for page in PAGES:
        text = open(os.path.join(static_dir, page), encoding="utf-8").read()
        total += len(list(m.REF.finditer(text)))
        for url, got, want in m.check(text):
            bad_all.append(f"     {page}  {url}  页面里是 {got} · 现在应该是 {want}")

    assert total >= 20, (
        f"9 页加起来只认出 {total} 处静态资源引用 ⇒ 扫描用的正则没在工作，"
        f"下面那条断言是【空过】的（本仓最恨的形态）"
    )
    assert not bad_all, (
        "这些静态资源引用有问题：\n" + "\n".join(bad_all) + "\n"
        "⇒ 「文件不存在」 ⇒ **要么删掉那行引用、要么把文件找回来**（⛔ 别留着 404）。\n"
        "⇒ 「版本号对不上」⇒ 跑  刷一遍。\n"
        "⚠️ 忘了刷的后果**不是报错、是线上有人看到半新半旧的页面**（2026-10-15 真发生过）。"
    )
