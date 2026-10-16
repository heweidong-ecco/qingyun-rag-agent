# `app/core/specs/` —— 本组模块的规格

> 📇 **一份 spec 对应一个模块，且【与模块同名】**。
> 判据：`app/core/<模块>.py` ⇔ `app/core/specs/<模块>.md`

## 📇 本目录索引

| spec | 对应 |
|---|---|
| `cache.md` | `app/core/cache.py` 的 spec |
| `config.md` | `app/core/config.py` 的 spec |
| `db.md` | `app/core/db.py` 的 spec |
| `db_metadata.md` | `app/core/db_metadata.py` 的 spec |
| `exceptions.md` | `app/core/exceptions.py` 的 spec |
| `llm_factory.md` | `app/core/llm_factory.py` 的 spec |
| `logger_config.md` | `app/core/logger_config.py` 的 spec |
| `metrics.md` | `app/core/metrics.py` 的 spec |

## 🔴 写法（⛔ 别自由发挥）

四段式 —— 模板与说明见 `app/specs/README.md`：
`✅ 做了什么` / `🟡 做到哪缺什么` / **`⚠️ 看代码会误判的地方 ⭐`** / `关联`

⭐ **重头是第三段** —— 前两段读代码也能推出来，**只有第三段推不出来**。
内容要来自**代码里的 ⚠️/🔴 注释**与 `、`DEC-*`，⛔ **不是读一遍代码的转录**
（本仓立场：**转录即负债**）。

## 📍 往上读
- `../CLAUDE.md`（本组）· `app/specs/README.md`（模板）· 仓库根 `CLAUDE.md`
