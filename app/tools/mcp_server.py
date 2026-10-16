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


#    MCP stdio 的子进程就是 `python app/tools/mcp_server.py`
#    （见 `agent/agent_graph_advanced.py` 的 `_MCP_SERVER_SCRIPT` + `StdioServerParameters`）。
_API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

# 导入所有工具（从各自独立的模块）
from tools.simple_tools import calculator, date_today, date_calc, json_extract, stats  # 新增导入
from tools.search_tools import web_search
#    `browser_tools` 的三个工具依赖 Playwright 的 chromium，而**本仓任何部署方式都没装它**
#    （`app/Dockerfile` / `docker-compose.yml` 都没有 `playwright install`）⇒ 调用必抛
#      `BrowserType.launch: Executable doesn't exist at .../chromium_headless_shell-1234/...`
from tools.code_executor import execute_python

# 导入工厂函数
from tools.mcp_tool_factory import create_mcp_tool_definition, create_mcp_tool_handler

#    2.x 的处理器是**构造参数**（`on_list_tools=` / `on_call_tool=`），不再是装饰器
#    ⇒ 建 server 那一刻必须已经拿得到那两个函数对象。详见文末那段。

# 将所有工具放入一个列表（新增工具只需在这里加一行！）
# 新增 工具列表（为每个工具可指定版本号）
TOOLS = [
    {"func": calculator, "version": "1.0.0"},
    {"func": date_today, "version": "1.0.0"},
    #    （全部派生自本列表 ——  的「一处事实源」），别处一行都不用改。
    {"func": date_calc, "version": "1.0.0"},
    {"func": json_extract, "version": "1.0.0"},
    {"func": stats, "version": "1.0.0"},
    {"func": web_search, "version": "2.0.0"},  # 已升级到 v2
    #    两个原因缺一不可（都实测过）：
    #      ① 本仓任何部署方式都没装 chromium（Dockerfile / compose 里都没有 `playwright install`）
    #      ② 本机缓存里的 chromium 是旧 build（1228），而 playwright 1.62 要 1234 ⇒ 版本不匹配
    #    ⇒ 取消注释前**先确认 chromium 真的装好了**，并用
    {"func": execute_python, "version": "1.5.0"},  # 已迭代多次
]

#   ⇒ `execute_python` 会**回落本地子进程**（= 宿主同权限）。
#   作者当时原话：「不要暴露在系统中执行，**是安全事故**」。
#   ⇒ 当年的做法是「**不注册它**」—— 「没有容器」和「跑在宿主上」之间，不选后者。


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
    #    本服务是 asyncio 的，而工具可能是**同步**的（如 browser_tools 的同步 Playwright）
    #    —— 直接在事件循环里执行会报
    #    `It looks like you are using Playwright Sync API inside the asyncio loop.`
    #    ⇒ `fetch_webpage` / `screenshot_webpage` 永远 unhealthy（健康检查 4/6 而非 6/6）。
    result = await asyncio.to_thread(handler, arguments)
    return types.CallToolResult(content=[TextContent(type="text", text=str(result))])


#    ⇒ `Server(...)` 必须建在两个处理器**定义之后**（1.x 那行原先在文件顶部，已挪到这里）。
server = Server("agent-tools", on_list_tools=list_tools, on_call_tool=call_tool)

# MCP Server 启动入口
async def run_mcp_server():
    """启动 MCP Server"""
    #    改前这里是 `print(...)` ⇒ **每次调用**都会让客户端报一条
    #      `ValidationError: Invalid JSON … input_value='MCP Server 启动中... 已注册 7 个工具'`
    #    （实测 3/3 稳定复现）—— 因为 **stdout 就是 MCP 的 stdio 传输通道**。
    #    现场与数据 ⇒ `docs/说明/mcp长驻会话-调研-20261008.md` §五
    print(f"MCP Server 启动中... 已注册 {len(TOOLS)} 个工具", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(run_mcp_server())