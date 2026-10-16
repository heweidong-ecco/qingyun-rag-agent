# `app/access/jwt_handler.py`

## ✅ 做了什么

**JWT 签发与校验**：`create_access_token` / `create_refresh_token` / `verify_access_token`
（`HS256`，有效期来自 `app/core/config.py`）。

## 🟡 做到哪 / 缺什么

- **4 处产品代码引用 · 3 个测试文件提到**。
- 被 `app/main.py` 的中间件与 `app/routing/deps.py` 的鉴权依赖使用。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **模块顶部的 `SECRET_KEY = JWT_SECRET_KEY` 是【同一个对象】**，⛔ 不是自己读环境变量。
   ⚠️ **删过两行 `os.getenv(...)`** —— 那两行**重新赋值**、把上面
   `from core.config import ...` 进来的同名常量**覆盖**了 ⇒ **那个 import 当时是死导入**。
   ⇒ ⛔ **别再往这里加 `os.getenv`**：它绕开 `config.py`，而 `config.py` 才负责
   「本地 / Docker 切换时覆盖主机名」那件事（见 `docs/规范/开发规范.md` §环境变量）。
2. ⚠️ **`test_auth_db_unavailable.py` 拿 `verify_access_token` 当 monkeypatch 的【缝】** ——
   `monkeypatch.setattr(jwt_handler, "verify_access_token", …)`。
   ⇒ **改这个函数名/搬它的家 ⇒ 补丁【静默失效】**，而用例会转去连真库（本仓同族栽过多次）。
3. ⚠️ `ALGORITHM` 是**模块常量**、不是配置项 —— 改它得改代码，⛔ 没有环境变量开关。

## 关联

- `app/core/specs/config.md` —— `JWT_SECRET_KEY` / 两个有效期常量的来源
- `app/access/specs/auth.md` —— API Key 那条**并行的**鉴权路径（两条不是一回事）
