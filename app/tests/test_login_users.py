"""· 登录账号入库 + 口令哈希 —— **离线契约**（本文件**一条都不碰真库**）。
"""
from datetime import datetime, timedelta

import pytest

from access import login_users as LU
from access.auth import authenticate_user


# ==================== 一 · 哈希本身 ====================


def test_同一口令两次哈希结果不同_但都能校验():
    a = LU.hash_password("hunter2")
    b = LU.hash_password("hunter2")
    assert a != b, "两行 salt 必须独立 —— 相同就说明 salt 没随机"
    assert LU.verify_password("hunter2", a) is True
    assert LU.verify_password("hunter2", b) is True


def test_口令错了就是不过():
    stored = LU.hash_password("hunter2")
    assert LU.verify_password("hunter3", stored) is False
    assert LU.verify_password("", stored) is False


def test_哈希串是四段且带轮数与盐():
    stored = LU.hash_password("x")
    algo, iters, salt_hex, hash_hex = stored.split("$")
    assert algo == "pbkdf2_sha256"
    assert int(iters) == LU.PBKDF2_ITERATIONS
    assert len(bytes.fromhex(salt_hex)) == LU._SALT_BYTES
    assert len(bytes.fromhex(hash_hex)) == 32


@pytest.mark.parametrize("bad", [
    "",
    "not-a-hash",
    "pbkdf2_sha256$abc$aa$bb",          # 轮数不是整数
    "pbkdf2_sha256$1000$zz$bb",         # 盐不是 hex
    "md5$1000$aa$bb",                   # 换算法
    "pbkdf2_sha256$1000$aa$bb$extra",   # 段数不对
])
def test_坏行一律不过_且不抛(bad):
    assert LU.verify_password("x", bad) is False


def test_轮数是从哈希串里读的_不是读模块常量(monkeypatch):
    """🔴 防回归：调大轮数**不许**让已有口令失效。"""
    stored = LU.hash_password("pw", iterations=1000)
    monkeypatch.setattr(LU, "PBKDF2_ITERATIONS", 999_999)
    assert LU.verify_password("pw", stored) is True, (
        "轮数若从模块常量读，这里会红 —— 而线上表现是「所有人登不进、且不报错」"
    )


# ==================== 二 · `authenticate_user` 的两条来源 ====================


def test_环境变量命中就返回真(monkeypatch):
    monkeypatch.setattr("access.auth._get_users_db", lambda: {"admin": "pw123"})
    assert authenticate_user("admin", "pw123") is True
    assert authenticate_user("admin", "pw124") is False


def test_环境变量命中时不查库(monkeypatch):
    """🔴 顺序守卫：反过来的后果是「**谁能在库里插一行 admin，谁就改了管理员口令**」。"""
    monkeypatch.setattr("access.auth._get_users_db", lambda: {"admin": "pw123"})

    def _boom(*a, **k):
        raise AssertionError("环境变量命中时⛔ 不许走库那条路")

    monkeypatch.setattr("access.login_users.authenticate_from_db", _boom)
    assert authenticate_user("admin", "pw123") is True


def test_非ascii口令在明文那条路上不许炸(monkeypatch):
    """⚠️ `compare_digest` 收 `str` 遇非 ASCII 直接 `TypeError` ⇒ 那是 500，不是 401。"""
    monkeypatch.setattr("access.auth._get_users_db", lambda: {"admin": "口令带中文🔐"})
    assert authenticate_user("admin", "口令带中文🔐") is True
    assert authenticate_user("admin", "口令带中文") is False


def test_环境变量里没有的名字_走库(monkeypatch):
    monkeypatch.setattr("access.auth._get_users_db", lambda: {"admin": "pw"})
    seen = {}

    def _stub(user_name, password):
        seen["called"] = (user_name, password)
        return True

    monkeypatch.setattr("access.login_users.authenticate_from_db", _stub)
    assert authenticate_user("hr001", "whatever") is True
    assert seen["called"] == ("hr001", "whatever")


# ==================== 三 · 库那条路：fail-closed ====================


class _Boom:
    def __enter__(self):
        raise RuntimeError("库挂了")

    def __exit__(self, *a):
        return False


def test_库挂了就是不通过_绝不放开(monkeypatch):
    monkeypatch.setattr(LU, "get_db", lambda: _Boom())
    assert LU.authenticate_from_db("hr001", "whatever") is False


class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Cur(self._row)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("row,why", [
    (None, "没这个账号"),
    (("", False, None), "已停用"),
])
def test_查不到或停用就是不通过(monkeypatch, row, why):
    monkeypatch.setattr(LU, "get_db", lambda: _Conn(row))
    assert LU.authenticate_from_db("hr001", "pw") is False, why


def test_过期了也不通过(monkeypatch):
    from datetime import datetime, timedelta
    stored = LU.hash_password("pw")
    monkeypatch.setattr(LU, "get_db",
                        lambda: _Conn((stored, True, datetime.now() - timedelta(seconds=1))))
    assert LU.authenticate_from_db("hr001", "pw") is False


def test_有了能用的行且口令对_就通过(monkeypatch):
    from datetime import datetime, timedelta
    stored = LU.hash_password("pw")
    monkeypatch.setattr(LU, "get_db",
                        lambda: _Conn((stored, True, datetime.now() + timedelta(days=1))))
    assert LU.authenticate_from_db("hr001", "pw") is True
    assert LU.authenticate_from_db("hr001", "wrong") is False


def test_永不过期_expires_at_为_NULL_照样能登(monkeypatch):
    stored = LU.hash_password("pw")
    monkeypatch.setattr(LU, "get_db", lambda: _Conn((stored, True, None)))
    assert LU.authenticate_from_db("hr001", "pw") is True


# ==================== 四 · 「这账号还活着吗」—— 撤销立即生效 ====================


@pytest.fixture(autouse=True)
def _清缓存():
    """⚠️ 缓存是**模块级全局**的 —— 不清会让用例互相污染（而失败会指错方向）。"""
    LU.invalidate_active_cache()
    yield
    LU.invalidate_active_cache()


class _CountingConn:
    """按行返回，并**记下查了几次**。"""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0
        self._row = None

    def cursor(self):
        outer = self

        class _C:
            def execute(self, *a, **k):
                outer.calls += 1
                outer._row = outer.rows.pop(0) if outer.rows else None

            def fetchone(self):
                return outer._row

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _C()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_库账号活着_不拦(monkeypatch):
    monkeypatch.setattr(LU, "get_db", lambda: _CountingConn([(True, None)]))
    assert LU.login_account_blocked("hr001") is False


def test_库账号被停用_拦(monkeypatch):
    """🔴 **这是唯一真正要防的那一种**：真实用户的账号被撤。"""
    monkeypatch.setattr(LU, "get_db", lambda: _CountingConn([(False, None)]))
    assert LU.login_account_blocked("hr001") is True


def test_库里没有这一行_不拦(monkeypatch):
    """⚠️ 见 `login_account_blocked` 的 docstring —— 这一条**必须是 False**，
    否则 CI（无 PG）与全部自签 JWT 的用例会一起死，而换来的保护是零。"""
    monkeypatch.setattr(LU, "get_db", lambda: _CountingConn([None]))
    assert LU.login_account_blocked("m6-deadbeef") is False


def test_库读不到_不拦_因为无法判定(monkeypatch):
    monkeypatch.setattr(LU, "get_db", lambda: _Boom())
    assert LU.login_account_blocked("hr001") is False


def test_缓存_TTL_内不重复查库(monkeypatch):
    conn = _CountingConn([(True, None)])
    monkeypatch.setattr(LU, "get_db", lambda: conn)
    for _ in range(5):
        assert LU.login_account_blocked("hr001") is False
    assert conn.calls == 1, "同一 TTL 窗口内只该查一次 —— 否则每个请求都打库"


def test_过期了的账号_拦(monkeypatch):
    monkeypatch.setattr(LU, "get_db",
                        lambda: _CountingConn([(True, datetime.now() - timedelta(seconds=1))]))
    assert LU.login_account_blocked("hr001") is True


# ==================== 五 · `login_account_is_live`：环境变量账号【永远放行】 ====================


def test_环境变量的账号永远放行_且不查库(monkeypatch):
    """🔴 这一条防的是把管理员锁在门外：`admin` **在 `login_users` 里没有行**，
    若一律走"查库、查不到就拒"，**管理员会被自己锁死**。"""
    monkeypatch.setattr("access.auth._get_users_db", lambda: {"admin": "pw"})

    def _boom(*a, **k):
        raise AssertionError("环境变量里的账号⛔ 不该走库那条路")

    monkeypatch.setattr("access.login_users.login_account_blocked", _boom)
    from access.auth import login_account_is_live
    assert login_account_is_live("admin") is True


def test_库里的账号_走库(monkeypatch):
    monkeypatch.setattr("access.auth._get_users_db", lambda: {"admin": "pw"})
    seen = {}

    def _stub(n):                     # ⚠️ 返回的是「**拦不拦**」⇒ 这里**不拦** = False
        seen["n"] = n
        return False

    monkeypatch.setattr("access.login_users.login_account_blocked", _stub)
    from access.auth import login_account_is_live
    assert login_account_is_live("hr001") is True
    assert seen["n"] == "hr001"


# ==================== 六 · `verify_jwt_token`：撤销【立即】生效 ====================


def test_令牌有效但账号已被撤销_当场拒(monkeypatch):
    """🔴 这条钉的是  §二·⑥ 的核心 —— 没有它，
    「撤销」只在下一次**登录**时生效，而已发出的令牌还能用到过期（refresh 7 天）。"""
    from access.jwt_handler import create_access_token
    from routing import deps

    token = create_access_token("hr001")
    monkeypatch.setattr("access.auth.login_account_is_live", lambda n: False)
    with pytest.raises(Exception) as e:
        deps.verify_jwt_token(token)
    assert "停用" in str(e.value) or "不存在" in str(e.value)


def test_令牌有效且账号还活着_放行(monkeypatch):
    from access.jwt_handler import create_access_token
    from routing import deps

    token = create_access_token("hr001")
    monkeypatch.setattr("access.auth.login_account_is_live", lambda n: True)
    assert deps.verify_jwt_token(token) == "hr001"
