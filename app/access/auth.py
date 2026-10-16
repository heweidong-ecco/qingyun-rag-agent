import uuid
import hmac
import hashlib
from datetime import datetime, timedelta

import psycopg2

from core.db import get_db
from core.exceptions import AppException, ErrorCode
from core.config import LOGIN_USER_NAME, LOGIN_PASSWORD, TEST_USER_PASSWORD

def generate_api_key() -> str:
    """生成一个API Key，格式：sk- + 随机字符串"""
    return "sk-" + uuid.uuid4().hex

def hash_api_key(api_key: str) -> str:
    """对API Key进行哈希，数据库中只存储哈希值"""
    return hashlib.sha256(api_key.encode()).hexdigest()


#: 「一把 key 能不能拿去认证」的 **SQL 侧唯一口径**（`is_active` 那一半）。
#:
#:    `verify_api_key`（按 key_hash 查一把）与 `ensure_admin_exists`（启动自检）
#:    **必须共用这一句** —— 别在两处各写一遍。
#:
#:    **2026-10-06 的缺陷就是这么来的**：自检只 `COUNT(*)` **行数**，不看 `is_active`
#:    ⇒ （按用户名撤，撤的是该用户名下的**全部行**）
#:    之后，自检照样报「管理员账户已存在，跳过自动创建」，而认证侧一把都过不去
#:    ⇒ **admin 锁死，而日志说一切正常**。裁定。
#:    守卫 ⇒ `app/tests/test_auth_ensure_admin_exists.py::test_两条路径的_is_active_口径是同一句`
#:
#: **必须 `COALESCE`** —— 迁移 `828721f77ef2` 建列时是 `nullable=True`，
#:    `is_active IS NULL` 的老行会被 `is_active = 1` **静默排除**（契约 D）。
ACTIVE_PREDICATE = "COALESCE(is_active, 1) = 1"


def _is_expired(expires_at, now=None) -> bool:
    """过期判据的**唯一口径**（`is_active` 之外的另一半）—— 同样两处共用。

    在 **Python 侧**比，不在 SQL 里写 `expires_at > NOW`：本仓 `expires_at` 存的是
       **naive 本地时间**（`create_user_api_key` 用 `datetime.now` 写进去的），
       而库里的 `NOW` 是 timestamptz（容器按 UTC）⇒ 在 SQL 里比会**差 8 小时**，
       正好是本仓踩过的「两套时区混在一个量里」那一族。
    """
    if isinstance(expires_at, str):
        # 🔴 2026-10-14（的甲 · `①b`）：**SQLite 那条路上 `expires_at` 回来是
        #    `str`，不是 `datetime`** —— 那一列的类型是 `TIMESTAMP`，而 SQLite 是**动态类型**，
        #    存进去的就是**一串文本** ⇒ 直接比会当场
        #    `TypeError: '<' not supported between instances of 'str' and 'datetime.datetime'`
        #    （实测撞到过：四个检索端点的隔离用例全红）。
        # ⚠️ **解出来仍是 naive 本地时间**（写进去的就是 `create_user_api_key` 的 `datetime.now`）
        #    ⇒ 与本函数上面那段「在 Python 侧比」的理由**逐字一致**，⛔ 别在这里顺手补时区。
        # 🔴 **解不出来 ⇒ 当成【已过期】**：这是鉴权路径，**fail-closed** 是唯一正确方向
        #    （⛔ 不是 fail-open 地放行）。
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return True
    return expires_at < (now if now is not None else datetime.now())


def create_user_api_key(user_name: str, expire_days: int = 30, role: str | None = None) -> str:
    """
    为新用户生成API Key并存入数据库。
    返回明文Key（只在生成时显示一次，之后无法找回）。

    ## `role`（2026-10-09 · B1）

    **默认 `None` ⇒ 写进库的是 `NULL`** —— 而 `get_user_role` 把 `NULL` 当
    「**还没人裁决**」⇒ **走回退**（探针身份 + `admin` 特判 + 其余 FREE）。
    **别给它一个默认值 `"free"`** —— 那会把"没写"与"写了 free"**变成同一件事**，
    而 B1 整套设计正是靠这条区分活着（见 `app/access/permission.py` 的 docstring）。

    传了值就**按传入的写**（不校验取值 —— 脏值的处置在**读侧**：
    `UserRole(raw)` 转不出来就回退，见 `permission.py`）。
    **写侧不做白名单**是**有意的**：多一处白名单 = 多一处会与 `UserRole` 分叉的事实源。
    """
    api_key = generate_api_key()
    hashed = hash_api_key(api_key)
    expires_at = datetime.now() + timedelta(days=expire_days)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_keys (user_name, key_hash, expires_at, role) "
                "VALUES (%s, %s, %s, %s)",
                (user_name, hashed, expires_at, role)
            )
            conn.commit()

    # **写侧必须主动失效缓存**（B1）—— 见 `permission.get_user_role`：
    #    它在**配额热路径**上带 60s 进程内缓存；不失效 ⇒ 改完角色**最多 60s 内还是旧的**，
    #    而那种"改了不生效"看起来像 bug，会把排查带到错的地方。
    #    **不给它包 `try/except`** —— `invalidate_role_cache` 是**纯内存**操作，没有可失败的理由；
    #    包一层宽捕获就是本仓最恨的「静默降级」（真坏了也不出声）。
    from access.permission import invalidate_role_cache   # 函数内 import：避免模块级循环依赖
    invalidate_role_cache(user_name)

    return api_key
# 创建管理员身份的代码
def ensure_admin_exists(logger=None):
    """
    应用启动时检查**管理员还有没有能用的凭据**。

    三种情形（第 3 种是 2026-10-06 补的 —— 此前它会被**误当成第 1 种**）：

    | # | 库里的状态 | 行为 |
    |---|---|---|
    | 1 | 名下有**能用**的 key | 静默跳过（`info`）—— 既有行为，一个字没改 |
    | 2 | 名下**一行都没有** | 首次启动：自动建一把、打印明文（仅此一次） |
    | 3 | **有行，但没有一行能用** | **不自动补发**，只打**响亮告警**（`error`） |

    **为什么第 3 种不能自动补发**： 是
       **按用户名撤**的，撤的是该用户名下的**全部行**。若这里自动补发，
       操作员那次「撤销」就会被**下一次重启静默还原**，而且新 key 的**明文会打进日志**
       （正是我们花力气清掉的那类残渣）。⇒ **决定权留给人**，告警里给出可照做的命令。

    「能用」= `ACTIVE_PREDICATE` **且**未过期 —— 与 `verify_api_key` **同一口径**
       （别再写成只数行数：那正是  修掉的那条缺陷）。

    **本函数必须在 `create_table` 之后调用**（`app/main.py` 的 startup 顺序）：
       它要读的 `is_active` 列在**老库**里是靠 `create_table` 里那句 `ALTER`
       补上的⇒ 顺序反了就是 `UndefinedColumn`，**应用直接起不来**。
       守卫 ⇒ `app/tests/test_auth_ensure_admin_exists.py::test_自检在建表之后调用`
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM api_keys WHERE user_name = 'admin'")
            total = cur.fetchone()[0]
            # 「能用」那半句 —— 必须与 verify_api_key 同一句谓词（见 ACTIVE_PREDICATE）
            cur.execute(
                "SELECT expires_at FROM api_keys "
                f"WHERE user_name = 'admin' AND {ACTIVE_PREDICATE}"
            )
            usable = sum(1 for (expires_at,) in cur.fetchall() if not _is_expired(expires_at))

    if usable > 0:
        if logger:
            logger.info("管理员账户已存在，跳过自动创建")
        return None

    if total > 0:
        # 有行、但一把都用不了（全被撤销 / 全过期）⇒ 响，但不替操作员做决定
        if logger:
            logger.error("=" * 60)
            logger.error(
                "🔴 管理员账户【存在，但一把能用的 key 都没有】：名下一共 %d 行，"
                "没有一行能通过认证（被 --revoke 撤销，或已过期）。", total
            )
            logger.error("⇒ 不会自动补发：那会把你的撤销决定静默还原，并把新 key 明文打进日志。")
            logger.error("⇒ 要恢复，请显式发一把（明文只显示这一次，接着存好）：")
            logger.error("     admin --days 3650")
            logger.error("=" * 60)
        return None

    # 创建管理员，有效期10年
    api_key = create_user_api_key("admin", expire_days=3650)

    if logger:
        logger.warning("=" * 60)
        logger.warning("首次启动：已自动创建管理员账户")
        logger.warning("管理员用户名: admin")  # 2026-09-20 去掉多余的 f（D1/pyflakes：f-string 无占位符）
        logger.warning(f"管理员 API Key: {api_key}")
        logger.warning("请立即复制并安全保存此 Key，它仅显示这一次！")
        logger.warning("=" * 60)

    return api_key

def verify_api_key(api_key: str):
    """
    验证API Key是否有效。
    返回该Key对应的user_name，如果无效则返回None。

    2026-10-05（待办）：**库不可用 ⇒ 抛 `AppException(SERVICE_UNAVAILABLE)`**，
       **不是返回 `None`**。

       * 返回 `None` 会让调用方（`deps.get_current_user`）报 **401** ——
         那等于**替用户断言「你的 key 坏了」**：他会去换一把**没问题的** key，
         然后照样连不上，而且**永远查不到原因**。
       * 改前是裸的 `with get_db` ⇒ 库一抖就是**非结构化的 500**。

       ⇒ 与 WS 侧**已裁**的 **1008 / 1011** 同一条口径（`app/routing/specs/deps.md` 的 表）：
         **凭据不行 ⇒ 换 key；认证服务不行 ⇒ 重试、别换 key。**
         HTTP 侧的对应值就是 **503 `SERVICE_UNAVAILABLE`**
         （`app/core/exceptions.py` 里**早就有这个码，此前从未被用过**）。
        原文写的是「500 **而不是 401**」—— **两个都不是答案**，理由见上。

    捕获范围 = **`psycopg2.Error`**（连接被拒、池耗尽 `PoolError` 都是它的子类），
       **不是 `except Exception`** —— 写成宽捕获会把**代码 bug** 伪装成"库挂了"，
       而且会**吞掉** `app/tests/test_rate_limit_identity.py` 的 `_no_db` 守卫
       （它靠抛 `AssertionError` 抓"谁碰了库"）⇒ **那道门静默失效**。
       守卫：`app/tests/test_auth_db_unavailable.py::test_非数据库异常必须照样冒泡`。
    """
    hashed = hash_api_key(api_key)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    #  契约 D：`is_active` 此前是**死列**（全仓没有一处读它）
                    #    ⇒ 发出去的 key 收不回来。
                    # **必须 `COALESCE(is_active, 1)`，不许写 `is_active = 1`** ——
                    #    迁移 `828721f77ef2` 建列时是 `nullable=True`，NULL 行会被**静默排除**
                    #    ⇒ 老 key 集体失效，而用户只看到一句"凭据无效"（查不到点上）。
                    #    "现网有没有 NULL 行"**没验过**（当时库没起）—— 正因为没验过才必须防。
                    #    守卫 ⇒ `app/tests/test_auth_api_key_active.py::test_a_null_is_active_row_still_authenticates`
                    # 2026-10-06：那半句抽成了 `ACTIVE_PREDICATE`，
                    #    因为 `ensure_admin_exists` 也必须用它 —— 两处各写一遍正是缺陷成因。
                    "SELECT user_name, expires_at FROM api_keys "
                    f"WHERE key_hash = %s AND {ACTIVE_PREDICATE}",
                    (hashed,)
                )
                row = cur.fetchone()
    except psycopg2.Error as exc:
        raise AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            f"认证服务不可用（数据库连接失败），请稍后重试。原因：{type(exc).__name__}",
        ) from exc

    if row is None:
        return None

    user_name, expires_at = row
    # 过期判据同样只此一处—— 别在这里另写一遍比较
    if _is_expired(expires_at):
        return None

    return user_name


# 登录凭据来自环境变量 —— 2026-09-15 从本文件的硬编码字面量迁出
# （那曾是**公开仓库上的活凭据**；决策）
#
# 每次调用都重新读环境变量（刻意不缓存到模块级）：这样测试可以 monkeypatch 干预。
def _get_users_db() -> dict:
    """从环境变量构造登录凭据表。

    - `LOGIN_USER_NAME` / `LOGIN_PASSWORD`：主账号。`LOGIN_PASSWORD` 缺失时应用根本起不来
      —— `config.validate_config()` 会在 startup 阶段抛 EnvironmentError。
    - `TEST_USER_PASSWORD`：**可选**。未设则该账号不存在 —— fail-closed，不留任何默认口令。
    """
    db = {}
    if LOGIN_PASSWORD:
        db[LOGIN_USER_NAME] = LOGIN_PASSWORD
    if TEST_USER_PASSWORD:
        db["test_user"] = TEST_USER_PASSWORD
    return db


def authenticate_user(user_name: str, password: str) -> bool:
    """验证用户名和密码。**两条来源，⛔ 顺序不能反**。

    ## 一 · 环境变量（`_get_users_db`）—— 明文比对

    它保住三件事：**本地开发** · **CI**（`conftest` 拿 `LOGIN_PASSWORD` 登）·
    **「第一个管理员从哪来」那条引导路径**（库里一个账号都还没有时，只能靠它）。

    ## 二 · 库（`access/login_users.py`）—— **口令是哈希的**

    加号 / 撤销 / 列账号 ⇒ `app/manage_users.py`。

    ## 🔴 为什么「环境变量优先」是有意的，⛔ 不是随手写的

    反过来（库优先）的后果：**谁能在库里插一行 `admin`，谁就把管理员口令改了** ——
    而插一行只需要一个写库的口子，**比改 `.env` 容易得多**。
    ⇒ 环境变量那两个名字**永远**走不可被库顶掉的那条路。
    """
    # ⚠️ 用 `compare_digest`（常数时间），⛔ 不是 `==`。
    #    两个都 `.encode()`：`compare_digest` **遇非 ASCII 串会直接抛 TypeError**
    #    ⇒ 中文/emoji 口令在"明文比对"这条路上**当场 500**（而这正是本仓最容易踩的那族）。
    env_db = _get_users_db()
    if user_name in env_db:
        return hmac.compare_digest(env_db[user_name].encode("utf-8"),
                                   password.encode("utf-8"))

    from access.login_users import authenticate_from_db   # 函数内 import：与本文件既有写法一致
    return authenticate_from_db(user_name, password)


def login_account_is_live(user_name: str) -> bool:
    """**【登录之后】每次校验都要过的那道门** —— 这个用户名现在还能不能用（§二·⑥）。

    ## 它解决什么

    JWT 是**无状态**的：`routing/deps.py::verify_jwt_token` 只验**签名与过期**，⛔ 不查库
    ⇒ **撤销一个账号，他手里的令牌在过期前一直有效**（access 15 分钟，**refresh 7 天**）
    ⇒ 「撤销」会变成"**下次登录才生效**"。本函数把那个窗口关掉。

    ## 🔴 环境变量的账号**永远放行**（⛔ 这不是漏洞，是必须的）

    `admin` / `test_user` **在 `login_users` 表里【没有行】**（它们由 `.env` 管）
    ⇒ `_get_users_db()` 里有的名字**直接放行**，由 `.env` 那条路负责。

    ## 🔴 「查不到 / 读不到」**不拦** —— 这条是**试出来的**

    第一版写成 fail-closed，**全量当场 12 failed**（用例大量自签 JWT、名字不在库里）
    ⇒ 而 **CI 压根没有 Postgres**，那会**每次全红**。
    完整口径与代价 ⇒ `login_users.login_account_blocked` 的 docstring（**⛔ 改之前先读它**）。
    """
    if user_name in _get_users_db():
        return True
    from access.login_users import login_account_blocked
    return not login_account_blocked(user_name)