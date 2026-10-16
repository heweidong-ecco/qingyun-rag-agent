# `app/tools/` —— 工具与执行器

> 📇 **本层 = 索引表 + 主要内容**：agent 进到这一层，先读这份；不够再往 `specs/` 转。

## 📇 本目录索引

**职责**：工具 · MCP · 执行器 · 缓存 · 健康

| 模块 | 它的 spec |
|---|---|
| `browser_tools.py` | `specs/browser_tools.md` |
| `code_executor.py` | `specs/code_executor.md` |
| `code_executor_impl.py` | `specs/code_executor_impl.md` |
| `executor_server.py` | `specs/executor_server.md` |
| `mcp_server.py` | `specs/mcp_server.md` |
| `mcp_tool_factory.py` | `specs/mcp_tool_factory.md` |
| `safe_math.py` | `specs/safe_math.md` |
| `search_tools.py` | `specs/search_tools.md` |
| `simple_tools.py` | `specs/simple_tools.md` |
| `simple_tools_impl.py` | `specs/simple_tools_impl.md` |
| `tool_cache.py` | `specs/tool_cache.md` |
| `tool_health.py` | `specs/tool_health.md` |
| `tool_visualizer.py` | `specs/tool_visualizer.md` |
| **`specs/`** | 本组模块的规格 —— **与模块同名**（见 `specs/CLAUDE.md`） |

## 🔴 本层特有的规矩

- 🔴 **改完一个模块 ⇒ 更新 `specs/<同名>.md`**（`pre-commit-gates.py` 对**新增模块**硬拦）
- 🔴 **导入写绝对形式、根是 `app/`**：`from core.config import X`（⛔ 不是 `from app.core...`）
- ⚠️ 本组的 `.py` **已不在 `app/` 根** ⇒ 凡 `dirname(__file__)` 算路径的**都已跟着搬**，
  ⛔ **别再把它和它的数据文件拆开**

## 🟡 本组做到哪（**2026-10-10 从 `ROADMAP.md` 移入**）

> ⚠️ **逐模块的"做到哪 / 看代码会误判什么"在各自的 `specs/`** —— 本节只留【整组】那条线。

- ✅ **2026-10-08 · 支线「MCP / 工具层」四个批次**（需求方点的，⛔ 不是从仓库推的）：

| 批 | 做了什么 | PR |
|---|---|---|
| ① | **工具缓存收口**：缓存唯一落点· 工具清单七处收口到一处 | `#113` |
| ② | **`execute_python` 进独立执行器容器** —— 应用**⛔ 不碰 `docker.sock`** | `#115` |
| ③ | **三个新工具** `date_calc` / `json_extract` / `stats`（工具数 4 → 7） | `#116` |
| ④-A | **解 `mcp` 上界到 2.x** + 服务端迁构造器回调 | `#117` |
| ④-B | **MCP 会话改长驻** ⇒ 每次工具调用 **2.42s → 4ms** | `#118` |

- 🔵 **顺带修掉的真 bug**（都是这批照出来的）：① 服务端 `print` 写到 **stdout**（= MCP 传输通道，
  **每次调用** 1 条 `ValidationError`）② `agent_graph_advanced.py` 里 **`asyncio` 根本没 import**
  ③ **长驻会话不会自愈**（真杀子进程验证照出 ·  §八 ⇒ ✅ **已随 PR `#119` 进主干**）。
- ⚠️ **仍未验**：真杀子进程的恢复**已验**；**高并发压力 / 创空间那台机器 / 长跑内存漂移** ⇒ **未做**。

## 📍 往上读

- `../CLAUDE.md`（`app/`）· 仓库根 `CLAUDE.md`（全局约定）
