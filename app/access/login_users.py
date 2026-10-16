"""登录账号的**【库】存储** —— 用户名 + 口令哈希。
"""
import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta

from core.db import get_db

#: `pbkdf2_hmac` 的轮数。**2026 年的本机实测**：20 万轮 ≈ 60 ms ——
#: 对"登录"这个动作是**无感**的（它一次请求只跑一次），而对**离线爆破**是**实打实的成本**。
#: ⚠️ 轮数**写进哈希串**（见 `_FORMAT`）⇒ **以后调大它不会让老口令失效**。
PBKDF2_ITERATIONS = 200_000

_ALGO = "pbkdf2_sha256"
_SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """把一个口令变成可入库的串：`pbkdf2_sha256$轮数$盐$哈希`（**三段都用 hex**）。

    ⚠️ **轮数与盐都写进串里** —— 校验时**从串里读**，⛔ 不是读模块常量。
    理由：否则将来把常量调大，**库里所有老口令会一起失效**（而且不报错，只是登不进）。
    """
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "%s$%d$%s$%s" % (_ALGO, iterations, salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    """校验口令。⛔ 用 `compare_digest`（常数时间）—— 别写成 `==`。

    ⚠️ **格式不认识 ⇒ 返回 `False`**（fail-closed），⛔ 不抛、⛔ 不"宽松放行"：
    一个坏行**只该让那一个账号登不进**，不该把门开开。
    """
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if algo != _ALGO:
        return False
    try:
        iterations = int(iters)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def authenticate_from_db(user_name: str, password: str) -> bool:
    """按【库】校验一个账号。**库出任何问题都返回 `False`**（见模块 docstring 边界 3）。

    「能用」的判据与 `api_keys` 那侧**同源**：`is_active` **且** 未过期 ——
    ⚠️ 但这里**不复用** `auth.ACTIVE_PREDICATE`：那一句写死了 `COALESCE(is_active, 1) = 1`
    的语义（"NULL 当启用"，为的是兼容 `api_keys` 那批老行）；
    而本表是**新表、列非空**，`NULL` 在这里只可能是坏数据 ⇒ **两种语义，别合并**。
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT password_hash, is_active, expires_at FROM login_users "
                    "WHERE user_name = %s",
                    (user_name,),
                )
                row = cur.fetchone()
    except Exception:                     # 库挂了 / 表还没建 ⇒ fail-closed
        return False
    if not row:
        return False
    password_hash, is_active, expires_at = row
    if not is_active:
        return False
    if expires_at is not None:
        if datetime.now() > expires_at:
            return False
    return verify_password(password, password_hash)


# ==================== 「这个账号现在还能用吗」—— 撤销为什么能立即生效 ====================

#: 「账号现在还活着吗」的**进程内缓存 TTL（秒）**。
#:
#: 🔴 **为什么必须有缓存**：下面那个 `is_login_user_active` 在**每一个带 JWT 的请求**上跑
#:    ⇒ 不缓存就是**每请求一次打库**（与 `permission.get_user_role` 同一个理由）。
_ACTIVE_CACHE_TTL_SECONDS = 30

#: {user_name: (检查时刻, 是否活着)}
_active_cache: dict[str, tuple[float, bool]] = {}


def invalidate_active_cache(user_name: str | None = None) -> None:
    """清缓存。⚠️ 只对本进程有效 —— 跨进程（`manage_users.py`）靠 TTL 兜。"""
    if user_name is None:
        _active_cache.clear()
    else:
        _active_cache.pop(user_name, None)


def login_account_blocked(user_name: str) -> bool:
    """**这个用户名该不该被【拦住】** —— 返回 `True` = 拦。

    ## 它解决什么

    JWT 是**无状态**的 —— `routing/deps.py::verify_jwt_token` 只验**签名与过期**，⛔ **不查库**
    ⇒ **撤销一个账号，他手里的令牌在过期前一直有效**（access 15 分钟，**refresh 7 天**）
    ⇒ 「撤销」会退化成"**下次登录才生效**"。
    ⇒ 这里把那个窗口关掉。**只有一种情况返回 `True`**：

    > **库里【查到了这一行】，而它说"停用"或"已过期"。**

    ## 🔴 为什么「查不到 = 不拦」—— 这条是**试出来的，不是拍出来的**

    本 Agent 第一版写成 **fail-closed**（查不到 / 库读不到 ⇒ 一律拦），
    结果**全量从 1136 passed 变成 12 failed**：本仓的用例大量**自签 JWT**
    （`create_access_token(f"m6-{uuid4()}")`）来模拟登录，那些名字**根本不在库里**
    ⇒ 全线 **401**。**而 CI 与离线批次压根没有 Postgres** ⇒ 那会是**每次 CI 全红**。

    ⇒ 正确口径是把"**无法判定**"与"**判定为不能用**"分开：

    | 情形 | 判 |
    |---|---|
    | 库里查到行，且 `is_active` 为假 / 已过期 | **拦** ✅ ← **唯一真正要防的那一种**（真实用户的账号被撤） |
    | 库里**没有这一行** | **不拦** —— 它本来就不归本表管（`admin` / `test_user` 在 `.env` 里；测试自签的名字什么都不是） |
    | 库读不到 / 表还没建 | **不拦**（**无法判定**）⚠️ 见下 |

    ⚠️ **第三条是 fail-open，代价说到底**：库不可用期间，**已撤销的人仍可能进得来**。
    但那时**应用本来就什么都干不了**（每个页面都在打 API，全 500）
    ⇒ 那个窗口的真实效果是"**拿到 500 而不是 401**"，不是"能正常使用"。
    📌 **而它的收益是硬的**：CI（无 PG）与全部离线用例**照常跑**。

    🔴 **⛔ 别把"没有这一行"改成"拦"** —— 那会立刻把 CI 与大批用例一起打死，
    而**换来的保护是零**：能自签 JWT 的人**已经拿到了 `JWT_SECRET_KEY`**，
    那个秘密本身才是边界（拿到它的人本来就能冒充任何人）。
    """
    now = time.monotonic()
    hit = _active_cache.get(user_name)
    if hit and now - hit[0] < _ACTIVE_CACHE_TTL_SECONDS:
        return hit[1]

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_active, expires_at FROM login_users WHERE user_name = %s",
                    (user_name,),
                )
                row = cur.fetchone()
    except Exception:                     # 库挂了 / 表还没建 ⇒ 无法判定 ⇒ 不拦
        _active_cache[user_name] = (now, False)
        return False

    blocked = False
    if row:                               # ⚠️ 只有"这一行存在"才有资格判
        is_active, expires_at = row
        blocked = (not is_active) or (expires_at is not None and datetime.now() > expires_at)
    _active_cache[user_name] = (now, blocked)
    return blocked


# ==================== 运维动作（`app/manage_users.py` 用的就是这几个）====================


def add_user(user_name: str, password: str, expire_days: int | None = None) -> None:
    """建号；**已存在则改口令**（幂等 —— 重跑一次不会报错）。"""
    expires_at = None
    if expire_days is not None:
        expires_at = datetime.now() + timedelta(days=expire_days)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO login_users (user_name, password_hash, is_active, expires_at)
                VALUES (%s, %s, TRUE, %s)
                ON CONFLICT (user_name) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        is_active     = TRUE,
                        expires_at    = EXCLUDED.expires_at
                """,
                (user_name, hash_password(password), expires_at),
            )
    invalidate_active_cache(user_name)    # 本进程内立即生效（跨进程靠 TTL，见 _ACTIVE_CACHE_TTL_SECONDS）


def set_active(user_name: str, active: bool) -> int:
    """启用 / 停用（**不删行** —— 删了就查不出"他曾经存在过"）。返回改了几行。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE login_users SET is_active = %s WHERE user_name = %s",
                (active, user_name),
            )
            n = cur.rowcount
    invalidate_active_cache(user_name)
    return n


def list_users() -> list[dict]:
    """列全部账号。⚠️ **⛔ 不含 `password_hash`** —— 它没有任何理由离开库。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_name, is_active, created_at, expires_at "
                "FROM login_users ORDER BY created_at, user_name"
            )
            return [
                {"user_name": r[0], "is_active": r[1], "created_at": r[2], "expires_at": r[3]}
                for r in cur.fetchall()
            ]
