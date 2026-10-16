"""两个额度池**接上了没有**（· 2026-10-11）。

`app/tests/test_quota_pools.py` 测的是**归池表本身**（纯函数、覆盖面）。
本文件测的是**接线** —— 那条表**真的被用上了吗**：
归池表全绿、而中间件忘了调它，是完全可能的（本仓那类"写了但没接上"栽过多次）。

## 还钉一条**最容易漏的**：总上限

/ 批6 **漏了 11 处图内总额闸**（`check_token_budget`，在 `agent_graph*.py` 等）。
不处理的话：中间件按池放行到 2×，**图内闸仍在 1× 处拦住** ⇒ **"翻倍"根本不会发生**，
而页头显示的却是 2× ⇒ **两个数对不上**。
⇒ 本文件钉住：`get_user_token_budget`（那 11 处共用的那一个）**= 两池之和**。

本文件**不连库、不连 Redis**。
"""
import asyncio


def _request(path, *, headers=(), client=("1.2.3.4", 5000)):
    from starlette.requests import Request

    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": client, "server": ("testserver", 80), "scheme": "http", "app": None,
    })


async def _call_next(_request):
    from starlette.responses import Response

    return Response("ok")


def _budgets(*, llm_remaining, emb_remaining, limit=10_000):
    """假的两池读数 —— **两个池给不同的数**，否则"按池判"与"按总数判"分不出来。"""
    def one(limit, remaining):
        return {"limit": limit, "used": limit - remaining, "remaining": remaining}

    return {
        "role": "free",
        "llm": one(limit, llm_remaining),
        "embedding": one(limit, emb_remaining),
        "total": one(limit * 2, llm_remaining + emb_remaining),
    }


def _drive(monkeypatch, path, budgets, *, headers=()):
    """把 `QuotaMiddleware` 真跑一遍（库/Redis 全换成假的）。返回 `(状态码, 响应头)`。"""
    import main

    monkeypatch.setattr(main, "resolve_quota_identity", lambda *a, **k: "someone")
    monkeypatch.setattr(main, "get_pool_budget_info", lambda _u: budgets)

    mw = main.QuotaMiddleware(main.app)
    resp = asyncio.run(mw.dispatch(_request(path, headers=headers), _call_next))
    # 响应头的键统一转小写再比 —— `dict(resp.headers)` 是小写的，
    #    写 `headers["X-Quota-Pool"]` 会拿到 None 而**看着像"没设这个头"**。
    return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}


# ═══════════════════════════════════════════════════════════════════
# 一 · 按【路径】选池，且**只**判那一个池
# ═══════════════════════════════════════════════════════════════════

def test_llm_path_is_gated_by_the_llm_pool(monkeypatch):
    """LLM 池用完 ⇒ LLM 那条路 **429**；而**同一次** Embedding 池还有余额。"""
    status, headers = _drive(
        monkeypatch, "/api/v1/rag/search", _budgets(llm_remaining=0, emb_remaining=9_999),
    )
    assert status == 429, "LLM 池已用完，LLM 那条路却没被拦住"
    assert headers.get("x-quota-pool") == "llm", f"没写明是哪个池：{headers.get('X-Quota-Pool')}"


def test_embedding_path_is_gated_by_the_embedding_pool(monkeypatch):
    """Embedding 池用完 ⇒ embedding 那条路 **429**；而 **LLM 池满额也救不了它**。"""
    status, headers = _drive(
        monkeypatch, "/api/v1/rag/insert", _budgets(llm_remaining=9_999, emb_remaining=0),
    )
    assert status == 429, "Embedding 池已用完，embedding 那条路却没被拦住"
    assert headers.get("x-quota-pool") == "embedding"


def test_the_two_pools_do_not_bleed_into_each_other(monkeypatch):
    """**两个池各管各的** —— 这正是"拆成两份"的全部意义。

    这一条只管「**一边满了不拦另一边**」。它**抓不住**"实现里其实在看总数"
    —— 那种情况我实测过：总数 = 两池之和，只要有一池还有余额，总数就 > 0 ⇒ 这条**照绿**。
    ⇒ 抓"用总数判"的是下面那条 `test_a_total_that_is_exhausted_does_not_block_a_pool_that_is_not`
    （**判据要能证伪：写之前先问"这条命令的输出会变吗"** —— 我第一版就没问，被变异自证照出来了）。
    """
    # ① Embedding 池耗尽，但走 LLM 那条路 ⇒ **放行**
    s1, _ = _drive(monkeypatch, "/api/v1/rag/search", _budgets(llm_remaining=50, emb_remaining=0))
    assert s1 == 200, "Embedding 池耗尽却把 LLM 那条路也拦了 —— 两个池没分开"

    # ② LLM 池耗尽，但走 embedding 那条路 ⇒ **放行**
    s2, _ = _drive(monkeypatch, "/api/v1/rag/insert", _budgets(llm_remaining=0, emb_remaining=50))
    assert s2 == 200, "LLM 池耗尽却把 embedding 那条路也拦了 —— 两个池没分开"


def test_a_total_that_is_exhausted_does_not_block_a_pool_that_is_not(monkeypatch):
    """**总计见底了，也不许拿它去拦某一个还有余额的池**。

    构造：**总计剩余 = 0，而 LLM 池还剩 50** —— 这**不是编的**：
    `total.used` 用 `get_daily_token_usage`（**含 `other` 那一栏**），
    而池的 `used` 只取各自那一栏 ⇒ 加上 `other` 之后，**总计确实可能先见底**。

    ⇒ 实现里若拿"总计"当判据（而不是按路径选池），这条会红。
    """
    import main

    b = _budgets(llm_remaining=50, emb_remaining=0)
    b["total"] = {"limit": 20_000, "used": 20_000, "remaining": 0}   # 总计已见底

    monkeypatch.setattr(main, "resolve_quota_identity", lambda *a, **k: "someone")
    monkeypatch.setattr(main, "get_pool_budget_info", lambda _u: b)
    mw = main.QuotaMiddleware(main.app)
    resp = asyncio.run(mw.dispatch(_request("/api/v1/rag/search"), _call_next))

    assert resp.status_code == 200, (
        "总计见底 ⇒ 把 LLM 那条路也拦了 —— 说明判的是【总计】而不是【按路径选出来的池】"
    )


def test_paths_outside_the_table_are_not_gated(monkeypatch):
    """作者 2026-10-11 裁「**表外路径不进池**」⇒ 两池全空**也照常放行**。

    拿 `/api/v1/agent/token/budget` 当样本是**故意的** —— 它是一条**真路由**、
    又不是豁免路径（豁免那条路在第一行就返回了，测不到"按池判"这件事）。
    这条**看着像在放行**，但它是**裁定**；代价靠
    `test_quota_pools.py::test_every_route_has_a_pool` 那道覆盖门兜。
    """
    status, headers = _drive(
        monkeypatch, "/api/v1/agent/token/budget", _budgets(llm_remaining=0, emb_remaining=0),
    )
    assert status == 200, "表外路径被拦了 —— 与「不进池」那条裁定不符"
    assert headers.get("x-quota-pool") == "total", "表外路径该报总计（它没被任何池管）"


# ═══════════════════════════════════════════════════════════════════
# 二 · 总上限 = 两池【相加】—— 那 11 处图内闸共用的就是它
# ═══════════════════════════════════════════════════════════════════

def test_user_budget_is_the_sum_of_the_pools(monkeypatch):
    """**总额那道闸的上限 = 两池之和**，不是「角色额度」那一个数。

    这钉的是 `get_user_token_budget` —— `check_token_budget_detail` 用它，
    而**全仓 11 处图内闸**（`agent_graph*.py` / `agent_checkpointer.py` / `plan_execute.py`）共用它。

    **不改它的后果**：中间件按池放行到 2×，而图内闸在 **1×** 处拦住
    ⇒ **"翻倍"根本不会发生**，且页头显示 2× 与实际可用 1× **对不上**。

    **两个池给【不同的数】**（100 / 7）—— 都给同一个数的话，
    "求两池之和"与"取角色额度再乘 2"**看不出区别**，这条就白写了。
    """
    import access.permission as permission
    from billing import quota_pools as qp
    from billing import token_tracker as tt

    monkeypatch.setattr(qp, "pool_daily_limit", lambda pool, role: 100 if pool == qp.POOL_LLM else 7)
    monkeypatch.setattr(permission, "get_user_role", lambda _u: "free")

    assert tt.get_user_token_budget("whoever") == 107, (
        "总额闸的上限不是两池之和 ⇒ 多半还在用『角色额度』那一个数 ⇒ 翻倍不会发生"
    )


def test_total_is_the_sum_of_the_two_tables(monkeypatch):
    """**走真函数**逐角色核：总额 = LLM 表 + Embedding 表。

    **2026-10-11本条改过一次** —— 它原先钉的是"两池同源 ⇒ 恰好 2×"
    （并且自己写着"将来某一池调小了，该改的是这一条"）。**那一天就是今天。**
    不是删断言：现在钉的是**加法**本身，不写倍数。
    走真函数（`get_user_token_budget`）—— 这样"拆了两张表、总额还在拿老数"那种假完成**测得出**。
    """
    import access.permission as permission
    from billing import token_tracker as tt
    from billing.token_config import EMBEDDING_DAILY_TOKEN, LLM_DAILY_TOKEN

    for role in ("free", "premium", "admin"):
        monkeypatch.setattr(permission, "get_user_role", lambda _u, r=role: r)
        want = LLM_DAILY_TOKEN[role] + EMBEDDING_DAILY_TOKEN[role]
        assert tt.get_user_token_budget("whoever") == want, (
            f"{role}：总额闸的上限 {tt.get_user_token_budget('whoever')} ≠ 两表之和 {want}"
        )


def test_reject_message_names_the_pool():
    """只说"预算已用完"会让用户以为**两份都空了** —— 而另一份可能还剩很多。"""
    from main import quota_reject_payload

    body = quota_reject_payload({
        "pool": "embedding", "daily_budget": 10_000, "used_today": 10_000, "remaining": 0,
    })
    assert body is not None
    assert "Embedding" in body["error"], f"文案没说是哪一份：「{body['error']}」"
    assert "额度" in body["error"]
