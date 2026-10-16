"""M6 · `/rag/search` 单模块测试闭环（2026-09-17）

本仓第一个「改代码 → 跑测试 → 看结果」闭环的**先例**。见 `ROADMAP.md`「⑦ M6」与 本仓的决策记录（⛔ 不随产品仓提供）。

分四层，**按代价递增** —— 分层是为了让 CI 能跑得起：

| 层 | 测什么 | 需要 | 进 CI |
|---|---|---|---|
| **L0** 纯逻辑 | `_rrf_fusion` 融合/排序/截断 · 四个 factory 的开关矩阵 | 只要 langchain | |
| **L1** 契约 | 401 · `top_k` 422 · `mode` 是 query 参数 · 404 | **redis**（中间件） | |
| **L2** 行为 | **mode→pipeline 分派** · RRF 真融合 · `top_k` 传递 · 响应形状 | redis + patch 6 个缝 | |
| **L3** 集成 | 真 pgvector + 真 BM25 + 真 DashScope embedding | postgres + 外网 | `@pytest.mark.integration` |

**为什么 L1/L2 需要 redis**：`RateLimitMiddleware` 对**每个非公开路径**都打 Redis。

> **2026-10-05 更正（批 3）**：这里原写「`rate_limiter.py` 没有 `except RedisError`
> ⇒ **Redis 不通时全站 500**（实测）」。**那句现在是假的** ——  已加兜底，
> Redis 不通改成 **fail-open**（放行 + ERROR 日志）⇒ L1/L2 在这一层**不再会 500**。
> **但"要 redis"这个前提本身没变**：中间件仍然每个非公开路径都**拨** Redis，
> 只是拨不通时不再炸。**别据此把 redis 从这套用例里撤掉** ——
> 「测得过」与「测的是真东西」是两件事：兜底一旦生效，**限流那一层其实整层没生效**，
> 这时候绿的 L1/L2 是**假绿**。
Postgres **不需要** —— L1/L2 的断言要么在中间件层、要么在 handler 之前就被挡下，
要么把库调用 patch 掉了，handler 之外的库连接一次都不发生。

**认证走自签 JWT**（`jwt_handler.create_access_token`），**不走 `/auth/login`** ——
登录要查库，会把 L1/L2 从"只要 redis"拖成"还要 postgres"，M6 的 CI 目标就没了。

**每个用例用独立的 `user_name`** —— 否则会撞上限流（3 次/秒、容量 20）
与配额（免费 100 次/天），测试互相污染。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from core.config import JWT_SECRET_KEY
from access.jwt_handler import create_access_token
from main import app

SEARCH_PATH = "/api/v1/rag/search"


@pytest.fixture
def client():
    """本文件的 TestClient —— 不复用 conftest 的，避免依赖它的登录 fixture。"""
    return TestClient(app)


def _auth_headers() -> dict:
    """自签一个 JWT，**用户名唯一** —— 见模块 docstring 末尾的限流说明。"""
    assert JWT_SECRET_KEY, "JWT_SECRET_KEY 未设置，无法自签 token"
    return {"Authorization": f"Bearer {create_access_token(f'm6-{uuid.uuid4().hex[:12]}')}"}


# ============================================================================
# L0 · 纯逻辑 —— 零 service，本层才是"离线子集"里最硬的资产
# ============================================================================

# 两份固定输入。选这个形状是有意的：
#   · 'B' **两路都命中** ⇒ 必须被标 'both' 且分数是两段之和（融合作业本身）
#   · 'A'/'C' 只在向量路 · 'D' 只在 BM25 路 ⇒ 三种 from 标签全覆盖
VECTOR_DOCS = [(1, "A", "docA", 0.90), (2, "B", "docB", 0.80), (3, "C", "docC", 0.70)]
BM25_DOCS = [(2, "B", "docB", 9.90), (4, "D", "docD", 5.00)]


@pytest.fixture
def pipeline():
    """只建一个 fast 管线。

    `RAGPipeline.__init__` **总是**构造一个 `ChatOpenAI` 对象（`rag_pipeline.py:48`）。
    构造函数**不发请求**，所以 CI 里给 `LLM_API_KEY` 一个 dummy 值即可，不产生网络流量。
    """
    from rag.rag_pipeline import create_fast_pipeline

    return create_fast_pipeline()


def test_rrf_fusion_merges_both_sources(pipeline):
    """两路都命中的文档：`from` 必须是 'both'，分数必须**是两段之和**（不是取大、也不是覆盖）。"""
    fused = pipeline._rrf_fusion(VECTOR_DOCS, BM25_DOCS, 10)
    by_content = {d["content"]: d for d in fused}

    assert by_content["B"]["from"] == "both"
    # B 在向量路 rank2、BM25 路 rank1 ⇒ 1/(60+2) + 1/(60+1)
    assert by_content["B"]["rrf_score"] == pytest.approx(round(1 / 62 + 1 / 61, 4))

    assert by_content["A"]["from"] == "vector"
    assert by_content["D"]["from"] == "bm25"


def test_rrf_fusion_ranks_descending_and_dedups(pipeline):
    """排序必须严格降序；同一条内容**只能出现一次**（融合表以 content 为键）。"""
    fused = pipeline._rrf_fusion(VECTOR_DOCS, BM25_DOCS, 10)
    scores = [d["rrf_score"] for d in fused]

    assert scores == sorted(scores, reverse=True)
    assert [d["content"] for d in fused] == ["B", "A", "D", "C"]
    # 'B' 在两路里都出现，但只能有一条 —— 这正是 RRF 融合的定义
    assert [d["content"] for d in fused].count("B") == 1


def test_rrf_fusion_truncates_to_top_k(pipeline):
    assert [d["content"] for d in pipeline._rrf_fusion(VECTOR_DOCS, BM25_DOCS, 2)] == ["B", "A"]


def test_rrf_fusion_handles_empty_inputs(pipeline):
    """两路都空 ⇒ 空列表（不抛异常）。单路空 ⇒ 仍走通，标签仍是单路那个。"""
    assert pipeline._rrf_fusion([], [], 10) == []
    assert {d["from"] for d in pipeline._rrf_fusion(VECTOR_DOCS, [], 10)} == {"vector"}
    assert {d["from"] for d in pipeline._rrf_fusion([], BM25_DOCS, 10)} == {"bm25"}


def test_rrf_fusion_requires_four_tuple(pipeline):
    """**回归守卫** —— 2026-08-17 复审的第 4 个 bug 就在这里。

    当时 `recriprocal_rank_fusion` 按 **3 元组**解包，而 `db.search_similar` 实际返回
    **4 列** `(id, content, source, similarity)` ⇒ 每次调用必抛 `ValueError`。
    本用例把"必须 4 元组"这条契约**显式钉住** —— 将来谁把上游改回 3 列，这里会红。
    """
    with pytest.raises(ValueError):
        pipeline._rrf_fusion([(1, "A")], [], 10)


@pytest.mark.parametrize(
    "factory, rewrite, expand, rerank",
    [
        ("create_fast_pipeline", False, False, False),
        ("create_accurate_pipeline", True, False, True),
        ("create_accurate_norerank_pipeline", True, False, False),
        ("create_full_pipeline", True, True, True),
    ],
)
def test_pipeline_factory_switch_matrix(factory, rewrite, expand, rerank):
    """四个 factory 的开关矩阵 —— `bm25` 四个都是 True，故单独断言。

    这一层锁的是"**开关**"，L2 才锁"`/rag/search` **挑了哪个** factory"。两者缺一不可。
    """
    import rag.rag_pipeline as rag_pipeline

    p = getattr(rag_pipeline, factory)()
    assert (p.enable_rewrite, p.enable_expand, p.enable_rerank) == (rewrite, expand, rerank)
    assert p.enable_bm25 is True


# ============================================================================
# L1 · 契约层（需 redis；不需要 postgres —— 断言都在 handler 之前命中）
# ============================================================================


def test_search_requires_auth(client):
    r = client.post(SEARCH_PATH, params={"mode": "fast"}, json={"question": "x", "top_k": 3})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_MISSING"


@pytest.mark.parametrize("bad_top_k", [0, 21])
def test_search_rejects_top_k_out_of_range(client, bad_top_k):
    """`QuestionRequest.top_k` 是 `ge=1, le=20` —— 边界两侧各测一个。"""
    r = client.post(
        SEARCH_PATH, params={"mode": "fast"}, headers=_auth_headers(),
        json={"question": "x", "top_k": bad_top_k},
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "top_k"]


def test_search_rejects_missing_question(client):
    r = client.post(SEARCH_PATH, params={"mode": "fast"}, headers=_auth_headers(), json={"top_k": 3})
    assert r.status_code == 422


def test_mode_is_query_param_not_body_field():
    """`mode` 在 OpenAPI 里必须是 **query 参数**，且**不在**请求体 schema 里。

    这条是**离线**的（只读 `app.openapi()`）。它守的是一个真发生过的坑：
    `locustfile_hybrid.py` 曾把 `mode` 放进 json body ⇒ 被 FastAPI **静默忽略**，
    压测实际跑的是默认模式。见 `docs/CODE_INVENTORY.md` §0-3 与 `test_locust_payload.py`。
    """
    post = app.openapi()["paths"][SEARCH_PATH]["post"]
    params = {p["name"] for p in post.get("parameters", [])}
    assert "mode" in params

    body_ref = post["requestBody"]["content"]["application/json"]["schema"]
    assert "mode" not in body_ref.get("properties", {})


# ============================================================================
# L2 · 行为层（需 redis + patch 掉 6 个外部缝）
# ============================================================================


@pytest.fixture
def offline_retrieval(monkeypatch):
    """把 `search_async` 的全部外部依赖换成固定返回值。

    6 个缝**全部**是 `rag_pipeline` 的模块级 `from X import Y` ⇒ patch 目标就是
    `rag_pipeline.<name>`。见 `rag_pipeline.py:8-11`。

    换掉 6 个而不是"按 mode 挑几个"，是为了让四个 mode 走**同一条** patch 集合：
    · embedding       → 不然真调 DashScope（花钱 + 依赖外网）
    · search_similar  → 不然真查 pgvector（要 postgres）
    · bm25_search     → 同上
    · rewrite_query   → `accurate*`/`full` 会真调 chat LLM（本机额度已耗尽）
    · expand_query    → `full` 会真调 chat LLM
    · rerank_async    → `accurate`/`full` 会真拉 torch + 2.3GB 模型
    """
    import rag.rag_pipeline as rag_pipeline

    # 2026-10-03：两个假函数加 `*, user_id`—— `search_async` 现在把它
    #    透传下来。这里**不校验** `user_id`：本文件测的是「mode 路由」，
    #    隔离的判据在 `app/tests/test_isolation.py`（别在这儿重复一遍）。
    async def fake_similar(_embedding, top_k=3, *, user_id):
        return list(VECTOR_DOCS)

    async def fake_bm25(_query, top_k=10, *, user_id):
        return list(BM25_DOCS)

    async def fake_rerank(_query, docs, top_k=3):
        return docs

    monkeypatch.setattr(rag_pipeline, "get_embedding", lambda text, model=None, **k: [0.0] * 1024)
    monkeypatch.setattr(rag_pipeline, "search_similar_async", fake_similar)
    monkeypatch.setattr(rag_pipeline, "bm25_search_async", fake_bm25)
    # 2026-10-05：两个桩必须吃下 `user_name` —— 真函数的签名是
    #    `(*, user_name: str)`（必填 keyword）。别把真签名改回去来迁就桩。
    # 🔴 **替身只负责"接得住任何调用"** —— ⛔ 别跟着真签名逐个改
    #    （本仓  的原话：真签名每加一个参数，这批 CI 筛不到的用例就再烂一次）。
    #    （2026-10-16）给这两个真函数加了 `thread_id=` ⇒ 这里加的是 `**kw`，⛔ 不是再补一个形参。
    monkeypatch.setattr(rag_pipeline, "rewrite_query", lambda q, h=None, *, user_name, **kw: q)
    monkeypatch.setattr(rag_pipeline, "expand_query", lambda q, num_variants=3, *, user_name, **kw: [q])
    monkeypatch.setattr(rag_pipeline, "rerank_async", fake_rerank)


@pytest.mark.parametrize(
    "mode, rewrite, expand, rerank",
    [
        ("fast", False, False, False),
        ("accurate", True, False, True),
        ("accurate_norerank", True, False, False),
        ("full", True, True, True),
    ],
)
def test_route_dispatches_mode_to_correct_pipeline(client, offline_retrieval, mode, rewrite, expand, rerank):
    """**本文件的靶心** —— `/rag/search` 的 `mode` 到底挑中了哪个管线。

    断言的是响应里的 `pipeline` 元信息（由 `RAGPipeline.search_async` 自己回填），
    所以它**同时**证明了两件事：路由挑对了 factory，且那条管线真的被跑到了。

    **"未知 mode"的用例不在这里** —— 它曾是个裸 `else`（`mode=garbage` 静默落进
    `accurate_norerank`）；M6 当时按裁决**只记录、不写进测试**（测试不该把缺陷固化成"预期行为"），
    **已于 2026-09-17 修为 422**，用例见下方的 `test_unknown_mode_is_rejected`。
    """
    r = client.post(
        SEARCH_PATH, params={"mode": mode}, headers=_auth_headers(),
        json={"question": "Python", "top_k": 3},
    )
    assert r.status_code == 200, r.text
    info = r.json()["pipeline"]
    assert (info["rewrite_enabled"], info["expand_enabled"], info["rerank_enabled"]) == (rewrite, expand, rerank)
    assert info["bm25_enabled"] is True


def test_mode_and_requested_by_are_filled_by_route(client, offline_retrieval):
    """`mode` / `requested_by` 是**路由层**加的两行（`api_v1_rag.py:494-495`），不是管线给的。"""
    r = client.post(
        SEARCH_PATH, params={"mode": "fast"}, headers=_auth_headers(),
        json={"question": "Python", "top_k": 3},
    )
    body = r.json()
    assert body["mode"] == "fast"
    assert body["requested_by"].startswith("m6-")


def test_response_shape_and_rrf_fusion_reaches_the_http_layer(client, offline_retrieval):
    """端到端形状 + **RRF 融合结果真的穿到了 HTTP 层**（`from: both` 是融合过的证据）。"""
    r = client.post(
        SEARCH_PATH, params={"mode": "fast"}, headers=_auth_headers(),
        json={"question": "Python", "top_k": 3},
    )
    body = r.json()

    assert set(body) >= {"query", "pipeline", "timing", "docs", "mode", "requested_by"}
    assert set(body["timing"]) >= {"rewrite_ms", "search_ms", "rerank_ms", "total_ms"}

    docs = body["docs"]
    assert [d["content"] for d in docs] == ["B", "A", "D"]
    assert docs[0]["from"] == "both"
    assert docs[0]["rrf_score"] == pytest.approx(round(1 / 62 + 1 / 61, 4))


def test_top_k_is_passed_through(client, offline_retrieval):
    """`top_k=1` 时响应只能回 1 条 —— 证明参数真的传下去了，没被吞。"""
    r = client.post(
        SEARCH_PATH, params={"mode": "fast"}, headers=_auth_headers(),
        json={"question": "Python", "top_k": 1},
    )
    assert len(r.json()["docs"]) == 1


def test_unknown_mode_is_rejected(client):
    """未知 `mode` 必须 **422**，不许静默兜底（2026-09-17 修）。

    修前：mode 分派是**裸 `else`** —— `mode=garbage` → **200**，静默落进
    `accurate_norerank`（多跑一次查询改写 = 多花钱、多延迟，**且不报错**）。
    修后：`mode` 声明为 `Literal`，非法值在**进入函数体之前**被 FastAPI 挡下。

    **零网络**：422 在 handler 之前产生，不需要 embedding / DB / 外网。
    """
    r = client.post(
        SEARCH_PATH, params={"mode": "garbage"}, headers=_auth_headers(),
        json={"question": "x", "top_k": 3},
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["query", "mode"]


def test_mode_defaults_to_accurate_norerank(client, offline_retrieval):
    """不传 `mode` 时走默认值 —— 与**显式**传 `accurate_norerank` 必须逐字段一致。"""
    headers = _auth_headers()
    body = {"question": "Python", "top_k": 3}
    omitted = client.post(SEARCH_PATH, headers=headers, json=body)
    explicit = client.post(SEARCH_PATH, params={"mode": "accurate_norerank"}, headers=headers, json=body)

    assert omitted.status_code == explicit.status_code == 200
    assert omitted.json()["mode"] == "accurate_norerank"
    assert omitted.json()["pipeline"] == explicit.json()["pipeline"]
    assert omitted.json()["docs"] == explicit.json()["docs"]


# ============================================================================
# L3 · 集成层 —— 真 pgvector + 真 BM25 + 真 embedding。**不进 CI**
# ============================================================================


@pytest.mark.integration
def test_search_fast_against_real_stack(client):
    """真实全链路。本机跑法（**必须带库名隔离**）：

        cd app && POSTGRES_DB=rag_test pytest test_rag_search.py -m integration -v

    `POSTGRES_DB=rag_test` **不能省** —— `rag_db` 是 `agent-eval-gate` 的评测知识库。
    本用例只读，但 `embedding_client` 会**记账**（`record_usage` 真写库）——
    这正是 2026-09-17 切开点 2 那次污染的形状。
    """
    r = client.post(
        SEARCH_PATH, params={"mode": "fast"}, headers=_auth_headers(),
        json={"question": "Python 编程语言", "top_k": 3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timing"]["search_ms"] > 0, "真实检索耗时应为正数"
    for doc in body["docs"]:
        assert doc["from"] in {"vector", "bm25", "both"}
