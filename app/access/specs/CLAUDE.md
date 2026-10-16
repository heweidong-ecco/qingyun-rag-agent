# `app/access/specs/` —— 本组模块的规格

> 📇 **一份 spec 对应一个模块，且【与模块同名】**。
> 判据：`app/access/<模块>.py` ⇔ `app/access/specs/<模块>.md`

## 📇 本目录索引

| spec | 对应 |
|---|---|
| `auth.md` | `app/access/auth.py` 的 spec |
| `jwt_handler.md` | `app/access/jwt_handler.py` 的 spec |
| `permission.md` | `app/access/permission.py` 的 spec |
| `rate_limiter.md` | `app/access/rate_limiter.py` 的 spec |
| `session_key.md` | `app/access/session_key.py` 的 spec |

## 🔴 写法（⛔ 别自由发挥）

四段式 —— 模板与说明见 `app/specs/README.md`：
`✅ 做了什么` / `🟡 做到哪缺什么` / **`⚠️ 看代码会误判的地方 ⭐`** / `关联`

⭐ **重头是第三段** —— 前两段读代码也能推出来，**只有第三段推不出来**。
内容要来自**代码里的 ⚠️/🔴 注释**与 `、`DEC-*`，⛔ **不是读一遍代码的转录**
（本仓立场：**转录即负债**）。

## 📍 往上读
- `../CLAUDE.md`（本组）· `app/specs/README.md`（模板）· 仓库根 `CLAUDE.md`
