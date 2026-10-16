"""
依赖注入函数集中定义
所有 FastAPI 的 Depends() 依赖在此管理。
"""
import asyncio
import json
from typing import Optional
from fastapi import Header, Depends, WebSocket, WebSocketException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.websockets import WebSocketDisconnect

from core.exceptions import AppException, ErrorCode
from access.auth import verify_api_key as verify_key
from access.jwt_handler import verify_access_token

# 全局 Bearer 认证方案实例（替换原来的 security）
# auto_error=False：允许请求只带 X-API-Key 而不带 Authorization 头时也能通过依赖解析，
# 否则 HTTPBearer 会在缺少 Authorization 头时直接抛 403，导致纯 API Key 认证全部失效。
oauth2_scheme = HTTPBearer(auto_error=False)

# ------------------ 纯 Token 验证函数（供内部调用） ------------------
def verify_jwt_token(token: str) -> str:
    """验证 JWT token 字符串，返回用户名，失败抛出异常。

    🔴 2026-10-15（§二·⑥）：**解码之后还要过一道「这账号还活着吗」**。

    ## 为什么（没有它的后果）

    JWT 是**无状态**的 —— 只验签名与过期，⛔ 不查库
    ⇒ **撤销一个账号，他手里的令牌在过期前一直有效**
    ⇒ 「撤销」会退化成"**下次登录才生效**"（而 `refresh` 有 **7 天**）
    ⇒ 对"发 100 个账号给外面的人"这件事 = **发错了收不回**。

    ⚠️ **它每次请求都会跑**，所以 `login_account_is_live` 那侧带**进程内缓存**
    （30 秒）⇒ 撤销的生效延迟 = 最多 30 秒，⛔ 不是 0 秒、也⛔ 不是 7 天。
    📌 环境变量的账号（`admin` / `test_user`）**永远放行** —— 它们不在库里，
    否则管理员会被自己锁在门外（见 `login_account_is_live` 的 docstring）。
    """
    user_name = verify_access_token(token)
    if user_name is None:
        raise AppException(ErrorCode.AUTH_EXPIRED, "access token 无效或已过期")
    from access.auth import login_account_is_live
    if not login_account_is_live(user_name):
        raise AppException(ErrorCode.AUTH_INVALID, "账号已停用、已过期或不存在")
    return user_name

# ==================== 依赖注入： API Key 认证 ====================
async def get_current_user(x_api_key: str = Header(None)) -> str:
    """
    从请求头 X-API-Key 中提取并验证 API Key。
    返回 user_name，无效则抛出异常。
    """
    if not x_api_key:
        raise AppException(ErrorCode.AUTH_MISSING, "缺少 API Key")
    
    user_name = verify_key(x_api_key)
    if user_name is None:
        raise AppException(ErrorCode.AUTH_EXPIRED, "API Key 无效或已过期")
    
    return user_name

# ==================== 依赖注入： JWT 认证 （与API Key共存）====================
async def get_current_user_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(oauth2_scheme)
) -> str:
    if credentials is None:
        raise AppException(ErrorCode.AUTH_MISSING, "缺少 Bearer Token")
    token = credentials.credentials
    user_name = verify_access_token(token)
    if user_name is None:
        raise AppException(ErrorCode.AUTH_EXPIRED, "access token 无效或已过期")
    return user_name

# ------------------ 新：Hybrid 认证（自动识别 API Key / JWT） ------------------
# 新的 Hybrid 认证依赖
async def get_current_user_hybrid(
    x_api_key: str = Header(None),
    credentials: HTTPAuthorizationCredentials = Depends(oauth2_scheme)
) -> str:
    """支持 X-API-Key 或 Bearer Token 两种认证方式"""
    if x_api_key:
        return await get_current_user(x_api_key)
    if credentials:
        token = credentials.credentials
        return verify_jwt_token(token)
    raise AppException(ErrorCode.AUTH_MISSING, "请提供 API Key 或 Bearer Token")

# ------------------ 管理员权限校验（Hybrid 版本） ------------------
async def require_admin(
    user_name: str = Depends(get_current_user_hybrid)
) -> str:
    """校验当前用户是否为管理员，支持 API Key / JWT"""
    from access.permission import get_user_role, UserRole
    role = get_user_role(user_name)
    if role != UserRole.ADMIN:
        raise AppException(ErrorCode.FORBIDDEN, "仅管理员可执行此操作")
    return user_name


# ===========================================================================
# WebSocket 首帧认证—— 与上面那套**不能互换**
# ===========================================================================
#
# **为什么不能照抄 HTTP 那套**：`new WebSocket(url)` **不能自定义请求头**
#    ⇒ `X-API-Key` / `Authorization` 两条路**客户端根本送不上来**。
#    浏览器唯一能带的只有 **URL 查询串**（会进访问日志/Cloudflare 边缘日志）与
#    **子协议头**（`Sec-WebSocket-Protocol`，非常规、代理可能剥）。
#    本轮裁定见 本仓的决策记录（⛔ 不随产品仓提供）：**首帧认证**。
#
# **为什么中间件帮不上忙**：`app/main.py` 那两个中间件是 `BaseHTTPMiddleware`，
#    它**只看 `scope["type"] == "http"`** ⇒ **永远看不到 WebSocket**。
#    ⇒ WS 的鉴权只能挂在**路由自己**身上（这也是它曾经整条裸奔的原因， 遗留·1）。

WS_AUTH_TIMEOUT_SECONDS = 5.0

# RFC 6455 关闭码。两个**别有用心地区分**：1008 是「你的凭据/帧不行」，
# 1011 是「我们这边不行」。混用会把一次 DB 抖动说成"你的 key 失效了"，
# 用户照着提示去换 key —— **修不好，还查不出原因**。
WS_CLOSE_POLICY_VIOLATION = 1008
WS_CLOSE_INTERNAL_ERROR = 1011


def resolve_ws_identity(payload) -> tuple[Optional[str], int, str]:
    """把**首帧 JSON** 解析成身份。返回 `(user_name, close_code, reason)`。

    * 认证通过 ⇒ `(user_name, 0, "")`
    * 认证不通过 ⇒ `(None, 1008, 原因)`
    * 认证**做不了**（验证器自身抛错，典型是库连不上） ⇒ `(None, 1011, 原因)`

    **纯函数、不抛异常**：调用方（`require_ws_user`）要拿这三个值去决定关连接的方式，
       异常会把"该说什么"和"怎么说"搅在一起。

    **fail-closed**：验证器抛错时**挡住**（不是放行）。
       与 `token_tracker.get_session_token_usage` 的 fail-open **故意相反** ——
       那边是**成本控制**（挡错了代价更大），这边是**安全边界**（放错了代价更大）。
       **别"统一"掉**（`token_tracker.py` 里也写着同一条）。
    """
    if not isinstance(payload, dict):
        return None, WS_CLOSE_POLICY_VIOLATION, "认证失败：首帧必须是 JSON 对象"
    if payload.get("type") != "auth":
        return None, WS_CLOSE_POLICY_VIOLATION, "认证失败：首帧的 type 必须是 auth"

    api_key = payload.get("api_key")
    token = payload.get("token")
    # 剥首尾空白：复制粘贴常带 `\n`。不剥的话会拿带空白的串算哈希 ⇒ 真 key 也验不过，
    # 而报错信息只有一句"凭据无效"，**排查不到点上**。
    api_key = api_key.strip() if isinstance(api_key, str) else ""
    token = token.strip() if isinstance(token, str) else ""

    try:
        if api_key:
            user_name = verify_key(api_key)
        elif token:
            user_name = verify_access_token(token)
        else:
            return None, WS_CLOSE_POLICY_VIOLATION, "认证失败：请提供 api_key 或 token"
    except Exception as exc:                      # noqa: BLE001 —— 见 docstring「fail-closed」
        return None, WS_CLOSE_INTERNAL_ERROR, f"认证服务不可用：{exc}"

    if not user_name:
        return None, WS_CLOSE_POLICY_VIOLATION, "认证失败：API Key / Token 无效或已过期"
    return user_name, 0, ""


async def require_ws_user(websocket: WebSocket) -> str:
    """WS 路由的鉴权依赖。**认证通过才返回 `user_name`**，否则抛 `WebSocketException`。

    **别再在端点里写 `await websocket.accept`**：这条依赖**已经 accept 过了**，
       而星型 1.6.0 的 `accept` **不幂等** —— 第二次会 `RuntimeError`
       （实测：`Expected ASGI message "websocket.send" … but got 'websocket.accept'`）。

    ## 顺序不能反：必须**先 accept，再验**

    握手阶段（`accept` 之前）关连接 ⇒ 浏览器侧只看到**握手失败**，
    **拿不到 1008**（实测）⇒ 前端无法区分"key 错了"和"网线掉了"，
    只能笼统提示"连接失败"。先 `accept` 再用 `close(1008)`，前端才能拿到确切的关闭码。

    ## 抛异常而不是自己 close

    星型自己会接住 `WebSocketException` 并 `close(code=…, reason=…)`
    （`ExceptionMiddleware.websocket_exception`）。
    **不要既自己 close 又抛异常** —— 连发两帧 close 会 `RuntimeError`
    （`Cannot call "send" once a close message has been sent.`）。

    ## 为什么接受 `Get` 到的首帧必须是认证帧

    首帧就是**唯一**的认证时机。若允许"先发业务帧再认证"，`/ws/agent` 的端点体
    会先跑起来（**构造 LLM、真花钱**）—— 那正是本次要堵的洞。
    """
    # 先把连接建立起来 —— 否则失败时浏览器只能看到握手失败（见 docstring）
    await websocket.accept()

    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=WS_AUTH_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise WebSocketException(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason=f"认证超时：{WS_AUTH_TIMEOUT_SECONDS:g} 秒内未收到认证帧",
        )
    except WebSocketDisconnect:
        # 客户端连上就跑了 —— 原样抛（socket 已经没了，再包装成"认证失败"是假告警）
        raise

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        payload = None

    user_name, code, reason = resolve_ws_identity(payload)
    if user_name is None:
        raise WebSocketException(code=code, reason=reason)

    # 认证通过 ⇒ 回一帧 ready，前端据此才发第一个业务帧（否则是竞态：帧可能白丢）
    await websocket.send_text(json.dumps({"type": "ready", "user": user_name}))
    return user_name


