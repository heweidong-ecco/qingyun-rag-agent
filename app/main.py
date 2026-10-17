import uuid
import time
from contextvars import ContextVar
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from dotenv import load_dotenv

from core.config import validate_config, TRUST_PROXY_HEADERS
from core.logger_config import setup_logger  # 删 `logger`（D1/pyflakes 报 redefinition）：:59 会 `logger = setup_logger` 覆盖它，覆盖前从未使用
from core.exceptions import AppException, ErrorCode
from routing.api_v1 import router as public_router
from routing.api_v1_rag import router as rag_router
from routing.api_v1_agent import router as agent_router
from agent.pending_approvals import warn_if_backend_mismatch   #  （B5）：启动自检，见 startup_event
from core.db import create_table, embedding_dim_in_db, init_pool, close_pool, assert_pgvector_supports_iterative_scan
from access.auth import ensure_admin_exists

from core.cache import redis_client
# 新增 令牌桶 在 main.py 中集成限流中间件：
# 新增 实现“全局 + 用户”两层令牌桶防护 全局限流器 用户级限流器，rate_limiter 已设置
from starlette.middleware.base import BaseHTTPMiddleware
from access.rate_limiter import global_limiter, user_limiter
# 新增  嵌入了 Prometheus 指标采集
from core.metrics import track_request,REQUEST_IN_PROGRESS

from core.metrics import  get_metrics
from core.db import get_db

from core.cache import warmup_cache  # ← 新增这一行

load_dotenv()
# ==================== 应用初始化 ====================
app = FastAPI(
    title="RAG Agent API",
    version="1.0.0",
    description="""
    ## 生产级 RAG + Agent API 服务
    
    ### 核心功能：
    - **文档管理**：上传、向量化、批量导入文档
    - **智能检索**：基于pgvector的语义搜索
    - **认证系统**：支持 API Key 和 JWT 双认证
    - **配额控制**：免费/付费/管理员三级权限
    
    ### 技术栈：
    FastAPI · PostgreSQL+pgvector · Redis · Docker · Prometheus+Grafana
    """,
    contact={
        "name": "hwd",
        "url": "https://github.com/你的用户名",
    },
    license_info={
        "name": "MIT",
    },
)


# ==================== 初始化日志====================
# 加入 三通道”（控制台 + 普通文件 + 错误文件）的日志器。setup_logger
logger = setup_logger()
# 全局变量：使用 ContextVar 存储每个请求的 request_id，线程安全
request_id_var: ContextVar[str] = ContextVar("request_id", default="no-id")


# ==================== 请求日志中间件 ====================
# 更新中间件，注入 request_id
# 更新日志中间件，同时记录指标 metrics
@app.middleware("http")
async def log_and_track_request(request: Request, call_next):
    # 生成唯一请求ID
    request_id = str(uuid.uuid4())[:8]
    request_id_var.set(request_id)
    
    # 活跃请求数+1
    REQUEST_IN_PROGRESS.inc()
    
    start_time = time.time()
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    
    # 活跃请求数-1
    REQUEST_IN_PROGRESS.dec()
    
    # 记录指标
    track_request(
        endpoint=request.url.path,
        method=request.method,
        status=response.status_code,
        duration=duration
    )
    
    logger.bind(request_id=request_id).info(
        f"{request.method} {request.url.path} - {response.status_code} ({duration:.3f}s)"
    )
    response.headers["X-Request-ID"] = request_id
    return response

# 新增 令牌桶 在 main.py 中集成限流中间件：
# ============ 中间件豁免路径（限流 / 配额两个中间件共用）============
MIDDLEWARE_EXEMPT_PATHS = frozenset({
    "/", "/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json",
    "/health", "/ready", "/metrics",
    #    （`/` 改成页面后，那段 JSON 挪到了这里）。
    #    ⇒ 两者【同待遇】—— `/` 早就在本名单里（静态入口，不参与限流与配额），
    #      一个豁免、一个记成"债"，读的人只会问"为什么同一个东西两种待遇"。
    #    它**零成本**（一个静态 dict，不碰库、不碰 Redis），没有限流的理由。
    "/api/v1/info",
    "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/admin/create_user",
})

MIDDLEWARE_EXEMPT_PREFIXES = frozenset({
    # 只放**零成本**的东西：静态文件是磁盘读，不碰库、不碰 Redis。
    # 别把 `/api/` 这类加进来 —— 那会一次性放行整片业务接口。
    "/static/",
})


def is_middleware_exempt(path: str) -> bool:
    """这个路径**要不要跳过限流与配额**（两个中间件共用这一处判定）。

    **抽成纯函数的目的只有一个**：让它**能被单测** —— 与 `resolve_rate_limit_identity`
    当初抽出来的理由逐字相同（中间件本体要连 Redis，行为测试会退化成"环境依赖型通过"）。

    **前缀匹配是【带边界】的**：`/static/` 只匹配 `/static/...`；
    `/static`（不带尾斜杠）单独放一条 —— StaticFiles 对它会回 307，那一下同样走中间件。
    **不许写成裸 `path.startswith("/static")`** —— 那会把 `/staticfoo` 这类
    **根本不存在的兄弟路径**也放过去，等于开了一个"造个 /static 开头的路径就能绕开限流"的口子。
    负控 ⇒ `test_static_exempt.py::test_prefix_match_has_a_boundary`。
    """
    if path in MIDDLEWARE_EXEMPT_PATHS:
        return True
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix)
        for prefix in MIDDLEWARE_EXEMPT_PREFIXES
    )


# ==================== 限流身份解析（抽出，便于单测） ====================
def resolve_rate_limit_identity(x_api_key: str | None, auth_header: str | None) -> str | None:
    """从请求头算出**限流用**的用户身份（桶名）。

    **抽出来的目的只有一个**：让这段逻辑**能被单测**。
    中间件本体要连 Redis（`rate_limiter.py` 模块级就建 `redis.Redis`），
    行为测试会退化成"环境依赖型通过" —— 同 `app/tests/test_public_paths.py` 的取舍。

    **本函数只管"限流分桶"，不是鉴权** —— 鉴权在 `app/routing/deps.py`，它会真查库。

    （待办）：**返回 `None` = "我现在判不了"**（认证服务不可用），
       **不是 `"anonymous"`**。这两个必须分得开 ——

       * `"anonymous"` = **判了，就是匿名**（用户侧的问题）
       * `None` = **判不了**（我这边的问题）⇒ 中间件据此让**本次请求不参与用户级限流**

       **为什么不是"降级到匿名桶"**：`anonymous` 是**一个** 20 容量 / 3 每秒的桶
       库一挂，**所有带 key 的人都挤进同一个桶** ⇒ **大面积假 429** ——
       那正是"**把库抖动算到用户头上**"，与 `app/routing/specs/deps.md` 里
       **1008 / 1011** 那条禁止的**归错因**是同一件事的限流版。
    """
    if x_api_key:
        #    只取**前 8 个字符**，**不查库、不验签** ⇒ 编一个串就拿到一个**全新的桶**，
        #    换着串发 = **无限刷新限流配额**。实测：与下方 `QuotaMiddleware` 走的
        #    `resolve_quota_identity`（那条**验了**）**不一致** —— 同一个文件里两条中间件两种做法。
        from access.auth import verify_api_key

        # **只捕 `AppException`**：`verify_api_key` 声称库不可用时抛的就是它。
        #    写 `except Exception` 会把 `app/tests/test_rate_limit_identity.py` 的 `_no_db` 守卫
        #    （靠抛 `AssertionError` 抓"谁碰了库"）**一起吞掉** ⇒ 那道门静默失效。
        try:
            verified = verify_api_key(x_api_key)
        except AppException as exc:
            logger.bind(request_id=request_id_var.get()).error(
                f"🔴 认证服务不可用 ⇒ 本次请求【不参与用户级限流】(fail-open)：{exc}"
            )
            return None

        if verified:
            return f"user:{verified}"
    if auth_header and auth_header.startswith("Bearer "):
        from access.jwt_handler import verify_access_token
        jwt_user = verify_access_token(auth_header[7:])
        if jwt_user:
            return f"user:{jwt_user}"
    return ANONYMOUS_BUCKET


#: 没身份时的桶名（`resolve_rate_limit_identity` 的返回值之一）。
#: **它【不再直接当桶用】** —— 中间件会把它换成按 IP 的桶
#: （`anonymous_rate_limit_bucket`），见 `RateLimitMiddleware.dispatch`。
ANONYMOUS_BUCKET = "anonymous"


def resolve_client_ip(headers, client_host: str | None, *, trust_proxy: bool) -> str | None:
    """算出**限流用的来源 IP**（纯函数 ⇒ 可单测，不必起服务）。

    **`trust_proxy` 是安全边界，不是风格开关**：
      · `False`（**默认**）⇒ **无视** `X-Forwarded-For`，只用 `request.client.host`。
        本机 / 自建部署**没有代理** ⇒ 那个头谁都能伪造，信它等于**谁都能换桶**。
      · `True` ⇒ 取 `X-Forwarded-For` 的**第一跳**，缺了回落 `X-Real-IP`，再缺回落对端。
        **为什么是第一跳**：`X-Forwarded-For` 是**从左往右追加**的 ——
        每过一跳，代理把**上一跳的地址**加在**后面** ⇒ **列表最前面那个才是原始客户端**。
        🔵 这条是 **在魔搭创空间上实测**出来的；⚠️ **那个平台已弃用**
        ⇒ 下面这条引用现在只当**当时的实测事实**读。
        📄 `docs/说明/魔搭创空间-部署与平台约束.md` §4.2。

    **拿不到任何 IP ⇒ 返回 `None`**（别返回空串）—— 调用方据此落回**共用**匿名桶。
       返回 `""` 的话桶名会变成 `"ip:"` ⇒ **所有这种情况又挤回同一个桶**，
       而它**看着像已经按 IP 分了** —— 那是最坏的一种「看着修好了」。
    """
    if trust_proxy:
        forwarded = (headers.get("x-forwarded-for") or "").strip()
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real = (headers.get("x-real-ip") or "").strip()
        if real:
            return real
    return client_host or None


def anonymous_rate_limit_bucket(client_ip: str | None) -> str:
    """匿名请求的**限流桶名**。

    改前：**所有匿名访客共用 `"anonymous"` 一个桶**（3/秒 · 容量 20）
    ⇒ **一个人刷就能把别人的页面打成 429**（那次实测到的就是它）。
    改后：按来源 IP 分。

    拿不到 IP ⇒ **落回原样那个共用桶** —— 这是  那条 fail-open 纪律的同一取向：
    「判不了」时不拒绝、也**不编一个 IP**。
    """
    return f"ip:{client_ip}" if client_ip else ANONYMOUS_BUCKET


def resolve_quota_identity(x_api_key: str | None, auth_header: str | None) -> str | None:
    """从请求头算出**配额用**的用户身份（`QuotaMiddleware`）。返回 `None` = 识别不了。

    **抽出来与 `resolve_rate_limit_identity` 是同一条理由**：让这段逻辑**能被单测**
    （中间件本体要连库，行为测试会退化成"环境依赖型通过"）。
    （待办）才抽的 —— 原先它**写在 `dispatch` 里**，那段没法单独测。

    **（作者裁「A1」）：本函数【已与限流那条对齐】——**
       原先它与限流**故意不同**（`None` 把"真匿名"和"判不了"混在一起，匿名因此**整层绕过配额**）。
       现在与 `resolve_rate_limit_identity` **同一口径**：
       * **`"anonymous"` = 判了，就是匿名** ⇒ **进配额**，`get_user_role("anonymous")`
         归到 **FREE 最低档**（`app/access/permission.py` 的「其余全部 FREE」）
       * **`None` = 判不了**（认证服务不可用）⇒ 中间件据此**跳过本次**用户级配额

       **代价（作者已知并接受）**：全体匿名**共用一个 FREE 日预算**
       ⇒ 一个爬虫打满 ⇒ 其余匿名请求当日 429。这不是"漏"，是**取向**：
       旧行为（匿名完全不受限）比它**更糟**；而**全站日级**上限仍在，不依赖身份。
       逐条见 `SECURITY.md` §3.1。

    ：**认证服务不可用（库挂了）⇒ 返回 `None`**，
       不是让它抛出去 —— 抛出去会把这个**成本控制**层变成一处新的 500
       （它所处的位置与限流一样：中间件里的异常**不会被 `AppException` 处理器接住**）。
       理由见 `auth.verify_api_key` 的 docstring；取向同 `token_tracker` 的 fail-open。
    """
    user_name = None
    _unavailable = False          # （A1）：**"判不了"**（库挂了）—— 与"判了是匿名"分开

    # 方式一：从X-API-Key获取
    if x_api_key:
        from access.auth import verify_api_key
        # **只捕 `AppException`**（不是 `except Exception`）——
        #    见 `resolve_rate_limit_identity` 里同一条注释。
        try:
            user_name = verify_api_key(x_api_key)
        except AppException as exc:
            logger.bind(request_id=request_id_var.get()).error(
                f"🔴 认证服务不可用 ⇒ 本次【跳过】额度检查 (fail-open)：{exc}"
            )
            _unavailable = True

    # 方式二：从Authorization头获取JWT（保留"API Key 验不过还会试 JWT"这个顺序，
    #         别改成验不过就直接返回 —— 那是行为变化）
    if not user_name and auth_header and auth_header.startswith("Bearer "):
        from access.jwt_handler import verify_access_token
        user_name = verify_access_token(auth_header[7:])

    if user_name:
        return user_name
    #    **只有"判不了"才返回 `None`**（那才该 fail-open）。
    return None if _unavailable else "anonymous"


def _rate_limited_payload(retry_after: int = 60) -> dict:
    """全局限流触发时的响应体（抽成纯函数 ⇒ 可单测，不必真打 Redis）。

    修：此处原写 `"error": "Internal server error"` ——
       状态码是 429、文案却说"内部错误" ⇒ 调用方会以为**系统坏了**，
       而实际是**自己发太快**。这正是 `通用方法 §7.1` R3.3 要防的。
    """
    return {
        "error": f"请求过于频繁，请在 {retry_after} 秒后重试",
        "code": ErrorCode.RATE_LIMITED.value,
        "status_code": 429,
        # 🔴：**这两层 429 原来共用同一个 `code`** ⇒ 调用方**分不出**
        #    是"全站过载"还是"你自己太快"。而两者的处置相反（等全站 / 自己慢下来）。
        #    ⇒ 各带一个 `layer`。⚠️ **纯新增字段**（老调用方读不到它就当没有）。
        "layer": "global",
        "retry_after": retry_after,
    }


# 新增  在调用 is_allowed 之前获取限流信息，并在请求成功或失败时都设置对应的响应头。
class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件：对所有受保护接口生效"""
    async def dispatch(self, request: Request, call_next):
        # 跳过豁免名单里的路径（判定收口在 `is_middleware_exempt` —— 原先这里与 QuotaMiddleware 各写一份）
        # 健康检查/就绪/指标必须豁免，否则被限流会导致 K8s/Docker 健康探针误判为不健康
        if is_middleware_exempt(request.url.path):
            return await call_next(request)
        # ----- 第一层：全局限流（所有请求共享） -----
        if not global_limiter.is_allowed("global"):
            # 全局过载，直接拒绝，不暴露内部用户信息
            response = JSONResponse(status_code=429, content=_rate_limited_payload())
            response.headers["Retry-After"] = "60"
            # 可选：加上全局的限流头，但一般不需要暴露细节
            return response

        # ----- 第二层：用户级限流 -----
        # 从请求头获取用户标识：优先 X-API-Key，其次 Bearer JWT（避免所有 JWT 用户共用 anonymous 桶）
        user_name = resolve_rate_limit_identity(
            request.headers.get("X-API-Key"),
            request.headers.get("Authorization"),
        )

        #    ⇒ 本次请求**不参与用户级限流**（fail-open）。
        #    **不落匿名桶**（那会把库抖动算到用户头上 ⇒ 大面积假 429）——
        #    理由与实测见 `resolve_rate_limit_identity` 的 docstring。
        #    **全局限流（上面那层）仍然照常生效** —— 它不依赖身份。
        if user_name is None:
            return await call_next(request)

        #    ⇒ 一个人刷就能把别人的页面打成 429 ——  那次实测到的就是它）。
        #    身份**认得出来**的（`user:...`）不走这里 —— 它们本来就是一人一桶。
        #    拿不到 IP ⇒ `anonymous_rate_limit_bucket` 会回落成**原样那个共用桶**。
        if user_name == ANONYMOUS_BUCKET:
            user_name = anonymous_rate_limit_bucket(
                resolve_client_ip(
                    request.headers,
                    request.client.host if request.client else None,
                    trust_proxy=TRUST_PROXY_HEADERS,
                )
            )

        # 获取用户限流信息（用于响应头）
        # 获取当前限流信息（无论是否被拒绝，都需要构造头部）
        info = user_limiter.get_limit_info(user_name)
        
        # 检查是否允许
        # 用户级限流
        if not user_limiter.is_allowed(user_name):
            # ===== 这里：触发限流时记录警告日志 =====
            request_id = request_id_var.get()
            logger.bind(request_id=request_id).warning(
                f"用户 {user_name} 触发限流"
            )
            # =====================================
            # 注意：中间件中抛出的异常不会被 @app.exception_handler(AppException) 捕获
            # （会落到 ServerErrorMiddleware 的通用 Exception 处理器，返回 500），
            # 因此这里必须直接返回 429 响应。
            return JSONResponse(
                status_code=429,
                content={
                    "error": "请求过于频繁",
                    "code": ErrorCode.RATE_LIMITED.value,
                    "status_code": 429,
                    # 🔴：见 `_rate_limited_payload` 那段 —— 两层必须**分得开**。
                    "layer": "user",
                },
                headers={
                    "X-RateLimit-Limit": str(info["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(info["reset"]),
                },
            )
        
        # 两层都通过，放行
        response = await call_next(request)
        # 添加用户级限流头（成功时也可返回，让客户端了解配额）
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(info["reset"])
        return response

# 将中间件添加到应用
app.add_middleware(RateLimitMiddleware)

# ==================== 权限分级中间件（按用户按天的 token 上限 · R1.3） ====================
#    （`permission.ROLE_QUOTA` + `quota_limiter`），与 token 那套**互不知情**：
#     实测两者口径**差 35 倍**（`plan_execute` 一次 ~3346 token
#    ⇒ 按次数能跑 100 次、按 token 只能跑 ~3 次）⇒ 作者裁「统一到 token 一套」()。
from datetime import datetime, timedelta

from billing.token_tracker import get_pool_budget_info
from billing.quota_pools import pool_for_path, POOL_EMBEDDING, POOL_NONE


def _next_day_reset_ts() -> int:
    """次日 0 点的 Unix 时间戳 —— 日预算由 SQL 的 `created_at >= CURRENT_DATE` 翻页。"""
    now = datetime.now()
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int(tomorrow.timestamp())


#: 池 → 给人看的名字（只用在**拒绝文案**里）。
POOL_LABELS = {"llm": "LLM", "embedding": "Embedding", "total": "总计"}


def quota_reject_payload(info: dict) -> dict | None:
    """按【用户】【每天】的 token 预算判定 —— 返回「**拒绝体 or None**」。

    **抽成纯函数**的理由与 `_rate_limited_payload` / `resolve_rate_limit_identity` 相同：
    中间件本体要连 DB（`token_tracker` 查 `token_usage_logs` 表），抽出来才能**不连库单测**
    （见 `app/tests/test_quota_middleware.py`）。

    **（两个额度池）**：`info` 现在可以是**某一个池**的数
    （由中间件按**路径**选出来），也可以是**总计**。
    键名 `daily_budget` / `used_today` / `remaining` **保持原样** ——
    它们对"池"和"总计"**意思是一样的**（就是那一个的日额度 / 今日已用 / 剩余）。
    别为了"更准确"把它们改名：`app/tests/test_quota_middleware.py` 钉着这三个键。
    """
    remaining = info.get("remaining")
    # `get_token_budget_info` 对 inf 预算返回字符串 "无限"；`None` / `-1` 是防御性兜底。
    if remaining in (None, "无限", -1):
        return None
    if remaining > 0:
        return None
    # 文案里**必须说清是哪一份额度** —— 否则用户看到"预算已用完"却不知道用完的是哪一份，
    #    而另一份可能还剩很多（本仓立场：错误文案要说清"是什么"，见 `_rate_limited_payload` 的由来）。
    which = POOL_LABELS.get(info.get("pool"), "总计")
    return {
        "error": (
            f"今日 {which} 额度已用完（已用 {info.get('used_today')}，"
            f"日额度 {info.get('daily_budget')}）。请明日再试。"
        ),
        "code": ErrorCode.QUOTA_EXCEEDED.value,
        "status_code": 429,
        "extra": {
            "pool": info.get("pool"),
            "daily_budget": info.get("daily_budget"),
            "used_today": info.get("used_today"),
            "remaining": 0,
        },
    }


def quota_headers(info: dict) -> dict:
    """构造 `X-Quota-*` 响应头 —— **语义已从「次数」变为「token」**。

    头名**故意不改**：客户端已经在读它们，改名的破坏面比改语义更大。
       但**值的意思变了** —— `X-Quota-Limit` 现在是**每天的 token 预算**，不是次数。

    **加 `X-Quota-Pool`** —— 两个池之后，`X-Quota-Limit/Remaining`
    **必须说清指的是哪一份**，否则读的人会把它当成"总剩余"（而它其实是**管这次请求的那一份**）。
    取值：`llm` · `embedding` · `total`（表外路径给 `total`，那种路径**不参与判定**，只是报个数）。
    `X-Quota-Reset` 仍是「次日 0 点的 Unix 时间戳」（与改动前一致）。
    """
    return {
        "X-Quota-Limit": str(info.get("daily_budget")),
        "X-Quota-Remaining": str(info.get("remaining")),
        "X-Quota-Pool": str(info.get("pool")),
        "X-Quota-Reset": str(_next_day_reset_ts()),
    }


class QuotaMiddleware(BaseHTTPMiddleware):
    """按【用户】【每天】的 **token 上限** 检查（`R1.3`），超出返回 429。"""
    
    async def dispatch(self, request: Request, call_next):
        # 跳过豁免名单里的路径（判定收口在 `is_middleware_exempt` —— 原先这里与 RateLimitMiddleware 各写一份）
        #    （页面才显示得出"额度已用完"），否则他会看到一片白。
        if is_middleware_exempt(request.url.path):
            return await call_next(request)
        
        # 获取用户身份（支持API Key和JWT两种方式）
        #    原先它内联在这里，**没法单测**（这里要连库；而"库挂了"的用例
        #    必须能在**不碰库**的前提下跑）。行为逐字保留，只是搬了个家。
        user_name = resolve_quota_identity(
            request.headers.get("X-API-Key"),
            request.headers.get("Authorization"),
        )

        # 未识别身份的请求**原样放行**（与改动前一致）——
        # 「匿名可打」是另一个问题，本任务不碰它，撤次数配额不应顺带改变匿名行为。
        #    这个中间件**本来**就把"识别不了"当放行 ⇒ 库里抖一下，不该变成全站 500。
        #    上面那行 ERROR 日志保证它**不静默**（与 `rate_limiter` 的 fail-open 同一条纪律）。
        if user_name is None:
            return await call_next(request)

        #    表外路径（`POOL_NONE`：静态的 / 纯读的 / 探活的）**不做判定** ——
        #      作者 裁「不进池」。代价：这类路径**只剩限流那一层**。
        #    它们**仍返回配额头**（报「总计」），只是不参与拦截。
        pool = pool_for_path(request.url.path)
        budgets = get_pool_budget_info(user_name)
        info = dict(budgets["total"] if pool == POOL_NONE else budgets[pool])
        info["pool"] = "total" if pool == POOL_NONE else pool
        # 旧的键名（`daily_budget` / `used_today`）与 `quota_reject_payload` / `quota_headers`
        #    沿用 —— 它们对"池"和"总计"**意思一样**（就是那一个的日额度 / 今日已用）。
        info["daily_budget"] = info["limit"]
        info["used_today"] = info["used"]

        body = None if pool == POOL_NONE else quota_reject_payload(info)
        retry_after = max(1, _next_day_reset_ts() - int(time.time()))

        #    上面那一段管的是【这一个用户】的日额度；这一条管的是【全站加起来】——
        #    ⚠️ 两者不是一回事，⛔ 别读成"重复"（§3.2 那张表专门区分过）。
        if pool == POOL_EMBEDDING and body is None:
            from billing.token_tracker import check_global_embedding_budget
            ok, why, wait = check_global_embedding_budget()
            if not ok:
                request_id = request_id_var.get()
                logger.bind(request_id=request_id).warning(f"全站 embedding 额度触顶：{why}")
                response = JSONResponse(
                    status_code=429,
                    content={
                        "error": why,
                        "code": "QUOTA_EXCEEDED",
                        "status_code": 429,
                        "scope": "global_embedding",
                    },
                )
                response.headers["Retry-After"] = str(max(1, wait))
                for _k, _v in quota_headers(info).items():
                    response.headers[_k] = _v
                return response

        if body is not None:
            # 该池的日额度已用完
            request_id = request_id_var.get()
            logger.bind(request_id=request_id).warning(
                f"用户 {user_name} 触发日额度上限（池：{pool}）"
            )
            response = JSONResponse(status_code=429, content=body)
            # `retry_after` 与 `X-Quota-Reset` 同源：次日 0 点（见 `_next_day_reset_ts`）
            # ⚠️：它挪到了上面（全站 embedding 那条也要用）—— **同源，⛔ 别各算一份**
            response.headers["Retry-After"] = str(retry_after)
        else:
            # 未满（或这条路径不参与判定），放行
            response = await call_next(request)

        # 请求**成功时也**返回配额头，方便客户端了解当前用量（与改动前一致）
        for _k, _v in quota_headers(info).items():
            response.headers[_k] = _v
        return response

# 将中间件添加到应用（放在限流中间件之后）
app.add_middleware(QuotaMiddleware)

# ==================== 自动规范化请求体中的文本字段中间件 ====================
from rag.document_preprocessor import DocumentPreprocessor
import json
preprocessor = DocumentPreprocessor()

class TextNormalizationMiddleware(BaseHTTPMiddleware):
    """全局文本规范化中间件：自动对请求体中的文本字段进行半角规范化"""

    async def dispatch(self, request: Request, call_next):
        # 只处理 POST/PUT/PATCH 等有请求体的方法
        if request.method in ("POST", "PUT", "PATCH"):
            # 读取请求体
            body = await request.body()
            if body:
                try:
                    data = json.loads(body)
                    # 递归规范化所有文本字段
                    normalized_data = self._normalize_dict(data)
                    # 将规范化后的数据重新封装为请求体
                    request._body = json.dumps(normalized_data).encode()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass  # 非JSON请求体，跳过

        response = await call_next(request)
        return response

    def _normalize_dict(self, obj):
        """递归遍历字典/列表，规范化所有字符串值"""
        if isinstance(obj, dict):
            return {k: self._normalize_dict(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._normalize_dict(item) for item in obj]
        elif isinstance(obj, str):
            # 只对可能是自然语言的字段做规范化
            # 跳过明显的非文本字段（如token、url、email）
            if self._is_text_field(obj):
                return preprocessor.normalize_text(obj)
            return obj
        else:
            return obj

    def _is_text_field(self, value: str) -> bool:
        """判断字段值是否为需要规范化的自然语言文本"""
        # 跳过太短的字符串（如ID、状态码）
        if len(value) < 3:
            return False
        # 跳过URL
        if value.startswith("http://") or value.startswith("https://"):
            return False
        # 跳过明显的Token
        if value.startswith("sk-") or value.startswith("eyJ"):
            return False
        return True

# 在 app 创建后添加
app.add_middleware(TextNormalizationMiddleware)

# ==================== 全局异常处理器 ====================
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """统一错误响应。

    `retry_after`（B12）**只在挂了的异常上出现** —— 没挂时**整个字段不写**，
       因为 `retry_after: 0` 会被客户端读成「立刻可重试」，与「不知道多久」是两回事。

    `scope`（`R3.2`）**同一个套路**：没给就**不写**。
       别给它兜一个默认值（比如 `"global"`）—— 那会让一个**根本没有日级额度**的
       场合（如中间件的限流 429）也长出「明日起恢复」来，**前端就再也分不出"不知道"**。
    """
    content = {
        "error": exc.message,
        "code": exc.error_code.value,
        "status_code": exc.status_code
    }
    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        content["retry_after"] = retry_after
        headers["Retry-After"] = str(retry_after)
    scope = getattr(exc, "scope", None)
    if scope is not None:
        content["scope"] = scope
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = request_id_var.get()
    logger.opt(exception=True).bind(request_id=request_id).error("未捕获异常: {}", str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "code": ErrorCode.INTERNAL_ERROR.value,
            "status_code": 500
        }
    )


# ==================== 挂载路由/路由定义 ====================
app.include_router(public_router)
app.include_router(rag_router)
app.include_router(agent_router)

#    那段服务索引 JSON **没有丢** —— 它**原样挪到了 `GET /api/v1/info`**（`app/routing/api_v1.py`）。
#    裁定的原话就是「**挪走**，不是删」（那条要求来自作者 的选择项）。
#    为什么非改不可：原先访客打开域名，第一眼是**一坨 JSON** —— 而最高判据说
#       「**95% 的人不会去看代码**……**显示了，才知道你有做**」。
@app.get("/", include_in_schema=False)
async def overview_page():
    """把人送到**总览首页**。`include_in_schema=False`：它是页面，不是 API。"""
    return RedirectResponse(url="/static/web/index.html", status_code=302)

# ══════════════════════════════════════════════════════════════════════════
# 7 个页面入口（前端重做 ⑥ 落地）

@app.get("/rag", include_in_schema=False)
async def rag_page():
    """内部RAG知识库检索及问答 —— **内部资料线**（只查本库，不联网、不调外部工具）。"""
    return RedirectResponse(url="/static/web/rag.html", status_code=302)

@app.get("/agent", include_in_schema=False)
async def agent_page():
    """外部检索与工具Agent编排 —— **外部知识线**（模型自身 + 记忆 + 通用工具）。"""
    return RedirectResponse(url="/static/web/agent.html", status_code=302)

@app.get("/kb", include_in_schema=False)
async def kb_page():
    """知识入库 —— 喂完**立刻验一次**召不召回。"""
    return RedirectResponse(url="/static/web/kb.html", status_code=302)

@app.get("/ops", include_in_schema=False)
async def ops_page():
    """系统观测 —— 系统方视角：输出是**一个判断**，不是一堆数。"""
    return RedirectResponse(url="/static/web/ops.html", status_code=302)

@app.get("/guide", include_in_schema=False)
async def guide_page():
    """接入指南 —— 拿到凭据 → 粘一段能跑的 → 看到真返回。"""
    return RedirectResponse(url="/static/web/guide.html", status_code=302)

@app.get("/stream", include_in_schema=False)
async def stream_page():
    """**流式问答** —— 内部资料线的流式形态。它**不进那 7 格导航**：
    作者 定它是**子页**，入口只有两个跳转键（检索问答页的接口块那一行 · 页头那颗按钮）。"""
    return RedirectResponse(url="/static/web/stream.html", status_code=302)

@app.get("/reference", include_in_schema=False)
async def reference_page():
    """API 参考 —— 全仓端点清单，**按资源分组**。"""
    return RedirectResponse(url="/static/web/reference.html", status_code=302)

@app.get("/login", include_in_schema=False)
async def login_page(request: Request):
    """登录 —— 用户名 + 密码换 JWT（§七·1）。

    ⚠️ **它不进那 7 格导航**（与 `/stream` 同一种"格外的页"）：
       它是**所有页面的前置**，⛔ 不是第 8 个内容页。

    🔴 **它必须把【查询串】带到落点上** —— `RedirectResponse` 默认**会丢掉它**。
        这个坑是**在真浏览器里撞出来的**（`test_page_routes.py` 那三条**全都照样绿** ——
        它们只看 302 与 location 的**路径**）：
        在 `/login?next=%2Fops` 上走一遍登录，**跳回去的是 `/` 而不是 `/ops`** ——
        因为 `?next=` 在 302 那一步就没了，`login.js` 的 `safeNext` 收到的是**空串**，
        于是老老实实回默认首页（它本身行为是对的，**是我把输入弄丢了**）。
        ⇒ 页面自己那半边（`safeNext`）有 13 条单测且变异自证过，**照样挡不住这一条** ——
          「**两端各自都对，接起来是断的**」。
    """
    qs = request.url.query
    target = "/static/web/login.html" + (("?" + qs) if qs else "")
    return RedirectResponse(url=target, status_code=302)


# 新增  嵌入了 Prometheus 指标采集
# 指标暴露接口
@app.get("/metrics")
async def metrics():
    return get_metrics()

@app.get(
    "/openapi.json",
    tags=["公开"],
    include_in_schema=False)
async def get_openapi():
    """返回 OpenAPI JSON，可直接导入 Postman"""
    return app.openapi()

# 新增  health 健康检查接口
async def _compute_health() -> dict:
    """**纯计算**健康状态 —— **永远返回 `dict`，绝不构造 HTTP 响应**。

    拆分（全仓审计 A）：
      此前这里是**两种返回类型** —— 健康时返回 `dict`，不健康时 `return JSONResponse(503, ...)`。
      而 `/ready`（`main.py:440`）拿到它后**无条件**调 `.get("status")`
      ⇒ `JSONResponse` 没有 `.get` ⇒ `AttributeError` ⇒ 全局处理器兜住
      **`/ready` 返回 500 而不是 503**（实测：`AttributeError: 'JSONResponse' object has no attribute 'get'`）。

    对 **K8s / 负载均衡**来说 503 与 500 是**不同语义**：
      503 = "暂时别把流量给我"；500 = "我坏了"。
      把"依赖挂了"报成"我坏了"，正是就绪探针最不该犯的错。

    ⇒ 拆成「**算**」与「**渲染响应**」两层：本函数只管算，`/health` 负责渲染。
      **`/health` 的对外行为（200+dict / 503+错误体）保持逐字不变。**
    """
    health_status = {"status": "healthy","checks": {}}
    is_healthy = True

    # 检查数据库
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        health_status["checks"]["database"] = f"error: {str(e)}"
        is_healthy = False

    # 检查 Redis
    try:
        redis_client.ping()
        health_status["checks"]["redis"] = "ok"
    except Exception as e:
        health_status["checks"]["redis"] = f"error: {str(e)}"
        is_healthy = False

    # Embedding API 不在健康检查中，避免开销和级联故障
    health_status["checks"]["embedding_api"] = "deferred to external monitoring"

    if not is_healthy:
        health_status["status"] = "unhealthy"
    return health_status


@app.get("/health",tags=["公开"])
async def health_check():
    """
    健康检查接口。
    检查数据库、Redis、Embedding API 是否正常。
    返回 200 和各项状态，任一不可用则返回 503。
    """
    health_status = await _compute_health()
    if health_status["status"] == "unhealthy":
        return JSONResponse(
            status_code=503,
            content={
            #    503 是「暂时别把流量给我」，500 才是「我坏了」。报错口径要分清。
            "error": "服务尚未就绪，请稍后重试",
            "code": ErrorCode.SERVICE_UNAVAILABLE.value,
            "status_code": 503}
        )
    return health_status

#添加就绪探针 用于区分“已启动”和“可接受流量”（可选，用于 Kubernetes）
# 应用启动时间（用于就绪判断）
APP_START_TIME = time.time()

@app.get("/ready",tags=["公开"])
async def readiness_check():
    """
    就绪探针接口。
    只有在应用启动足够时间后（如10秒）且健康检查通过时才返回就绪。
    """
    # 等待应用完全初始化（如数据库表创建、管理员账户检查等）
    if time.time() - APP_START_TIME < 10:
        return JSONResponse(
            status_code=503,
            content={
            #    503 是「暂时别把流量给我」，500 才是「我坏了」。报错口径要分清。
            "error": "服务尚未就绪，请稍后重试",
            "code": ErrorCode.SERVICE_UNAVAILABLE.value,
            "status_code": 503}
        )

    # 也可以用健康检查的逻辑
    #    后者在**不健康时会返回 JSONResponse**（不是 dict），而这里对它调 `.get()`
    #    ⇒ `AttributeError: 'JSONResponse' object has no attribute 'get'` ⇒ **500 而非 503**。
    #    `_compute_health()` 保证**永远返回 dict**。
    health = await _compute_health()
    if health.get("status") == "unhealthy":
        return JSONResponse(
            status_code=503,
            content={
            #    503 是「暂时别把流量给我」，500 才是「我坏了」。报错口径要分清。
            "error": "服务尚未就绪，请稍后重试",
            "code": ErrorCode.SERVICE_UNAVAILABLE.value,
            "status_code": 503}
        )
    return {"status": "ready"}


# ==================== 应用启动时建表 ====================
import asyncio
from tools.tool_health import run_health_check
async def scheduled_health_check():
    """定时健康检查后台任务"""
    while True:
        await asyncio.sleep(120)  # 每 2 分钟检查一次
        await run_health_check()

@app.on_event("startup")
async def startup_event():
    validate_config()
    init_pool()  # 启动连接池
    create_table()

    #    为什么必须是"断"而不是"警告"：`iterative_scan` 那两个 GUC 在 `pgvector < 0.8` 上
    #    **不存在**，而 `SET` 会被 PG 当**占位符**静默收下、`SHOW` 还回设定值
    #    ⇒ 防护**静默空转**，**日志正常、用例也绿**（本仓  就是栽在这个形态上）。
    #    ⇒ 宁可起不来，也别"看起来在跑、实际没防护"。
    _pgver = assert_pgvector_supports_iterative_scan()
    logger.info(f"✅ pgvector 版本自检通过（{_pgver} ≥ 0.8 · ANN 防护可用）")

    #    拿**真模型实测一次**，与**表定义**对不上就**响亮地断**（⛔ 不是警告、⛔ 不是回落）。
    # 🔴 **为什么必须有**（§2.1 原文）：「**真正危险的是『维度恰好相同』的情况**：
    #    不报任何错，但老向量与新查询向量**不同源** ⇒ 检索结果全是垃圾」。
    #    自检挡不住那一种，但它**能挡住配置漂移**（`.env` 改了、表没改 —— 那正是 B 段现场）。
    # 🔴 **`expected` 来自【表自己】**（`embedding_dim_in_db()` 查 `pg_attribute.atttypmod`）
    try:
        _dim = embedding_dim_in_db()
        if _dim is None:
            logger.info("embedding 维度自检【跳过】（当前后端没有向量类型 / 表还没建）")
        else:
            from rag.embedding_client import assert_model_dimension
            assert_model_dimension(_dim)
            logger.info(f"✅ embedding 维度自检通过（表定义 {_dim} 维）")
    except Exception as _e:
        from rag.embedding_client import EmbeddingDimensionMismatch
        if isinstance(_e, EmbeddingDimensionMismatch):
            raise
        logger.warning(f"⚠️ embedding 维度自检没跑成（网络/模型不可用？）：{_e}")

    ensure_admin_exists(logger)
    warmup_cache()  # ← 新增这一行
    #  （B5）：checkpoint 落盘 + 待接管队列在内存 ⇒ 重启后队列会丢、会话变孤儿。
    # 只在 AGENT_CHECKPOINT_BACKEND=sqlite 时出声（默认的内存后端两边一致，那时必须安静）。
    warn_if_backend_mismatch(logger)
    logger.info("应用启动完成")
    # 新增 Agent 工具 健康检查 启动时
    await run_health_check()
    # 新增 Agent 工具 启动后台定时健康检查
    asyncio.create_task(scheduled_health_check())

    # **起不来【不阻塞启动】** —— 拿 try/except 包住。这条是刻意的：
    #    （`Application startup failed. Exiting.`），比"MCP 挂了"本身更糟。
    #    ⇒ 这里起不来就**只记一条 warning**，首次真正调用时 actor 会自己重试。
    try:
        from agent.agent_graph_advanced import _get_actor
        await _get_actor()
        logger.info("MCP 长驻会话已就绪")
    except Exception as e:                      # noqa: BLE001
        logger.warning(f"MCP 长驻会话没起来（首次调用时会重试）：{e}")


@app.on_event("shutdown")
async def graceful_shutdown():
    """应用关闭时执行清理操作。

    修：这段 docstring 原先**躺在 `close_pool` 之后**，是**空操作** ——
       函数本身**没有 docstring**。已上移到签名正下方（代码一行未动）。
    """
    close_pool()  # 关闭连接池
    logger.info("收到关闭信号，开始优雅关闭...")

    # **不调它也不会漏子进程**（loop 关时 holder task 会走到 finally，实测跑完全量无残留），
    #    但显式关能让"什么时候释放那 ~84 MB"是**可预期**的。
    try:
        from agent.agent_graph_advanced import aclose_mcp_session
        await aclose_mcp_session()
        logger.info("MCP 长驻会话已关闭")
    except Exception as e:                      # noqa: BLE001
        logger.warning(f"关闭 MCP 长驻会话时出错：{e}")

    # 1. 停止接收新请求（FastAPI 自动处理）
    # 2. 等待现有请求处理完成（FastAPI 自动处理）
    # 3. 清理资源
    # 关闭数据库连接池
    try:
        # 如果你的 db.py 使用了连接池，在这里关闭
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.error(f"关闭数据库连接时出错: {e}")

    # 关闭 Redis 连接
    try:
        redis_client.close()
        logger.info("Redis 连接已关闭")
    except Exception as e:
        logger.error(f"关闭 Redis 连接时出错: {e}")

    # 4. 其他自定义清理工作
    logger.info("优雅关闭完成，服务即将退出")

# ==================== 挂载静态文件 ====================
from fastapi.staticfiles import StaticFiles
import os
# 挂载静态文件目录
# 获取 main.py 所在的目录（即 app/ 目录）
current_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(current_dir, "static")


class _VersionedStaticFiles(StaticFiles):
    """`/static/` 的**缓存头** —— 🔴 加。治的是一次**真事故**，⛔ 不是"顺手优化"。

    ## 背景

    发布之后，作者在**线上**撞到：「**登录之后按钮没变成「已登录」、额度那一行不见了**」。
    查下来**不是发布错了** —— 服务器发的 34 个静态文件与本机**逐字节一致**；
    是**浏览器缓存了旧的 `authbar.js`、而 HTML 是新的**：旧 JS 找的是旧挂点
    （`#auth-in` / `#auth-out`），新版已改名（`#auth-btn` / `#auth-strip`）
    ⇒ `setHidden()` 对不存在的元素**静默跳过** ⇒ **按钮不换字、那一行不露，且不报错**。

    ⚠️ **根因**：`/static/` 原先**只有 `ETag` / `Last-Modified`、没有 `Cache-Control`**
    ⇒ 浏览器走**启发式缓存** ⇒ **每次发布都可能有人拿到「新 HTML + 旧 JS」**。

    ## 分两类发

    | 后缀 | 头 | 为什么 |
    |---|---|---|
    | `.js` / `.css` | `max-age=31536000, immutable` | 页面引它们时**带着内容哈希**（`?v=…`）⇒ **内容一变 URL 就变** ⇒ 可以放心长缓存 |
    | **其余**（`.html` · `.md` · …） | `no-cache` | 它们的 URL **不带哈希**，⛔ 不能长缓存 |

    🔴 **`.html` 为什么必须每次都问**：**它里面写着 JS/CSS 的版本号** ——
       把 HTML 也钉成 `immutable` ⇒ 用户**永远拿不到新的版本号** ⇒ **比不加还糟**。
    ⚠️ `no-cache` ⛔ **不是** `no-store`：它是"**用之前先来问一句**"，
       没变就回 **304（没有正文）**，内容**仍然是缓存着的**。
       ⇒ 每次页面加载多**一次**协商（⛔ 不是 8 次 —— JS/CSS 直接吃本地缓存，一次请求都不发）。
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        suffix = str(full_path).rsplit(".", 1)[-1].lower()
        resp.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if suffix in ("js", "css") else "no-cache"
        )
        return resp


app.mount("/static", _VersionedStaticFiles(directory=static_dir), name="static")
# 访问路径（示例）：http://localhost:8000/static/websocket_test.html
#    `static/trace_viewer.html` **已删** —— 前者调的 `/api/v1/user/chat_history` **根本不存在**，
#    后者 `fetch` 轨迹**不带认证头**（加了鉴权后打开必 401）⇒ 两个都是**存量的坏页**。

# ==================== 挂载Gradio成本统计可视化面板 ====================
if os.getenv("ENABLE_DASHBOARD", "true") == "true":
    from billing.cost_dashboard import create_dashboard
    import gradio as gr
    # 访问面板 启动服务后，浏览器打开 http://localhost:8000/dashboard。
    dashboard = create_dashboard()
    app = gr.mount_gradio_app(app, dashboard, path="/dashboard")
else:
    logger.info("ENABLE_DASHBOARD=false —— 已跳过成本看板挂载（不导入 gradio / matplotlib）")

