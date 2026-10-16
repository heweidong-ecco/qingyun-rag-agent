"""`rate_limiter` 的容错与过期（批 3 ·  +  · 2026-10-05）

两条**早已裁过**的方向（作者 2026-09-30，见 `app/access/specs/rate_limiter.md` 末表）：


 那条**不是推断**：`app/tests/test_rag_search.py` 里早就写着「实测」，
`.github/workflows/ci.yml:11` 也把它当**给 redis service 的理由**写着。

## 本文件两类用例

* **离线**（12 条里 11 条）：把 `rate_limiter.redis_client` 换成一个**按需抛异常**的替身。
* **真 Redis**（`test_新桶在真Redis里真的带上了TTL`）：它要问 Redis **`TTL` 是多少**。
  没有别的办法能证明「`EXPIRE` 真的生效」—— 读脚本文本只能证明「**写了这句**」。
  CI 有 redis service（`.github/workflows/ci.yml:66`）， 复用 `qingyun-rag-agent-redis-1`。
  **本仓已有同一取舍**：`app/tests/test_rag_search.py` 的 L1/L2 同样**必须真连 Redis**。
"""
import math
import re
import uuid

import pytest
import redis as redis_mod

import access.rate_limiter as rate_limiter
import billing.token_config as token_config
from access.rate_limiter import TokenBucketLimiter, global_limiter, user_limiter


# ==================== 替身 ====================

class _FakeRedis:
    """不连真 Redis 的替身。

    `boom` 一设：**所有命令都抛它** —— 用来模拟"Redis 不通"。
    它能抛**任意**异常（不限于 `RedisError`），这正是
    `test_只捕获redis错误_别的异常要照样冒泡` 需要的能力。
    """

    def __init__(self, *, boom=None, eval_result=1, hgetall_result=None):
        self.boom = boom
        self.eval_result = eval_result
        self.hgetall_result = hgetall_result if hgetall_result is not None else {}
        self.eval_calls = []

    def eval(self, script, numkeys, key, *args):
        self.eval_calls.append({"script": script, "numkeys": numkeys, "key": key, "args": args})
        if self.boom is not None:
            raise self.boom
        return self.eval_result

    def hgetall(self, key):
        if self.boom is not None:
            raise self.boom
        return self.hgetall_result


@pytest.fixture
def redis_down(monkeypatch):
    """Redis 连接被拒（`ConnectionError` 是 `RedisError` 的子类，真断连就是它）。"""
    fake = _FakeRedis(boom=redis_mod.exceptions.ConnectionError("Error 61 connecting to redis:6379"))
    monkeypatch.setattr(rate_limiter, "redis_client", fake)
    return fake


@pytest.fixture
def logs():
    """收集 `loguru` 的 ERROR 日志。

    `loguru` 的 logger 是**全局单例** ⇒ 本仓任何模块 `logger.error(...)` 都会被收进来。
    """
    from loguru import logger

    messages = []
    sink_id = logger.add(messages.append, format="{message}", level="ERROR")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def _limiter():
    """一个**小**桶，便于断言。不碰真 Redis（下面每条要么 patch、要么自己清理）。"""
    return TokenBucketLimiter(rate=1.0, capacity=2)


# ====================  · Redis 不通 ====================

def test_redis不通时放行而不是把全站打崩(redis_down):
    """**本条就是  本体**：改前这里会抛出去 ⇒ 中间件 ⇒ 非公开路径**全站 500**。

    改后：**放行**（fail-open）。理由——限流是**保护**，不是**边界**；
    Redis 抖 3 秒，代价是"限流失效 3 秒"，不是"全站 500 3 秒"。
    """
    assert _limiter().is_allowed("bucket-x") is True


def test_redis不通时留下响的日志(redis_down, logs):
    """**必须响** —— 否则就是「**限流悄悄失效**」，比报错更危险。

    （作者 2026-09-30 裁那条时就带着这一句：「fail-open + **必须响的日志**」。）
    """
    _limiter().is_allowed("bucket-x")

    assert logs, "Redis 不通却一条 ERROR 都没打 —— 那是静默降级"
    assert any("限流" in m for m in logs), f"日志没点明是限流失效：{logs}"


def test_redis不通时get_remaining报满桶(redis_down):
    """只读展示：报满桶（不是 0 —— 那会让调用方以为「被限死了」）。"""
    lim = _limiter()
    assert lim.get_remaining("bucket-x") == lim.capacity


def test_redis不通时get_limit_info仍然能算出响应头(redis_down):
    """中间件在 `is_allowed` **之前**就调它 ⇒ 它不能抛，否则照样是 500。"""
    lim = _limiter()
    info = lim.get_limit_info("bucket-x")

    assert info["limit"] == lim.capacity
    assert info["remaining"] == lim.capacity
    assert isinstance(info["reset"], int)


def test_只捕获redis错误_别的异常要照样冒泡(monkeypatch):
    """**防写宽的守卫**：不许写成 `except Exception`。

    `TypeError` 是**代码 bug**，不是"Redis 不通" —— 写成宽捕获的话，
    真 bug 会被伪装成"限流悄悄失效"，**永远查不出来**。
    """
    monkeypatch.setattr(rate_limiter, "redis_client", _FakeRedis(boom=TypeError("代码 bug")))

    with pytest.raises(TypeError):
        _limiter().is_allowed("bucket-x")


# ====================  · 反向守卫（别把正常路径也变成永远放行）====================

def test_正常时桶空就拒绝(monkeypatch):
    """反向守卫：fail-open **只**在 Redis 出错时发生。

    没有这条，把 `is_allowed` 直接改成 `return True` 也能让上面几条全绿。
    """
    monkeypatch.setattr(rate_limiter, "redis_client", _FakeRedis(eval_result=0))
    assert _limiter().is_allowed("bucket-x") is False


def test_正常时桶里有令牌就放行(monkeypatch):
    """反向守卫：好路径一个字都不能变。"""
    monkeypatch.setattr(rate_limiter, "redis_client", _FakeRedis(eval_result=1))
    assert _limiter().is_allowed("bucket-x") is True


# ====================  · 桶的 TTL ====================

def test_脚本里每个return之前都EXPIRE过():
    """** 的结构判据**：`EXPIRE` 必须在**每一条 `return` 之前**。

    Lua 里 `return` 会**直接退出** ⇒ 写在 `return` **之后**的 `EXPIRE` **永远不会跑**
    （那就成了"看着加了、其实没加"）。
    """
    script = rate_limiter._TOKEN_BUCKET_LUA

    expire_at = script.find("EXPIRE")
    returns = [m.start() for m in re.finditer(r"\breturn\b", script)]

    assert expire_at != -1, "脚本里没有 EXPIRE"
    assert returns, "脚本里居然没有 return（判据本身失效了）"
    for pos in returns:
        assert expire_at < pos, "有一个 return 排在 EXPIRE 前面 ⇒ 那条路径上的键永不过期"


def test_脚本的EXPIRE用的是传进去的那个TTL():
    """只断言"脚本里有 EXPIRE"不够 —— 还得确认它用的是 `ARGV[4]`（传进来的 TTL），

    而不是脚本里另外写死一个数（那会让 `token_config` 的那个常量变成摆设）。
    """
    script = rate_limiter._TOKEN_BUCKET_LUA

    assert re.search(r"EXPIRE['\"]?\s*,\s*KEYS\[1\]\s*,\s*ARGV\[4\]", script), \
        "EXPIRE 没有用 ARGV[4]（传进来的 TTL）"


def test_eval真的收到了TTL(monkeypatch):
    """接线守卫：`is_allowed` 必须把 TTL 传下去。

    上面那条只管"脚本里写了 `ARGV[4]`"，本条管"**值真的传到了**" —— 两条缺一不可。
    """
    fake = _FakeRedis(eval_result=1)
    monkeypatch.setattr(rate_limiter, "redis_client", fake)

    lim = _limiter()
    lim.is_allowed("bucket-x")

    args = fake.eval_calls[0]["args"]
    assert args[-1] == lim.ttl, f"eval 收到的最后一个参数不是 TTL：{args}"


def test_桶的TTL必须覆盖回满时间():
    """**推导型守卫**（不写死 60）。

    桶的语义是「**回满即无意义**」⇒ TTL 必须 ≫ `capacity / rate`。
    将来谁把 `capacity` 调大、`rate` 调小到回满时间超过 TTL，**本条立刻转红**。
    """
    for name, lim in (("global_limiter", global_limiter), ("user_limiter", user_limiter)):
        refill = math.ceil(lim.capacity / lim.rate)
        assert lim.ttl >= refill, \
            f"{name} 的 TTL={lim.ttl}s 小于回满时间 {refill}s ⇒ 桶可能没回满就先过期了"


def test_TTL可以配(monkeypatch):
    """TTL 与限流参数同族（都是"写死的策略参数"）⇒ 走 `token_config`（先例）。"""
    assert token_config.RATE_LIMIT_BUCKET_TTL > 0
    assert TokenBucketLimiter(rate=1.0, capacity=2).ttl == token_config.RATE_LIMIT_BUCKET_TTL
    assert TokenBucketLimiter(rate=1.0, capacity=2, ttl=7).ttl == 7


# ====================  · 真 Redis（**唯一**要连 Redis 的一条）====================

def test_新桶在真Redis里真的带上了TTL():
    """** 的唯一真凭证**：不读脚本、不读代码，**直接问 Redis**。

    判据：`TTL key` ⇒ **> 0**（`-1` = 有键但**永不过期** —— 那正是改前的现状）。
    桶名带 `uuid4` ⇒ 与真流量隔离；`finally` 里删掉，不留垃圾。
    """
    bucket = f"rate-limit-test:{uuid.uuid4().hex}"
    lim = TokenBucketLimiter(rate=1.0, capacity=2)
    key = lim._get_bucket_key(bucket)

    try:
        assert lim.is_allowed(bucket) is True

        ttl = rate_limiter.redis_client.ttl(key)
        assert ttl > 0, f"键上没有 TTL（ttl={ttl}；-1 = 永不 过期，-2 = 键都不在）"
        assert ttl <= lim.ttl, f"TTL={ttl} 比配置的 {lim.ttl} 还长"
    finally:
        rate_limiter.redis_client.delete(key)
