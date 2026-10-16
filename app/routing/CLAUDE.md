# `app/routing/` —— HTTP / WS 边界

> 📇 **本层 = 索引表 + 主要内容**：agent 进到这一层，先读这份；不够再往 `specs/` 转。

## 📇 本目录索引

**职责**：3 张路由表 + 依赖 + Schema + SSE

| 模块 | 它的 spec |
|---|---|
| `api_v1.py` | `specs/api_v1.md` |
| `api_v1_agent.py` | `specs/api_v1_agent.md` |
| `api_v1_rag.py` | `specs/api_v1_rag.md` |
| `deps.py` | `specs/deps.md` |
| `schemas.py` | `specs/schemas.md` |
| `sse.py` | `specs/sse.md` |
| `websocket_callback.py` | `specs/websocket_callback.md` |
| **`specs/`** | 本组模块的规格 —— **与模块同名**（见 `specs/CLAUDE.md`） |

## 🔴 本层特有的规矩

- 🔴 **改完一个模块 ⇒ 更新 `specs/<同名>.md`**（`pre-commit-gates.py` 对**新增模块**硬拦）
- 🔴 **导入写绝对形式、根是 `app/`**：`from core.config import X`（⛔ 不是 `from app.core...`）
- ⚠️ 本组的 `.py` **已不在 `app/` 根** ⇒ 凡 `dirname(__file__)` 算路径的**都已跟着搬**，
  ⛔ **别再把它和它的数据文件拆开**

## 📍 往上读

- `../CLAUDE.md`（`app/`）· 仓库根 `CLAUDE.md`（全局约定）
