# `app/tools/mcp_server.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **可用 · 是四条执行路径的【唯一工具事实源】的产地** —— ⚠️ 但它自己**没有测试**（唯一入口是子进程） |
| **对外提供** | `TOOLS`（**注册表**）· `TOOLS_DEFINITION` · `TOOL_HANDLERS` · `server`（MCP `Server` 实例）· `list_tools` / `call_tool`（**2.x 的构造器回调**，⛔ 不是装饰器函数）· `run_mcp_server()` |
| **谁在用** | 🔴 **六处**，且**四条执行路径全派生自它的 `TOOLS`**：<br>`agent_graph.py`（`TOOLS`）· `agent_checkpointer.py`（`TOOLS`）· `agent_graph_advanced_learning.py`（`TOOLS` + `TOOL_HANDLERS`）· `plan_execute.py`（`TOOLS` + `TOOL_HANDLERS`）· `api_v1_rag.py`（`TOOLS`）· `api_v1_agent.py`（`TOOLS_DEFINITION`）<br>⚠️ `agent_graph_advanced.py` **不 import 它** —— 那条路**经 MCP client 动态取表**（这正是"改这里 = 改四条链"的原因） |
| **测试** | 🔴 **无专属测试文件**。间接覆盖：`app/tests/test_tool_registry_single_source.py`（**子进程** import 它验注册表 —— `TOOLS` 是模块级建的，同进程改 env ⛔ 无效）· `app/tests/test_mcp_protocol_e2e.py`（🆕 ：**真子进程 + 真协议**调它）· `app/tests/test_agent_repairs.py`（`call_tool` 的 offload 守卫） |

## ✅ 做了什么

- ⭐ **工具的【唯一事实源】**（`TOOLS`，一行一个工具）—— 把散在 7 处的清单收口到这一处
- **工具定义与处理器由工厂自动生成**（`TOOLS_DEFINITION` / `TOOL_HANDLERS`，都从 `TOOLS` 派生）⇒ ⛔ 不需要手维护第二张映射表
- **健康检查过滤**：`list_tools` 把 `UNHEALTHY` 的工具**移出清单**（这是**当前实际生效的降级机制**，⛔ 不是 `tool_health.py`）
- **在 async 边界处 offload**：`call_tool` 里 `await asyncio.to_thread(handler, arguments)` —— 让**同步**工具（如 Playwright）不在事件循环里跑
- 🔴 **2026-10-08：迁到 mcp 2.x 的构造器回调** —— 见下「看代码会误判」
- ✅ **2026-10-16：原来那个「单容器部署」开关已删** —— 它随那套部署一起下线，且**没有任何地方再设它**
  （实测 compose / `.env.example` / `scripts/` 全 0 命中）⇒ 是**死配置**，删它是**行为不变**的收尾。
  ⚠️ **但那条安全口径还在**：`execute_python` 走**独立执行器容器**，⛔ 不在应用进程里起子进程。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| 🔴 **`TOOLS` 是【模块级】建的** | 任何**按 env 过滤**的做法都**只在 import 时执行一次** ⇒ 同一进程里改 env **静默无效**。⚠️ 要验注册表**必须走子进程**（`test_tool_registry_single_source.py` 正是这么做的）。<br>⚠️ 将来若再加一个按 env 过滤的开关，**先读这一行** |
| ⚠️ **两个浏览器工具仍注释着** | `fetch_webpage` / `screenshot_webpage` —— 依赖未安装的 chromium（需求方裁「挂起 + 注释掉 + 标『可扩展能力』」） |
| ⚠️ ~~**没有 MCP 会话复用**~~ | ✅ **2026-10-08已解决** —— 客户端侧改成**长驻会话**（actor），**实测 2s → 4–7ms**。<br>⚠️ **但那是 `agent_graph_advanced.py` 的事，本文件一行没动** —— 本文件仍是"被起一次就服务一个连接"的服务端 |
| ⚠️ **服务端启动形态仍是"握手"** | 用了 `create_initialization_options()`（协议 `2026-07-28`「Make MCP stateless」**没删它**，见 `docs/reference/mcp-stateless-and-transport.md`） |

## ⚠️ 看代码会误判的地方 ⭐

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🔴 2.x 的处理器是**构造参数**，⛔ 不是装饰器 —— 而且**顺序**是硬的

```python
# 🔴 `Server(...)` 必须建在两个处理器【定义之后】
server = Server("agent-tools", on_list_tools=list_tools, on_call_tool=call_tool)
```
- 1.x 是 `@server.list_tools()` 装饰器，`server = Server("agent-tools")` 写在**文件顶部**；
- 2.x 改成构造器回调 ⇒ 那一行**必须挪到两个函数定义之后**（否则 `NameError`）。
- 🔴 **改这个文件时别把它挪回顶部** —— 挪回去**当场炸**，而且炸的是 import 期。

### 2. 🔴 `inputSchema` / `input_schema` —— **构造**与**读属性**是两回事

| 玩法 | 能不能用 `inputSchema` |
|---|---|
| **构造** `Tool(name=…, inputSchema=…)` | ✅ 能（`populate_by_name=True`） |
| **读属性** `tool.inputSchema` | 🔴 **不能** ⇒ `AttributeError: … Did you mean: 'input_schema'?` |
| **拿线上拼法** | `tool.model_dump(by_alias=True)["inputSchema"]` |

⚠️ **本文件里那处 `inputSchema=tool_def["inputSchema"]` 是【构造】，所以它是对的** ——
⛔ 别看到别处（`agent_graph_advanced.py` / `api_v1_agent.py`）改成了 `.input_schema` 就跟着改这里。
📄 那条坑的全文与教训 ⇒  §2.3。

### 3. ⚠️ `call_tool` 的参数**可能是 `None`**

`types.CallToolRequestParams(name="x").arguments` **缺省是 `None`**，⛔ 不是 `{}`
（📌 判据：`./venv/bin/python -c "from mcp import types; print(types.CallToolRequestParams(name='x').arguments)"` ⇒ `None`）。
⇒ 处理器里那行 `arguments = params.arguments or {}` **是必须的**，⛔ 不是防御性编程。

### 4. 🔴 那个 `@server.list_tools()` 的**名字还在**，但**签名全变了**

`list_tools` / `call_tool` **仍叫这两个名字**（本批有意保留，让 diff 最小），
但它们 1.x 与 2.x 的**签名、返回值、调用方式都不一样**：

| | 1.x | 2.x |
|---|---|---|
| 列表 | `async def list_tools() -> list[Tool]` | `async def list_tools(ctx, params) -> ListToolsResult` |
| 调用 | `async def call_tool(name, arguments)` | `async def call_tool(ctx, params) -> CallToolResult` |

⚠️ **有代码直接调它俩**（`app/tests/test_agent_repairs.py` 的 offload 守卫就调 `ms.call_tool(None, CallToolRequestParams(...))`）
⇒ 改签名时**必须一起搜全仓**：`grep -rn "\.call_tool(\|\.list_tools(" app/`。

### 5. 🔴 「改这里」= 「改四条链」—— 不是比喻

`TOOLS` 是**四条执行路径的共同上游**。往它加一行 ⇒ **四条链的 LLM 工具表全变**（那是 有意收口的结果）。
⚠️ 所以加工具时要登记的**不止这里**（**五处**，见  §三），而**后两处原本零守卫**。

### 6. ⚠️ `server` 是**模块级对象**，import 那一刻就建好了

⇒ `import mcp_server` 会**立刻**跑完：建 `TOOLS` → 按部署形态过滤 → 生成 `TOOLS_DEFINITION`/`TOOL_HANDLERS` → 建 `Server`。
⚠️ 副作用：**它 import 时会连带拉起 langchain**（经 `simple_tools` / `search_tools` / `code_executor`）
⇒ 这就是「每次工具调用 ≈ 2s」里的主要成本（子进程要重新 import 一遍）。

### 7. ⛔ **谁都不许往 stdout 写** —— 那是 MCP 的 **stdio 传输通道**

🔴 **2026-10-08立**。改前本文件有两处 `print(...)`（**stdout**），
后果是客户端**每次工具调用**都报一条：

```
ValidationError: Invalid JSON … input_value='MCP Server 启动中... 已注册 7 个工具'
```
（实测 **3/3 稳定复现**）。⚠️ 其中 `list_tools` 那处**更危险** ——
它跑在 `tools/list` **请求当中**，正是客户端在等响应的时候。

⚠️ **它不致命**（客户端记一条错就过去了，调用照常返回结果）—— **但那正是它危险的地方**：
本仓立场：**一个每次都报的错，⛔ 不该因为它不影响结果就当没事**。

✅ **两处已改 `sys.stderr`**。📌 守卫 ⇒ `app/tests/test_mcp_stdout_is_clean.py`（3 条）：
· **AST 扫**裸 `print` —— ⛔ **不能用 grep**：注释里也写着 `print(`（**就在本文件里**），
  grep 会把**说明文字**当成违规
· **反向对照**（防「AST 出错 ⇒ 返空 ⇒ 永远绿」的空壳）
· **行为面**：真起子进程，断言**它的 stdout 为空**、且启动日志**仍在 stderr**
  （⛔ 别用「删掉那行日志」让上面那条绿）

### 8. ⚠️ 那两个"遍历范围"的说法**曾经互相矛盾**，现已订正

`tool_health.py` 里曾有一句注释称「`run_health_check` 是**按 `mcp_server.TOOLS` 遍历**的」
—— 🔴 **那句是假的**（它遍历的是 `TEST_ARGS_MAP`）。**那个坑不在本文件**，但**从本文件读不出来的**是：
**「工具在 `TOOLS` 里」≠「它会被体检」**（后者还要在 `tool_health.TEST_ARGS_MAP` 里登记）。
📄 见 `app/tools/specs/tool_health.md` §⚠️ 第 1 条 · `app/tests/test_tool_registration_completeness.py`。

## 关联

- 🔧 **它产出的清单被谁用** ⇒ `app/agent/specs/agent_graph.md` · `app/agent/specs/agent_graph_advanced_learning.md` · `app/agent/specs/plan_execute.md`
- 🔧 **健康检查（那个"降级"的另一半）** ⇒ `app/tools/specs/tool_health.md`
- 🔧 **缓存在哪加** ⇒ `app/tools/specs/tool_cache.md`（缓存包在**工具函数体**上，⛔ 不在本文件）
- 📄 **"七处收口到一处"的裁定** ⇒  · **加工具要登记在五处** ⇒  §三
- 📄 **2.x 迁移（含 §⚠️ 第 1 / 2 / 3 条的出处）** ⇒  · `docs/reference/mcp-stateless-and-transport.md`
- 📄 **会话模型（"每次起子进程"的由来与代价）** ⇒  §2.1（**-B** 要动它）
