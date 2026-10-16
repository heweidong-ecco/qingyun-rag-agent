import time

import redis
from loguru import logger

from core.config import REDIS_HOST, REDIS_PORT
#    ⇒ 这个导入**上移到顶部**了 —— 原先它在文件中部，而上面的类要用其中的常量。
from billing.token_config import (
    GLOBAL_LIMIT_RATE, GLOBAL_LIMIT_CAPACITY,
    USER_LIMIT_RATE, USER_LIMIT_CAPACITY,
    RATE_LIMIT_BUCKET_TTL,
)

#    （`pyflakes` 一直在报）。它们就在本次要改的这几行旁边，故一并清掉。

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True
)


#    ① 原先它在 `is_allowed` 里**每次调用都重新拼一遍字符串**（白花）；
#    ② 提出来才**测得到**：「每个 `return` 之前都 `EXPIRE` 过」这条只能用**文本判据**，
#       藏在函数体里就验不了（见 `app/tests/test_rate_limiter_resilience.py`）。
_TOKEN_BUCKET_LUA = """
local bucket = redis.call('HGETALL', KEYS[1])
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])

local tokens = capacity  -- 初始令牌数
local last_time = now    -- 上次补充时间

if next(bucket) ~= nil then
    tokens = tonumber(bucket[2])  -- 当前令牌数
    last_time = tonumber(bucket[4])  -- 上次补充时间
end

-- 计算新增令牌
local elapsed = now - last_time
local new_tokens = math.floor(elapsed * rate)
tokens = math.min(capacity, tokens + new_tokens)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1  -- 消耗一个令牌
    allowed = 1          -- 允许
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'last_time', now)
--    而空闲的桶**必然已经回满** ⇒ 不改变任何一次判定，只让键不再永久堆积。
redis.call('EXPIRE', KEYS[1], ARGV[4])

return allowed
"""

class TokenBucketLimiter:
    """
    基于Redis的令牌桶限流器。
    每个用户维护一个独立的桶，用Redis Hash存储。
    """
    
    def __init__(self, rate: float = 3.0, capacity: int = 20, ttl: int | None = None):
        """
        rate: 每秒补充的令牌数
        capacity: 桶的最大容量（允许的最大突发请求数）
        ttl: 桶键在 Redis 里存活多少秒。`None` ⇒ 用 `token_config.RATE_LIMIT_BUCKET_TTL`。
             ：**改前根本没有这个概念** ⇒ 键**永不过期**。
        """
        self.rate = rate
        self.capacity = capacity
        self.ttl = RATE_LIMIT_BUCKET_TTL if ttl is None else ttl
    
    def _get_bucket_key(self, user_name: str) -> str:
        """为每个用户生成独立的桶键"""
        return f"rate_limit:{user_name}"
    
    def is_allowed(self, user_name: str) -> bool:
        """
        检查用户是否允许此次请求。
        如果允许，消耗一个令牌；否则返回False。

        ：**Redis 不通 ⇒ 放行**（fail-open）+ 一条 ERROR 日志。
           改前**没有任何兜底** ⇒ 异常冒到 `RateLimitMiddleware`
           **非公开路径全站 500**（实测出处：`app/tests/test_rag_search.py`
           `.github/workflows/ci.yml:11`）。理由：限流是**保护**，不是**边界** ——
           "限流失效 3 秒" ≪ "全站 500 3 秒"。
           与之**故意相反**的是 `deps.py` 的鉴权（那边 fail-closed：放错了 = 匿名进门）。
           **别"统一"掉** —— `token_tracker` 与 `deps.py` 里各写着同一条。

        捕获范围 = **`redis.RedisError`**（含 `ConnectionError` / `TimeoutError` /
           `ResponseError`），**不是 `except Exception`** —— 那会把**代码 bug** 也
           伪装成"Redis 不通"，真 bug 就永远查不出来了。
           守卫：`app/tests/test_rate_limiter_resilience.py::test_只捕获redis错误_别的异常要照样冒泡`。
        """
        key = self._get_bucket_key(user_name)
        now = time.time()

        try:
            result = redis_client.eval(
                _TOKEN_BUCKET_LUA,
                1,
                key,
                now,
                self.rate,
                self.capacity,
                self.ttl,
            )
        except redis.RedisError as exc:
            logger.error(
                f"🔴 限流器 Redis 不可用 ⇒ 本次请求【放行】、用户级限流【已失效】："
                f"{type(exc).__name__}: {exc}"
            )
            return True

        return bool(result)
    
    def get_remaining(self, user_name: str) -> int:
        """查询用户剩余令牌数

        ：Redis 不通 ⇒ **报满桶**（`self.capacity`）。
           不是报 0 —— 报 0 会让调用方以为"**被限死了**"，
           而真实情况恰恰相反：**限流已经失效了**（fail-open）。两者的处置完全相反。
        """
        key = self._get_bucket_key(user_name)
        try:
            data = redis_client.hgetall(key)
        except redis.RedisError as exc:
            logger.error(
                f"🔴 限流器 Redis 不可用 ⇒ `get_remaining` 报满桶："
                f"{type(exc).__name__}: {exc}"
            )
            return self.capacity
        if not data:
            return self.capacity
        return int(float(data.get("tokens", self.capacity)))
    
    #增加 get_limit_info 方法，返回当前剩余令牌数及令牌完全恢复的时间戳。
    def get_limit_info(self, user_name: str) -> dict:
        """
        返回当前用户的限流信息，用于构造响应头。
        返回: {
            'remaining': int,     # 剩余令牌数
            'reset': int,         # 令牌桶完全回满的 Unix 时间戳（秒）
            'limit': int,         # 桶的容量（总限制）
        }
        """
        key = self._get_bucket_key(user_name)
        now = time.time()

        #    所以**它不兜住 ⇒ 照样是全站 500**。⇒ 同样 fail-open：报满桶。
        try:
            data = redis_client.hgetall(key)
        except redis.RedisError as exc:
            logger.error(
                f"🔴 限流器 Redis 不可用 ⇒ `get_limit_info` 报满桶（响应头照发）："
                f"{type(exc).__name__}: {exc}"
            )
            data = {}

        #    **但它一开始被我误报成"桶不回填"（N16）** —— 核了消费入口才发现**回填是有的**，
        #    在 `is_allowed()` 的 **Lua 脚本**里（`elapsed = now - last_time` ⇒ `tokens += elapsed*rate`），
        #    而且是对的。⇒ 本函数是**只读展示**，它算的 `reset_time = now + need/rate` **也是对的**。
        #    ⇒ 这两行只是死变量，删掉；**N16 已撤回**。
        if not data:
            tokens = self.capacity
        else:
            tokens = float(data.get("tokens", self.capacity))
        
        # 计算恢复到满桶所需时间
        need = self.capacity - tokens
        if need <= 0:
            reset_time = now
        else:
            reset_time = now + (need / self.rate)
        
        return {
            "remaining": int(tokens),
            "reset": int(reset_time),
            "limit": self.capacity
        }

# 新增 实现“全局 + 用户”两层令牌桶防护

#    它们与额度类常量同型（都是"写死的策略参数"）⇒ 收口到一处，便于统一看/统一调。
#    行为不变：默认值与原来逐字相同（100/150 · 3/20）。
#    因为类里的 `self.ttl` 默认值要用同一份常量，写在中间会变成"先用后导"。
#    导入位置变了，**取值没变**。

# 全局限流器：每秒 GLOBAL_LIMIT_RATE 次，桶容量 GLOBAL_LIMIT_CAPACITY（允许一定突发）
global_limiter = TokenBucketLimiter(rate=GLOBAL_LIMIT_RATE, capacity=GLOBAL_LIMIT_CAPACITY)

# 用户级限流器：每秒 USER_LIMIT_RATE 次，桶容量 USER_LIMIT_CAPACITY
user_limiter = TokenBucketLimiter(rate=USER_LIMIT_RATE, capacity=USER_LIMIT_CAPACITY)