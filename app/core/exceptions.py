"""
统一异常与错误码定义
所有业务异常和错误码在此集中管理，其他模块从此导入。
"""
from enum import Enum


# ==================== 业务错误码枚举 ====================
class ErrorCode(str, Enum):
    # 通用
    UNKNOWN = "UNKNOWN"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

    # 参数校验
    PARAM_INVALID = "PARAM_INVALID"
    PARAM_MISSING = "PARAM_MISSING"

    # 认证与授权
    AUTH_MISSING = "AUTH_MISSING"
    AUTH_INVALID = "AUTH_INVALID"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_WRONG_TYPE = "AUTH_WRONG_TYPE"
    FORBIDDEN = "FORBIDDEN"

    # 资源
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"

    # 限流与配额
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"

    # 外部服务
    EXTERNAL_API_ERROR = "EXTERNAL_API_ERROR"


# 错误码到 HTTP 状态码的映射
ERROR_CODE_TO_HTTP_STATUS = {
    ErrorCode.UNKNOWN: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.PARAM_INVALID: 422,
    ErrorCode.PARAM_MISSING: 422,
    ErrorCode.AUTH_MISSING: 401,
    ErrorCode.AUTH_INVALID: 401,
    ErrorCode.AUTH_EXPIRED: 401,
    ErrorCode.AUTH_WRONG_TYPE: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.RESOURCE_CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.EXTERNAL_API_ERROR: 502,
}


# ==================== 统一业务异常类 ====================
class AppException(Exception):
    """自定义业务异常，附带错误码，由全局异常处理器统一捕获"""
    def __init__(self, error_code: ErrorCode, message: str = None,
                 retry_after: int = None, scope: str = None):
        self.error_code = error_code
        self.message = message or error_code.value  # 未提供消息则使用错误码名称
        self.status_code = ERROR_CODE_TO_HTTP_STATUS.get(error_code, 500)
        # 客户端还要等多少秒才能重试。只在【限流/配额】这类可恢复的错误上给；
        # 其他错误留 None ⇒ 处理器不会写这个字段（避免"等 0 秒"被误读成"立刻可试"）。
        self.retry_after = retry_after
        # `scope`（`R3.2`）：这一条 429 **是哪一种额度**用完了。
        #    目前只有两个值：`"global"`（全站日级）/ `"session"`（会话级）。
        #    ## 为什么非得有它 —— 不是"多给一点信息"
        #    两者 `ErrorCode` **都是 `QUOTA_EXCEEDED`**（本文件 `:34` 就一个枚举）
        #    ⇒ 前端**不看文案就分不出是哪种**。而两者的**恢复条件完全不同**：
        #      全站级：全站共享，做什么都救不回来，只能等跨天；
        #      会话级：是**你自己这个 thread** 的今日用量 ⇒ 开个新会话立刻能继续。
        #    `R3.2` 要求卡片写清「**何时恢复**」⇒ 不分清就**只能写一句混话**
        #      （改前 `chat.html` 那句「今日额度已用完 / 会话额度已用完」就是）。
        #    ## 与 `retry_after` 同一套路
        #    留 `None` ⇒ 处理器**整个字段不写**，前端据此退回通用文案，不猜一个口径。
        #    **有意只接了对话页那一条链**）：全仓别的 `QUOTA_EXCEEDED`
        #       **都还没带** ⇒ 它们发出去的 429 **没有** `scope`，这是**已知且有意**的。
        self.scope = scope