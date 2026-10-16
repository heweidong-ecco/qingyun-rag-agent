# 工具调用缓存，升级版了，包含三大风险的防护
import hashlib
import json
import logging
import time
import random
import redis
from functools import wraps
from typing import Callable, Dict, Optional
from core.config import REDIS_HOST, REDIS_PORT

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True
)

# ==================== TTL 表（**唯一一份**）====================
#    此前那份只盖住 `/agent/mcp_chat` 一条路，落在那个模块里。
# **`0` = 不缓存**，是有意写的，不是漏填。
TTL_BY_TOOL: Dict[str, int] = {
    # 纯函数：同一表达式结果永远一样 ⇒ 可长缓存
    "calculator": 86400,

    # **`date_calc` 能安全给 86400 的唯一理由**：它的 `start_date` **必填**
    #    （`simple_tools_impl.date_calc_impl` 的红线）⇒ **没有"今天"这种隐藏输入**。
    #    若哪天给它加了"默认今天" ⇒ **必须同时把这一行改成 0** —— 与 `date_today` 同因：
    #    缓存键只含入参，"（今天,1）"跨天会命中昨天的结果。
    "date_calc": 86400,
    "json_extract": 86400,
    "stats": 86400,

    # 不缓存。原因不是"它便宜"，是**它会错**：
    #    它返回「今天是X月X日」，而缓存键只含入参（空）——
    #    23:30 缓存、00:10 命中 ⇒ **跨天那一小时返回昨天**。
    #    这条不是假设：`agent_graph_advanced` 那份内联缓存**已经在跑**（TTL 3600）。
    "date_today": 0,

    # 搜索结果有时效性；**且失败不入缓存**（由 `should_cache` 保证）
    "web_search": 300,

    # 代码执行结果**不复用**（同一段代码可能依赖外部状态）
    "execute_python": 0,
}


def get_ttl(tool_name: str) -> int:
    """查某工具的 TTL。

    **查不到就抛 `KeyError`** —— 不给默认值。
    理由：`TTL_BY_TOOL.get(name, 60)` 会把「新加了工具、但忘了登记 TTL」
    变成「它被缓存 60 秒」—— **不报错、没人会知道**。
    本仓的立场：**「从不命中」与「没人违规」在机器痕迹上完全一样。**
    """
    return TTL_BY_TOOL[tool_name]


# ==================== 基础缓存操作 ====================
def get_tool_cache_key(tool_name: str, *args, **kwargs) -> str:
    """为工具调用生成缓存键"""
    raw = f"{tool_name}:{str(args)}:{str(sorted(kwargs.items()))}"
    return "tool:" + hashlib.md5(raw.encode()).hexdigest()

def get_cached_tool_result(tool_name: str, *args, **kwargs):
    """获取缓存的工具调用结果。返回None表示未命中或空值缓存"""
    key = get_tool_cache_key(tool_name, *args, **kwargs)
    cached = redis_client.get(key)
    if cached is None:
        return None  # 未命中
    if cached == "__NULL__":
        return None  # 命中了空值缓存（防穿透）
    return json.loads(cached)

def set_cached_tool_result(tool_name: str, result, expire_seconds: int = 300, *args, **kwargs):
    """缓存工具调用结果，带随机抖动防雪崩"""
    key = get_tool_cache_key(tool_name, *args, **kwargs)
    if result is None:
        # 防穿透：空值也缓存，但时间短
        redis_client.set(key, "__NULL__", ex=60)
    else:
        # 防雪崩：过期时间加随机抖动
        actual_expire = expire_seconds + random.randint(0, expire_seconds // 10)
        redis_client.set(key, json.dumps(result), ex=actual_expire)

# ==================== 缓存装饰器 ====================

#    **抢不到锁时最多等多久**；超了就**降级为直接执行**（这次不写缓存）。
_LOCK_WAIT_SECONDS = 2.0

#    **只捕 `redis.RedisError`**，**不是 `except Exception`** ——
#    后者会把**代码 bug** 伪装成"Redis 不通"，真 bug 就永远查不出来。
#    同形先例：`app/access/rate_limiter.py` 的  兜底
#      （守卫：`app/tests/test_rate_limiter_resilience.py::test_只捕获redis错误_别的异常要照样冒泡`）。
def _warn_redis_down(tool_name: str, exc: Exception) -> None:
    logger.error(
        f"🔴 工具缓存 Redis 不可用 ⇒ `{tool_name}` 本次【直通执行】、缓存【已失效】："
        f"{type(exc).__name__}: {exc}"
    )


def cached_tool(
    expire_seconds: Optional[int] = None,
    *,
    name: Optional[str] = None,
    should_cache: Optional[Callable[[object], bool]] = None,
):
    """装饰器：自动为工具函数添加缓存（含穿透、击穿、雪崩防护）

    参数:
        expire_seconds: 直接指定 TTL。为 `None` 时查 `TTL_BY_TOOL[name]`
                        （**查不到会抛 `KeyError`**，见 `get_ttl`）。
        name:          缓存键里的工具名。缺省用被装饰函数的 `__name__`。
                        用 `name=` 是**必须的**当函数名 ≠ 工具名时 ——
                        键名与 `TTL_BY_TOOL` 的键**必须**是同一个东西。
        should_cache:  结果谓词。返回 `False` ⇒ **不写缓存**（本次照常返回结果）。
                        用它而不是"猜哪些结果算失败" —— 每条工具失败的形状不同，
                        猜出来的规则是**静默**的（本仓为此栽过多次）。

    **两种降级，别混**：
      ① **抢锁超时**（`_LOCK_WAIT_SECONDS`）⇒ 直接执行、**不写缓存**；
      ② **Redis 不通**（`redis.RedisError`）⇒ 直接执行、**不写缓存**、**打一条 ERROR**（fail-open）。

    **本函数的第一条不变量**：**`func` 绝不出现在任何 `except redis.RedisError` 的 `try` 里** ——
       否则**工具自己抛 `RedisError`** 会被当成"缓存挂了"，于是**再执行一遍**（双跑）。
       守卫用例：`test_tool_raising_redis_error_is_not_retried`。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            tool_name = name or func.__name__
            ttl = expire_seconds if expire_seconds is not None else get_ttl(tool_name)

            # TTL = 0 ⇒ 直通（不读、不写、不加锁）
            if ttl == 0:
                return func(*args, **kwargs)

            # 1. 查缓存（Redis 不通 ⇒ fail-open）
            try:
                cached = get_cached_tool_result(tool_name, *args, **kwargs)
            except redis.RedisError as exc:
                _warn_redis_down(tool_name, exc)
                return func(*args, **kwargs)
            if cached is not None:
                return cached

            # 2. 防击穿：互斥锁（**有上限地等** —— 用循环，不用递归；理由见文件头那段）
            lock_key = f"lock:{tool_name}"
            deadline = time.monotonic() + _LOCK_WAIT_SECONDS
            while True:
                try:
                    got_lock = redis_client.set(lock_key, "1", nx=True, ex=10)
                except redis.RedisError as exc:
                    _warn_redis_down(tool_name, exc)
                    return func(*args, **kwargs)

                if got_lock:
                    try:
                        # 3. 执行工具函数
                        #    这一行**故意**留在一个没有 `except redis.RedisError` 的 try 里
                        #       （只有 `finally`）—— 见 docstring 的不变量。
                        result = func(*args, **kwargs)
                        # 4. 回写缓存（带防穿透和防雪崩）
                        #    写不进去**只记日志**，本次结果照常返回（结果已经算出来了）
                        try:
                            if should_cache is None or should_cache(result):
                                set_cached_tool_result(tool_name, result, ttl, *args, **kwargs)
                        except redis.RedisError as exc:
                            _warn_redis_down(tool_name, exc)
                        return result
                    finally:
                        try:
                            redis_client.delete(lock_key)
                        except redis.RedisError:
                            pass          # 删不掉 ⇒ 锁 10 秒后自己过期，不因此失败

                # 5. 没拿到锁：到点就**降级**；否则等一小会儿，再看别人有没有把缓存填好
                if time.monotonic() >= deadline:
                    return func(*args, **kwargs)      # 降级：直接执行，这次不写缓存
                time.sleep(0.05)
                try:
                    cached = get_cached_tool_result(tool_name, *args, **kwargs)
                except redis.RedisError as exc:
                    _warn_redis_down(tool_name, exc)
                    return func(*args, **kwargs)
                if cached is not None:
                    return cached
        return wrapper
    return decorator