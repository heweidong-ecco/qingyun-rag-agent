# `app/tools/browser_tools.py`

## ✅ 做了什么

**三个 Playwright 工具**：`fetch_webpage`（取网页正文）· `screenshot_webpage`（截图）等。

## 🟡 做到哪 / 缺什么

- 🔴 **⛔ 它【不在工具表里】** —— `app/tools/mcp_server.py` 里那两行是**注释掉的**：
  ```python
  # from browser_tools import fetch_webpage, screenshot_webpage
  # {"func": fetch_webpage, "version": "1.0.0"},
  ```
  **判据**：`grep -n "browser_tools" app/tools/mcp_server.py` ⇒ **只有注释**。
- **原因**（2026-09-21）：这三个工具依赖 **Playwright 的 chromium**，
  而**本仓任何部署方式都没装它** ⇒ 注册了也只会让工具全 unhealthy。
- **6 处产品代码引用 · 1 个测试文件提到**（多是注释与守卫说明）。
- ⚠️ **全仓无人 import `fetch_webpage`** ⇒ 它当前**没有任何调用方**。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **"代码在" ≠ "模型能用"** —— 本文件是**挂起的能力**（从工具表摘掉）。
   ⇒ 看到这三个函数**别以为它们在服务路径上**。要接回：先装 chromium，
   **同时**改 `mcp_server.py` 那两行（记着这条待办）。
2. 🔴 **⛔ 不许把它改成 `async def`** —— 它**同时被 MCP server 的【同步】路径调用**。
   改了会报「`using Playwright Sync API inside the asyncio loop`」（**当初就是这么定的**）。
   📌 出处：`app/tests/test_agent_repairs.py` 里那条专门说明。
3. ⚠️ **截图目录**：`dirname(__file__)/../../screenshots`（= `<仓根>/screenshots`）。
   ⚠️ **2026-10-09 从 `../` 改成 `../../`**（本文件从 `app/` 挪进了 `app/tools/`）——
   ⇒ **再挪动必须同步改**，否则截图**静默**落到 `app/screenshots/`（那个目录不存在、会自动建）。
4. ⚠️ `screenshots/` 在 `.gitignore` 里 ⇒ 截图不入库。

## 关联

- `app/tools/specs/mcp_server.md` —— 工具注册表（**这里才有"模型能看见什么"的真答案**）
- `app/tools/specs/tool_health.md` —— 健康检查（挂起的工具也在这里被标 unhealthy）
-  ——  那条待办
