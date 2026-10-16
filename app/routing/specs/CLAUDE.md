# `app/routing/specs/` —— 本组模块的规格

> 📇 **一份 spec 对应一个模块，且【与模块同名】**。
> 判据：`app/routing/<模块>.py` ⇔ `app/routing/specs/<模块>.md`

## 📇 本目录索引

| spec | 对应 |
|---|---|
| `api_v1.md` | `app/routing/api_v1.py` 的 spec |
| `api_v1_agent.md` | `app/routing/api_v1_agent.py` 的 spec |
| `api_v1_rag.md` | `app/routing/api_v1_rag.py` 的 spec |
| `deps.md` | `app/routing/deps.py` 的 spec |
| `schemas.md` | `app/routing/schemas.py` 的 spec |
| `sse.md` | `app/routing/sse.py` 的 spec |
| `websocket_callback.md` | `app/routing/websocket_callback.py` 的 spec |

## 🔴 写法（⛔ 别自由发挥）

四段式 —— 模板与说明见 `app/specs/README.md`：
`✅ 做了什么` / `🟡 做到哪缺什么` / **`⚠️ 看代码会误判的地方 ⭐`** / `关联`

⭐ **重头是第三段** —— 前两段读代码也能推出来，**只有第三段推不出来**。
内容要来自**代码里的 ⚠️/🔴 注释**与 `、`DEC-*`，⛔ **不是读一遍代码的转录**
（本仓立场：**转录即负债**）。

## 📍 往上读
- `../CLAUDE.md`（本组）· `app/specs/README.md`（模板）· 仓库根 `CLAUDE.md`
