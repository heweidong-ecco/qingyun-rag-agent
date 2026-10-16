"""`/ops`（**系统观测**）页 —— 2026-10-12 **大幅收窄**（前端重做 ⑥）。

## 这个文件为什么只剩两条

它原来有 **12 条**。逐条判过之后：

- **7 条删了**（钉旧骨架的：`data-ops` 四格 · `panel.js` / `ops.js` · `RagOps.buildQuery`
  `id="debugview"` 里那段"调试视角"声明 —— 那些对象**随旧页面一起没了**）。
- **3 条搬了**（入口路由那三条：302 / 目标在盘 / 不进 openapi）
  **并进 `app/tests/test_page_routes.py`** —— 那份参数化过**全 7 页**，
  加一页只需在表里加一行，不必记得再开一个文件。
- **2 条留下**（就是下面这两条）—— **它们守的不是"页面长什么样"，是一条安全约束**。

## 留下的这两条守什么

**「越权面」**： 把 `/debug/quota` 与 `/debug/rate_limit` 的 **`{user_name}` 路径形参删掉了**
⇒ 服务端**只认调用者自己**。而这一页**整个存在理由**就是「只查你自己」。

⇒ 一旦页面把那个输入框或那条带 `{}` 的路径**加回来**：
① 它**看起来能查别人**；② 服务端**根本不收那个参数**（静默忽略）
⇒ **显示了一个做不到的东西** —— 比没有更糟（最高判据「显示了，才知道你有做」的反面）。
而这一页的接口**全是读接口**，所以它不会 400、不会报错，**只是查出来的是你自己的**。

## 断言对象没变（所以这两条现在就是绿的）

它们读的是 `app/static/web/ops.html` —— 那个文件是**原地覆盖**（换了内容、没换路径），
所以这两条**一直都在钉新页**。实测：新 `ops.html` 里 `data-param` / `user_name` / `{}` 路径 **各 0 处**。

判据（可打印）：`venv/bin/python -m pytest app/tests/test_ops_page.py -q -p no:warnings`
"""
import os
import re

from main import static_dir

_PAGE = os.path.join(static_dir, "web", "ops.html")


def _page_text():
    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


def _strip_comments(text):
    """剥掉 HTML 注释与 JS 行注释。

    **为什么必须剥**：本页的注释里**大量在讲**那条被删掉的路径（那是沿革记录，给人读的）。
    不剥的话，下面那条会**报在它自己头上** —— 本仓栽过同型的第 5 次，纪律是
    「**要提那串，就拆开写或只描述它**」。这里采取的是"剥注释"，因为注释是**有意的记录**。
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//[^\n]*", r"\1", text, flags=re.M)
    return text


def test_page_has_no_place_to_type_someone_elses_name():
    """**页面里不许有任何"查谁"的入口**（的前提）。

    那条前提是：两条端点的 `{user_name}` **路径形参被删掉了** ⇒ 服务端**只认调用者**。

    ⇒ 页面上若有 `user_name` 输入框：
    ① 它**看起来能查别人**；② 而服务端**根本不收那个参数**（静默忽略，查的仍是自己）
    ⇒ 那个框是**假的** —— 比没有更糟（本仓立场：「**显示了，才知道你有做**」的反面：
    **显示了一个做不到的东西**）。

    **剥掉注释再扫** —— 页面里**有意**用注释记录着那条被删掉的路径（沿革），
       不剥的话本条会报在它自己头上（本仓栽过同型 5 次）。
    **反证**：给任意一格加一个 `<input data-param="user_name">` ⇒ 本条红。
    """
    live = _strip_comments(_page_text())
    assert "data-param" not in live, (
        "页面里出现了 `data-param` —— 本页 **4 条端点一个参数都不收**（把形参删了）\n"
        "⇒ 任何参数控件都是**摆设**：服务端静默忽略它，查的仍是调用者自己"
    )
    assert "user_name" not in live, (
        "页面的**活代码**里出现了 `user_name` —— 本页⛔ 没有任何「查谁」的入口"
    )


def test_page_has_no_path_parameter_in_any_url():
    """**页面里不许出现带 `{...}` 的路径**（的前提 · 另一半）。

    与上一条**分工不同**：上一条管"控件"，这条管"路径字面量"。
    两者都能把**越权面**弄回来，而**只有这一条**能测出"照旧文档把 `{user_name}` 写回去"。

    剥注释再扫（同上）。
    **反证**：把任意一格的调用处改成写死一条带 `{}` 的路径 ⇒ 本条红。
    """
    live = _strip_comments(_page_text())
    hits = re.findall(r"/api/v1/[A-Za-z0-9_/]*\{[^}]*\}", live)
    assert hits == [], (
        f"页面里出现了带形参的路径：{hits}\n"
        f"⇒ 那正是  删掉的东西 —— 加回来 = **越权面回来**，"
        f"而这一页的整个存在理由就是「只查你自己」"
    )


# ==================== 2026-10-13：这一页接线之后补的那几条 ====================
#
# 上面那两条守的是**安全约束**（越权面），下面这几条守的是**这一页的身份**：
#    它是一个**判断**页，判决由真探针结果算出来；而它「不给访客探活入口」是**有意的**（§2.3）。
#
# 为什么值得立：这一页差点变成"把几个数摆出来让人自己猜" ——
#    而 `编排 §10.2` 第 5 行明写「输出是**一个判断**，不是一堆数」。
#    这几条把那个身份**钉在结构上**（按钮 + 四格 + 那一行"系统方视角"的标注）。

import os as _os


def _live_text():
    """剥掉注释之后的页面正文（与上面那两条同一个理由：注释是**有意**的沿革记录）。"""
    return _strip_comments(_page_text())


def test_the_page_says_whose_view_this_is():
    """**页首必须标清「这是系统方视角」**（`编排 §10.2` 第 5 行 ·  §2.4）。

    反证：把那句删掉 ⇒ 本条红。它与 `场景清单` S7 的「谁 = 我们（系统方）」**不矛盾**：
       前者说"这一页对访客开不开"（开着，但标明），后者说"这条链的使用者是谁"。
    """
    t = _live_text()
    assert "系统方视角" in t, "页首那句「这是系统方视角」不见了 —— 访客会把它当成自己的用量页"
    assert "不是访客视角" in t, "少了那半句直白的标注"


def test_the_page_has_one_probe_trigger_and_four_verdict_slots():
    """**一个「探一次」+ 四格判断依据**（不做逐步点击 · 四格缺一不可）。"""
    t = _page_text()
    assert 'id="probe"' in t, "没有那个「探一次」按钮 ⇒ 这一页点不动"
    for i in ("m-alive", "m-backlog", "m-cache", "m-limits"):
        assert 'id="%s"' % i in t, "判断依据缺了一格：%s（四格是活着/积压/缓存/我的限）" % i
    # 判决那句**不是**写死在 HTML 里的（它必须由真探针结果算出来）
    assert 'id="verdict-body"' in t, "判决那一格没了"


def test_the_probe_table_is_built_from_the_table_not_hand_written():
    """**分项探针表由 `js/ops.js` 那张探针表建出来**，不是 HTML 里手写六行。

    手写的话，加一条探针要改两处 ⇒ 两处必然漂（本仓的老毛病）。
    """
    t = _page_text()
    assert 'id="probes-body"' in t, "分项表那个 tbody 挂点没了"
    assert "O.PROBES" in t, "页面没有从 `js/ops.js` 的探针表建行 —— 那六行是哪来的？"
    assert _os.path.isfile(_os.path.join(_os.path.dirname(_PAGE), "..", "js", "ops.js")), \
        "`app/static/js/ops.js` 不在 —— 本页的纯逻辑与用例都在那儿"


def test_the_page_does_not_offer_the_health_check_trigger():
    """**不给访客"探活"的入口** —— 那一条会**真跑代码 + 真联网**（§2.3）。

    实测过的两件：`POST /agent/tool_health/refresh` → `run_health_check` 逐条真调工具，
    其中 `web_search` **真联网**、`execute_python` **真执行代码**（`tool_health.py:111-117`）。
    ⇒ 访客点一下就会真的跑起来。**本页只读"已经探过的那份结果"**。
    反证：在页面上加一个调它的按钮 ⇒ 本条红。
    """
    t = _page_text()
    assert "tool_health/refresh" not in t, (
        "页面里出现了探活端点 —— 访客一点就会**真跑代码 + 真联网**。\n"
        "⇒ 要它的话，先想清这件事（§2.3 记了权衡）。"
    )
    assert "refresh" not in _live_text().lower().replace("刷新", ""), \
        "页面正文里出现了 refresh 相关的入口字样 —— 同上"
