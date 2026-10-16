"""：库不可用时，认证链答什么（批 3 · ）
"""
import asyncio
from datetime import datetime, timedelta

import psycopg2
import pytest
from starlette.requests import Request
from starlette.responses import Response

import access.auth as auth
import main
from routing.deps import get_current_user
from core.exceptions import AppException, ErrorCode


# ==================== 替身 ====================

def _raising(exc):
    """把 `auth.get_db` 换成「一调就抛」的替身。"""
    def _boom(*args, **kwargs):
        raise exc
    return _boom


class _Cursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        self.sql = args

    def fetchone(self):
        return self._row


class _Conn:
    """只实现 `auth.verify_api_key` 用到的那几笔。"""

    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor(self._row)

    def commit(self):
        pass


def _conn_returning(row):
    return lambda: _Conn(row)


CONN_REFUSED = psycopg2.OperationalError("could not connect to server: Connection refused")


# ==================== 源头：`verify_api_key` ====================

def test_库不可用时抛服务不可用而不是返回None(monkeypatch):
    """核心：**抛**，不是返回 `None`。

    返回 `None` 是本条最容易修错的方向 —— 它会让调用方报 **401**
    （`get_current_user` 里 `user_name is None` ⇒ `AUTH_EXPIRED`），
    于是"我这边坏了"被说成"你的凭据坏了"。见模块 docstring。
    """
    monkeypatch.setattr(auth, "get_db", _raising(CONN_REFUSED))

    with pytest.raises(AppException) as ei:
        auth.verify_api_key("sk-whatever")

    assert ei.value.error_code is ErrorCode.SERVICE_UNAVAILABLE
    assert ei.value.status_code == 503


def test_连接池耗尽也算服务不可用(monkeypatch):
    """另一种同族失败：**池满**（`psycopg2.pool.PoolError`，是 `psycopg2.Error` 的子类）。

    它同样"库没坏、但我现在办不了事"⇒ 同一个应答。
    """
    from psycopg2 import pool as pg_pool
    monkeypatch.setattr(auth, "get_db", _raising(pg_pool.PoolError("connection pool exhausted")))

    with pytest.raises(AppException) as ei:
        auth.verify_api_key("sk-whatever")

    assert ei.value.error_code is ErrorCode.SERVICE_UNAVAILABLE


def test_非数据库异常必须照样冒泡(monkeypatch):
    """**防写宽的守卫**：不许写成 `except Exception`。

    一个 `TypeError` 是**代码 bug**，不是"库不可用" —— 把它也伪装成 503
    ⇒ 真 bug 永远查不出来（本仓的「静默降级比报错更危险」）。

    同一条理由也保护 `app/tests/test_rate_limit_identity.py` 的 `_no_db` fixture：
    它靠**抛 `AssertionError`** 抓「谁碰了库」；捕获写宽了，那道守卫就**静默失效**。
    """
    monkeypatch.setattr(auth, "get_db", _raising(TypeError("这是代码 bug，不是库挂了")))

    with pytest.raises(TypeError):
        auth.verify_api_key("sk-whatever")


# ==================== 反向守卫：别修过头 ====================

def test_库正常但查不到仍然返回None(monkeypatch):
    """反向守卫：**真的**没有这把 key ⇒ 仍是 `None`（⇒ 调用方报 401，这是对的）。"""
    monkeypatch.setattr(auth, "get_db", _conn_returning(None))
    assert auth.verify_api_key("sk-nope") is None


def test_库正常且key有效就返回用户名(monkeypatch):
    """反向守卫：好路径一个字都不能变。"""
    monkeypatch.setattr(auth, "get_db", _conn_returning(("alice", datetime.now() + timedelta(days=1))))
    assert auth.verify_api_key("sk-good") == "alice"


def test_库正常但key过期仍然返回None(monkeypatch):
    """反向守卫：过期判定不受本次改动影响。"""
    monkeypatch.setattr(auth, "get_db", _conn_returning(("alice", datetime.now() - timedelta(seconds=1))))
    assert auth.verify_api_key("sk-expired") is None


# ==================== 安全边界：不吞 ====================

def test_安全边界_库不可用时冒出来的是503(monkeypatch):
    """`deps.get_current_user` = **安全边界** ⇒ **fail-closed**（不是放行）。

    但它挡住时的**说法**必须是"我这边不行"：503，不是 401、也不是 500。
    """
    monkeypatch.setattr(auth, "get_db", _raising(CONN_REFUSED))

    with pytest.raises(AppException) as ei:
        asyncio.run(get_current_user("sk-whatever"))

    assert ei.value.status_code == 503
    assert ei.value.error_code is ErrorCode.SERVICE_UNAVAILABLE


def test_安全边界_缺key仍是401(monkeypatch):
    """反向守卫：**没带** key 是纯粹的"你的问题" ⇒ 仍是 401，别被这次改动带偏。"""
    with pytest.raises(AppException) as ei:
        asyncio.run(get_current_user(None))

    assert ei.value.status_code == 401
    assert ei.value.error_code is ErrorCode.AUTH_MISSING


# ==================== 限流分桶：fail-open ====================

def test_限流分桶遇库异常时给出识别不了而不是匿名(monkeypatch):
    """`main.resolve_rate_limit_identity` = **保护措施** ⇒ **fail-open**。

    返回 `None`（= **不参与用户级限流**），**不是 `"anonymous"`**：
    `anonymous` 是**一个** 20 容量 / 3 每秒的桶 ⇒ 库一挂、所有带 key 的人都挤进去
    ⇒ **大面积假 429** —— 那等于**把库抖动算到用户头上**（1008/1011 那条的限流版）。
    """
    monkeypatch.setattr(auth, "get_db", _raising(CONN_REFUSED))

    assert main.resolve_rate_limit_identity("sk-whatever", None) is None


def test_限流分桶验不过是匿名桶而不是识别不了(monkeypatch):
    """**反向守卫**：库**好的**、只是**验不过** ⇒ 仍是 `"anonymous"`。

    这两条必须**分得开**：`None` = 「我现在判不了」（服务问题）；
    `"anonymous"` = 「判了，就是匿名」（用户问题）。合起来就是一个新的错因混淆。
    """
    monkeypatch.setattr(auth, "verify_api_key", lambda k: None)

    assert main.resolve_rate_limit_identity("sk-forged", None) == "anonymous"


def test_限流分桶遇到别的异常不许吞(monkeypatch):
    """同上：不许 `except Exception` —— 它会把 `_no_db` 那道守卫一起吞掉。"""
    monkeypatch.setattr(auth, "verify_api_key", _raising(TypeError("代码 bug")))

    with pytest.raises(TypeError):
        main.resolve_rate_limit_identity("sk-whatever", None)


# ==================== 额度中间件：fail-open ====================

@pytest.fixture
def logs():
    """收集 `loguru` 的 ERROR 日志（同 `test_rate_limiter_resilience.py` 的取法）。

    `loguru` 的 logger 是**全局单例** ⇒ 模块里任何 `logger.error(...)` 都会进来。
    """
    from loguru import logger

    messages = []
    sink_id = logger.add(messages.append, format="{message}", level="ERROR")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def test_额度中间件遇库异常时跳过而不是全站500(monkeypatch):
    """`main.resolve_quota_identity` = **成本控制** ⇒ **fail-open**（不是 500）。

    **它在中间件里** ⇒ 异常**不会被 `AppException` 处理器接住** ⇒ 库一抖就是全站 500。
    返回 `None` 走的是这个中间件**本来就有**的出口（"未识别身份 ⇒ 原样放行"），
    本任务**没有**顺带改变匿名行为（那是）。
    """
    monkeypatch.setattr(auth, "get_db", _raising(CONN_REFUSED))

    assert main.resolve_quota_identity("sk-whatever", None) is None


def test_额度中间件遇库异常时留下响的日志(monkeypatch, logs):
    """**必须响** —— fail-open **加上一条静默**就等于「额度悄悄不生效了」。"""
    monkeypatch.setattr(auth, "get_db", _raising(CONN_REFUSED))

    main.resolve_quota_identity("sk-whatever", None)

    assert logs, "库不可用却一条 ERROR 都没打 —— 那是静默降级"
    assert any("额度" in m or "认证服务不可用" in m for m in logs), f"日志没说清是哪一层被跳过了：{logs}"


def test_额度中间件_库好的但key无效就是匿名(monkeypatch):
    """**改（作者裁「A1」）：验不过 ⇒ `"anonymous"`，不再是 `None`。**

    **这条【有意不等于】改动前** —— 旧行为是"验不过 ⇒ `None` ⇒ 跳过配额"，
    而那正是 `SECURITY.md` §3.1 那个洞（**匿名可以无限量打**）。
    现在 `None` **只留给"判不了"**（库挂了），真匿名一律是 `"anonymous"` ⇒ **进配额**。
    `app/main.py` 的 `resolve_quota_identity` docstring · `SECURITY.md` §3.1。
    """
    monkeypatch.setattr(auth, "verify_api_key", lambda k: None)

    assert main.resolve_quota_identity("sk-forged", None) == "anonymous"


def test_额度中间件_库好的key有效就返回用户名(monkeypatch):
    """反向守卫：好路径一个字都不能变。"""
    monkeypatch.setattr(auth, "verify_api_key", lambda k: "alice")

    assert main.resolve_quota_identity("sk-good", None) == "alice"


def test_额度中间件_没带凭据就是匿名():
    """**改（作者裁「A1」）：没带凭据 ⇒ `"anonymous"`，不再是 `None`。**

    **这是 A1 的【核心那一条】**：真匿名必须**进配额**（走 FREE 最低档），
    否则匿名就是**整层绕过**（`SECURITY.md` §3.1）。
    **反向守卫**：若哪天有人把它改回 `None`，本条**立刻红** —— 那正是"匿名又放开了"。
    """
    assert main.resolve_quota_identity(None, None) == "anonymous"

    # 与它成对的那条：**"判不了"仍必须是 `None`**（fail-open 没被这次改动带走）
    #    —— 见上面 `test_额度中间件遇库异常时跳过而不是全站500`。


def test_额度中间件_APIKey验不过还会去试JWT(monkeypatch):
    """**行为保持守卫**：抽取时最容易丢的就是这条顺序。

    改前是「先试 API Key，**拿不到身份**再试 JWT」——
    不是「API Key 验不过就直接返回」。抽函数时顺手写成 `return verify_api_key(...)`
    就是一个**静默的行为变化**：同时带 key 与 Bearer 的客户端会**丢配额身份**。
    """
    import access.jwt_handler as jwt_handler
    monkeypatch.setattr(auth, "verify_api_key", lambda k: None)
    monkeypatch.setattr(jwt_handler, "verify_access_token", lambda t: "bob")

    assert main.resolve_quota_identity("sk-forged", "Bearer good.jwt") == "bob"


def test_额度中间件遇到别的异常不许吞(monkeypatch):
    """同上：不许 `except Exception`（会把 `_no_db` 那道守卫一起吞掉）。"""
    monkeypatch.setattr(auth, "verify_api_key", _raising(TypeError("代码 bug")))

    with pytest.raises(TypeError):
        main.resolve_quota_identity("sk-whatever", None)


# ==================== 接线：两个中间件真的照 `None` 办了事吗 ====================

def _req(path: str = "/api/v1/chat", **headers) -> Request:
    """造一个**不经过 ASGI 栈**的 `Request`（只喂 `dispatch` 用到的那几个键）。"""
    raw = [(_k.replace("_", "-").encode(), _v.encode()) for _k, _v in headers.items()]
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "server": ("testserver", 80), "root_path": "",
        "path": path, "query_string": b"", "headers": raw,
    })


async def _ok(request):
    return Response(content="ok", status_code=200)


def _forbid(*args, **kwargs):
    raise AssertionError("中间件碰了它不该碰的东西 —— 这条路径本来就该绕开")


def test_限流中间件_判不了身份就整段绕开用户级限流(monkeypatch):
    """**接线判据**：`resolve_rate_limit_identity` 给 `None` ⇒ **不碰用户桶**、原样放行。

    这里把用户桶的两个方法都换成**一碰就炸**：中间件但凡还在用 `user_name` 查桶，
    本条立刻红 —— 而**不是**像"返回值断言"那样，删掉 `if user_name is None` 还照样绿。
    全局限流（上面那层）**故意放行** —— 它**不依赖身份**，本次改动没碰它，
    但它挡在前面，不 patch 就会去连 Redis。
    """
    monkeypatch.setattr(main.global_limiter, "is_allowed", lambda *a: True)
    monkeypatch.setattr(main.user_limiter, "get_limit_info", _forbid)
    monkeypatch.setattr(main.user_limiter, "is_allowed", _forbid)
    monkeypatch.setattr(main, "resolve_rate_limit_identity", lambda *a, **k: None)

    resp = asyncio.run(main.RateLimitMiddleware(app=None).dispatch(_req(X_API_Key="sk-whatever"), _ok))

    assert resp.status_code == 200


def test_限流中间件_身份正常时用户桶照常生效(monkeypatch):
    """**反向守卫**：别把 `None` 那条写成"无脑放行"。

    没有这条，把 `if user_name is None` 改成 `return await call_next(request)` 无条件
    提前返回——上面那条**依然绿**（因为它本来就是 `None`），而**全站限流其实已经关了**。
    """
    called = []
    monkeypatch.setattr(main.global_limiter, "is_allowed", lambda *a: True)
    monkeypatch.setattr(main.user_limiter, "get_limit_info",
                        lambda u: called.append(("info", u)) or {"limit": 20, "remaining": 19, "reset": 0})
    monkeypatch.setattr(main.user_limiter, "is_allowed",
                        lambda u: called.append(("allow", u)) or True)
    monkeypatch.setattr(main, "resolve_rate_limit_identity", lambda *a, **k: "user:alice")

    resp = asyncio.run(main.RateLimitMiddleware(app=None).dispatch(_req(X_API_Key="sk-good"), _ok))

    assert resp.status_code == 200
    assert called == [("info", "user:alice"), ("allow", "user:alice")]
    assert resp.headers["X-RateLimit-Remaining"] == "19"


def test_额度中间件_判不了身份就整段绕开额度检查(monkeypatch):
    """**接线判据**（同限流那条）：`resolve_quota_identity` 给 `None` ⇒ **不查库**、原样放行。

    `get_pool_budget_info` 要查 `token_usage_logs` ⇒ 换成**一碰就炸**：
    库不可用时它**本来也查不动** —— 那正是这次要避免的第二处 500。

    **（两个额度池）靶子改过一次**：改前钉的是 `get_token_budget_info`，
    那一批把它换成了**按池**的 `get_pool_budget_info`⇒ 老名字**没有这个属性**了。
    **不是把断言删掉**：这条**仍然**在钉「身份判不了 ⇒ 整层不碰」。
    """
    monkeypatch.setattr(main, "resolve_quota_identity", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_pool_budget_info", _forbid)

    resp = asyncio.run(main.QuotaMiddleware(app=None).dispatch(_req(X_API_Key="sk-whatever"), _ok))

    assert resp.status_code == 200


def test_额度中间件_身份正常时照样查预算(monkeypatch):
    """**反向守卫**：身份正常时**必须**查 —— 别顺手把额度层整层关掉。

    **（两个额度池）这一条被【加强】了** —— 改前走的是默认路径
    （`/api/v1/chat`，**不在归池表里**）⇒ 改完之后它会落到 `POOL_NONE`、
    **那条路根本不判** ⇒ 用它来验"额度层没被关掉"就**测不到东西了**。
    ⇒ 现在**显式走一条 LLM 池里的路径**，并顺带钉住**判的是哪一个池**。
    """
    pool_info = {
        "limit": 1000, "used": 900, "remaining": 100,
        "pool": "llm", "daily_budget": 1000, "used_today": 900,
    }
    monkeypatch.setattr(main, "resolve_quota_identity", lambda *a, **k: "user:alice")
    monkeypatch.setattr(
        main, "get_pool_budget_info",
        lambda u: {"role": "free", "llm": pool_info, "embedding": pool_info, "total": pool_info},
    )

    resp = asyncio.run(main.QuotaMiddleware(app=None).dispatch(
        _req("/api/v1/rag/search", X_API_Key="sk-good"), _ok))

    assert resp.status_code == 200
    assert "X-Quota-Remaining" in resp.headers
    # 路径落在 LLM 池里 ⇒ 报的必须**是 LLM 那一份的**剩余（不是总计）
    assert resp.headers["X-Quota-Pool"] == "llm"
    assert resp.headers["X-Quota-Remaining"] == "100"
