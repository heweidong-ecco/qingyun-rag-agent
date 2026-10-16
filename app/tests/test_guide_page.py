"""`/guide`（**接入指南**）页 —— 2026-10-13 **接线**时新立的那几条。

## 这一页与别的六页**不是一类**

七页里只有它**不动手做事**：别的六页各自真调后端（检索 / 入库 / 编排 / 探针 / 展示 / 列举），
**它只讲"怎么用"**。⇒ 所以它**没有** `app/static/js/guide.js`，也**不**该有。

**这是需求方 2026-10-13 拍的口径**（他先问了三个问题：「这一页有什么用」「他接入以后会烧我的 token 吗」
「这是展示和说明吗」）⇒ 结论：**留下，但只做成纯文档（零 token）**。
决策全文 ⇒ 本仓的决策记录（⛔ 不随产品仓提供）

## 于是下面这几条守的是什么

| # | 守什么 | 破了会怎样 |
|---|---|---|
| 1 | **它不发请求**（纯文档） | 一旦有人给它加个"真跑一次"，访客点一下就开始花 token —— 而那正是这次**有意不做**的事 |
| 2 | **凭据那两条路说对了** | 页面原来写「拿到之后一律放在 `X-API-Key` 头上」—— 而 `/auth/login` 发的是 **JWT**，要放 `Authorization`。**说错话的说明书比没有更糟** |
| 3 | **`/admin/create_user` 要管理员，说了** | 漏了 ⇒ 访客照着调，得到 403 而不知道为什么 |
| 4 | **四档里两档在这台上会 500，说了** | 漏了 ⇒ 访客以为他调错了（真因：本服务镜像**有意不装**重排依赖） |
| 5 | **错误体两种形状，说了** | 原页写「一层 `error` 包住，里面 `type`/`code` 两级」—— 三处都错，且**漏了 422 根本不走统一体** |

**这些东西靠"下次注意"守不住**（本仓立场：**只有文字就漏，结构才执行**）——
   所以每一条都落成**一句可打印的断言**。改页面的文案时若把这五条碰掉，本条会当场点名。

判据（可打印）：`venv/bin/python -m pytest app/tests/test_guide_page.py -q -p no:warnings`
"""
import os
import re

from main import static_dir

_PAGE = os.path.join(static_dir, "web", "guide.html")


def _page_text() -> str:
    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


def _strip_comments(text: str) -> str:
    """剥掉 HTML 注释与 JS 注释。

    **必须剥**：本页顶部那段注释与正文里都在讲**沿革**（"原来是这么写的、为什么改"），
       不剥的话下面几条会**报在它自己头上**——本仓栽过同型的第 5 次，纪律是
       「**要提那串，就拆开写或只描述它**」。这里采取的是"剥注释"（注释是**有意**的记录）。
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//[^\n]*", r"\1", text, flags=re.M)
    return text


#: 扫"它会不会自己发请求"时的两条调用形状（与 `test_web_pages.py` 那两把尺子同源）。
_CALL = re.compile(r"(?:getJSON|fetch)\s*\(")


def test_this_page_is_a_document_and_does_not_call_the_backend():
    """**这一页不发任何请求** —— 它是说明书，不是控制台。

    为什么单独立一条：别的六页**每页都有真调用**，于是"给这页也加个按钮吧"是**很自然的下一个动作** ——
    而那恰好会把这次**有意不做**的事（让访客花 token）加回来。
    ⇒ 这条一红，就是在提醒：**要加真跑，先回去读 **（那里记了为什么不做、以及反悔成本）。

    **反证**：在页面里加一句 `<script>fetch('/api/v1/rag/search')</script>` ⇒ 本条红。
    它只管**页面自己**；`/static/js/session.js` 那一句 `fetch(CLAIM_PATH)` 是**共用凭据层**的事
       （每页都引它），不算这一页"在调后端"。
    """
    hits = _CALL.findall(_strip_comments(_page_text()))
    assert hits == [], (
        f"`web/guide.html` 里出现了 {len(hits)} 处请求调用：{hits}\n"
        f"⇒ 这一页是【说明书】（需求方 2026-10-13 定：留下，但只做纯文档、零 token）。\n"
        f"⇒ 真要加真跑，先读 本仓的决策记录（⛔ 不随产品仓提供）（含反悔成本）。"
    )


def test_the_page_says_login_returns_a_jwt_not_an_api_key():
    """**`/auth/login` 发的是 JWT，不是 API Key** —— 页面原来那句话是错的（§二·1）。

    原话是「拿到之后**一律放在 `X-API-Key` 头上**」，而 `api_v1.py:104-108` 的 `login`
    回的是 `access_token` / `refresh_token` —— 那是 **JWT**，要放 `Authorization: Bearer`。
    ⇒ 一个**说错话**的接入指南，比没有更糟：访客照着做会得到 401，而**看不出哪里错了**。

    **反证**：把正文里 `Authorization` 那个词删掉（退回"一律 X-API-Key"）⇒ 本条红。
    """
    live = _strip_comments(_page_text())
    assert "Authorization" in live, (
        "页面正文里没有 `Authorization` 这个词 ⇒ 那句「凭据一律放 X-API-Key」又回来了。\n"
        "⇒ 而 `/auth/login` 发的是 JWT（`api_v1.py:104-108`），它只能走 `Authorization: Bearer`。"
    )
    assert "JWT" in live, "少了「login 回的是 JWT」那句直白的说明"


def test_the_page_says_create_user_needs_admin():
    """**`/admin/create_user` 要管理员**（`api_v1.py:184` 的 `Depends(require_admin)`）。

    漏了它的后果：访客照着建号 ⇒ **403**，而页面上一个字都没提示过"这一步要管理员"。
    **反证**：把那句"要管理员"删掉 ⇒ 本条红。
    """
    live = _strip_comments(_page_text())
    spots = [m.start() for m in re.finditer("create_user", live)]
    assert spots, "页面里没有提到 `/admin/create_user` ⇒ 建号那条路没了"
    # **扫【每一处】**，不是只看第一处 —— 第一次出现是在那个 `POST …` 的路径里，
    #    而"要管理员"那句在**后面**那段小结里（写死一个 `find()` 会量错地方）。
    assert any("管理员" in live[max(0, i - 120): i + 300] for i in spots), (
        "每一处 `create_user` 附近都没有「管理员」三个字 ⇒ 访客会以为谁都能建号。\n"
        "⇒ 要它在**同一个句子里**：`/admin/create_user`（要管理员，非管理员回 403）。"
    )


def test_the_page_warns_that_two_modes_500_on_this_deployment():
    """**四档 mode 里 `accurate` / `full` 在本 线上会 500** —— 页面必须说（§二·3）。

    真因（核过，不是推的）：那两档走 `enable_rerank=True` 的管线
    （`rag_pipeline.py:250,263`）⇒ 调 `rerank_async` ⇒ `reranker.py:14` 惰性
    `from sentence_transformers import CrossEncoder` ⇒ **本服务镜像有意不装它**
    ⇒ `ModuleNotFoundError` **一路不接**（`rag_pipeline.py:143` 裸调用）⇒ **500**。

    **反证**：把"500"那句删掉 ⇒ 本条红。
    """
    live = _strip_comments(_page_text())
    assert "500" in live, (
        "页面里没有提到 500 ⇒ 「哪两档在这台上跑不了」这件事又没了。\n"
        "⇒ 访客点了 `mode=accurate` 只会以为自己调错，而真因是镜像有意不装重排依赖。"
    )
    for m in ("accurate", "full"):
        assert m in live, f"四档表里少了 `{m}`"


def test_the_page_describes_the_real_error_envelope_and_the_422_exception():
    """**错误体是 `{error, code, status_code}`，而 422 不走它**（§二·4）。

    原页写的是「**一层 `error` 包住的**，里面 `type` / `code` 两级」—— 三处都不对：
    `error` **不是包层**（它自己就是那句给人看的 message）· **没有 `type` 这一级**
    而且 **422 根本不走这个体**。核过：`main.py:645-649` 是统一体，而**全仓没有**
    `RequestValidationError` 处理器（`grep -rn RequestValidationError app/main.py` ⇒ 0）
    ⇒ 422 回的是 FastAPI 默认的 `{"detail":[...]}`。

    **反证**：把"422 不走这个体"那段删掉 ⇒ 本条红。
    """
    live = _strip_comments(_page_text())
    for k in ("status_code", "code", "detail"):
        assert k in live, f"错误体那段少了 `{k}` —— 形状说漏了"
    assert "422" in live, "少了 422 那条"
    i = live.find("detail")
    near = live[max(0, i - 400): i + 200]
    assert "422" in near, (
        "`detail` 与 `422` 不在同一段 ⇒ 「422 不走统一体」这句多半被删了。\n"
        f"实际那一段：{near.strip()[:200]}"
    )
