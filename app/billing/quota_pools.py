"""两个额度池（**LLM / Embedding**）—— **按【路径】归池**。
"""
from billing.token_config import (
    DEFAULT_DAILY_TOKEN_BUDGET,
    EMBEDDING_DAILY_TOKEN,
    LLM_DAILY_TOKEN,
)

#: 会花钱的池 —— 只有这两个受额度管。
POOL_LLM = "llm"
POOL_EMBEDDING = "embedding"

#: **不进池** —— 公开的 / 静态的 / 纯读的 / 探活那些。它们**照旧放行**（额度那一层不看它们）。
POOL_NONE = "none"

LIMITED_POOLS = (POOL_LLM, POOL_EMBEDDING)
ALL_POOLS = (POOL_LLM, POOL_EMBEDDING, POOL_NONE)

#: 池 → 它那张**日额度表**。用 `POOL_LLM` / `POOL_EMBEDDING` 这两个常量当键
#: （**别再写一遍 `"llm"` / `"embedding"` 字面量** —— 本仓「一个名字两个来源必然漂移」）。
_POOL_TABLES = {
    POOL_LLM: LLM_DAILY_TOKEN,
    POOL_EMBEDDING: EMBEDDING_DAILY_TOKEN,
}


# ==================== 归池表（**74 条** · 逐条核过代码） ====================
# **每一条的归类都有代码依据**（`文件:行号`），不是照路由名猜的。
#    逐条依据 ⇒ `qingyun-rag-agent-TODO待办/批6-归池表-20261011.md`（作者 逐条裁过）。
PATH_POOLS = {
    # ---------- LLM 池（16 条） ----------
    # 其中 8 条是【两边都动】（对话/检索/记忆写入 —— 会顺手打一次 embedding）。
    # 作者 裁：**这 8 条归 LLM 池**（依据：成本大头是 LLM；
    #    且真实记账按 purpose 早分好了，归池只决定"哪份额度先被判定用尽"）。
    "/api/v1/agent/advanced_chat": POOL_LLM,              # 【两边都动】 learning:534 inject_memories
    "/api/v1/agent/advanced_chat/stream": POOL_LLM,       # 【两边都动】
    "/api/v1/agent/approve": POOL_LLM,                    # 续跑 agent_graph / checkpointer_agent（两图都不打 embedding）
    "/api/v1/agent/langgraph_chat": POOL_LLM,
    "/api/v1/agent/langgraph_chat/stream": POOL_LLM,
    "/api/v1/agent/mcp_chat": POOL_LLM,                   # 【两边都动】 advanced:527 inject_memories
    "/api/v1/agent/mcp_chat/stream": POOL_LLM,            # 【两边都动】
    "/api/v1/agent/memory/add": POOL_LLM,                 # 【两边都动】 mem0 add = LLM 抽取 + embedder
    "/api/v1/agent/memory_chat": POOL_LLM,                # checkpointer_agent（不 import memory_store）
    "/api/v1/agent/memory_chat/stream": POOL_LLM,
    "/api/v1/agent/plan_execute": POOL_LLM,
    "/api/v1/agent/plan_execute/stream": POOL_LLM,
    "/api/v1/rag/rewrite_search": POOL_LLM,               # 【两边都动】 改写=LLM + hybrid=embedding
    "/api/v1/rag/search": POOL_LLM,                       # 【两边都动】 4 个 mode 都打 embedding + LLM 生成
    "/api/v1/rag/stream_search": POOL_LLM,                # 【两边都动】
    # WS 那条**也在表里**（否则"覆盖数"对不上），但 见文件末尾那条边界。
    "/api/v1/ws/agent": POOL_LLM,

    # ---------- Embedding 池（8 条） ----------
    "/api/v1/agent/memory/search": POOL_EMBEDDING,        # mem0 search → embedder
    "/api/v1/rag/benchmark-embedding": POOL_EMBEDDING,    # 一次请求打【两次】embedding
    "/api/v1/rag/hybrid_search": POOL_EMBEDDING,          # hybrid_search.py:83
    "/api/v1/rag/insert": POOL_EMBEDDING,                 # api_v1_rag.py:168
    "/api/v1/rag/insert_batch": POOL_EMBEDDING,           # :270
    #    ⇒ 它不花钱，归 `POOL_NONE`。取整篇那条同池（同一个「不花钱」的理由）。
    "/api/v1/rag/documents": POOL_NONE,
    "/api/v1/rag/pg_search": POOL_EMBEDDING,              # :476
    "/api/v1/rag/rerank_search": POOL_EMBEDDING,          # 同上 + 本地 Cross-Encoder（本地模型，不花钱）
    "/api/v1/rag/upload_document": POOL_EMBEDDING,        # :351

    # ---------- 不进池（50 条） ----------
    # (a) 静态入口页 —— 302 到 HTML，本身不含数据
    #    旧的 10 条（`/approvals` `/chat` `/cost` `/eval` `/lab` `/system` `/tools` `/trace` …）
    #    随旧骨架一起删；换成新 7 页对应的 7 条。
    "/": POOL_NONE,
    "/agent": POOL_NONE,
    "/guide": POOL_NONE,
    "/kb": POOL_NONE,
    #    🔴 **它公开是必须的**（它就是"拿凭据"那一步，要凭据才能开 = 死循环），
    #    而它自己**一个 token 都不花**（表单打的是 `/api/v1/auth/login`，那条就在下面）。
    #    📌 路由鉴权基线里同样有它。
    "/login": POOL_NONE,
    "/ops": POOL_NONE,
    "/rag": POOL_NONE,
    "/reference": POOL_NONE,
    "/stream": POOL_NONE,
    # (b) 探活 / 元数据 / 公开 9 条
    "/api/v1/": POOL_NONE,
    "/api/v1/admin/create_user": POOL_NONE,
    "/api/v1/auth/login": POOL_NONE,
    "/api/v1/auth/refresh": POOL_NONE,
    "/api/v1/info": POOL_NONE,
    "/health": POOL_NONE,
    "/metrics": POOL_NONE,
    "/openapi.json": POOL_NONE,
    "/ready": POOL_NONE,
    # (c) 纯读 / 不调模型 30 条
    "/api/v1/agent/approvals/history": POOL_NONE,
    "/api/v1/agent/available_tools": POOL_NONE,
    "/api/v1/agent/budget/check": POOL_NONE,
    "/api/v1/agent/budget/estimates": POOL_NONE,
    "/api/v1/agent/budget/intercepts": POOL_NONE,
    "/api/v1/agent/cost/monthly_report": POOL_NONE,
    "/api/v1/agent/cost/overview": POOL_NONE,
    "/api/v1/agent/cost/records": POOL_NONE,
    # 沙箱里跑任意 Python：**不调模型** ⇒ 不进池。那是**另一类风险**（CPU / 逃逸），与额度无关。
    "/api/v1/agent/execute_code": POOL_NONE,
    "/api/v1/agent/mcp_tools_dynamic": POOL_NONE,
    "/api/v1/agent/pending": POOL_NONE,
    "/api/v1/agent/pending/context": POOL_NONE,
    "/api/v1/agent/token/budget": POOL_NONE,
    "/api/v1/agent/token/history": POOL_NONE,
    "/api/v1/agent/token/purpose": POOL_NONE,
    "/api/v1/agent/token/recent": POOL_NONE,
    "/api/v1/agent/token/thread": POOL_NONE,
    "/api/v1/agent/token/usage": POOL_NONE,
    "/api/v1/agent/tool_health": POOL_NONE,
    "/api/v1/agent/tool_health/refresh": POOL_NONE,      # 跑健康检查，不调模型
    "/api/v1/agent/tool_versions": POOL_NONE,
    "/api/v1/agent/trace/{thread_id}": POOL_NONE,
    "/api/v1/agent/trace/{thread_id}/cost": POOL_NONE,
    "/api/v1/agent/traces": POOL_NONE,
    "/api/v1/debug/cache_stats": POOL_NONE,
    "/api/v1/debug/count": POOL_NONE,
    "/api/v1/debug/quota": POOL_NONE,
    "/api/v1/debug/rate_limit": POOL_NONE,
    "/api/v1/rag/chat_history": POOL_NONE,               # 读 Redis
    #    **池表按【路径】查** ⇒ 两条共用这一个键。两件事都不花钱 ⇒ POOL_NONE。
    #    教训：加新端点时**先看这个键在不在** —— 重复键在 Python 里是**后者静默覆盖前者**，
    #       而只有 ruff 的 F601 会报（就是它拦下的）。
    "/api/v1/rag/documents/{doc_id}": POOL_NONE,         # 纯取文本 / 纯删库
}


def _segments(path: str) -> tuple:
    """`/a/b/` → `("a", "b")` —— 去掉首尾斜杠与空段。"""
    return tuple(s for s in (path or "").split("/") if s)


def _matches(pattern: tuple, actual: tuple) -> bool:
    """模板段 ↔ 真实段：`{...}` 是通配，其余**必须逐字相等**。"""
    if len(pattern) != len(actual):
        return False
    return all(
        (p.startswith("{") and p.endswith("}")) or p == a
        for p, a in zip(pattern, actual)
    )


#: 预先把模板切成段 —— 别每次请求现切（`pool_for_path` 在**每个请求**上跑）。
_POOL_BY_SEGMENTS = tuple((_segments(p), pool) for p, pool in PATH_POOLS.items())


def pool_for_path(path: str) -> str:
    """这条路径归**哪个池** —— **纯函数**（可单测，不必起服务）。

    **拿不到就返回 `POOL_NONE`（放行）** —— 作者 裁「**表外路径不进池**」，
    理由：把 `/static/*`、`/docs` 这些当成 LLM 用，**一次页面加载 ≈8 个请求全进 LLM 池**
    ⇒ 几刷就顶满 —— 那正是  那个 429 的同一件事。
    **代价**：新加的路由**忘了归池 = 静默不受限** ⇒ 所以**必须配一道门**
    （`app/tests/test_quota_pools.py`：路由模板集合 ⇔ 本表键集合，**双向**比）。
    """
    segs = _segments(path)
    for pattern, pool in _POOL_BY_SEGMENTS:
        if _matches(pattern, segs):
            return pool
    return POOL_NONE


# ==================== 每池的日额度 ====================

def pool_daily_limit(pool: str, role: str) -> int:
    """**某一个池**的日额度（token）。

    **：两池【不再同源】**（作者裁）——
    各读**自己那张**表（`LLM_DAILY_TOKEN` / `EMBEDDING_DAILY_TOKEN`）。
    **这正是  当初把它们分开写的目的**：那次两池的数是**一样**的
    （都 10 000），但**结构上已经允许它们不一样**；现在是**真的不一样**了。
    拿不准角色（拼错 / 新角色没登记）⇒ 落 `DEFAULT_DAILY_TOKEN_BUDGET`（**最低档**，fail-closed）。
    """
    table = _POOL_TABLES.get(pool)
    if table is None:
        return 0        # POOL_NONE：不收钱，也就没有额度
    return table.get(role, DEFAULT_DAILY_TOKEN_BUDGET)


def total_daily_limit(role: str) -> int:
    """**两池【相加】** = 该用户一天的总上限。

    作者 明确「**用加法，不要用乘法**」—— 见模块头那段。
    """
    return sum(pool_daily_limit(p, role) for p in LIMITED_POOLS)
