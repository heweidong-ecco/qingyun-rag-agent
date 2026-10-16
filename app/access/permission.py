import time
from enum import Enum

import psycopg2

class UserRole(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    ADMIN = "admin"


# 2026-10-03 删（决策一落地 ·）：
#    此处原有 `ROLE_QUOTA = {FREE: 100, PREMIUM: 10000, ADMIN: inf}` 与 `get_user_quota()`。
#    那是**一套独立的「每日请求次数」配额**，与 `token_config` 的 **token 日额度**（token）
#    **互不知情** ——  实测两者口径**差 35 倍**（`plan_execute` 一次 ~3346 token
#    ⇒ 按次数能跑 100 次、按 token 只能跑 ~3 次）。
#    ⇒ 需求方裁「**统一到 token 一套**」⇒ 次数那套**整张表连入口一起删**。
#    配额表在 `token_config` 的**两张池表**（`LLM_DAILY_TOKEN` / `EMBEDDING_DAILY_TOKEN`）；
#       判定在 `token_tracker`，全路径那层挂在 `main.QuotaMiddleware` 上（= `R1.3`）。
#       **2026-10-11 更正**：本行原写「`token_config.ROLE_DAILY_TOKEN`」——
#          那个名字**当天已删**（日额度按池拆成两张表）⇒ 原来那句是**过期指路牌**。
#    注意： 原写「降级为**接口权重**、**不删**」—— **该条已被  推翻**。


# 2026-10-03 · **隔离探针身份**（§七 裁决 1 · 决策 2）
#
# 需求方 2026-10-03 原话：「**在对应代码位置注释或者写文档记录这几个用户身份**」——
# 就是下面这三个。**它们是【测试身份】，不是业务角色。**
#
#   · `isolation_a` / `isolation_b` —— **同角色**那一档（都是 FREE）互不串
#   · `isolation_c`                —— **跨角色**那一档（PREMIUM vs FREE）互不串
#   · `test_user`                  —— 历史沿用，与 `isolation_c` 同属 PREMIUM 探针
#
# 用途：验证多用户资源隔离，用例在 `app/tests/test_isolation.py`。
# 身份记录（需求方要的"留档"）= **本注释** + 本仓的决策记录（⛔ 不随产品仓提供），
# **不另开文件**（决策 2）。
#
# `isolation_a` / `isolation_b` **不在这里列** —— 它们走默认分支拿 FREE。
#    用例里有反向守卫钉住这一点（别把默认分支改成 PREMIUM）。
#
# **别删**（删了  §六 的判据跑不起来）；**别当成真业务角色**（它们没有业务含义）。
# 这张表的正式出路是**接 DB**（§遗留 4），届时要**连这段注释一起搬**。
_PREMIUM_PROBE_USERS = {"test_user", "isolation_c"}


# ═══════════════════════════════════════════════════════════════════════
# B1 · 角色接 DB（2026-10-09）
# ═══════════════════════════════════════════════════════════════════════

#: 进程内缓存活多久。**别改成 `@lru_cache`**（那是永不过期）——
#: 改了角色要么重启进程、要么"改了不生效且没人知道"，而后者正是本仓最恨的形态。
ROLE_CACHE_TTL_SECONDS = 60

#: `{user_name: (UserRole, 到期时刻)}` —— 用 `monotonic` 而不是墙钟（墙钟会跳）。
_role_cache: dict[str, tuple[UserRole, float]] = {}


def _now() -> float:
    """**包一层**是为了能被测试拨快 —— 别让测试去 patch `time.monotonic`
    （那是**同一个 `time` 模块对象**，patch 它会**波及整个进程**，连 pytest 自己都用它）。"""
    return time.monotonic()


def _hardcoded_role(user_name: str) -> UserRole:
    """**回退**用的那一套 —— 也就是 B1 之前 `get_user_role` 的**全部**逻辑。

    **别在 `get_user_role` 里再写一遍** —— 那样"回退规则"就有**两处事实源**了。
    """
    if user_name == "admin":
        return UserRole.ADMIN
    if user_name in _PREMIUM_PROBE_USERS:
        return UserRole.PREMIUM
    return UserRole.FREE


def _fetch_role_from_db(user_name: str) -> str | None:
    """**本模块唯一的 DB 访问入口**（收口在一处是有意的，两条理由）：

    1. 测试可以**整体替换**它 ⇒ 决策逻辑（缓存 / 回退 / 脏值）**不必连一个真库**就能测；
    2. `app/tests/test_rate_limit_identity.py::_no_db` 那道「**本文件的测试不许碰数据库**」
       守卫**只需替换这同一个名字**，就能把本模块的库访问**全部**拦下来
       —— 否则那道守卫会**静默失效**（它现在只 patch `auth.get_db`，
       而 `permission` 若从别处拿连接，守卫看不）。

    **异常一律往上抛**（不在这里吞）—— 回退是 `get_user_role` 的职责，不是这里的。
    返回 `None` = 该用户名下**没有激活的 key 行**（与"查到了但值是 NULL"是一个意思）。
    """
    from core.db import get_db          # 函数内 import：与本仓"导入期不建连接"的口径一致

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM api_keys "
                "WHERE user_name = %s AND COALESCE(is_active, 1) = 1 "
                "ORDER BY created_at DESC LIMIT 1",
                (user_name,),
            )
            row = cur.fetchone()
    return row[0] if row else None


def invalidate_role_cache(user_name: str | None = None) -> None:
    """**写侧改完角色必须调它** —— 没有它，改完最多要等 `ROLE_CACHE_TTL_SECONDS` 秒才生效。

    * `invalidate_role_cache("alice")` ⇒ 只失效 alice
    * `invalidate_role_cache`         ⇒ 清空（测试用；生产别这么干）

    **多进程**：本机/容器多 worker 时，这里只失效**当前进程**那份
    ⇒ 其余 worker 最多再等一个 TTL。这是**有意的取舍**，
    别为它引入 Redis 广播 —— 那会把一次内存读换成一次网络往返，而 B1 要的正好相反。
    """
    if user_name is None:
        _role_cache.clear()
    else:
        _role_cache.pop(user_name, None)


def get_user_role(user_name: str) -> UserRole:
    """根据用户名获取角色。**权威 = `api_keys.role`**（2026-10-09 · B1）。

    ## 三层

    1. **`api_keys.role`（可空）是权威** —— 查到什么就是什么，**连 `admin` 也一样**
       （那正是"接 DB"的意思）。
    2. **进程内缓存 `ROLE_CACHE_TTL_SECONDS` 秒** —— 本函数在**配额热路径**上
       （`token_tracker.get_user_token_budget`），且一个请求最多调 **10 次**
       （`api_v1_agent` 7 处 admin 判定 + 配额 3 处）⇒ **不缓存就是每请求最多 10 次同一条查询**。
    3. **回退** ⇒ `_hardcoded_role` —— DB **没写**（`NULL`）/ **脏值** / **库挂了**，三样都走它。

    ## 为什么"`NULL` ⇒ 回退"而不是"`NULL` ⇒ FREE"

    那一列是**后加的**（`role TEXT`，可空）⇒ 加列之前写进去的老行**都是 `NULL`**。
    **"没写"与"写了 free"不是一回事**：前者是"还没人裁决"，后者是"裁决过，就是 free"。
    ⇒ 回退那套里带着**探针身份**与 **`admin` 特判**，`app/tests/test_isolation.py` 的
      **跨角色隔离**用例正是靠它（`isolation_c`=PREMIUM / `isolation_a`,`b`=FREE）。
    **别把回退改成"默认 PREMIUM"** ⇒ `app/tests/test_user_role_from_db.py` 有反向守卫钉着。

    ## 库挂了为什么**回退**而不是抛（与 `resolve_ws_identity` 的 fail-closed **故意相反**）

    那边是**安全边界**（放错了代价更大）；这边**只是配额分档** ——
    库一抖就把全站打死，代价更大。**别"统一"掉这两条**。

    ## 缓存与 monkeypatch（原 `permission.md` §6 那条担心的，实测结论）

    原文写着「**一旦缓存，`monkeypatch` 改角色对已经调过的代码路径就失效**」——
    **这条担心是真的**，所以：① TTL 只 **60 秒**（不是 `lru_cache` 那种永不过期）；
    ② 写侧改角色后**必须**调 `invalidate_role_cache(user)` **主动失效**。
    实测结论 ⇒ `app/access/specs/permission.md` §6。

    **`isolation_c` 是【测试身份】**（§七 裁决 1），加它是为了能测「跨角色」那一档的隔离。
    别把它读成"premium 用户的代表" —— 它没有业务含义。
    """
    now = _now()
    hit = _role_cache.get(user_name)
    if hit is not None and hit[1] > now:
        return hit[0]

    role = _hardcoded_role(user_name)          # ← 先按回退那套定一个
    try:
        raw = _fetch_role_from_db(user_name)
        if raw:
            try:
                role = UserRole(raw)
            except ValueError:
                role = _hardcoded_role(user_name)   # ← 脏值 ⇒ 回退
    except psycopg2.Error:
        # **捕获范围 = `psycopg2.Error`**（连接被拒、池耗尽 `PoolError` 都是它的子类），
        #    **不是 `except Exception`** —— 见 `app/access/auth.py:153` 那条**同款**说明：
        #    宽捕获会把**代码 bug**（SQL 打错、`TypeError`）伪装成"库挂了"，
        #    于是**真 bug 永远查不出来**（本仓原话：「静默降级比报错更危险」）。
        #    而它还有一个**具体**的副作用：`_no_db` 守卫靠抛 `AssertionError` 抓"谁碰了库"，
        #    捕获写宽了 ⇒ **那道守卫静默失效**（2026-10-09 实测过这个形态）。
        role = _hardcoded_role(user_name)  # ← 库挂了 ⇒ 回退（不抛）

    _role_cache[user_name] = (role, now + ROLE_CACHE_TTL_SECONDS)
    return role
