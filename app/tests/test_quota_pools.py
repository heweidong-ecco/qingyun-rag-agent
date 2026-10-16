"""两个额度池 —— **按路径归池**的判据。
"""
import os
import sys

from billing import quota_pools as qp


# ═══════════════════════════════════════════════════════════════════
# 一 · 覆盖门：每一条路由都得有池
# ═══════════════════════════════════════════════════════════════════

def _collect(app_routes, http: list, ws: list) -> None:
    """递归收路由 —— **必须进 `_IncludedRouter`**。

    `FastAPI 0.141` 起 `include_router` 的结果被包成 `_IncludedRouter`，
    **`len(app.routes)` 不再等于路由总数**（本仓 `check_route_auth.py` 记过这条；
    不递归的话本仓 70 多条里只看得到 5 条 ⇒ 下面那条覆盖门会**变成空跑**）。
    """
    for r in app_routes:
        kind = type(r).__name__
        if kind == "_IncludedRouter":
            _collect(getattr(r.original_router, "routes", []) or [], http, ws)
        elif kind == "APIRoute":
            http.append(r.path)
        elif kind == "APIWebSocketRoute":
            ws.append(r.path)


def _route_paths() -> set:
    """app 里**真实存在的路由模板**（含 WS）。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from main import app

    http, ws = [], []
    _collect(app.routes, http, ws)
    return set(http) | set(ws)


def test_every_route_has_a_pool():
    """**新加的路由必须有池** —— 这是本条最要紧的一道门。

    **两个方向都要比**：
      · 表里有、app 里没有 ⇒ **表腐了**（那条路由删了/改名了，而表还写着）；
      · app 里有、表里没有 ⇒ **新路由静默不受限** —— 本仓最怕的那类漏。
    """
    routes = _route_paths()
    declared = set(qp.PATH_POOLS)

    # 防空跑：路由集合是空的话，下面两条断言**恒真**
    assert len(routes) >= 60, (
        f"只收到 {len(routes)} 条路由 —— 枚举口径变了（多半是没递归进 _IncludedRouter）"
        "⇒ 这条门已经没在测东西"
    )

    missing = sorted(routes - declared)
    assert not missing, (
        f"这些路由【没有归池】⇒ 它们会**静默不受限**（`pool_for_path` 回 `POOL_NONE`）：{missing}\n"
        "⇒ 到 `app/billing/quota_pools.py` 的 `PATH_POOLS` 里给它们各补一条，并写明代码依据。"
    )

    stale = sorted(declared - routes)
    assert not stale, (
        f"归池表里这些路径**在 app 里不存在**（删了？改名了？）：{stale}\n"
        "⇒ 表腐了。⛔ 别留着 —— 它会让下一个人以为那条路由还在。"
    )


def test_the_coverage_gate_is_not_vacuous():
    """**自证有效性**：把一条真路由从表里拿掉 ⇒ 上面那条门**必须**判它缺。

    没有这条，「上面到底会不会红」就只是我的声称。
    """
    routes = _route_paths()
    victim = sorted(r for r in routes if qp.PATH_POOLS.get(r) == qp.POOL_LLM)[0]
    pretend = {k: v for k, v in qp.PATH_POOLS.items() if k != victim}
    assert sorted(routes - set(pretend)), (
        f"把 {victim} 从表里拿掉之后，覆盖门居然看不出缺 —— 那它测不出任何东西"
    )


# ═══════════════════════════════════════════════════════════════════
# 二 · 归类是纯函数，**带参路径**也要选得中
# ═══════════════════════════════════════════════════════════════════

def test_llm_paths():
    for p in ("/api/v1/rag/search", "/api/v1/agent/langgraph_chat",
              "/api/v1/agent/mcp_chat/stream", "/api/v1/ws/agent"):
        assert qp.pool_for_path(p) == qp.POOL_LLM, p


def test_embedding_paths():
    for p in ("/api/v1/rag/insert", "/api/v1/rag/upload_document",
              "/api/v1/rag/pg_search", "/api/v1/agent/memory/search",
              "/api/v1/rag/benchmark-embedding"):
        assert qp.pool_for_path(p) == qp.POOL_EMBEDDING, p


def test_paths_with_parameters_still_resolve():
    """**运行时给的是填过参的路径** —— 归池表存的是 `{thread_id}` 那种模板。

    这条要是没过，`/agent/trace/abc` 会**静默**落进 `POOL_NONE`（而它本来就该是 none），
    但 `/rag/documents/{doc_id}` 这类**将来改成要计费的**就会静默不受限 —— **不报错**。
    """
    assert qp.pool_for_path("/api/v1/agent/trace/abc123") == qp.POOL_NONE
    assert qp.pool_for_path("/api/v1/agent/trace/abc123/cost") == qp.POOL_NONE
    assert qp.pool_for_path("/api/v1/rag/documents/42") == qp.POOL_NONE


def test_non_pool_paths():
    for p in ("/health", "/api/v1/agent/token/budget", "/api/v1/agent/execute_code",
              "/api/v1/rag/chat_history", "/api/v1/agent/traces"):
        assert qp.pool_for_path(p) == qp.POOL_NONE, p


def test_unknown_paths_are_not_in_a_pool():
    """表外路径 ⇒ `POOL_NONE`（作者 裁「**不进池**」）。

    这条**看着像在放行**，其实是**故意的** —— 把 `/static/*` 当成 LLM 用会几刷就顶满
    （那正是  那个 429 的同一件事）。代价靠上面那道覆盖门兜。
    """
    for p in ("/static/web/app.css", "/docs", "/redoc",
              "/api/v1/以后新加的", "/chat/extra"):
        assert qp.pool_for_path(p) == qp.POOL_NONE, p


def test_matching_is_by_segment_not_by_prefix():
    """**反证 / 负控**：不是前缀匹配。

    没有这条时，一个图省事的实现（`path.startswith(pattern)`）会**照样全绿** ——
    而它会把 `/api/v1/rag/searchX` 也算成 LLM 池里的。
    """
    assert qp.pool_for_path("/api/v1/rag/searchX") == qp.POOL_NONE
    assert qp.pool_for_path("/api/v1/rag/search/deep") == qp.POOL_NONE, "段数不同，不该匹配"
    assert qp.pool_for_path("/api/v1/agent/trace") == qp.POOL_NONE, "少一段，不该匹配上带参那条"
    assert qp.pool_for_path("/x/api/v1/rag/search") == qp.POOL_NONE, "前面多一段，不该匹配"


# ═══════════════════════════════════════════════════════════════════
# 三 · 总额 = 两池【相加】，不是「× 2」
# ═══════════════════════════════════════════════════════════════════

def test_pool_limit_follows_the_role():
    """每个池取的是**它自己那张表**里的值（两池**不再同源**）。"""
    from billing.token_config import EMBEDDING_DAILY_TOKEN, LLM_DAILY_TOKEN

    assert qp.pool_daily_limit(qp.POOL_LLM, "free") == LLM_DAILY_TOKEN["free"]
    assert qp.pool_daily_limit(qp.POOL_EMBEDDING, "free") == EMBEDDING_DAILY_TOKEN["free"]
    assert qp.pool_daily_limit(qp.POOL_LLM, "premium") == LLM_DAILY_TOKEN["premium"]
    assert qp.pool_daily_limit(qp.POOL_EMBEDDING, "premium") == EMBEDDING_DAILY_TOKEN["premium"]


def test_each_pool_reads_its_OWN_table(monkeypatch):
    """**结构判据**：两个池读的是**两张不同的表** —— 改一张，另一张不许跟着动。

    **为什么非有这条**：把两池**又合回一张表**的改动，会让
    「某一池单独调」这件事**悄悄做不到**，而上面那条（对着真值比）**可能照样绿**。
    改的是**字典里的条目**（不是重新绑定模块名）—— 后者不会影响 `_POOL_TABLES`
    在 import 期抓住的那个对象，测出来的是**假绿**。
    """
    monkeypatch.setitem(qp.LLM_DAILY_TOKEN, "free", 12345)
    assert qp.pool_daily_limit(qp.POOL_LLM, "free") == 12345, "LLM 池没读 LLM 那张表"
    assert qp.pool_daily_limit(qp.POOL_EMBEDDING, "free") != 12345, (
        "改了 LLM 表，Embedding 池也跟着变了 ⇒ 两池又合回一张表了"
    )


def test_pool_none_has_no_limit():
    """不进池的路径没有额度可言 —— 给它一个数会让"总上限"凭空变大。"""
    assert qp.pool_daily_limit(qp.POOL_NONE, "free") == 0


def test_total_is_the_sum_of_the_two_pools(monkeypatch):
    """**作者 定的：用加法，不要用乘法。**

    构造：**两池给不同的数**（100 和 7）⇒ 总额必须是 **107**。
    - 写成 `2 × 某一边` ⇒ 会得到 200 或 14 ⇒ **本条红**
    - 写死一个"总数"常量 ⇒ 改某一池时它对不上 ⇒ **本条红**

    这正是那条理由的机械判据：「**某个额度变了，总额会自己变，不用手动维护**」。
    """
    monkeypatch.setattr(
        qp, "pool_daily_limit",
        lambda pool, role: 100 if pool == qp.POOL_LLM else 7,
    )
    assert qp.total_daily_limit("free") == 107, (
        "总额不是两池之和 —— 多半写成了『× 2』或某个写死的常量"
    )


def test_total_is_the_sum_of_the_two_TABLES():
    """总额 = **LLM 表 + Embedding 表**（逐角色核一遍真值）。

    **本条改过一次**：它原先叫 `..._both_pools_start_equal`，
    钉的是"两池同源 ⇒ 恰好 2×"。**那一版自己就写着**「将来某一池调小了，
    该改的是这一条」—— **那一天就是今天**（free 的 LLM 调到 50 000，Embedding 仍是 10 000）。
    **不是删断言**：现在的判据更硬 —— 它钉「总额**确实**是两张表相加」，
    而**不写任何倍数**（写死倍数在今天就错了）。
    """
    from billing.token_config import EMBEDDING_DAILY_TOKEN, LLM_DAILY_TOKEN

    for role in ("free", "premium", "admin"):
        want = LLM_DAILY_TOKEN[role] + EMBEDDING_DAILY_TOKEN[role]
        assert qp.total_daily_limit(role) == want, (
            f"{role}：总额 {qp.total_daily_limit(role)} ≠ 两表之和 {want}"
        )
