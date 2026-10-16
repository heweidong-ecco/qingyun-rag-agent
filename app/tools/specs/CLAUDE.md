# `app/tools/specs/` —— 本组模块的规格

> 📇 **一份 spec 对应一个模块，且【与模块同名】**。
> 判据：`app/tools/<模块>.py` ⇔ `app/tools/specs/<模块>.md`

## 📇 本目录索引

| spec | 对应 |
|---|---|
| `browser_tools.md` | `app/tools/browser_tools.py` 的 spec |
| `code_executor.md` | `app/tools/code_executor.py` 的 spec |
| `code_executor_impl.md` | `app/tools/code_executor_impl.py` 的 spec |
| `executor_server.md` | `app/tools/executor_server.py` 的 spec |
| `mcp_server.md` | `app/tools/mcp_server.py` 的 spec |
| `mcp_tool_factory.md` | `app/tools/mcp_tool_factory.py` 的 spec |
| `safe_math.md` | `app/tools/safe_math.py` 的 spec |
| `search_tools.md` | `app/tools/search_tools.py` 的 spec |
| `simple_tools.md` | `app/tools/simple_tools.py` 的 spec |
| `simple_tools_impl.md` | `app/tools/simple_tools_impl.py` 的 spec |
| `tool_cache.md` | `app/tools/tool_cache.py` 的 spec |
| `tool_health.md` | `app/tools/tool_health.py` 的 spec |
| `tool_visualizer.md` | `app/tools/tool_visualizer.py` 的 spec |

## 🔴 写法（⛔ 别自由发挥）

四段式 —— 模板与说明见 `app/specs/README.md`：
`✅ 做了什么` / `🟡 做到哪缺什么` / **`⚠️ 看代码会误判的地方 ⭐`** / `关联`

⭐ **重头是第三段** —— 前两段读代码也能推出来，**只有第三段推不出来**。
内容要来自**代码里的 ⚠️/🔴 注释**与 `、`DEC-*`，⛔ **不是读一遍代码的转录**
（本仓立场：**转录即负债**）。

## 📍 往上读
- `../CLAUDE.md`（本组）· `app/specs/README.md`（模板）· 仓库根 `CLAUDE.md`
