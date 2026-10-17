# `app/access/auth.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **可用（生产）** —— 两条并行的认证：**API Key**（查库）与**登录口令**（比环境变量） |
| **对外提供** | `generate_api_key()` · `hash_api_key(k)` · `ACTIVE_PREDICATE` · `create_user_api_key(user_name, expire_days=30)` · `ensure_admin_exists(logger=None)` · `verify_api_key(api_key)` · `authenticate_user(user_name, password)` · `_get_users_db()` · `_is_expired()` |
| **谁在用** | 🔴 **`verify_api_key` 有 3 个消费口**：`app/routing/deps.py:13` · **`app/main.py:163`** · **`app/main.py:204`**。另：`app/routing/api_v1.py:21`（`:71` `authenticate_user` 登录 · `:156` `create_user_api_key`）· `app/main.py:16 → :712` `ensure_admin_exists` · |
| **测试** | ✅ `app/tests/test_auth.py`（5）· `app/tests/test_auth_db_unavailable.py`（**22**）· `app/tests/test_auth_api_key_active.py`（5）· `app/tests/test_auth_ensure_admin_exists.py`（8） |

## ✅ 做了什么

**API Key 路线**（一把 key 长期有效）：`uuid4().hex` 生成 → **明文只给一次** → 库里存 `sha256`。
认证 = 按 `key_hash` 查一行，判两件事：**`is_active`** 与 **未过期**。

**登录口令路线**（发 JWT）：`_get_users_db()` 从**环境变量**现造一张小表，`==` 比对。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| ⚠️ **`hash_api_key` 是无盐 `sha256`** | ⛔ **别把它当"口令哈希"** —— 没有盐、没有慢哈希。**它够用是因为 key 本身是 128 位随机**，不是因为这个函数强。⇒ ⛔ **别把这个写法搬到用户口令上**（口令路线走的是环境变量明文比对，见 §⚠️ 第 6 条） |
| ⚠️ **没有"改角色"、没有"禁用用户"** | 表里只有 key。角色是硬编码的（`app/access/specs/permission.md`） |
| ⚠️ **没有 key 轮换/过期的自动提醒** | `expire_days` 默认 30；到期就静默失效（症状 = 401） |
| ⚠️ **老库的 `is_active` 列靠 `create_table()` 的 `ALTER` 补** | ⇒ 有**调用顺序**要求，见 §⚠️ 第 4 条 |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 0. 🔴 **`_is_expired` 的入参可能是 `str`**（的甲）

**SQLite 那条路上 `expires_at` 回来是 `str`，不是 `datetime`** —— 那一列类型是 `TIMESTAMP`，
而 SQLite 是**动态类型**，存进去的就是**一串文本**。

⇒ 直接 `expires_at < datetime.now()` 会当场
`TypeError: '<' not supported between instances of 'str' and 'datetime.datetime'`
（实测：**四个检索端点的隔离用例全红**）。

🔴 **解不出来时回 `True`（= 已过期）是有意的**：这是鉴权路径，**fail-closed** 是唯一正确方向
（⛔ 不是 fail-open 地放行）。⚠️ **⛔ 别在这里顺手补时区** —— 解析出来仍是
**naive 本地时间**（写进去的就是 `datetime.now`），理由与 `_is_expired` 自己的 docstring **逐字一致**。

### 1. 🔴 「一把 key 能不能用」= **两半口径**，两半都**必须共用**

| 半 | 唯一来源 | 谁必须用 |
|---|---|---|
| **`is_active`** | `ACTIVE_PREDICATE = "COALESCE(is_active, 1) = 1"` | `verify_api_key` **和** `ensure_admin_exists` |
| **未过期** | `_is_expired(expires_at)` | 同上 |

⛔ **别在别处另写一遍** —— **的缺陷就是这么来的**：
自检只 `COUNT(*)` **数行数**、不看 `is_active` ⇒ `issue_api_key.py admin --revoke` 之后，
**自检照样报「管理员账户已存在」，而认证侧一把都过不去** ⇒ **admin 锁死，而日志说一切正常。**

🔴 **判据**：`grep -n 'ACTIVE_PREDICATE\|_is_expired' app/access/auth.py` ⇒ 两个定义 + 各自的**两个**使用点。

> ⚠️ **必须带 `COALESCE`**：迁移建列时是 `nullable=True`，`is_active IS NULL` 的老行会被 `is_active = 1`
> **静默排除**（契约 D）⇒ 老 key 集体失效，而用户只看到一句"凭据无效"。

### 2. 🔴 过期**在 Python 侧比**，⛔ 不在 SQL 里写 `expires_at > NOW()`

**理由（文件里写明了）**：本仓 `expires_at` 存的是 **naive 本地时间**（`create_user_api_key` 用 `datetime.now()` 写进去的），
而库里的 `NOW()` 是 **timestamptz**（容器按 **UTC**）⇒ **在 SQL 里比会差 8 小时**。

⇒ ⛔ **别为了"性能"把这句挪进 SQL** —— 它会**静默**变成一条时区判据错 8 小时的查询，
而且**测试大概率照常绿**（本机两套时区混用那族，本仓有前科）。

### 3. 🔴 库挂了 ⇒ **抛 `AppException(SERVICE_UNAVAILABLE)`（503）**，⛔ 不是返回 `None`

返回 `None` 会让 `deps.get_current_user` 报 **401** —— 那等于**替用户断言「你的 key 坏了」**：
他会去换一把**没问题的** key，然后照样连不上，**永远查不到原因**。

⇒ 取向：**凭据不行 ⇒ 换 key；认证服务不行 ⇒ 重试、⛔ 别换 key。**

⚠️ **捕获范围是 `psycopg2.Error`**，⛔ **不是 `except Exception`** ——
宽捕获会把**代码 bug** 伪装成"库挂了"，而且会**吞掉** `app/tests/test_rate_limit_identity.py` 的 `_no_db` 守卫
（它靠抛 `AssertionError` 抓"谁碰了库"）⇒ **那道门静默失效**。
🔒 守卫 ⇒ `app/tests/test_auth_db_unavailable.py::test_非数据库异常必须照样冒泡`。

🔴 **这个约定有 3 个消费口**（`deps.py:13` + `main.py:163` + `main.py:204`）——
⇒ ⛔ **改它要三处一起看**；只看 `deps.py` 会漏掉中间件那两处。

### 4. 🔴 `ensure_admin_exists` **必须在 `create_table()` 之后**调用

它要读的 `is_active` 列，在**老库**里是靠 `create_table()` 里那句 `ALTER TABLE api_keys ADD COLUMN` 补上的
⇒ **顺序反了就是 `UndefinedColumn`，应用直接起不来。**
🔒 守卫 ⇒ `app/tests/test_auth_ensure_admin_exists.py::test_自检在建表之后调用`（调用点在 `app/main.py:712`）。

### 5. 🔴 `ensure_admin_exists` 的**第 3 种情形不自动补发** —— 这是**决定**，⛔ 不是遗漏

| # | 库里的状态 | 行为 |
|---|---|---|
| 1 | 名下有**能用**的 key | 静默跳过（`info`） |
| 2 | 名下**一行都没有** | 首次启动：自动建一把、**打印明文**（仅此一次） |
| 3 | **有行，但没有一行能用** | ⛔ **不补发**，只打**响亮 `error` 告警**并给出可照做的命令 |

**为什么第 3 种不补发**：`issue_api_key.py <user> --revoke` 是**按用户名撤**的（撤的是该用户名下**全部行**）。
若这里自动补发，操作员那次「撤销」会被**下一次重启静默还原**，且新 key 的**明文会打进日志**
（正是本仓花力气清掉的那类残渣）⇒ **决定权留给人。**

⚠️ 情形 3 在 之前会被**误当成情形 1** —— 那正是上面 §1 那条缺陷的症状面。

### 6. 🔴 `authenticate_user` 比的是**环境变量里的明文口令**，⛔ 与 `verify_api_key` 无关

```python
return _get_users_db().get(user_name) == password      # 明文 ，⛔ 没有哈希
```

⚠️ **这是两条完全独立的认证路线**，⛔ 别混：

| | `verify_api_key` | `authenticate_user` |
|---|---|---|
| 凭据形态 | `sk-…` API Key | 用户名 + 口令 |
| 存哪 | **库里**（存 `sha256`） | **环境变量**（明文） |
| 失败 | 返回 `None` ⇒ 401 | 返回 `False` |
| 环境不可用 | **503** | ⛔ 不适用（不碰库） |

⇒ ⛔ **别"统一"这两条** —— 它们服务的调用方不同（API 客户端 vs 登录页）。

### 7. ⚠️ `_get_users_db()` **每次调用都重新读环境变量**（刻意不缓存）

⇒ 为了让**测试能 `monkeypatch` 环境变量**。⛔ **别顺手加缓存** —— 那会让一批用例静默失效。

### 8. ⚠️ `TEST_USER_PASSWORD` 不设 ⇒ **`test_user` 不存在**

fail-closed：⛔ **不留任何默认口令**。⇒ 本地没设这个变量时，"用 `test_user` 登录"会**失败**，
而那**不是 bug**（`app/access/specs/permission.md` §⚠️ 第 3 条：`test_user` 是测试身份）。

### 9. ⚠️ `create_user_api_key` 返回的**明文只有这一次**

库里只有 `sha256` ⇒ **丢了找不回**，只能再发一把。
⚠️ 且  撤的是**该用户名下全部** key —— ⛔ 不是"撤这一把"。

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/deps.md` | `get_current_user` 怎么消费 401 / 503 这条约定（**三个消费口之一**） |
| `app/access/specs/session_key.md` | ⚠️ 用户名**没字符校验** ⇒ 含 `:` 是可能的（那里有守卫） |
| `app/access/specs/permission.md` | 角色判定（`"admin"` 那个硬编码字面量） |
| `app/specs/main.md` | startup 顺序（`ensure_admin_exists` 在建表之后）+ 中间件那两处调用 |
| `docs/契约/环境变量.md` | `LOGIN_USER_NAME` / `LOGIN_PASSWORD` / `TEST_USER_PASSWORD` / `JWT_SECRET_KEY` |
