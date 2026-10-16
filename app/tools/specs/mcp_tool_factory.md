# `app/tools/mcp_tool_factory.py`

## ✅ 做了什么

**MCP 工具工厂**：把 LangChain 的 `@tool` 函数**自动封装**成 MCP 标准接口
（`create_mcp_tool_definition` / `create_mcp_tool_handler`）。

## 🟡 做到哪 / 缺什么

- **2 处产品代码引用 · 1 个测试文件提到**。
- 被 `app/tools/mcp_server.py` 用来**从 `TOOLS` 表批量产出** MCP 定义
  **加一个工具只需动注册表**，⛔ 不用手写 MCP 那一层。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **本处理器必须保持【同步】—— 它有【三个】调用方，其中一个是同步的。**
   ⚠️ **2026-09-20 的教训**：当时为了修 MCP 那条链**先把它改成了 `async`**
   ⇒ 同步那个调用方拿到的就不是结果了。
   ⇒ ⛔ **别因为"看起来该 async"就改**：先数清调用方。
2. 🔴 **它的失败后果被【健康检查放大】**：`app/tools/tool_health.py` 的判据是
   「**结果里含『工具调用失败』 ⇒ unhealthy**」⇒ 这里返回一句**措辞**
   就可能让**6 个工具全 unhealthy**（看着像"工具全坏了"，实为一处措辞）。
   ⇒ 改**返回值文案**前，先看健康检查的判据。
3. ⚠️ **`version` 是形参、默认 `"1.0.0"`** —— 全仓**没有版本管理机制**，
   ⛔ 别把它当成"有版本控制"。

## 关联

- `app/tools/specs/mcp_server.md` —— 调用方与注册表
- `app/tools/specs/tool_health.md` —— 那条被放大的判据
- 本仓的决策记录（⛔ 不随产品仓提供）* · * —— MCP 上界与长驻会话那两批
