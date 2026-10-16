"""
MCP Server：使用工厂函数自动注册所有工具
"""
import asyncio
import os
import sys
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# 2026-10-09（模块化重构）：**本文件会被【当脚本】跑** ——
#    MCP stdio 的子进程就是 `python app/tools/mcp_server.py`
#    （见 `agent/agent_graph_advanced.py` 的 `_MCP_SERVER_SCRIPT` + `StdioServerParameters`）。
#
#    **被当脚本跑时 `sys.path[0]` = 本文件所在目录**（`app/tools/`），
#       而下面那些 `from tools.xxx import` / `from core.xxx import` 的根是 **`app/`**
#       ⇒ **全都解析不到** ⇒ 子进程起不来（表现为「MCP 会话起不来」，
#       而不是一句清楚的 ModuleNotFoundError —— 因为它被包在 TaskGroup 里了）。
#    ⇒ 显式把 `app/`（= 本文件的上一级）塞进 sys.path。
#    被 `import tools.mcp_server` 时 `app/` 已在路径上 ⇒ 这个分支不执行，**无副作用**。
_API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

# 导入所有工具（从各自独立的模块）
from tools.simple_tools import calculator, date_today, date_calc, json_extract, stats  # 新增导入
from tools.search_tools import web_search
# 2026-09-21 注释（N13 · 需求方裁「挂起 + 注释掉 + 标『# 可扩展能力』」）：
#    `browser_tools` 的三个工具依赖 Playwright 的 chromium，而**本仓任何部署方式都没装它**
#    （`app/Dockerfile` / `docker-compose.yml` 都没有 `playwright install`）⇒ 调用必抛
#      `BrowserType.launch: Executable doesn't exist at .../chromium_headless_shell-1234/...`
#    **实测（2026-09-21）**：本机 playwright 是 1.62（要 build **1234**），
#       缓存里只有旧的 **1228**（556 MB）⇒ 版本不匹配，**照样跑不了**。
#    ⇒ **# 可扩展能力**：装好 chromium 后，把下面这两行取消注释即可启用（见 `TOOLS`）。
# from browser_tools import fetch_webpage, screenshot_webpage
from tools.code_executor import execute_python

# 导入工厂函数
from tools.mcp_tool_factory import create_mcp_tool_definition, create_mcp_tool_handler

# 2026-10-08：`Server(...)` 的**创建挪到了文件末尾**（两个处理器定义之后）——
#    2.x 的处理器是**构造参数**（`on_list_tools=` / `on_call_tool=`），不再是装饰器
#    ⇒ 建 server 那一刻必须已经拿得到那两个函数对象。详见文末那段。

# 将所有工具放入一个列表（新增工具只需在这里加一行！）
# 新增 工具列表（为每个工具可指定版本号）
TOOLS = [
    {"func": calculator, "version": "1.0.0"},
    {"func": date_today, "version": "1.0.0"},
    # 2026-10-08：三个本地纯函数工具。四条执行路径**自动**拿到它们
    #    （全部派生自本列表 ——  的「一处事实源」），别处一行都不用改。
    {"func": date_calc, "version": "1.0.0"},
    {"func": json_extract, "version": "1.0.0"},
    {"func": stats, "version": "1.0.0"},
    {"func": web_search, "version": "2.0.0"},  # 已升级到 v2
    # 2026-09-21 注释（N13）：**# 可扩展能力** —— 依赖未安装的 chromium，调用必失败。
    #    两个原因缺一不可（都实测过）：
    #      ① 本仓任何部署方式都没装 chromium（Dockerfile / compose 里都没有 `playwright install`）
    #      ② 本机缓存里的 chromium 是旧 build（1228），而 playwright 1.62 要 1234 ⇒ 版本不匹配
    #    ⇒ 取消注释前**先确认 chromium 真的装好了**，并用
    #      `app/tests/test_agent_repairs.py` 里那两条（现已 skip）的用例验回来。
    #    **为什么注释掉而不是留着**：这两行在 `TOOLS` 里 ⇒ LLM 的工具表**从 TOOLS 派生**
    #       （2026-09-20 裁「乙」）⇒ 留着就等于**给 LLM 一个每调必炸的工具**（实测确认过）。
    # {"func": fetch_webpage, "version": "1.0.0"},
    # {"func": screenshot_webpage, "version": "1.0.0"},
    {"func": execute_python, "version": "1.5.0"},  # 已迭代多次
]

# 🔴 2026-10-16：**删掉了原来那个「单容器部署」开关的分支** —— 它随那套部署一起下线了。
#
#   ⇒ `execute_python` 会**回落本地子进程**（= 宿主同权限）。
#   需求方当时原话：「不要暴露在系统中执行，**是安全事故**」。
#   ⇒ 当年的做法是「**不注册它**」—— 「没有容器」和「跑在宿主上」之间，不选后者。
#
# ⚠️ **现在它是【死配置】**（2026-10-16 实测：`docker-compose.yml` · `.env.example`
#   全部 `scripts/` 里**没有任何地方再设那个环境变量**；设它的那套部署本人也早删了）
#   ⇒ 那个 `if` **永不执行** ⇒ 删掉是**行为不变**的收尾，⛔ 不是"改了行为"。
#
# 🔴 **但那条安全口径没有消失**：`execute_python` 走**独立的执行器容器**
#   （`docker-compose.yml` 的 `executor`），⛔ **不在应用进程里起子进程**。
#   ⇒ **将来若要支持"没有执行器容器"的部署**：⛔ 别用"回落本地子进程"那种做法，
#     要显式地**不注册这个工具**（同当年），并把开关起一个与平台无关的名字。
#
# 守卫：`test_tool_registry_single_source.py::test_execute_python_is_registered`（**正向对照**）。


# 工具定义和处理器由工厂函数自动生成（不再需要手动维护映射表）
# 新增 版本号
TOOLS_DEFINITION = {
    tool["func"].name: create_mcp_tool_definition(tool["func"], version=tool["version"])
    for tool in TOOLS
}
TOOL_HANDLERS = {
    tool["func"].name: create_mcp_tool_handler(tool["func"])
    for tool in TOOLS
}


# 增加健康检查过滤。
from tools.tool_health import get_tool_health, UNHEALTHY


# 工具列表接口
# 2026-10-08**签名变了** —— 1.x 是 `@server.list_tools` 装饰的**空参**函数，
#    2.x 是**构造器回调** `(ctx, params) -> ListToolsResult`。
#    **返回值也从裸 `list[Tool]` 变成 `ListToolsResult(tools=[...])`**。
async def list_tools(ctx, params) -> types.ListToolsResult:
    """返回所有可用工具的清单（自动过滤不健康的工具）"""
    tools = []
    for tool_def in TOOLS_DEFINITION.values():
        tool_name = tool_def["name"]

        # 检查工具健康状态
        health = get_tool_health(tool_name)

        if health == UNHEALTHY:
            # 2026-10-08：**必须是 stderr** —— 见文件末尾那段「谁都不许往
            #    stdout 写」。这一处**尤其**危险：它跑在 `tools/list` **请求当中**，
            #    正是客户端在等响应的时候。
            print(f"工具 {tool_name} 不健康，已从工具列表中移除", file=sys.stderr)
            continue  # 跳过不健康的工具

        tools.append(Tool(
            name=tool_def["name"],
            description=tool_def["description"],
            inputSchema=tool_def["inputSchema"]
        ))
    return types.ListToolsResult(tools=tools)


# 工具调用接口
# 2026-10-08**签名变了** —— 1.x 是 `(name, arguments)`，2.x 从 `params` 上取。
#    **`params.arguments` 缺省是 `None`，不是 `{}`**（实测
#    `CallToolRequestParams(name="x").arguments is None`）⇒ 必须 `or {}`。
async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    """接收工具调用请求，转发给实际工具并返回结果"""
    name = params.name
    arguments = params.arguments or {}
    if name not in TOOL_HANDLERS:
        # 「找不到工具」按**协议级错误**抛 —— 与 `CallToolResult` 的 docstring 一致：
        #    "Errors in finding the tool, or any other exceptional condition,
        #     should be reported as an MCP error response."
        raise ValueError(f"未知工具: {name}")

    handler = TOOL_HANDLERS[name]
    # 2026-09-20 **在 async 边界处 offload**：
    #    本服务是 asyncio 的，而工具可能是**同步**的（如 browser_tools 的同步 Playwright）
    #    —— 直接在事件循环里执行会报
    #    `It looks like you are using Playwright Sync API inside the asyncio loop.`
    #    ⇒ `fetch_webpage` / `screenshot_webpage` 永远 unhealthy（健康检查 4/6 而非 6/6）。
    #    为什么 offload 放在**这里**、而不是把 handler 改成 async：
    #       处理器还有**同步**调用方（`agent_graph_advanced_learning.py:230`），
    #       改成 async 会让那边拿到 coroutine ⇒ 工具静默失效（实测）。见 mcp_tool_factory 的注释。
    result = await asyncio.to_thread(handler, arguments)
    return types.CallToolResult(content=[TextContent(type="text", text=str(result))])


# 2026-10-08：2.x 的处理器是**构造参数**，不再是装饰器
#    ⇒ `Server(...)` 必须建在两个处理器**定义之后**（1.x 那行原先在文件顶部，已挪到这里）。
server = Server("agent-tools", on_list_tools=list_tools, on_call_tool=call_tool)

# MCP Server 启动入口
async def run_mcp_server():
    """启动 MCP Server"""
    # 2026-10-08：**stderr**，不是 stdout。
    #    改前这里是 `print(...)` ⇒ **每次调用**都会让客户端报一条
    #      `ValidationError: Invalid JSON … input_value='MCP Server 启动中... 已注册 7 个工具'`
    #    （实测 3/3 稳定复现）—— 因为 **stdout 就是 MCP 的 stdio 传输通道**。
    #    现场与数据 ⇒ `docs/说明/mcp长驻会话-调研-20261008.md` §五
    print(f"MCP Server 启动中... 已注册 {len(TOOLS)} 个工具", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(run_mcp_server())