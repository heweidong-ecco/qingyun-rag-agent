# `app/access/login_users.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **已实施**（2026-10-15） |
| **对外提供** | `hash_password()` · `verify_password()` · `authenticate_from_db()` · `add_user()` · `set_active()` · `list_users()` · 🔴 **`login_account_blocked()`**（**撤销立即生效**那半边）· `invalidate_active_cache()` |
| **谁在用** | `access/auth.py::authenticate_user`（**第二条来源**）· `app/manage_users.py`（运维动作） |
| **落盘** | 表 `login_users` —— DDL 在 `app/core/db.py::create_table()` |

## ✅ 做了什么

- **登录账号可以入库了** —— 在此之前**只有环境变量里那两个**（`LOGIN_USER_NAME` / `test_user`），
  加一个账号 = **改 `.env` + 重启** ⇒ 一条路走不通，谈不上"给外面发 N 个账号"。
- **口令一律哈希**：`pbkdf2_sha256$轮数$盐$哈希`（**标准库 `hashlib.pbkdf2_hmac`**，
  20 万轮，每行独立 16 字节 salt）。
- **建号 / 换口令 / 停用 / 列出** ⇒ `app/manage_users.py`（**在容器里跑的一条命令**，⛔ 不是端点）。
  ⚠️ **建号默认 15 天到期**（`DEFAULT_EXPIRE_DAYS`）—— 需求方 2026-10-15 原话：
  「**15 天没有联系，这个 HR 基本是放弃了**」。要永不过期得**显式** `--forever`。
- 🔵 **「撤销立即生效」**（2026-10-15 同日补）：`deps.verify_jwt_token` + `/auth/refresh`
  都过 `login_account_blocked` ⇒ **已发出的令牌在撤销后最多 30 秒失效**
  （⛔ 不加这一步的话，「撤销」只在下一次**登录**时生效，而 `refresh` 有 **7 天**）。

## 🟡 做到哪 / 缺什么

| # | 没做 | 说明 |
|---|---|---|
| 1 | ⛔ **自助注册** | **有意不做** —— 公开注册 = 谁都能进来刷 token。要发账号就**人去建** |
| 2 | ⬜ **首次登录强制改密** | 现在建号口令是**随机生成、只打印一次**、之后可以一直用。⚠️ 想收紧再说 |
| 3 | ⬜ **改密审计** | 谁在什么时候改了谁的口令，**没有记录**（`manage_users` 只打印到自己终端） |
| 4 | ⚠️ **环境变量里那两个口令仍是明文比对** | **有意**（§二·②甲）：它们在 `.env`（600 权限）里，**与 `DASHSCOPE_API_KEY` 同一个保护级别** —— 能读它的人目标不会是登录口令 |

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「把模块常量 `PBKDF2_ITERATIONS` 调大 = 升级安全」** | ⚠️ **调它只影响【新】口令** —— 校验时读的是**哈希串里那个轮数**，⛔ 不是模块常量。**这是有意的**：否则调一次轮数，**库里所有老账号一起失效**，而且**不报错、只是登不进**。守卫 ⇒ `test_轮数是从哈希串里读的_不是读模块常量` |
| 🔴 **「环境变量和库里的账号，谁优先都行」** | ⛔ **环境变量必须优先** —— 反过来（库优先）的后果是「**谁能在库里插一行 `admin`，谁就改了管理员口令**」，而插一行只需一个写库的口子，比改 `.env` 容易得多 |
| 🔴 **「`authenticate_user` 里那个 `.encode()` 是多余的」** | ⛔ **不是** —— `hmac.compare_digest` 收 `str` 时**遇非 ASCII 会直接抛 `TypeError`** ⇒ **中文/emoji 口令当场 500**（不是 401）。守卫 ⇒ `test_非ascii口令在明文那条路上不许炸` |
| ⚠️ **「本表 `is_active` 是 INTEGER，跟 `api_keys` 保持一致」** | ⛔ **这里是 BOOLEAN**，**有意不同**：`api_keys` 用整数 + `COALESCE(is_active,1)=1` 是为了兼容**加列前的老行（NULL）**；本表是**新表、列 NOT NULL**，`NULL` 只可能是坏数据 ⇒ **两种语义，⛔ 别统一** |
| 🔴 **「角色也要在建号时一起设」** | ⛔ **不用** —— 角色由 `api_keys.role` 定，而**库里查不到的新用户名默认就是 FREE**（`access/permission.py::_hardcoded_role`）⇒ 建号时**⛔ 不需要**插 `api_keys` 行 |
| ⚠️ **「表没建好也能登录」** | ⛔ 表不存在 / 库挂 ⇒ `authenticate_from_db` **返回 `False`**（fail-closed）—— 但**环境变量那条路照常能用** ⇒ 新库部署时 `admin` 不会被锁在外面 |
| 🔴🔴 **「`login_account_blocked` 查不到行 / 库读不到 ⇒ 应该【拦】才安全」** | ⛔ **不该** —— 第一版就是这么写的，**全量当场 12 failed**：本仓用例大量**自签 JWT**（`create_access_token(f"m6-{uuid4()}")`）模拟登录，那些名字**根本不在库里** ⇒ 全线 401；而 **CI 压根没有 Postgres** ⇒ **每次 CI 全红**。<br>✅ 正确口径：**只有「查到了这一行、且它说停用/过期」才拦**；查不到 / 读不到 ⇒ **不拦**。<br>⚠️ **换来的保护是零**：能自签 JWT 的人**已经拿到了 `JWT_SECRET_KEY`** —— 那个秘密才是边界。<br>📌 代价：库不可用期间**已撤销的人仍可能进得来**，但那时应用本来就全 500（每个页面都在打 API）。<br>📌 守卫 ⇒ `test_库里没有这一行_不拦` · `test_库读不到_不拦_因为无法判定`（**⛔ 别把这两条改成"拦"**）。 |
| 🔴 **「`revoke` 之后就立刻进不来了」** | ✅ **2026-10-15 起是对的** —— `deps.verify_jwt_token` 与 `/auth/refresh` **都会过 `login_account_blocked`**。<br>⚠️ **但有 30 秒延迟**（进程内缓存 TTL）⇒ 撤销是"**最多 30 秒内生效**"，⛔ 不是 0 秒。<br>⚠️ **两个入口都要堵** —— 只堵 `verify_jwt_token` 的话，那张 refresh 还能**一直换新令牌**（最长 7 天）。 |

## 判据（可打印）

```bash
# 离线（不碰库）—— 29 条
venv/bin/python -m pytest app/tests/test_login_users.py -q -p no:warnings

# 真库那一侧（要 PG；不进 CI）
POSTGRES_DB=rag_test venv/bin/python -m pytest app/tests/test_login_users_db.py -q -m needs_db -p no:warnings

# 端到端（在服务器上、容器里建一个号，再用它登一次）
sudo docker compose exec api python manage_users.py add smoke_test
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<host>/api/v1/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"user_name":"smoke_test","password":"<上一步打印出来的>"}'    # ⇒ 200
```

## 关联

- 🔴 本仓的决策记录（⛔ 不随产品仓提供） —— **这一条的完整来龙去脉**
- `app/access/specs/auth.md` —— **它插在哪儿**（`authenticate_user` 的两条来源与优先顺序）
- `app/access/specs/permission.md` —— **角色从哪来**（`api_keys.role` + `_hardcoded_role` 回退）
- `app/routing/specs/deps.md`（若在）· `app/routing/deps.py::get_current_user_hybrid` ——
  **JWT 与 `X-API-Key` 两条路在这里汇流**，最终都解析成同一个 `user_name`
