# `app/access/permission.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **已接 DB**（2026-10-09 · B1）—— **`api_keys.role` 为权威**，带 **60s 进程内缓存** + **写侧主动失效**；查不到/脏值/库挂了**回退**到下面那套硬编码 |
| **对外提供** | `UserRole`（`str, Enum`：`FREE` / `PREMIUM` / `ADMIN`）· `get_user_role(user_name) -> UserRole` · 🆕 **`invalidate_role_cache(user_name=None)`**（**写侧改完角色必须调它**） |
| **谁在用** | **11 处**：`deps.py:76`（`require_admin`）· `api_v1.py:244` · `api_v1_rag.py:383` · `api_v1_agent.py` **7 处**（admin 判定）· `token_tracker.py:513`（`get_user_token_budget` —— 🔴 **配额热路径**，一个请求最多调 10 次） |
| **测试** | 🆕 `app/tests/test_user_role_from_db.py`（**19 条，本模块自己的用例**，⭐ 含反向守卫与"非数据库异常必须冒泡"）· `test_isolation.py` · `test_pending_visibility.py` · `test_approve_ownership.py` · `test_trace_isolation.py` · `test_quota_middleware.py` · `test_token_config.py` · `test_rate_limit_identity.py`（`_no_db` 守卫也拦本模块 —— 见那里 fixture 的说明） |

## ✅ 做了什么

**三角色判定 + 测试身份表 + 三层取值**：

```python
def get_user_role(user_name: str) -> UserRole:
    # ① 缓存（TTL 60s）—— 热路径在这里挡住，⛔ 不查库
    # ② 查 api_keys.role（经唯一入口 _fetch_role_from_db）
    # ③ 查不到 / 脏值 / 库挂了 ⇒ 回退到下面这套（= B1 之前的全部逻辑，⛔ 只有一份）
    if user_name == "admin":
        return UserRole.ADMIN
    if user_name in _PREMIUM_PROBE_USERS:
        return UserRole.PREMIUM
    return UserRole.FREE
```

**它是「多用户隔离」的判据来源之一**—— `ADMIN` 是**跨用户可见**的那个例外，
所以 11 个调用点里**大半是**「本人 or admin」这种归属校验的**后半句**。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| ✅ ~~角色表没接 DB~~ | **2026-10-09 已接**（B1）—— `api_keys.role` 为权威 + 60s 缓存 + 写侧失效。⚠️ **旧文的挂起理由与落点已作废，⛔ 别照旧读**： 🅱️「后端先行」**早已满足**； 写的 /`R1.3` **`R1.3` 当天就随  落地了** |
| ✅ ~~没有"改角色"的入口~~ | **2026-10-09 已有两处**： · `POST /admin/create_user` 的 `role` 字段。⚠️ **⚠️ 没有"改已有用户的角色"入口** —— 两个入口都是**发新 key 时**写；改现有用户的角色**目前只能再发一把** |
| ⚠️ **没有"不存在的用户"这一档** | 任何没被特判的字符串**都是 FREE**，包括拼错的用户名 ⇒ 见 §⚠️ 第 1 条 |
| ⚠️ **脏值只在读侧兜** | 写侧**⛔ 不校验**取值（`role` 是 TEXT）⇒ `role='胡说八道'` 能写进去，读侧靠 `UserRole(raw)` 转不出来 ⇒ **回退**。⛔ **这是有意的**：多一处白名单 = 多一处会与 `UserRole` 分叉的事实源 |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🔴 **未知用户名 ⇒ `FREE`，⛔ 不是报错** —— 这是 fail-**open**，刻意的

函数**不查库、不抛异常**：任何没被特判的名字都拿 `FREE`。
⇒ **拼错一个用户名不会失败，只会降级成 free** —— 而 `FREE` 是有额度上限的，
所以症状是**"这个人莫名其妙被限额了"**，⛔ **不是**一条报错。

⚠️ 与 `app/routing/deps.py` 的鉴权**方向相反**：那里未知凭据是 **fail-closed**（401）。
⇒ **别把两处的取向记混**：**"你是谁"必须 fail-closed；"你是什么档"允许 fail-open。**

### 2. 🔴 `isolation_a` / `isolation_b` **不在本文件的表里** —— ⛔ 别去补

它们**故意不列**：走默认分支拿 `FREE`。那正是「**同角色**那一档」的测法
（两个都是 FREE 的账号，互相看不见对方的东西）。

🔒 **用例里有反向守卫钉住这一点** —— ⛔ **别把默认分支改成 `PREMIUM`**：那会让"同角色隔离"那个用例
**静默失去意义**（两边都变 PREMIUM，测的还是同角色 —— 但已经不是它想测的那一档了）。

### 3. 🔴 `isolation_c` / `test_user` 是**【测试身份】，⛔ 不是业务角色**

作者 2026-10-03 原话：「**在对应代码位置注释或者写文档记录这几个用户身份**」——就是下面这三个：

| 身份 | 属于哪一档 |
|---|---|
| `isolation_a` / `isolation_b` | **同角色**（都是 FREE）互不串 |
| `isolation_c` | **跨角色**（PREMIUM vs FREE）互不串 |
| `test_user` | 历史沿用，与 `isolation_c` 同属 PREMIUM 探针 |

⛔ **别把 `isolation_c` 读成"premium 用户的代表"** —— 它**没有业务含义**，只是为了让"跨角色"那一档**测得出来**。
⛔ **也别删它**（删了  §六 的判据**跑不起来**）。

> 📌 **"留档"这件事本身**（作者要的）= **本注释** +，⛔ **不另开文件**（决策 2）。

### 4. 🔴 那张**次数配额表已删** —— 看 git 历史会看到它，⛔ 别以为还在

本文件原先有 `ROLE_QUOTA = {FREE: 100, PREMIUM: 10000, ADMIN: inf}` 与 `get_user_quota()`。
**2026-10-03 整张表连入口一起删**（→）。

**删的理由（判据可打印）**：那是**一套独立的「每日请求次数」配额**，与 `token_config` 的 **token 日额度**（token）
**互不知情** ——  实测**两者口径差 35 倍**（`plan_execute` 一次 ~3346 token ⇒ 按次数能跑 100 次、按 token 只能跑 ~3 次）。

⚠️ ** 原写「降级为接口权重、不删」—— 该条已被  推翻。**
⚠️ **配额表现在的家在 `token_config`**（🔴 2026-10-11 起是**两张池表**：`LLM_DAILY_TOKEN` / `EMBEDDING_DAILY_TOKEN` —— 旧名 `ROLE_DAILY_TOKEN` **已删**，），判定在 `token_tracker`，全路径那层挂在 `main.QuotaMiddleware`。

### 5. ⚠️ `UserRole` 是 `str, Enum` ⇒ 与字符串键**直接可比**

所以 `token_config` 里那张表**用字符串当键**（`"free"` / `"premium"` / `"admin"`）也能对上。
⚠️ 代价：**写错字符串不会报错**，只会**查不到** ⇒ 见 `app/billing/specs/token_config.md`。

### 6. ⚠️ 缓存（**2026-10-09 起有了 —— 但⛔ 不是 `@lru_cache`**）

**原文（2026-10-09 之前，⛔ 保留，它说的担心是真的）：**

> `get_user_role` 无 `lru_cache`。⚠️ **看着像"该缓存没加"，其实是有意的**：一旦缓存，
> `monkeypatch` 改角色对**已经调过**的代码路径就失效，本仓大量用例靠这个机制。
> ⇒ ⛔ **别顺手加 `@lru_cache`**。

**2026-10-09（B1）换了缓存，结论如下 —— 🔴 这是【实测】，⛔ 不是推理：**

* **为什么非加不可**：接 DB 之后不缓存 = **每请求最多 10 次同一条查询**
  （`token_tracker:513` 在配额热路径上；`api_v1_agent` 一个请求 7 处 admin 判定）
  —— 而查的是一个**几乎不变**的值。
* **用的是 TTL 60s，⛔ 不是 `lru_cache`** —— 后者**永不过期**：改了角色要么重启进程、
  要么"改了不生效且没人知道"，而后者正是本仓最恨的形态。
* **写侧主动失效**：`create_user_api_key(...)` 写完调 `invalidate_role_cache(user)`
  ⇒ **本进程内当场生效**（判据见下）。多 worker 时其余进程最多再等一个 TTL —— **有意的取舍**。

**原担心（"`monkeypatch` 会失效"）实测成不成立：**

| 那 3 处 | 怎么改的 | 结论 |
|---|---|---|
| `test_trace_cost.py:293` · `test_token_config.py:77` · `test_budget_soft_return.py:525` | **`monkeypatch.setattr(<模块>, "get_user_role", <lambda>)`** —— **换的是函数本身** | ✅ **不受影响**，而且理由不是"碰巧绿"：**缓存活在被换掉的那个函数【里面】**，函数被整个替换 ⇒ 缓存根本参与不进来 |

⚠️ **但有一种情况它【会】咬人**，所以另加了两道防护：

1. 🔴 **改的是"输入"而不是函数**（例如改库里的 `role`、或改 `_PREMIUM_PROBE_USERS`）
   ⇒ 旧缓存会让改动**最多 60s 不生效**。
   ⇒ 那是 `invalidate_role_cache` 存在的理由；`test_user_role_from_db.py::test_写侧失效要立即生效`
   专门钉它。
2. 🔴 **缓存是【进程级】的 ⇒ 会跨用例残留**（某条用例的替身值被后面的用例读到，
   而结论就**取决于用例执行顺序**）。
   ⇒ `app/conftest.py` 有一条 **autouse** `_clear_role_cache_between_tests` 消掉它。
   ⚠️ 那条**⛔ 不是**"为了让测试过"的开关 —— 缓存本身有 10 条用例专门钉。

### 7. 🔴 库挂了 ⇒ **回退**，⛔ 不是抛 —— 与 `resolve_ws_identity` 的 fail-closed **故意相反**

| | 语义 | 为什么 |
|---|---|---|
| `deps.resolve_ws_identity` | **fail-closed**（验证器抛错 ⇒ **挡住**） | **安全边界**：放错了代价更大 |
| `permission.get_user_role` | **fail-open**（库挂了 ⇒ **回退**，仍给一个角色） | **只是配额分档**：库一抖就把全站打死，代价更大 |

⛔ **别"统一"两边**（`deps.py` 里也写着同一条）。
⚠️ **捕获范围 = `psycopg2.Error`，⛔ 不是 `except Exception`** ——
本仓对这条**已有明文决定**（`app/access/auth.py:153`）：宽捕获会把**代码 bug** 伪装成"库挂了"
⇒ 真 bug 永远查不出来；**并且**会让 `test_rate_limit_identity.py::_no_db` 那道
「不许碰库」守卫**静默失效**（它靠抛 `AssertionError` 抓）。
📌 反证：`test_user_role_from_db.py::test_非数据库异常必须冒泡`。

### 8. 🔴 `role` 为 `NULL` ⇒ **回退**，⛔ 不是 ⇒ `FREE`

那一列是**后加的**（可空）⇒ 加列之前写进去的老行**都是 `NULL`**。
**"没人写过"与"写了 free"不是一回事**：前者要走回退（探针身份 + `admin` 特判）。
⛔ **别在写侧补 `or "free"`**（`auth.py` / `api_v1.py` / `issue_api_key.py` 三处都有注释钉它）。
📌 反向守卫：`test_user_role_from_db.py::test_没写_role_时要回退到硬编码`（6 档参数化）。

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/deps.md` | `require_admin` 怎么用它（**fail-closed** 的那一侧） |
| `app/billing/specs/token_config.md` | **日额度那两张池表** —— 角色**真正影响的东西**（额度） |
| `app/billing/specs/token_tracker.md` | 按角色取额度的调用点（`:495`） |
