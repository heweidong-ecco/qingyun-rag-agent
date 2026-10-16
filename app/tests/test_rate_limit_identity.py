"""限流身份解析的回归测试（对应 B9-b · 2026-09-30）

背景（**实测，不是推断**）：
    `app/main.py` 的**限流中间件**原先用 `f"user:{x_api_key[:8]}"` 当身份 ——
    只取请求头里**前 8 个字符**，**不查库、不验签**。

    后果：**随便编一个 `X-API-Key`，就拿到一个全新的限流桶** ⇒ 反复换串 = 无限刷新。
    **但它只绕过【限流】，绕过不了【鉴权】** —— `app/routing/deps.py:35` 会真查库，
    无效 key 直接抛 `AUTH_EXPIRED`。所以影响面是"能白刷限流桶"，**不是"能拿数据"**。

    **同一个文件里的两条中间件原本不一致**：`QuotaMiddleware`（`main.py:211`）
    **验了**签，`RateLimitMiddleware`（`main.py:141`）**没验**。本测试钉的是后者。

**设计约束：本文件的测试【一条都不许碰数据库】。**
    CI 跑的是 `pytest app/ -m "not integration and not needs_db"` —— **那里没有 Postgres**。
    **2026-09-30 就是在这里栽的**：本文件初版有 2 条没 stub `verify_api_key`
    ⇒ 真去连库 ⇒ **CI 红（2 failed）**，而**本地因为 Postgres 开着照样绿** ——
    而当时本文件的 docstring 还写着「不需要 DB」。**那句是假的。**
    ⇒ 现在用下面的 `_no_db` fixture 把「碰了库」变成**明确的失败**，
    而不是一个看不懂的 `psycopg2.OperationalError`。
"""
import access.auth as auth
import pytest
from main import resolve_rate_limit_identity


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """把「本文件的测试碰了数据库」变成**一条明确的失败**。

    做法：把 `auth.get_db` 换成一个直接抛 `AssertionError` 的函数。
    于是任何**忘了 stub** 的测试会立刻报「不许碰数据库」，
    而不会退化成一个"看起来像环境问题"的 psycopg2 报错（那正是上次误判的形态）。

    ## 2026-10-09（B1）：**还多拦一处，否则这道守卫会【静默失效】**

    B1 之后 `permission.get_user_role` 也要查库了。它**不是**走 `auth.get_db`
    （那是 `access.auth` 自己的绑定），所以**上面那一行拦不到它**。

    **而它的失败方式比"红"更糟**：`get_user_role` **吞掉所有异常走回退**
    （这是有意的 —— 库抖了不该把全站打死，见 `permission.py`）
    **守卫不报错、库却真被碰了**，两边都看不出来。
    **实测**（2026-10-09）：把 `core.db.get_db` 换成抛 `AssertionError` 的替身后调
    `get_user_role("isolation_a")` ⇒ **它被调了 1 次**、异常被吞、静默返回 `free`。

    ⇒ 这里补两样，**都要**：
    1. 把 `permission._fetch_role_from_db`（**本模块唯一的 DB 入口**，收口在一处就是为这个）
       换成同款 `_boom`；
    2. `invalidate_role_cache` —— 不清缓存的话，结论会**随上一条用例的缓存状态**变
       （命中缓存 ⇒ 根本不查库 ⇒ 用例"过"了但**什么都没测**）。
    """
    def _boom(*args, **kwargs):
        raise AssertionError(
            "本文件的测试不许碰数据库 —— "
            "CI 跑的是 `-m 'not integration and not needs_db'`，那里没有 Postgres。"
            "需要验签的用例请 monkeypatch(auth, 'verify_api_key', ...)。"
        )
    monkeypatch.setattr(auth, "get_db", _boom, raising=False)

    # B1：`permission` 那条库访问路径（见 docstring）
    import access.permission as permission
    monkeypatch.setattr(permission, "_fetch_role_from_db", _boom, raising=False)
    permission.invalidate_role_cache()


def test_no_db_guard_actually_blocks():
    """**自证**：`_no_db` 真的会拦住碰库的代码 —— 否则它只是一句声称。

    没有这条，上一条 fixture 就只是"我写了"，**谁也没验过它到底拦不拦得住**。
    （本仓 `test_public_paths.py` 的 `test_would_have_caught_the_original_bug` 是同一写法。）
    """
    with pytest.raises(AssertionError, match="不许碰数据库"):
        auth.get_db()


def test_no_db_guard_也拦住_permission_那条路():
    """**自证（B1）**：`_no_db` 也拦得住 `permission` 查角色那条路。

    **为什么这条必须存在**：B1 让 `get_user_role` 也查库了，而它**不是**走 `auth.get_db`
    ⇒ 上面 fixture 里光有那一行**拦不到它**。而它的失败方式**比"红"更糟**：
    `get_user_role` 会**吞掉库异常走回退**（那是有意的）⇒ **守卫不报错、库却真被碰了**。
    2026-10-09 实测过这个形态：把 `core.db.get_db` 换成抛 `AssertionError` 的替身后调
    `get_user_role("isolation_a")` ⇒ **它被调了 1 次**、异常被吞、静默返回 `free`。

    ⇒ 这条同时钉两件事：
    ① fixture 里补的那一行**真的在拦**；
    ② `get_user_role` 的捕获范围是 **`psycopg2.Error` 而不是 `Exception`** ——
       写成宽捕获，`AssertionError` 会被它吞掉，**这条用例当场红**
       （本仓原话见 `app/access/auth.py:153`：「捕获写宽 ⇒ 那道门静默失效」）。
    """
    from access.permission import get_user_role

    with pytest.raises(AssertionError, match="不许碰数据库"):
        get_user_role("isolation_a")


# 一个形态逼真、但数据库里不存在的 key。
# 形态取自【本仓自己的】`auth.generate_api_key`：`"sk-" + uuid4.hex` ⇒ `sk-` + 32 位 hex。
#    别按 DashScope key 理解它 —— DashScope 的 body 是大小写混合字母数字，不是纯小写 hex。
FORGED = "sk-" + "a" * 32


def test_missing_key_and_no_jwt_is_anonymous():
    """两样都没有 ⇒ 落匿名桶。"""
    assert resolve_rate_limit_identity(None, None) == "anonymous"


def test_forged_api_key_does_not_get_its_own_bucket(monkeypatch):
    """本条就是 bug 本体：编造的 key **不得**拿到一个独立桶。

    改前：`f"user:{key[:8]}"` ⇒ 返回 `user:sk-aaaaa` ⇒ **独立桶** ⇒ 本断言失败。
    改后：验签不过 ⇒ 落 `anonymous` ⇒ 本断言通过。

    **必须 stub `verify_api_key`** —— 不然它会真去查库，而 CI 没有 Postgres。
    """
    monkeypatch.setattr(auth, "verify_api_key", lambda k: None)
    assert resolve_rate_limit_identity(FORGED, None) == "anonymous"


def test_two_different_forged_keys_land_in_the_same_bucket(monkeypatch):
    """**加粗的那条**：换串也刷不出新桶。

    只断言"伪造的 key 不等于它自己"，仍可能放过 `user:<前8位>` 这类**仍会分化**的写法。
    这里从**效果**上钉死：**两个不同的伪造 key，必须落【同一个】桶**。
    """
    monkeypatch.setattr(auth, "verify_api_key", lambda k: None)
    other = "sk-" + "b" * 32
    assert resolve_rate_limit_identity(FORGED, None) == \
           resolve_rate_limit_identity(other, None)


def test_valid_api_key_gets_a_bucket_of_its_own(monkeypatch):
    """反向守卫：**真的** key 仍要拿独立桶 —— 别修过头把正常路径也塞进匿名。

    只有一条"伪造的进匿名"的断言，**把整个分支删掉**也能通过 ⇒ 那样会悄悄退化成
    "所有带 key 的请求共用一个匿名桶"，是另一种坏法。这条防的就是它。
    """
    monkeypatch.setattr(auth, "verify_api_key", lambda k: "admin" if k == FORGED else None)
    assert resolve_rate_limit_identity(FORGED, None) == "user:admin"


def test_unverifiable_key_falls_back_to_anonymous_not_rejected(monkeypatch):
    """判定要**降级**，不是**拒绝** —— 因为「匿名还开着」。

    带了一个坏 key，只说明"它想显得像用户但没验过"；客户端**本来就可以不带 key**。
    ⇒ 拒绝会顺带把匿名入口一起关掉，那是**另一个决定**（B9 仍挂着），不该在这里顺手做掉。
    """
    monkeypatch.setattr(auth, "verify_api_key", lambda k: None)
    assert resolve_rate_limit_identity(FORGED, None) == "anonymous"


def test_jwt_path_still_works(monkeypatch):
    """JWT 那条分支不能被这次改动碰坏。"""
    import access.jwt_handler as jwt_handler
    monkeypatch.setattr(jwt_handler, "verify_access_token",
                        lambda t: "alice" if t == "good.jwt.token" else None)

    assert resolve_rate_limit_identity(None, "Bearer good.jwt.token") == "user:alice"
    assert resolve_rate_limit_identity(None, "Bearer forged.jwt") == "anonymous"


def test_api_key_takes_precedence_over_jwt(monkeypatch):
    """两条都带时，优先级不变：**先 API Key，后 JWT**（改前就是这么写的，别改掉）。"""
    monkeypatch.setattr(auth, "verify_api_key", lambda k: "admin" if k == FORGED else None)
    import access.jwt_handler as jwt_handler
    monkeypatch.setattr(jwt_handler, "verify_access_token", lambda t: "alice")

    assert resolve_rate_limit_identity(FORGED, "Bearer whatever") == "user:admin"
