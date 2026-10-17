"""
API v1 路由集中定义
所有 /api/v1 前缀的接口在此管理。
"""
import time
from fastapi import APIRouter, Depends

from core.config import ACCESS_TOKEN_EXPIRE_MINUTES
from core.exceptions import ErrorCode, AppException
from routing.schemas import (
    DebugCacheStatsResponse,
    DebugCountResponse,
    DebugQuotaResponse,
    DebugRateLimitResponse,
    QuestionRequest,
    LoginRequest,
    RefreshRequest,
    UserCreate,
    #    加它们之前那两条的 200 schema 是**空的 `{}`** —— 而前端登录页会打它们，
    #    `test_response_model_declared.py` 那条门会当场红（门没错：空 schema = 没承诺）。
    TokenResponse,
    AccessTokenResponse,
)
# `get_current_user_hybrid` **曾被当死导入删掉**（见下面那段沿革），
#    「登录即可」，用的就是它。别再按"它没被用到"删第二遍：判据要现跑 `ruff`，不是看历史。
from routing.deps import require_admin, get_current_user_hybrid
from core.db import get_db
from rag.embedding_client import get_embedding
# B11（①b）：全站日级熔断
from billing.breaker import circuit, global_key
from access.auth import create_user_api_key, authenticate_user
from access.jwt_handler import (
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
)
from access.permission import get_user_role
from billing.token_tracker import get_token_budget_info
from access.rate_limiter import user_limiter
from core.cache import redis_client
#    且**从未被使用**；而 `client` 已改成惰性构造，留着这行会让 `api_v1` 直接 ImportError。
#    那条端点已删。


router = APIRouter(prefix="/api/v1")


# ==================== 公开接口 ====================
@router.get(
    "/",
    tags=["公开"]
)
async def root():
    return {"status": "ok", "version": "v1"}


@router.get(
    "/info",
    tags=["公开"],
    summary="服务索引（**原 `GET /` 的那段 JSON**，2026-10-09 挪来）",
    description=(
        "⚠️ 这一段**原先挂在 `GET /`** 上，而 `GET /` 在 2026-10-09 改成了"
        "**总览首页**（302 → `/static/web/index.html`）。\n\n"
        "🔴 裁定的原话是「**挪走，⛔ 不是删**」—— 所以它整段**原样**在这里，"
        "⛔ 一个字都没改。**若你是来找那段 JSON 的，就是它。**"
    ),
)
async def service_info():
    """服务索引。内容原样照搬原 `GET /`（见 `app/main.py` 里那段注释的沿革）。"""
    return {
        "status": "ok",
        "version": "2.0.0",
        "services": {
            "public": "/api/v1",
            "rag": "/api/v1/rag",
            "agent": "/api/v1/agent",
        },
    }

# ==================== 认证接口 ====================
@router.post(
    "/auth/login",
    summary="获取短期令牌和长期令牌",
    description="用户登录，返回 access_token短期令牌 和 refresh_token长期令牌",
    tags=["认证"],
    response_description="新创建的短期令牌时效15分钟，长期令牌30天",
    response_model=TokenResponse,
    responses={401: {"description": "用户名或密码错误","content": {"example": {"code": "AUTH_INVALID"}}}}
)
async def login(req: LoginRequest):
    """用户登录，返回 access_token 和 refresh_token"""
    if not authenticate_user(req.user_name, req.password):
        raise AppException(ErrorCode.AUTH_INVALID, "用户名或密码错误")
    return {
        "access_token": create_access_token(req.user_name),
        "refresh_token": create_refresh_token(req.user_name),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.post(
    "/auth/refresh",
    summary="换取新的短期令牌",
    description="输入refresh_token，用 refresh_token 换取新的 access_token",
    tags=["认证"],
    response_description="新创建的短期令牌时效15分钟",
    responses={
        401: {
            "description": "无效或过期的刷新令牌",
            "content": {
                "application/json": {
                    "examples": {
                        "AUTH_INVALID": {
                            "summary": "无效令牌",
                            "value": {
                                "error": "刷新令牌已无效",
                                "code": "AUTH_INVALID",
                                "status_code": 401
                            }
                        },
                        "AUTH_EXPIRED": {
                            "summary": "过期的刷新令牌",
                            "value": {
                                "error": "刷新令牌已过期",
                                "code": "AUTH_EXPIRED",
                                "status_code": 401
                            }
                        }
                    }
                }
            }
        }
    },
    response_model=AccessTokenResponse,
)
async def refresh(req: RefreshRequest):
    """用 refresh_token 换取新的 access_token。

    🔴  §二·⑥：**这里也必须过「账号还活着吗」那道门**。
    ⛔ 只堵 `deps.verify_jwt_token` **不够** —— 那张 refresh 令牌本身没被撤销，
    他还能**一直换新的 access**（最长 7 天）；而换出来的令牌虽然会被 API 侧拒掉，
    但**这一步会一直回 200**，前端看起来像"登录是好的、只是页面坏了"。
    """
    user_name = verify_refresh_token(req.refresh_token)
    if user_name is None:
        raise AppException(ErrorCode.AUTH_EXPIRED, "refresh_token 无效或已过期")
    from access.auth import login_account_is_live
    if not login_account_is_live(user_name):
        raise AppException(ErrorCode.AUTH_INVALID, "账号已停用、已过期或不存在")
    return {
        "access_token": create_access_token(user_name),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


# ==================== 用户管理 ====================
@router.post(
    "/admin/create_user",
    summary="创建新用户并生成API Key",
    description="""
    **管理员专用接口**：输入用户名和有效期，生成一个全新的 API Key。

    **认证要求**：**必须由管理员调用** —— 本接口以 `Depends(require_admin)` **强制校验**，
    非管理员直接得到 **403**（`仅管理员可执行此操作`）。
    初始管理员由应用**启动时**的 `ensure_admin_exists` 创建。

    **安全注意**：
    - API Key **仅在此次响应中返回一次**，系统不会存储明文Key。
    - 请立即复制并妥善保存，关闭页面后将无法找回。
    - 数据库仅存储 Key 的 SHA256 哈希值，即使数据库泄露也无法还原明文。
    """,
    tags=["用户管理"],
    response_description="新创建的用户信息和API Key（仅展示一次）",
    responses={
        200: {"description": "用户创建成功，返回API Key明文"},
        422: {"description": "请求参数格式错误，如用户名包含非法字符","content": {"example": {"code": "PARAM_INVALID"}}},
        500: {"description": "服务器内部错误，如数据库写入失败", "content": {"example": {"code": "INTERNAL_ERROR"}}}
    }
)
async def create_user(
    req: UserCreate,
    user_name: str = Depends(require_admin),
):
    """管理员创建新用户并返回 API Key"""
    # `req.role` 默认 `None` ⇒ 写进库的是 `NULL`（= "还没裁决"）——
    #    别在这里补一个 `or "free"`，那会把"没写"与"写了 free"变成同一件事（B1）。
    api_key = create_user_api_key(req.user_name, req.expire_days, role=req.role)
    return {
        "user_name": req.user_name,
        "api_key": api_key,
        "expire_days": req.expire_days,
        "role": req.role,
        "warning": "请立即保存此Key，它只显示一次！",
    }



# ==================== 【已删除】GET /api/v1/users/{user_id} ====================
# 原先是：
#     async def get_user(user_id: int = Path(...), include_detail: bool = Query(False)):
#         return {"user_id": user_id, "detail": include_detail}



# ==================== 调试接口 ====================
#    `Depends(require_admin)`。此前它们**匿名可打** —— 其中 `/debug/quota/{user_name}` 与
#    `/debug/rate_limit/{user_name}` **泄露任意用户的角色/配额/限流桶**（**可枚举用户名**），
#    `/rag/benchmark-embedding` 更是**全仓唯一匿名真烧钱**的端点。
@router.get(
    "/debug/count",
    summary="调试：查看数据库中文档数量",
    response_model=DebugCountResponse,
    tags=["调试"]
)
async def debug_count(_user: str = Depends(get_current_user_hybrid)):
    """查看文档总数（**全站口径** · 不是"你的"）。

    `_user` **不用** —— 它只用来**要一次登录**。名字带下划线是刻意的：
    别删掉这个参数"因为它没用到"，那会把这**一条**变成匿名可打。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM documents")
            count = cur.fetchone()[0]
    return {"total_documents": count}

@router.get(
    "/debug/quota",
    summary="调试：查看【自己】的日配额",
    description="查看**调用者自己**的日配额（**token 口径** · 按角色不同预算）。"
                "⚠️ 只查自己 —— 路径参数 `{user_name}` 已删",
    response_model=DebugQuotaResponse,
    tags=["调试"]
)
async def check_quota(user_name: str = Depends(get_current_user_hybrid)):
    """查看**自己**的日配额（**token 口径**）。

    改：原先返回「每日**请求次数**」（`permission.ROLE_QUOTA` + `quota_limiter`）。
    两者口径实测**差 35 倍**⇒ 作者裁「统一到 token 一套」。
    字段名 `daily_limit` / `remaining` **保留**，但**单位已从「次」变成 token**。

    ：`user_name` **从路径参数改成依赖注入的返回值** ——
    它现在**只能是调用者自己**，传不进别人。这正是本节 4 条能放开的那一步。
    """
    role = get_user_role(user_name)
    info = get_token_budget_info(user_name)
    return {
        "user_name": user_name,
        "role": role,
        "daily_limit": info["daily_budget"],
        "remaining": info["remaining"],
        "used_today": info["used_today"],
    }

#对比测试：缓存命中和无缓存命中 时间差距，正常生产级数据库大概是5倍，看数据库大小
@router.post(
    "/rag/benchmark-embedding",
    summary="对比测试：缓存命中和无缓存命中 时间差距，正常生产级数据库大概是5倍，看数据库大小",
    tags=["调试"]
)
async def benchmark_embedding(req: QuestionRequest, _admin: str = Depends(require_admin)):
    # B11 · 全站日级熔断（`①b`）。
    #    此前本端点在全仓**独一份**：**匿名可打、且真花钱**（下面 `get_embedding()` 真调 DashScope）。
    #    **熔断层与鉴权层是两件事，别因为有了 B11 就不加鉴权**：
    #      B11 管的是「**全站今天超预算了就别再烧**」，**管不住「谁都能烧」**。
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    start = time.time()
    # 🔴：**本条没有会话**（上面 317 行早就写着"接不上 B8"）
    #    ⇒ 给一个**显式**标记，⛔ 别让它静默落 `"system"` —— 那个值的语义是「连用户都没有」，
    #    而这里 `_admin` 是真的。`user_name` 那块与 `thread_id` 这块是两根轴，别合并。
    vec1 = get_embedding(req.question, user_name=_admin, thread_id="benchmark")
    t1 = time.time() - start

    start = time.time()
    vec2= get_embedding(req.question, user_name=_admin, thread_id="benchmark")
    t2= time.time() - start

    return {
        "question":req.question,
        "first_call_ms": round(t1*1000,2),
        "second_call_ms": round(t2 * 1000, 2),
        "speedup": f"{t1 / t2:.1f}x",
        "same_vector": vec1[:5] == vec2[:5]
    }

#增加一个调试接口，用来查看当前缓存中有多少个 Embedding 键
@router.get(
    "/debug/cache_stats",
    summary="调试接口，用来查看当前缓存中有多少个 Embedding 键",
    response_model=DebugCacheStatsResponse,
    tags=["调试"]
)
async def cache_stats(_user: str = Depends(get_current_user_hybrid)):
    """Redis 里 `emb:*` 键的条数与前 5 个样本键（**全站口径** · 不是"你的"）。

    **`count == 0` 不等于"缓存坏了"** —— 还没有 embedding 被缓存过就自然是 0。
    同 `debug_count`：`_user` 只用来**要一次登录**，别当死参数删掉。
    """
    count = 0
    sample = []
    for key in redis_client.scan_iter(match="emb:*", count=100):
        count += 1
        if len(sample) < 5:
            sample.append(key)
    return {
        "cached_embeddings_count": count,
        "sample_keys": sample
    }


# ==================== 【已删除】GET /api/v1/tool/benchmark ====================
# 原先它把 `get_weather("Beijing")` 调两次、比耗时，输出 `speedup`。

# 新增命令桶 调试接口：
@router.get(
    "/debug/rate_limit",
    summary="调试接口，查询【自己】的剩余令牌数",
    description="查询**调用者自己**的限流桶剩余令牌。⚠️ 只查自己 —— "
                "路径参数 `{user_name}` 已删",
    response_model=DebugRateLimitResponse,
    tags=["调试"]
)
async def check_rate_limit(user_name: str = Depends(get_current_user_hybrid)):
    """查询**自己**的剩余令牌数。

    ：`user_name` **从路径参数改成依赖注入的返回值**（同 `check_quota`）。

    **桶在 Redis 里，不在进程内存** —— `user_limiter` 是 `access/rate_limiter.py:211`
    建的 `TokenBucketLimiter`，它的实现是操作 **Redis HASH 的 Lua 脚本**（`_TOKEN_BUCKET_LUA`）。
    而 `capacity` / `rate` **是代码常量**（`billing/token_config.py` 的
    `USER_LIMIT_CAPACITY` / `USER_LIMIT_RATE`），**不是实时值** ⇒ 前端那一格要标出来。
    """
    remaining = user_limiter.get_remaining(user_name)
    return {
        "user_name": user_name,
        "remaining_tokens": remaining,
        "capacity": user_limiter.capacity,
        "rate": user_limiter.rate
    }



