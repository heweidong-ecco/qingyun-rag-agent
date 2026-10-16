"""`app/tools/tool_cache.py` 的口径守卫（**全离线** —— monkeypatch 掉 redis，不碰真 redis）。

**本文件钉住三件事**：
  ① **查不到 TTL 必须报错** —— 不许 `.get(name, 60)` 那种静默默认值
  ② **`0` = 不缓存**，且**一次 redis 都不碰**（`date_today` 靠它躲开跨天那一小时）
  ③ **失败结果不入缓存** —— 由 `should_cache` 谓词显式决定，不靠"猜哪些结果算失败"

它还兼一个**新工具登记网**：`test_every_mcp_registered_tool_has_a_ttl` ——
   以后往 `mcp_server.TOOLS` 加工具却忘了登记 TTL，**只有这条用例会红**。
"""
import pytest
import redis

import tools.tool_cache as T


class _FakeRedis:
    """只实现 `tool_cache` 用到的四个方法，并**记下调用**。"""

    def __init__(self):
        self.store = {}
        self.sets = 0

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx:
            if k in self.store:
                return False
            self.store[k] = v
            return True
        self.sets += 1
        self.store[k] = v
        return True

    def delete(self, k):
        self.store.pop(k, None)


@pytest.fixture
def fake(monkeypatch):
    f = _FakeRedis()
    monkeypatch.setattr(T, "redis_client", f)
    return f


def test_get_ttl_unknown_name_raises():
    """查不到 **抛 KeyError** —— 不许 `.get(name, 60)` 那种静默默认。

    反证检验：把断言取反（改成 `== 60`）⇒ 本用例会红 ⇒ 这条尺子在量它该量的事。
    """
    with pytest.raises(KeyError):
        T.get_ttl("这个工具不存在")


def test_every_mcp_registered_tool_has_a_ttl():
    """每一条**已注册给 LLM 的工具**都要有 TTL 条目（含 `0`）。

    本用例是「新加了工具但忘了登记 TTL」的**唯一**拦网 ——
    跑它才会红；别指望 import 期报错（`tool_cache` 不能反向 import `mcp_server`）。
    """
    from tools.mcp_server import TOOLS

    missing = [t["func"].name for t in TOOLS if t["func"].name not in T.TTL_BY_TOOL]
    assert missing == [], f"这些工具没登记 TTL：{missing}"


def test_ttl_zero_never_touches_redis(fake):
    """TTL = 0 ⇒ **直通**：一次 redis 都不碰。"""

    calls = []

    @T.cached_tool(name="date_today")
    def _probe():
        calls.append(1)
        return "x"

    assert _probe() == "x"
    assert _probe() == "x"
    assert calls == [1, 1], "TTL=0 时每次都该真执行"
    assert fake.sets == 0, "TTL=0 时⛔ 不该写缓存"


def test_hit_returns_cached_and_skips_body(fake):
    calls = []

    @T.cached_tool(expire_seconds=300, name="calculator")
    def _probe(x):
        calls.append(x)
        return x * 2

    assert _probe(3) == 6
    assert _probe(3) == 6
    assert calls == [3], "第二次该走缓存"


def test_failure_result_is_not_cached(fake):
    """`should_cache` 返回 False ⇒ **不写缓存**（下一次还会真跑）。"""
    calls = []

    @T.cached_tool(
        expire_seconds=300,
        name="web_search",
        should_cache=lambda r: not str(r).startswith("搜索失败"),
    )
    def _probe(q):
        calls.append(q)
        return "搜索失败：网络不通"

    assert _probe("a").startswith("搜索失败")
    assert _probe("a").startswith("搜索失败")
    assert calls == ["a", "a"], "失败结果⛔ 不许被缓存"


# ==================== fail-open：Redis 不通不拒服务====================
# 需求方 2026-10-08 裁定。**为什么必须 fail-open**：接上缓存后，`web_search` /
#    `calculator` 会从"不需要 Redis"变成"需要 Redis" ⇒ Redis 一挂它们就 500。
#    **缓存是优化，不该因为它拿不到就拒服务**（本仓 `app/tools/tool_cache.py` 文件头那句）。
# 同形先例：`app/access/rate_limiter.py` 的  兜底。


class _DownRedis:
    """每次调用都抛 `redis.RedisError`（模拟 Redis 挂了）。"""

    def get(self, k):
        raise redis.ConnectionError("redis 挂了")

    def set(self, k, v, nx=False, ex=None):
        raise redis.ConnectionError("redis 挂了")

    def delete(self, k):
        raise redis.ConnectionError("redis 挂了")


def test_redis_down_falls_open(monkeypatch):
    """Redis 不通 ⇒ **直通执行**（结果照常返回），不抛给调用方。"""
    monkeypatch.setattr(T, "redis_client", _DownRedis())

    calls = []

    @T.cached_tool(name="calculator")
    def _probe(x):
        calls.append(x)
        return x * 2

    assert _probe(3) == 6
    assert calls == [3]


def test_non_redis_exception_from_tool_still_bubbles(fake):
    """**只捕 `redis.RedisError`** —— 工具自己抛的异常必须**照样冒泡**。

    为什么必须测：fail-open 如果把 `except Exception` 一起吞了，
       **代码 bug 就会伪装成"Redis 不通"** ⇒ 真 bug 永远查不出来。
    同形先例（本仓已有一条）：`rate_limiter.py` 的 S8 兜底，
       守卫是 `app/tests/test_rate_limiter_resilience.py::test_只捕获redis错误_别的异常要照样冒泡`。
    """

    @T.cached_tool(name="calculator")
    def _boom(x):
        raise ValueError("工具自己的 bug")

    with pytest.raises(ValueError):
        _boom(1)


def test_tool_raising_redis_error_is_not_retried(fake):
    """**不许双跑**：工具自己抛 `RedisError` ⇒ 冒泡，**不因 fail-open 再执行一遍**。

    这是 `cached_tool` 的第一条不变量 —— `func` **绝不出现在**任何
    `except redis.RedisError` 的 `try` 里（见其 docstring）。
    反证：把 `result = func(...)` 挪进一个 `except redis.RedisError` 的 try，
       本用例会红（`calls == [1, 1]`）—— 说明这条尺子**在量它该量的事**。
    """
    calls = []

    @T.cached_tool(name="calculator")
    def _boom(x):
        calls.append(x)
        raise redis.ConnectionError("工具内部自己用了 redis")

    with pytest.raises(redis.ConnectionError):
        _boom(1)

    assert calls == [1], "⛔ 工具只该执行一次（fail-open 不许把它变成重试）"
