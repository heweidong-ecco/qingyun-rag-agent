"""· 登录账号入库 —— **真库往返**（带 `needs_db` · 不进 CI）。
"""
import uuid

import pytest

from access import login_users as LU
from core.db import get_db

pytestmark = pytest.mark.needs_db


def _cleanup(user_name: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM login_users WHERE user_name = %s", (user_name,))


@pytest.fixture
def name():
    n = "t_" + uuid.uuid4().hex[:12]
    try:
        yield n
    finally:
        _cleanup(n)


def test_建号之后_能用口令登进来(name):
    LU.add_user(name, "pw-A")
    assert LU.authenticate_from_db(name, "pw-A") is True
    assert LU.authenticate_from_db(name, "pw-B") is False


def test_改口令之后_新口令能过_老口令不能(name):
    LU.add_user(name, "pw-A")
    LU.add_user(name, "pw-B")          # 同名 ⇒ 幂等改口令
    assert LU.authenticate_from_db(name, "pw-B") is True
    assert LU.authenticate_from_db(name, "pw-A") is False


def test_停用之后登不进_启用之后又能(name):
    LU.add_user(name, "pw")
    assert LU.set_active(name, False) == 1
    assert LU.authenticate_from_db(name, "pw") is False
    assert LU.set_active(name, True) == 1
    assert LU.authenticate_from_db(name, "pw") is True


def test_停用改不存在的账号_回_0(name):
    assert LU.set_active(name + "_nope", False) == 0


def test_列出来的账里没有口令哈希(name):
    LU.add_user(name, "pw")
    rows = LU.list_users()
    hit = [r for r in rows if r["user_name"] == name]
    assert len(hit) == 1, "刚建的号必须能在 list 里看到"
    assert "password_hash" not in hit[0], (
        "🔴 口令哈希没有任何理由离开库 —— 它进了这一层就可能被打进日志/响应"
    )
    assert hit[0]["is_active"] is True


def test_中文口令端到端也能登(name):
    """⚠️ 非 ASCII 走的是**哈希**那条路，⛔ 不经 `compare_digest(str)` —— 两边都要验。"""
    LU.add_user(name, "口令带中文🔐")
    assert LU.authenticate_from_db(name, "口令带中文🔐") is True
    assert LU.authenticate_from_db(name, "口令带中文") is False


def test_设了到期的号_过期当天就登不进(name):
    """⚠️ 用 `--days 0` 这种边界：`expires_at = now + 0 天` ⇒ **立刻过期**。"""
    LU.add_user(name, "pw", expire_days=0)
    assert LU.authenticate_from_db(name, "pw") is False
