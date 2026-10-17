"""压测场景（`T5-8`）—— **给别人看的证据**，不是内部调优。
"""
import itertools
import os
import random
import sys

from locust import HttpUser, between, task

BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:8000")
PAID = os.getenv("BENCH_PAID", "").strip() == "1"

#: 付费档打哪个 `mode`（`app/routing/api_v1_rag.py` 的 query 参数）。
#: ⚠️ **默认 = 服务的默认档**；改它是为了做**对照**（`fast` 不改写 vs `accurate_norerank` 改写），
#:    见口径文档 §六。
PAID_MODE = os.getenv("BENCH_MODE", "accurate_norerank")

#: 🔴 **强制"每句都不一样"**（`BENCH_UNIQUE=1`）—— 用来量**改写的冷路径**。
#:
#: 为什么需要它（实测踩了两遍）：改写结果**带 Redis 缓存**
UNIQUE = os.getenv("BENCH_UNIQUE", "").strip() == "1"
_uniq = itertools.count()


def _key_pool() -> list:
    """读 key 池：`RAG_API_KEYS`（逗号分隔）优先，其次 `RAG_API_KEY`（单把）。

    ⚠️ **为什么必须是"池"**：一把 key 只对应**一个**限流桶（3 req/s）——
       要压过这个数，只能**多身份**，⛔ 不能靠"压测时把限流调高"（那测的就不是产品了）。
    """
    raw = os.getenv("RAG_API_KEYS", "").strip() or os.getenv("RAG_API_KEY", "").strip()
    return [k.strip() for k in raw.split(",") if k.strip()]


KEYS = _key_pool()

#: 轮询发号 —— 每个虚拟用户在 `on_start` 拿一个号，`号 % len(KEYS)` 决定用哪把 key。
#: ⚠️ CPython 下 `next()` 的自增是原子的（GIL），够用了，⛔ 别再上锁。
_counter = itertools.count()

#: 付费档的**问题池**。🔴 **字段名是 `question`** —— `app/routing/schemas.py::QuestionRequest` 的必填字段。
#: ⚠️ **修**：这里原先写的是 `query` ⇒ 服务端会当成"缺 `question`"回 **422**，
#:    也就是说**付费档跑出来的全是 422，从来没有真的检索过**（而那会被读成"检索很慢/很好"）。
PAID_QUESTIONS = (
    "Python 是一门什么样的编程语言？",
    "向量数据库和关系型数据库有什么区别？",
    "RAG 里的重排序是做什么用的？",
    "什么是 token 预算？为什么要按用户分池？",
    "BM25 和向量检索各自擅长什么？",
    "查询改写能解决哪一类问题？",
    "增量索引是怎么做的？",
    "为什么向量检索会返回少于 top_k 的结果？",
    "文档分块的大小会影响什么？",
    "怎么衡量一次 RAG 回答的好坏？",
    "同一个问题两次检索结果不一样，可能是什么原因？",
    "为什么要在检索前做权限过滤？",
    "限流和配额有什么区别？",
    "熔断器在什么情况下会打开？",
    "流式输出和一次性返回各有什么代价？",
    "缓存键应该怎么设计？",
    "怎么做一次可复现的压测？",
    "P99 和平均值哪个更能说明问题？",
    "为什么要把错误按错误码分类？",
    "向量维度和模型的选取有什么关系？",
)


def _limit_detail(response) -> str:
    """把「**是哪一层限制**」从响应里抠成人读的一句话（⛔ 没有就返回空串）。

    判据**不是状态码**（三层都回 429），而是**每一层各自的痕迹**：

    | 层 | 它独有的痕迹（`app/main.py::RateLimitMiddleware`） |
    |---|---|
    | **全局**令牌桶 | 响应体里有 `retry_after` · 响应头 `Retry-After: 60` |
    | **用户**令牌桶 | 响应头 `X-RateLimit-Limit` / `X-RateLimit-Remaining` |
    | **额度 / 熔断** | 响应体里有 `code=QUOTA_EXCEEDED` + **`scope`**（`session` / `global`） |

    ⚠️ 三层都**共用** `code=RATE_LIMITED` / `code=QUOTA_EXCEEDED` 这套编码，
       所以**只看 `code` 分不开**"全局"与"用户"两层 —— 必须看上面那两组头/字段。
    """
    parts = []
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        for k in ("code", "scope", "error", "retry_after"):
            v = body.get(k)
            if v is not None:
                parts.append(f"{k}={v}")

    h = response.headers
    # 判"是哪一层"：全局层带 Retry-After；用户层带 X-RateLimit-*
    if h.get("Retry-After") or (isinstance(body, dict) and body.get("retry_after")):
        parts.append("layer=global")
    elif h.get("X-RateLimit-Limit"):
        parts.append("layer=user")
    for k in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-Quota-Pool"):
        v = h.get(k)
        if v is not None:
            parts.append(f"{k.removeprefix('X-')}={v}")
    return " ".join(parts)


def _ok_or_fail(response) -> None:
    """`catch_response=True` 时用它收口：成功就 `success()`，失败**把限制种类写进信息**。"""
    if response.status_code < 400:
        response.success()
        return
    detail = _limit_detail(response)
    response.failure(f"HTTP {response.status_code} {detail}".strip())


class BenchUser(HttpUser):
    """一个虚拟用户。`wait_time` 决定**单人节奏**，并发数由 `-u` 给 —— 两件事别混。"""

    host = BASE_URL
    wait_time = between(0.5, 1.5)

    def on_start(self):
        """**钉住自己那把 key**，然后**先验一次鉴权** —— 别让"全是 401"混进结果里。

        旧基线就是这么废掉的：那三份 的数据**错误率 77 % / 96 % / 97 %**，
           它们的 QPS 与延迟**没有意义**（见 `locust/README.md` × 仓根 `archive/`）。
        """
        self.seq = next(_counter)
        self.api_key = KEYS[self.seq % len(KEYS)] if KEYS else ""
        self.auth = {"X-API-Key": self.api_key} if self.api_key else {}
        # 付费档：每个虚拟用户**自己的会话**（⛔ 别都挤 `default` —— 那会撞**会话级**额度，
        #   而那是另一层限制，混进来就分不清"是限流还是会话额度"）。
        self.thread_id = f"bench-{self.seq}"

        if not self.api_key:
            print("🔴 `RAG_API_KEYS` / `RAG_API_KEY` 都没设 —— 没有 key 就只能拿到 401。",
                  file=sys.stderr)
            raise SystemExit(2)

        with self.client.get("/api/v1/agent/tool_versions", headers=self.auth,
                             name="/auth_precheck (on_start)", catch_response=True) as r:
            if r.status_code == 401:
                print(
                    "🔴 401 —— 这把 key 不通（过期 / 被撤销 / 拼错）。压测前先解决鉴权，"
                    "⛔ 否则跑出来的是一堆 401 的延迟数据（本仓 2026-06 那三份旧基线就是这么废的）。",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            _ok_or_fail(r)

    def _question(self) -> str:
        """取一句问题。`BENCH_UNIQUE=1` 时挂一个自增序号 ⇒ **强制缓存 miss**（见模块头 `UNIQUE`）。"""
        q = random.choice(PAID_QUESTIONS)
        return f"{q}（探针 {next(_uniq)}）" if UNIQUE else q

    # ---------- 免费档（默认开）----------
    @task(5)
    def health(self):
        """⚠️ `/health` 是**公开路径**（不过用户级限流）⇒ 它是一条【不算限流的地板】。"""
        with self.client.get("/health", name="/health", catch_response=True) as r:
            _ok_or_fail(r)

    @task(3)
    def tool_versions(self):
        with self.client.get("/api/v1/agent/tool_versions", headers=self.auth,
                             name="/agent/tool_versions", catch_response=True) as r:
            _ok_or_fail(r)

    @task(3)
    def available_tools(self):
        with self.client.get("/api/v1/agent/available_tools", headers=self.auth,
                             name="/agent/available_tools", catch_response=True) as r:
            _ok_or_fail(r)

    @task(2)
    def budget_estimates(self):
        with self.client.get("/api/v1/agent/budget/estimates", headers=self.auth,
                             name="/agent/budget/estimates", catch_response=True) as r:
            _ok_or_fail(r)

    # ---------- 付费档（opt-in · 默认关）----------
    if PAID:
        @task(1)
        def rag_search(self):
            """**每次请求都花钱**（改写走 LLM + embedding + 真打库）。只在 `BENCH_PAID=1` 时注册。

            `thread_id` **每个虚拟用户一个** —— 让「会话级额度」那层**别混进来**（见 `on_start`）。
            """
            with self.client.post(
                "/api/v1/rag/search",
                params={"thread_id": self.thread_id, "mode": PAID_MODE},
                json={"question": self._question(), "top_k": 3},
                headers=self.auth,
                # 名字里带档位 —— 对照跑（`fast` vs `accurate_norerank`）时日志自己就说清了
                name=f"/rag/search (付费·{PAID_MODE})",
                catch_response=True,
            ) as r:
                _ok_or_fail(r)
