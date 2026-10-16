"""B1 · `get_user_role` 接 DB 的**行为契约**。
"""
import psycopg2

import pytest

from access import permission
from access.permission import UserRole, get_user_role, invalidate_role_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    """每条用例前后都清缓存 —— 不清的话结论**随上一条用例的状态**变（不确定）。"""
    invalidate_role_cache()
    yield
    invalidate_role_cache()


def _stub_fetch(monkeypatch, value=None, *, raises=None, counter=None):
    """把 `permission._fetch_role_from_db` 换成替身。

    `value`   —— 替身"查到"的 `role`（`None` = 该用户名下没有**激活的** key 行）
    `raises`  —— 让替身抛异常（模拟"库挂了"）
    `counter` —— 传一个 dict ⇒ 记 `calls`（给缓存那两条用例数调用次数）
    """
    def _fake(user_name):
        if counter is not None:
            counter["calls"] = counter.get("calls", 0) + 1
        if raises is not None:
            raise raises
        return value

    monkeypatch.setattr(permission, "_fetch_role_from_db", _fake)


# ── 1. DB 有值 ⇒ 以 DB 为准 ──────────────────────────────────────────

def test_db_值优先_普通用户(monkeypatch):
    _stub_fetch(monkeypatch, "premium")
    assert get_user_role("alice") == UserRole.PREMIUM


def test_db_值优先_连_admin_也一样(monkeypatch):
    """`admin` 在硬编码里是 ADMIN；**接了 DB 就以 DB 为准** —— 这正是"接 DB"的含义。"""
    _stub_fetch(monkeypatch, "free")
    assert get_user_role("admin") == UserRole.FREE


def test_db_值可以降级探针身份(monkeypatch):
    """`isolation_c` 硬编码是 PREMIUM；DB 写 `free` ⇒ 以 DB 为准。"""
    _stub_fetch(monkeypatch, "free")
    assert get_user_role("isolation_c") == UserRole.FREE


# ── 2. 回退（DB 没值 / 库挂了 / 脏值）──────────────────────────────

@pytest.mark.parametrize("user,expected", [
    ("admin", UserRole.ADMIN),
    ("isolation_c", UserRole.PREMIUM),
    ("test_user", UserRole.PREMIUM),
    ("isolation_a", UserRole.FREE),
    ("isolation_b", UserRole.FREE),
    ("some-random-stranger", UserRole.FREE),
])
def test_没写_role_时要回退到硬编码(monkeypatch, user, expected):
    """`NULL` = "加列之前写进去的老行" ⇒ 走**回退**。

    **这条就是反向守卫**：谁把"查不到就回退"改成"默认 PREMIUM"，
    下面 `isolation_a` / `isolation_b` / 陌生人这三档**立刻红**。
    """
    _stub_fetch(monkeypatch, None)
    assert get_user_role(user) == expected


def test_库挂了也要回退_而且不抛(monkeypatch):
    """库抖了不该把全站打死（与 `resolve_ws_identity` 的 fail-closed **故意相反**：
    那边是安全边界，这边只是配额分档）。"""
    _stub_fetch(monkeypatch, raises=psycopg2.OperationalError("connection refused"))
    assert get_user_role("isolation_c") == UserRole.PREMIUM
    assert get_user_role("isolation_a") == UserRole.FREE


def test_池耗尽也要回退(monkeypatch):
    """`PoolError` 是 `psycopg2.Error` 的子类 —— 与"连接被拒"同一档。"""
    _stub_fetch(monkeypatch, raises=psycopg2.pool.PoolError("connection pool exhausted"))
    assert get_user_role("isolation_a") == UserRole.FREE


def test_非数据库异常必须冒泡(monkeypatch):
    """**防写宽的守卫**：不许把捕获写成 `except Exception`。

    **本仓对这条已有明文决定**（`app/access/auth.py:153`，且那边有同款测试）——
    一个 `TypeError` 是**代码 bug**，不是"库不可用"；伪装成"回退"⇒ **真 bug 永远查不出来**。

    **而它还有一个具体的副作用**：`app/tests/test_rate_limit_identity.py::_no_db`
    那道守卫靠**抛 `AssertionError`** 抓"谁碰了库" —— 捕获写宽了，**那道门就静默失效**
    （实测过：`core.db.get_db` 被调了 1 次、异常被吞、守卫不报错）。
    """
    _stub_fetch(monkeypatch, raises=TypeError("这是代码 bug，不是库挂了"))
    with pytest.raises(TypeError):
        get_user_role("isolation_a")


def test_脏值要回退_不抛也不默认_PREMIUM(monkeypatch):
    """`role` 是 TEXT，可能被写进任何东西。不许当成 PREMIUM，不许抛。"""
    _stub_fetch(monkeypatch, "胡说八道")
    assert get_user_role("isolation_a") == UserRole.FREE
    assert get_user_role("some-random-stranger") == UserRole.FREE


def test_空串也回退(monkeypatch):
    _stub_fetch(monkeypatch, "")
    assert get_user_role("isolation_a") == UserRole.FREE


# ── 3. 缓存（TTL 60s + 写侧失效）──────────────────────────────────────

def test_TTL_内不再查库(monkeypatch):
    c = {}
    _stub_fetch(monkeypatch, "premium", counter=c)
    get_user_role("alice"); get_user_role("alice"); get_user_role("alice")
    assert c["calls"] == 1, f"TTL 内应当只查一次库，实得 {c['calls']}"


def test_不同用户各有各的条目(monkeypatch):
    c = {}
    _stub_fetch(monkeypatch, "premium", counter=c)
    get_user_role("alice"); get_user_role("bob")
    assert c["calls"] == 2


def test_TTL_过期后重新查库(monkeypatch):
    c = {}
    _stub_fetch(monkeypatch, "premium", counter=c)
    get_user_role("alice")
    # 把时钟拨过 TTL —— 不真等 60 秒。
    #    patch 的是 **`permission._now`**（本模块自己的一层）而不是 `time.monotonic` ——
    #    后者是**同一个 `time` 模块对象**，patch 它会**波及整个进程**（pytest 自身也用）。
    real = permission._now()
    monkeypatch.setattr(permission, "_now", lambda: real + permission.ROLE_CACHE_TTL_SECONDS + 1)
    get_user_role("alice")
    assert c["calls"] == 2, "TTL 过了就该重查"


def test_写侧失效要立即生效(monkeypatch):
    """这条钉的是【写侧主动失效】—— 没有它，改完角色最多要等 60s 才生效。"""
    c = {}
    _stub_fetch(monkeypatch, "free", counter=c)
    assert get_user_role("alice") == UserRole.FREE

    _stub_fetch(monkeypatch, "premium")          # 写侧把角色改成 premium
    assert get_user_role("alice") == UserRole.FREE, "⚠️ 还没失效 ⇒ 仍应命中旧缓存（这正是要钉的语义）"

    invalidate_role_cache("alice")               # ← 写侧调它
    assert get_user_role("alice") == UserRole.PREMIUM, "🔴 失效之后必须【当场】变"


def test_失效单个用户不误伤别人(monkeypatch):
    c = {}
    _stub_fetch(monkeypatch, "premium", counter=c)
    get_user_role("alice"); get_user_role("bob")
    invalidate_role_cache("alice")
    get_user_role("bob")                          # bob 的条目还在
    assert c["calls"] == 2, "失效 alice 不该让 bob 也重查"
