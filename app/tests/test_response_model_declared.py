"""**凡被前端调用的端点，必须有 `response_model`**（那条门 · 立）。
"""
import os
import re

import pytest

from main import app, static_dir

#: 还没补的老端点 —— **显式列出 + 写清理由**，不许往里加新东西。
#:
#: 🔴 **（§七·1）：现在是【空的】。**
#:    ⚠️ 它是被下面那条 `test_pending_entries_are_still_real` **自己撵出去的**
PENDING: dict = {}

#: 这些端点的 200 **不是 JSON 体**（SSE 逐帧）⇒ `response_model` 对它们**不适用**。
#: **不是"还没补"** —— 给它硬加一个「整个返回是一个 JSON 对象」的声明，
#:    那**是一句假话**（它返回的是一条流，`data:` 一帧一帧走）。
#: 帧序与契约 ⇒ `app/routing/specs/sse.md`
NOT_JSON = {
    "/api/v1/rag/stream_search": "SSE —— 边生成边吐 data: 帧",
    #    的「用流式」开关会打它们（`app/static/js/agent.js` 的三个 `STREAM_*_PATH` 常量）。
    #    **不是"还没补"** —— 它们的 200 是一条流（`data:` 一帧一帧），
    #    给它们硬加一句"返回是一个 JSON 对象"**是一句假话**。
    #    帧序 ⇒ `app/routing/specs/sse.md`；页面侧的分帧纯逻辑 ⇒ `app/static/js/agent.js`
    "/api/v1/agent/langgraph_chat/stream": "SSE —— 本页主链的流式形态",
    "/api/v1/agent/mcp_chat/stream": "SSE —— 链列表第 2 条的流式形态",
    "/api/v1/agent/plan_execute/stream": "SSE —— 链列表第 3 条的流式形态（⚠️ 它只流规划段，之后一段静默）",
}

#    页面取数从裸 `fetch(` 改成了 `RagAuth.apiFetch(`（那一个入口负责贴认证头 + 令牌过期换一把）。
#    ⚠️ 而 `fetch\(` **匹配不到** `apiFetch(`（`apiFetch` 里那个 F 是大写）
#    这个集合会**静默缩水**，下面那条就变成**假绿** —— 是 `NOT_JSON` 那条防空跑断言把它抓出来的。
_FETCH_LITERAL = re.compile(r"(?:getJSON|fetch|apiFetch)\(\s*['\"`](/api/v1/[^'\"`?]*)")
_JS_PATH_CONST = re.compile(r"^\s*const\s+[A-Z][A-Z0-9_]*\s*=\s*'(/api/v1/[^'?]*)'", re.M)


def _frontend_raw() -> set:
    """前端**真的会去打**的那些路径字面量（两把尺子合起来）。

    🔴 **⛔ 这里【不做】`rstrip("/")`** —— 结尾那个 `/` 是**信息**：
    它表示前端**要在后面拼东西**（`FULL_PATH + id`）。
    旧版在这里就 rstrip 掉了 ⇒ 拼接路径**塌缩成上一级**（`/api/v1/rag/documents/`
    ⇒ `/api/v1/rag/documents` = **列表端点**，而它**有** schema）⇒ **DELETE 那条整条空过**。
    那正是 漏掉的原因（修）。
    """
    found = set()
    roots = [os.path.join(static_dir, "js"), os.path.join(static_dir, "web")]
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not (name.endswith(".js") or name.endswith(".html")):
                    continue
                if name.endswith(".test.js"):
                    continue          # 用例里的路径不是"前端会打的"
                text = open(os.path.join(dirpath, name), encoding="utf-8").read()
                # 清单页：把端点【列出来给人看】，不是调用
                if name == "reference.html":
                    continue
                found |= set(_FETCH_LITERAL.findall(text))
                found |= set(_JS_PATH_CONST.findall(text))
    return {u for u in found if u}


def _frontend_called() -> set:
    """前端会打的【精确】端点路径（结尾 `/` 去掉）—— 供防空跑与 `NOT_JSON` 核对用。"""
    return {u.rstrip("/") or "/" for u in _frontend_raw()}


def _frontend_prefixes() -> set:
    """前端**要往下拼**的前缀（字面量以 `/` 结尾的）——  补的那把尺子。"""
    return {u for u in _frontend_raw() if u.endswith("/")}


def _route_methods() -> dict:
    """`{路径: {方法: 有没有声明 200 的 JSON schema}}` —— **按方法**，⛔ 不是按路径。

    🔴 **为什么必须按方法**（的第二个洞）：旧版是
    「这条路径上有**任意一个**方法声明了 ⇒ 整条路径算声明过」并且**一命中就 `break`**。
    于是 `/api/v1/rag/documents/{doc_id}` 被它上面的 **GET**（`DocFullResponse`）
    **掩蔽**掉了 —— **同一条路径上的 DELETE 缺声明，永远看不出来**。

    判据取 **OpenAPI 的 `200` schema**，⛔ 不是去读路由对象的 `response_model` ——
       实测：本版 FastAPI 的 `include_router` 把子路由包成
       **`fastapi.routing._IncludedRouter`**，它**没有 `.routes` / `.router` / `.path`**
       ⇒ 从 `app.routes` 那一层**根本走不到** API 路由。而且这件事**没有任何报错**
       ⇒ 只会让本门**恒绿**。⇒ 所以另有一条防空跑。
    """
    out = {}
    for path, item in app.openapi()["paths"].items():
        key = path.rstrip("/") or "/"
        per = {}
        for method, spec in item.items():
            schema = (spec.get("responses", {}).get("200", {})
                      .get("content", {}).get("application/json", {}).get("schema") or {})
            per[method] = bool(schema)      # 空 `{}` = 没声明；有 `$ref` = 声明了
        out[key] = per
    return out


def _declared() -> set:
    """**至少有一个方法**声明了形状的那些路径（= 旧语义，只给防空跑那条用）。

    ⚠️ **判"某条端点该不该有声明"⛔ 别用它** —— 那要用 `_route_methods()`。
    这一条留着只为防空跑（`test_the_scan_is_not_vacuous`）：它问的是
    「能不能从 OpenAPI 里读出东西」，不是"某条端点合不合规"。
    """
    return {p for p, methods in _route_methods().items() if any(methods.values())}


def _called_route_keys() -> list:
    """前端会打的 **`(路径, 方法)`** 对 —— **前缀要展开**（的第二个洞）。

    展开规则：字面量以 `/` 结尾 ⇒ 它是**前缀** ⇒ 凡是 OpenAPI 里以它开头的路径
    （也就是那些带路径参数、由前端拼出来的）**全部**进来；
    再对**每一个**路径 × **每一个**方法要求 200 schema。

    ⇒ 这样 `'/api/v1/rag/documents/' + id` 就会落到
      **`DELETE /api/v1/rag/documents/{doc_id}`** 上，而**不再**塌缩成列表端点。
    """
    routes = _route_methods()
    keys = set()
    for p in _frontend_called():
        if p in routes:
            keys |= {(p, m) for m in routes[p]}
        else:
            keys.add((p, None))          # 前端在打一条**不存在**的路由 ⇒ 也该报
    for pref in _frontend_prefixes():
        for rp, methods in routes.items():
            if rp.startswith(pref) and rp != pref.rstrip("/"):
                keys |= {(rp, m) for m in methods}
    return sorted(keys, key=lambda km: (km[0], km[1] or ""))


def test_the_scan_is_not_vacuous():
    """两把握尺子**都必须扫到东西** —— 否则下面的断言恒真（本仓最恨的空跑）。"""
    called = _frontend_called()
    declared = _declared()
    assert len(called) >= 3, f"只扫到 {len(called)} 个前端端点 ⇒ 扫描规则坏了：{sorted(called)}"
    assert all(k in called for k in NOT_JSON), (
        f"`NOT_JSON` 里那些端点必须**真的**被前端调用 —— 否则那是一条空话：{sorted(NOT_JSON)}"
    )
    assert declared, (
        "一条【声明了返回形状】的路径都没扫到 ⇒ 下面那条会**空过**。\n"
        "⚠️ 最可能的成因：`_declared()` 读的路子又坏了（本仓栽过：`_IncludedRouter` 把子路由藏了）。"
    )


def test_every_frontend_called_endpoint_declares_its_shape():
    """**被前端调用的端点，每一个方法都要声明返回形状**（或显式列在 `PENDING` / `NOT_JSON` 里）。

    🔴 **本条改成【按方法】+【展开拼接前缀】判。**
    旧版有两个洞，叠在一起把 `DELETE /api/v1/rag/documents/{doc_id}` 整条放过去了：

    1. **按路径、不按方法**，且一命中就 `break` ⇒ 同路径的 **GET** 声明过，
       DELETE 的缺失被**掩蔽**；
    2. 尺子把 `'/api/v1/rag/documents/'` 的结尾 `/` `rstrip` 掉了
       前端的**拼接路径塌缩成列表端点** ⇒ 真正的 DELETE 路径**从没进过集合**。
    """
    routes = _route_methods()
    missing, no_route = [], []
    for path, method in _called_route_keys():
        if path in NOT_JSON or path in PENDING:
            continue                        # SSE 不适用 / 显式缓期
        if method is None:
            no_route.append(path)
        elif not routes.get(path, {}).get(method):
            missing.append(f"{method.upper():6s} {path}")
    assert not no_route, (
        "前端在打这些路径，而后端**没有这条路由**：\n  " + "\n  ".join(no_route) +
        "\n⇒ 要么路径拼错了（页面会 404），要么端点被删了而页面没跟上。"
    )
    assert not missing, (
        "这些【被前端调用】的端点没有声明返回形状：\n  " + "\n  ".join(missing) +
        "\n⇒ `response_model` **真会裁字段**（实测：声明成只含 a 的模型 ⇒ 实返 {'a': 1}，"
        "多出来的键被**静默吃掉**）⇒ 前端在用的字段没了，**不会有任何门红**。\n"
        "⇒ 处置：给它补一个模型（**先真调一次核出真实返回的键**，⛔ 别照文档抄）。\n"
        "⚠️ 判据是【路径 × 方法】—— ⛔ 别因为同一条路径上别的方法声明过就放过它。"
    )


def test_pending_is_empty():
    """🔴 `PENDING` **现在应当是空的** —— 里面有东西 = 有端点还没声明返回形状。

    ⚠️ **为什么单立一条**：`PENDING` 空了之后，下面那条
       `@pytest.mark.parametrize("path", sorted(PENDING))` **会收集到 0 条用例** ——
       那就是一条**静默的空跑**（本仓最恨的形态：「**从不命中**与**没人违规**在机器痕迹上完全一样」）。
       ⇒ 这一条把它变成**一句会说话的断言**。

    ⚠️ 若将来**确实**要往 `PENDING` 里加一条（比如某个老端点暂时补不了）：**把这一条改掉**，
       ⛔ 别把它删了 —— 删了就等于连"现在是空的"这件事都没人管了。
    """
    assert not PENDING, (
        f"`PENDING` 里现在有 {sorted(PENDING)} —— 说明这些端点没有声明返回形状。\n"
        f"⇒ 要么给它们补 `response_model`，要么**在这里写清为什么暂时补不了**。"
    )


@pytest.mark.parametrize("path", sorted(PENDING))
def test_pending_entries_are_still_real(path):
    """`PENDING` 里那些**必须还在** —— 补完了就该**把它删掉**，别留着当"允许清单"。"""
    called = _frontend_called()
    assert path in called, (
        f"`PENDING` 里的 {path} 已经不在【被前端调用】的集合里了 ⇒ "
        f"要么它被删了、要么改用别的方式拼路径 ⇒ **把这一行从 PENDING 里删掉**"
    )
