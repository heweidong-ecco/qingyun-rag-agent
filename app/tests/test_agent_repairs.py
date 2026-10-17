"""Agent 三线的回归测试（对应 的 就绪度探测）

背景：把 API 真起来逐个打，查出**三条 Agent 路径全断**，而 RAG 那条线完好。
三条**没有一条是业务逻辑错** —— 全是「**依赖漂移**」：装上的版本比代码写作时新。
"""
import asyncio
import contextlib
import os

import pytest


# ===========================================================================
# (a) memory_store：必须按 mem0 2.x 的签名调用
# ===========================================================================
def test_search_user_memory_matches_mem0_2x_signature(monkeypatch):
    """mem0 2.0.20 只收 `filters=` + `top_k=`；传 `user_id=`/`limit=` 会抛 ValueError。

    这是**线上真实发生的**那条异常（见模块 docstring 表 a）。
    """
    import agent.memory_store as memory_store

    captured = {}

    class _Mem0Stub:
        """复刻 mem0 2.0.20 的真实契约：旧参数一律拒绝。"""

        def search(self, query, **kwargs):
            bad = {"user_id", "limit"} & set(kwargs)
            if bad:
                raise ValueError(
                    "Top-level entity parameters frozenset({'user_id'}) are not supported "
                    "in search(). Use filters={'user_id': '...'} instead."
                )
            captured.update(kwargs)
            # **真实**形状:`{"results": [...]}` —— mem0 2.x 的 search 返回 **dict**，
            #    不是 list。第一版测试里我写成了 `[{"memory": …}]`（我以为的形状），
            #    于是"修好了"是假的 —— 线上照样 500（`TypeError: string indices
            #    must be integers`，因为遍历 dict 拿到的是键）。
            return {"results": [{"memory": "用户偏好：用表格回答"}]}

    monkeypatch.setattr(memory_store, "mem0_client", _Mem0Stub())

    out = memory_store.search_user_memory("u1", "我的偏好", top_k=3)

    assert out == ["用户偏好：用表格回答"]
    assert captured.get("filters") == {"user_id": "u1"}, "必须用 filters= 传 user_id"
    assert captured.get("top_k") == 3, "limit= 已改名为 top_k="


def test_search_user_memory_empty_still_returns_list(monkeypatch):
    """没有记忆时返回空列表（不是 None）—— 调用方靠它决定是否注入。"""
    import agent.memory_store as memory_store

    class _Empty:
        def search(self, query, **kwargs):
            return {"results": []}   # 真实形状：空也是 {"results": []}

    monkeypatch.setattr(memory_store, "mem0_client", _Empty())
    assert memory_store.search_user_memory("u1", "任意") == []


# ===========================================================================
# (b) MCP 会话：stdio_client 是 async context manager，不能 await
# ===========================================================================
def _install_mcp_fakes(monkeypatch, m, events):
    """给 MCP 客户端装一套会记录生命周期事件的替身。"""

    @contextlib.asynccontextmanager
    async def _fake_stdio(server_params):
        events.append("stdio:enter")
        try:
            yield ("READ_STREAM", "WRITE_STREAM")
        finally:
            events.append("stdio:exit")

    class _FakeSession:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            events.append("session:enter")
            return self

        async def __aexit__(self, *exc):
            events.append("session:exit")
            return False

        async def initialize(self):
            events.append("initialize")

        async def call_tool(self, name, arguments):
            events.append(f"call_tool:{name}")

            class _Content:
                text = "42"

            class _Result:
                content = [_Content()]

            return _Result()

    monkeypatch.setattr(m, "stdio_client", _fake_stdio)
    monkeypatch.setattr(m, "ClientSession", _FakeSession)


def test_call_mcp_tool_keeps_session_lifecycle_inside_one_task(monkeypatch):
    """一次 `call_mcp_tool` 里，会话是**开 → 用 → 关**（三层嵌套顺序正确）。

    **为什么不许池化**（这是 实测撞出来的）:
      `stdio_client` 基于 **anyio**，其 cancel scope 要求「**进入与退出在同一个 task**」。
      池化的生命周期天然跨 task（启动 task 建 · 请求 task 用 · 归还 task 关）
      实测报 `RuntimeError: Attempted to exit cancel scope in a different task
      than it was entered in` —— 而且它会让**应用启动直接失败**
      （`Application startup failed. Exiting.`）。

    ##：**本用例比它自称的弱，已订正**

    它**原**写的是「`call_mcp_tool` **返回时**已关闭」，并声称是"不许改回池化"的反向保护。
    **实测：那句是虚的** —— 断言只检查 `asyncio.run` **结束之后**的事件序列，
    **不检查"返回那一刻"的状态**。
    ⇒ -B 把会话改成**长驻**（正是它声称要拦的事），它**照样绿**
      （长驻会话的 `finally` 恰好在 loop 关闭那一刻跑，退出事件照样落在末尾）。

    ⇒ **"进入与退出在同一个 task"这条不变量的守卫，已经搬到**
      `app/tests/test_mcp_long_session.py`（它钉 actor 的形状：`initialize` 只一次 · 按 loop 认领
      一次失败不弄死会话）。
    **本用例仍然有用**：它钉住**三层嵌套的顺序**（stdio ⊃ session ⊃ initialize ⊃ call_tool），
      以及**跑完确实关了**（不是把会话漏出去当孤儿）。
    """
    import agent.agent_graph_advanced as m

    events = []
    _install_mcp_fakes(monkeypatch, m, events)

    out = asyncio.run(m.call_mcp_tool("calculator", {"expression": "6*7"}))

    assert out == "42"
    assert events == [
        "stdio:enter", "session:enter", "initialize",
        "call_tool:calculator", "session:exit", "stdio:exit",
    ], f"生命周期不对（嵌套/关闭时机）：{events}"


def test_mcp_server_path_does_not_depend_on_cwd(monkeypatch, tmp_path):
    """MCP server 的路径必须锚在**模块自身位置**，不能是相对 CWD 的相对路径。

    实测（这是"传输层修好了健康检查还是全红"的答案）:
      本仓文档起服务的姿势是 `cd app && uvicorn main:app`，而路径写的是
      `"app/tools/mcp_server.py"` ⇒ 从 `app/` 看是 `app/app/tools/mcp_server.py` —— **不存在**
      ⇒ MCP 子进程起不来 ⇒ 6 个工具全 unhealthy（报 `ExceptionGroup:
      unhandled errors in a TaskGroup`，子异常被 tool_health 的 except 吞掉了）。

      对照实测（同一次调用，只换 cwd）:
        · cwd = 仓库根 → `2`（成功）
        · cwd = `app/` → TaskGroup 异常

    ⇒ 本用例 `chdir` 到一个**完全无关的临时目录**，钉住"与 CWD 无关"。
    """
    import agent.agent_graph_advanced as m

    seen = {}

    class _Params:
        # ⚠️ `env` 这个形参⛔ 别删 —— 给真实构造加了这个关键字，
        #    替身不接就会 `TypeError: unexpected keyword argument 'env'`，
        #    而那会**伪装成"路径守卫红了"**（判据指错方向是本仓最恨的形态）。
        def __init__(self, command=None, args=None, env=None):
            seen["args"] = args
            seen["env"] = env

    @contextlib.asynccontextmanager
    async def _fake_stdio(server_params):
        yield ("r", "w")

    class _FakeSession:
        def __init__(self, r, w):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments):
            class _C:
                text = "ok"

            class _R:
                content = [_C()]

            return _R()

    monkeypatch.setattr(m, "StdioServerParameters", _Params)
    monkeypatch.setattr(m, "stdio_client", _fake_stdio)
    monkeypatch.setattr(m, "ClientSession", _FakeSession)

    monkeypatch.chdir(tmp_path)  # ← 换到完全无关的目录
    asyncio.run(m.call_mcp_tool("calculator", {"expression": "1+1"}))

    script = seen["args"][0]
    assert os.path.isabs(script), f"MCP server 路径必须是绝对路径，实际 {script!r}"
    assert os.path.isfile(script), f"MCP server 路径必须指向真实文件，实际 {script!r}"


def test_mcp_server_gets_the_full_environment(monkeypatch):
    """：MCP 子进程**必须拿到与父进程同一份 env**。

    ## 这条钉的是什么

    MCP 的 `stdio_client` 默认**只给子进程继承 6 个变量**
    （Linux 白名单 = `HOME` / `LOGNAME` / `PATH` / `SHELL` / `TERM` / `USER`，
     见 `mcp/client/stdio.py::DEFAULT_INHERITED_ENV_VARS`）——
    **我们 `.env` 里的东西一个都到不了**。

    ⇒ 尤其是 **`DOCKER_ENV`**：它不在白名单 ⇒ 子进程里 `IS_DOCKER=False`
    ⇒ `core/config.py` 的「本地开发覆盖」把 **`REDIS_HOST` / `POSTGRES_HOST` 双双改成
    `localhost`** ⇒ **容器里那两个服务叫 `redis` / `postgres`** ⇒ **连不上**。

    ## 🔴 为什么它值得一条守卫

    **本地永远是对的**（本机 `localhost:6379` 恰好就是真的）⇒ 这个缺陷
    **只在容器里现形**，而现形方式是**日志里一行 fail-open 的警告**
    （`工具缓存 Redis 不可用 ⇒ Error 111 connecting to localhost:6379`），
    ⛔ 不会让任何用例变红。⇒ **没有这条守卫，它下次会被原样再犯一次。**
    """
    import agent.agent_graph_advanced as m

    seen = {}

    class _Params:
        def __init__(self, command=None, args=None, env=None):
            seen["env"] = env

    @contextlib.asynccontextmanager
    async def _fake_stdio(server_params):
        yield ("r", "w")

    class _FakeSession:
        def __init__(self, r, w):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments):
            class _C:
                text = "ok"

            class _R:
                content = [_C()]

            return _R()

    monkeypatch.setattr(m, "StdioServerParameters", _Params)
    monkeypatch.setattr(m, "stdio_client", _fake_stdio)
    monkeypatch.setattr(m, "ClientSession", _FakeSession)

    # 刻意放一个**只在 `.env` 里出现**的变量，冒充 `DOCKER_ENV`
    monkeypatch.setenv("DOCKER_ENV", "true")

    async def _run():
        # ⚠️ 被钉的函数是 `mcp_session`（`@asynccontextmanager`）——
        #    它就是这个 `StdioServerParameters` 的**唯一构造处**。
        async with m.mcp_session():
            pass

    asyncio.run(_run())

    env = seen.get("env")
    assert env is not None, (
        "⛔ `StdioServerParameters` 没收到 `env=` ⇒ MCP 子进程只会继承 6 个变量 ⇒ "
        "容器里 `REDIS_HOST`/`POSTGRES_HOST` 会退回 `localhost` 而连不上"
    )
    assert env.get("DOCKER_ENV") == "true", (
        f"子进程 env 里没有 DOCKER_ENV（实际 {sorted(env)[:8]}…）⇒ 它一定是被降级成了"
        "「只继承 6 个变量的白名单」"
    )


def test_get_mcp_tools_also_keeps_lifecycle_inside_one_task(monkeypatch):
    """同上（`/agent/mcp_tools_dynamic` 走 `get_mcp_tools`）：三层嵌套顺序正确 + 跑完确实关了。

    **口径订正**：本条与上一条同因 —— 原写「必须**自开自关**」，
    而-B 之后会话是**长驻**的。**不再是"自开自关"，是"整个生命周期落在
    一个 holder task 里、且跑完会关"**。那两条不变量的新守卫 ⇒ `app/tests/test_mcp_long_session.py`
    """
    import agent.agent_graph_advanced as m

    events = []

    @contextlib.asynccontextmanager
    async def _fake_stdio(server_params):
        events.append("stdio:enter")
        try:
            yield ("r", "w")
        finally:
            events.append("stdio:exit")

    class _FakeSession:
        def __init__(self, r, w):
            pass

        async def __aenter__(self):
            events.append("session:enter")
            return self

        async def __aexit__(self, *e):
            events.append("session:exit")
            return False

        async def initialize(self):
            events.append("initialize")

        async def list_tools(self):
            events.append("list_tools")
            return "TOOLS"

    monkeypatch.setattr(m, "stdio_client", _fake_stdio)
    monkeypatch.setattr(m, "ClientSession", _FakeSession)

    assert asyncio.run(m.get_mcp_tools()) == "TOOLS"
    assert events == ["stdio:enter", "session:enter", "initialize", "list_tools",
                      "session:exit", "stdio:exit"], events


def test_mcp_server_command_uses_current_interpreter(monkeypatch):
    """MCP 子进程必须用**当前解释器**(venv 的 python)。

    裸 `"python"` 会落到系统 python —— 它**没有本仓依赖**，MCP server 起不来。
    """
    import sys

    import agent.agent_graph_advanced as m

    seen = {}

    class _Params:
        # ⚠️ `env` 同  —— 替身不接这个关键字会 `TypeError`，
        #    而那会被 `_McpSessionActor` 包成「MCP 会话起不来」，**看起来像别的问题**。
        def __init__(self, command=None, args=None, env=None):
            seen["command"] = command
            seen["args"] = args
            seen["env"] = env

    @contextlib.asynccontextmanager
    async def _fake_stdio(server_params):
        yield ("r", "w")

    class _FakeSession:
        def __init__(self, r, w):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments):
            class _Content:
                text = "ok"

            class _Result:
                content = [_Content()]

            return _Result()

    monkeypatch.setattr(m, "StdioServerParameters", _Params)
    monkeypatch.setattr(m, "stdio_client", _fake_stdio)
    monkeypatch.setattr(m, "ClientSession", _FakeSession)

    asyncio.run(m.call_mcp_tool("calculator", {"expression": "1+1"}))

    assert seen["command"] == sys.executable, (
        f"MCP 子进程应用当前解释器 {sys.executable}，实际是 {seen['command']!r}"
    )


# ===========================================================================
# (d) mcp_tool_factory：handler 必须把 arguments 字典【原样】交给 invoke()
# ===========================================================================
def test_mcp_tool_handler_passes_dict_to_invoke():
    """：原实现 `tool_func.invoke(tool_func.args_schema(**arguments))`
    把 **pydantic 实例**喂给 `StructuredTool.invoke`，而它要的是 **dict**

        TypeError: calculator missing 1 required positional argument: 'expression'

    实测对比（同一个真工具）:
      · `calculator.invoke({"expression":"1+1"})`        → `'2'`
      · `calculator.invoke(args_schema(**{...}))`        → TypeError

    后果被放大：健康检查的判据是「结果里含『**工具调用失败**』⇒ unhealthy」
       ⇒ 这个 bug 让 **6 个工具全标红**，**与传输层是否修好无关**。

    用**真实**的 `@tool`(langchain)，不用 mock —— 这里要验的正是"我们和
       langchain 的契约对不对"，mock 掉 langchain 就把要验的东西验没了。
    """
    from langchain_core.tools import tool

    import tools.mcp_tool_factory as f

    @tool
    def probe(x: str) -> str:
        """probe 工具（测试用）"""
        return f"got:{x}"

    handler = f.create_mcp_tool_handler(probe)

    # 处理器保持**同步** —— 理由见 test_mcp_server_call_tool_offloads_sync_handler。
    assert not asyncio.iscoroutinefunction(handler)
    assert handler({"x": "hello"}) == "got:hello"


def test_mcp_server_call_tool_offloads_sync_handler(monkeypatch):
    """把同步工具**丢出事件循环**这件事，必须发生在 **async 边界**（`mcp_server.call_tool`）。

    背景（两条都要满足，缺一条都不行）:
      · MCP server（`mcp_server.py:71` 的 `call_tool`）**是 asyncio 服务** ⇒ 同步工具
        直接在它的处理器里跑 ⇒ 同步 Playwright 报
        `It looks like you are using Playwright Sync API inside the asyncio loop.`
      · 但**不能在工厂里把 handler 改成 async** —— 处理器有**第三个调用方**：
        `agent_graph_advanced_learning.py:230` 在**同步**节点里 `result = handler(tool_args)`，
        拿到 coroutine 后 `str(result)` 会写成 `<coroutine object …>`（实测）
        ⇒ 三代 REACT 分支的工具调用**静默失效**。

    ⇒ **正确的落点是在 async 边界处 offload**（`await asyncio.to_thread(handler, …)`），
      处理器本身保持同步 ⇒ 三个调用方都不用改。

    这一条是"修一条路径时必须问：同一个形状还有别的入口吗"的**第四次**实例 ——
       前三次见  §六。
    """
    import tools.mcp_server as ms

    seen = {}

    def _sync_handler(arguments):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return "OFFLOADED_OK"

    monkeypatch.setitem(ms.TOOL_HANDLERS, "probe", _sync_handler)

    from mcp.types import CallToolRequestParams

    out = asyncio.run(
        ms.call_tool(None, CallToolRequestParams(name="probe", arguments={}))
    )

    #    变成 `(ctx, params)`（2.x 的构造器回调），返回值从裸 `list` 变成 `CallToolResult`
    #    （文本在 `.content[0].text`）。
    #    **下面两条断言一个字没动** —— 它们守的是"同步工具不能在事件循环里跑"，与签名无关。
    assert out.content[0].text == "OFFLOADED_OK"
    assert seen["on_loop"] is False, (
        "同步工具不能在事件循环所在线程里执行 —— 必须在 async 边界处丢到线程里"
    )



# ===========================================================================
# (e) get_llm_with_mcp_tools：list_tools 返回 ListToolsResult（不写版本号 —— 会过期）
# ===========================================================================
def test_get_llm_with_mcp_tools_unpacks_list_tools_result(monkeypatch):
    """`list_tools` 返回的是 **`ListToolsResult`**（列表在 `.tools`），不是列表本身。

    实测:原实现 `for mcp_tool in tools:` 直接遍历结果对象
       pydantic 模型迭代出的是 **(key, value) 元组**
       `AttributeError: 'tuple' object has no attribute 'name'`
       ⇒ `/agent/mcp_chat`（三代）**500**。

    用**真实的** mcp 类型构造，不用自造假对象 —— 要验的就是"我们和 mcp 的契约对不对"，
       自造一个"我以为长这样"的假对象，就把要验的东西验没了（本 PR 已经栽过一次：
       mem0 的返回形状我就是这么验错的）。
    """
    from mcp.types import ListToolsResult, Tool

    import agent.agent_graph_advanced as m

    captured = {}

    class _FakeLLM:
        """整个替换 `llm`，而不是 patch 它的方法 ——
        `ChatOpenAI` 是 pydantic 模型，直接 setattr 会抛
        `ValueError: "ChatOpenAI" object has no field "bind_tools"`（我第一版就这么错的，
        那会让用例**因错误的理由红**）。"""

        def bind_tools(self, tools):
            captured["tools"] = tools
            return "BOUND_LLM"

    monkeypatch.setattr(m, "llm", _FakeLLM())

    async def _fake_get_mcp_tools():
        return ListToolsResult(tools=[
            Tool(name="calculator", description="算数", inputSchema={"type": "object"}),
            Tool(name="date_today", description="日期", inputSchema={"type": "object"}),
        ])

    monkeypatch.setattr(m, "get_mcp_tools", _fake_get_mcp_tools)

    assert asyncio.run(m.get_llm_with_mcp_tools()) == "BOUND_LLM"
    assert [t["name"] for t in captured["tools"]] == ["calculator", "date_today"]


# ===========================================================================
# (f) 同步 Playwright 不能在事件循环所在线程里跑
# ===========================================================================
@pytest.mark.skip(
    reason="N13（2026-09-21）：该端点已按作者裁决【注释掉】（浏览器工具依赖未安装的 chromium，"
           "本仓任何部署方式都跑不了）。⛔ 这不是「这条用例没用了」—— 它守的坑是真的："
           "重新启用端点时**必须一并取消本 skip**，否则「同步 Playwright 跑在事件循环里」会踩回来。"
)
def test_fetch_webpage_is_not_invoked_on_the_event_loop(client, auth_headers, monkeypatch):
    """实测:`playwright._impl._errors.Error: It looks like you are
    using Playwright Sync API inside the asyncio loop.`（`browser_tools.py:13`）

    ⇒ 端点必须把**同步**工具调用丢到线程里（`asyncio.to_thread`）。
    不能把 `browser_tools` 改成 async —— 它同时被 **MCP server 的同步路径**调用
       （`create_mcp_tool_handler` 里是同步 `tool_func.invoke`）。

    本用例的判据：替身在被调用时检查**当前线程有没有正在跑的事件循环**。
    """
    import asyncio as aio

    import routing.api_v1_agent as api_v1_agent

    seen = {}

    class _Stub:
        def invoke(self, arguments):
            try:
                aio.get_running_loop()
                seen["on_loop"] = True
            except RuntimeError:
                seen["on_loop"] = False
            return "FAKE_HTML"

    monkeypatch.setattr(api_v1_agent, "fetch_webpage", _Stub())
    r = client.post("/api/v1/agent/fetch_webpage?url=https://example.com",
                    headers=auth_headers)

    assert r.status_code == 200
    assert seen["on_loop"] is False, (
        "同步 Playwright 不能在事件循环所在线程里执行 —— 必须丢到线程里"
    )


@pytest.mark.skip(
    reason="N13（2026-09-21）：同上（`fetch_webpage` 那条的孪生兄弟）—— 端点已注释掉。"
           "重新启用端点时必须一并取消本 skip。"
)
def test_screenshot_webpage_is_not_invoked_on_the_event_loop(client, auth_headers, monkeypatch):
    """同上（两个端点都要修 —— 防止只改一个）。"""
    import asyncio as aio

    import routing.api_v1_agent as api_v1_agent

    seen = {}

    class _Stub:
        def invoke(self, arguments):
            try:
                aio.get_running_loop()
                seen["on_loop"] = True
            except RuntimeError:
                seen["on_loop"] = False
            return "FAKE_PNG_PATH"

    monkeypatch.setattr(api_v1_agent, "screenshot_webpage", _Stub())
    r = client.post("/api/v1/agent/screenshot_webpage?url=https://example.com",
                    headers=auth_headers)

    assert r.status_code == 200
    assert seen["on_loop"] is False, (
        "同步 Playwright 不能在事件循环所在线程里执行 —— 必须丢到线程里"
    )


# ===========================================================================
# (c) /agent/langgraph_chat：等待审批时必须说出来，不能返回空答案
# ===========================================================================
def test_stopped_at_approval_is_reported_not_silently_empty():
    """停在审批点 ⇒ `status='pending_approval'` + 待批工具调用。

    旧行为：`answer` 直接取最后一条消息的 `.content`，而那条是**空**的 ⇒ 调用方收到
    200 + `answer: ""`，**看不出是在等审批**（明明有 `/agent/approve` 配套）。
    """
    from langchain_core.messages import AIMessage, HumanMessage

    from routing.api_v1_agent import summarize_agent_result

    stopped = {
        "messages": [
            HumanMessage(content="请计算 6*7"),
            AIMessage(
                content="",  # ← 关键：只带工具调用，没有文字
                tool_calls=[{"name": "calculator", "args": {"expression": "6*7"},
                             "id": "call_1", "type": "tool_call"}],
            ),
        ]
    }
    out = summarize_agent_result(stopped)

    assert out["status"] == "pending_approval"
    assert [t["name"] for t in out["pending_tool_calls"]] == ["calculator"]
    assert out.get("answer", "") == "", "等待审批时不该伪造答案"


def test_stopped_at_approval_also_when_the_model_wrote_text_first():
    """反向漏洞：模型**既写文字又调工具**时，同样是在等审批。

    真实 LLM 常见这种形态（"我来帮你算一下。" + tool_calls）。原判据写成
    `if tool_calls and not content:` ⇒ 这种形态被判成 `answered`，
    **调用方照样不知道要去 `/agent/approve`** —— 原缺陷原样保留。

    判据只需看 `tool_calls`：图带 `interrupt_before=["approval"]` ⇒ 停在审批点时末条消息
       必带 tool_calls；而 ToolMessage **没有 `tool_calls` 属性**，本来就不会误报（见下一条用例）。

    **（B4）更正**：原文这里写「**只要有 tool_calls 就一定停在审批点**」——
       **B4 之后这句是假的**（非敏感的 `calculator`/`date_today` 直接跑完，不停）。
       但**本用例仍然成立**，因为它是**手工造的 state**、不跑图：
       它测的是「**给定一个停在审批点的返回态，能不能正确报出来**」，
       不是「有 tool_calls 就会停」。**判据的等价性**见 `api_v1_agent.py` 的
       `summarize_agent_result` docstring（那里写了它依赖什么、什么时候会失效）。
    """
    from langchain_core.messages import AIMessage, HumanMessage

    from routing.api_v1_agent import summarize_agent_result

    stopped = {
        "messages": [
            HumanMessage(content="6*7=?"),
            AIMessage(content="我来帮你算一下。",
                      tool_calls=[{"name": "calculator", "args": {"expression": "6*7"},
                                   "id": "c1", "type": "tool_call"}]),
        ]
    }
    out = summarize_agent_result(stopped)

    assert out["status"] == "pending_approval"
    assert [t["name"] for t in out["pending_tool_calls"]] == ["calculator"]


def test_react_tool_list_has_no_duplicate_names():
    """REACT 子图绑定的工具列表**不能有重名** —— LLM 会直接拒收。

    实测:模块级 `agent_graph_advanced_learning.tools` 在第 47-51 行
       已列了 `fetch_webpage` / `fetch_webpage_html` / `screenshot_webpage`，
       紧接着第 54 行又 `tools.extend([fetch_webpage, fetch_webpage_html])`
       ⇒ **两个工具各出现两次** ⇒ `llm_react.bind_tools(tools)` 发给 DeepSeek 时被拒：
       `openai.BadRequestError: 400 - {'error': {'message': 'Tool names must be unique.'}}`
       ⇒ **3 代 Agent 的 REACT 分支 100% 500**。

    它此前**测不出来**，因为 `/agent/advanced_chat` 会先在 mem0 那一步 500（bug 1），
       根本走不到 REACT 分支 —— **修好 bug 1 才把它暴露出来**。
       这也是"真把 API 起来逐个打"比"只跑单测"更能发现问题的一个实例。
    """
    import agent.agent_graph_advanced_learning as L

    names = [t.name for t in L.tools]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"REACT 工具列表有重名（LLM 会拒收）：{dupes}"


def test_llm_tool_table_is_sourced_from_mcp_registry():
    """REACT 的 LLM 工具表必须与 MCP 注册表**同源** —— 否则 LLM 看得、调不到。

    实测:`mcp_server.TOOLS` 只有 **6** 个工具，而
       `agent_graph_advanced_learning.tools` 有 **7** 个（多出 `fetch_webpage_html`）。
       而 `mcp_server.TOOL_HANDLERS` 是**从 `TOOLS` 生成的** ⇒ `fetch_webpage_html`
       永远取不到 handler ⇒ 走到那一步只回一句「**未找到工具: fetch_webpage_html**」，
       **不报错、不 500** —— 是**静默失败**。

    **为什么不是"把它加进 MCP 就好"**：`fetch_webpage_html` **同样依赖 Playwright/Chromium**
       （`browser_tools.py:3` 模块级 `from playwright.sync_api import sync_playwright`
       + `p.chromium.launch`），而本仓**任何部署方式都不装浏览器**
       （`app/Dockerfile` 与 `docker-compose.yml` **都没有 `playwright install`**）
       ⇒ **加进去它也永远 unhealthy**，只是把「4/6」变成「4/7」。
       （实测报错：`BrowserType.launch: Executable doesn't exist at …chromium_headless_shell-1234…`）

    ⇒ 修法：让 LLM 工具表**从 MCP `TOOLS` 派生**（**单一事实源**）。
       这样"两表漂移"这类缺陷**结构上不可能再发生** —— 而不是这次手工对齐、下次再加工具时又漂。
    """
    import agent.agent_graph_advanced_learning as L
    import tools.mcp_server as mcp_server

    mcp_names = {t["func"].name for t in mcp_server.TOOLS}
    llm_names = {t.name for t in L.tools}

    only_llm = sorted(llm_names - mcp_names)
    only_mcp = sorted(mcp_names - llm_names)

    assert llm_names == mcp_names, (
        "LLM 工具表与 MCP 注册表不一致 —— "
        f"**LLM 有而 MCP 没有**（LLM 会调它，但永远取不到 handler ⇒ 静默失败）：{only_llm}；"
        f"**MCP 有而 LLM 没有**（注册了却从不暴露）：{only_mcp}。"
        "两表必须同源（LLM 表应由 mcp_server.TOOLS 派生）。"
    )


def test_react_subgraph_sets_final_output(monkeypatch):
    """REACT 子图必须把最终答案落成 `final_output`。

    实测:该子图只有 `agent` 与 `tools` 两个节点，**没有任何节点写
       `final_output`**，而端点读的是 `result.get("final_output", "处理完成")`
       ⇒ **走 REACT 意图时永远返回占位串「处理完成」**（其余四个子图都写了）。

    用**替身 LLM**（替换 `make_llm`）驱动，不真调模型：
       模型返回一条**不带 tool_calls** 的消息 ⇒ `should_continue` 直接 END。
    """
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

    import agent.agent_graph_advanced_learning as L

    _ANSWER = "三步计划是：先学语法，再写小项目，最后读源码。"

    class _Bound:
        def invoke(self, messages):
            return AIMessage(content=_ANSWER)

        def stream(self, messages, config=None):
            """：`agent_decide` 已从 `.invoke` 改成 `.stream`（真流式）
            ⇒ 替身必须跟上，否则 `AttributeError: '_Bound' object has no attribute 'stream'`。

            **只吐一块是故意的**：本用例测的是「`final_output` 落没落」，不是「分几块」
               —— 分块与否由 `app/tests/test_agent_stream_chains.py` 覆盖。
            必须是 `AIMessageChunk`（不是 `AIMessage`）：节点用 `+` 聚合，
               `AIMessage` **没有 `__add__`** ⇒ 会 `TypeError`。
            """
            yield AIMessageChunk(content=_ANSWER)

    class _FakeLLM:
        def __init__(self, **kwargs):
            pass

        def bind_tools(self, tools):
            return _Bound()

    #    该模块已改走 `llm_factory.make_llm()`（`ChatOpenAI` 的 import 被**删掉**了）
    #    ⇒ 旧写法会 `AttributeError: module has no attribute 'ChatOpenAI'`。
    #    这里**必须挡 `L.make_llm`**（模块内的属性引用）——
    #       挡 `llm_factory.make_llm` **不管用**：模块是 `from llm_factory import make_llm`
    monkeypatch.setattr(L, "make_llm", lambda *a, **k: _FakeLLM())
    # **必须同时挡掉记忆注入那条路** —— `agent_decide` 会调
    #    `inject_memories_to_prompt` → `search_user_memory` → **mem0 自己去连
    #    DashScope 做 embedding**。不挡的话这条用例就**依赖真凭据**：
    #    本地有真 key 所以"绿"，CI 是 dummy key
    monkeypatch.setattr(L, "inject_memories_to_prompt", lambda prompt, state: prompt)

    graph = L.create_react_subgraph()
    out = graph.invoke({"messages": [HumanMessage(content="帮我规划学习路线")]})

    assert out.get("final_output") == "三步计划是：先学语法，再写小项目，最后读源码。", (
        "REACT 子图没写 final_output ⇒ 端点只会返回占位串「处理完成」"
    )


def test_react_subgraph_aggregates_fragmented_tool_calls(monkeypatch):
    """**碎片化的 `tool_calls` 必须被 `+` 聚合还原** —— 否则工具**永远不会被执行**。

    为什么这是**真形态**：流式下 `tool_calls` 是**碎片化到达**的 ——
       实测（`langchain-core 0.3.86`）：

       | 块 | 内容 | `.tool_calls` |
       |---|---|---|
       | 前一块 | `name` + `id`，args 只有前半截 | `[{'name': …, 'args': {}, 'id': …}]`（**args 是空的**） |
       | 后一块 | `name=None`、`id=None`，args 续上后半截 | **`[]`**（整块进了 `invalid_tool_calls`） |
       | `前 + 后` | —— | `[{'name': …, 'args': {完整}, 'id': …}]` |

       ⇒ **`tool_calls` 只在【聚合后】才成立**。聚合若退化成"只留最后一块"或"只拼 `content`"，
       它会**静默变空** ⇒ 本子图的 `should_continue` 判不出 `"tools"` ⇒ 直接走 `summarize`
       ⇒ **照样写出一个看着正常的 `final_output`**，而工具**一次都没跑**。
       判据**不能**是"接口返回正常" —— 那正是上面这段说的静默失效。

    本用例是**反证式**的（做过，非声称）：把 `agent_decide` 里的
       `response = chunk if response is None else response + chunk` 改成 `response = chunk`
       ⇒ **必红**（那正是"碎片丢掉"的状态）。
    """
    from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

    import agent.agent_graph_advanced_learning as L

    # 故意用**不存在**的工具名：走 `未找到工具: …` 分支 ⇒ 不联网、不执行任何真工具。
    #    本用例要测的是「**有没有路由到 `tools`**」，不是"工具执行得对不对"。
    _TOOL = "no_such_tool_for_this_test"
    _ANSWER = "答案是 42。"

    class _Bound:
        def __init__(self):
            self.calls = 0

        def stream(self, messages, config=None):
            self.calls += 1
            if self.calls == 1:
                # 照抄真 provider 的碎片形态：**两块**才拼得出一个 `tool_calls`。
                yield AIMessageChunk(content="", tool_call_chunks=[
                    {"name": _TOOL, "args": '{"url":', "id": "c1", "index": 0},
                ])
                yield AIMessageChunk(content="", tool_call_chunks=[
                    {"name": None, "args": ' "https://example.com"}', "id": None, "index": 0},
                ])
            else:
                yield AIMessageChunk(content=_ANSWER)

    class _FakeLLM:
        def __init__(self, **kwargs):
            pass

        def bind_tools(self, tools):
            return _Bound()

    # 同 `test_react_subgraph_sets_final_output`：必须挡 `L.make_llm`（模块内的名字绑定），
    #    挡 `llm_factory.make_llm` **不管用**。
    monkeypatch.setattr(L, "make_llm", lambda *a, **k: _FakeLLM())
    # 也必须挡掉记忆注入那条路 —— 否则会去连 DashScope 做 embedding
    #    （本机有真 key 所以"绿"，CI 是 dummy key ⇒ 401 ⇒ CI 红）。
    monkeypatch.setattr(L, "inject_memories_to_prompt", lambda prompt, state: prompt)

    graph = L.create_react_subgraph()
    out = graph.invoke({"messages": [HumanMessage(content="抓一下 example.com")]})

    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs, (
        "聚合丢了碎片化的 tool_calls ⇒ should_continue 判不出 tools ⇒ 工具一次都没跑，"
        f"而 final_output 照样有值：{[type(m).__name__ for m in out['messages']]}"
    )
    # 下面两条**比"有 ToolMessage"更强**：`name` 与 `id` **都在前一块**上，
    #    能对上 ⇒ 证明 `+` 真的**跨块合并**了，不是"某一块的 `tool_calls` 恰好还在"。
    assert tool_msgs[0].name == _TOOL
    assert tool_msgs[0].tool_call_id == "c1"
    assert out.get("final_output") == _ANSWER, (
        f"工具跑完后应回到 agent 拿到终稿，实际：{out.get('final_output')!r}"
    )


def test_mcp_tools_dynamic_handles_list_tools_result(client, auth_headers, monkeypatch):
    """`/agent/mcp_tools_dynamic` 也必须解 `.tools` —— 与 bug 7 是**同一个 bug 的第二个入口**。

    实测:`get_mcp_tools` 返回 `ListToolsResult`，而该端点写的是
       `for t in tools` + `len(tools)` ⇒ `AttributeError: 'tuple' object has no attribute
       'name'` ⇒ **该路由 100% 500**。

    这是"修一条路径时必须问：同一个形状还有别的入口吗"的**第五次**实例 ——
       `get_mcp_tools` 全仓只有**两个**调用方，改了一个漏了另一个，grep 一次就能发现。
    """
    from mcp.types import ListToolsResult, Tool

    import routing.api_v1_agent as api_v1_agent

    async def _fake_get_mcp_tools():
        return ListToolsResult(tools=[
            Tool(name="calculator", description="算数", inputSchema={"type": "object"}),
            Tool(name="date_today", description="日期", inputSchema={"type": "object"}),
        ])

    monkeypatch.setattr(api_v1_agent, "get_mcp_tools", _fake_get_mcp_tools)

    r = client.get("/api/v1/agent/mcp_tools_dynamic", headers=auth_headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["name"] for t in body["tools"]] == ["calculator", "date_today"]
    assert body["total"] == 2


def test_completed_run_returns_the_answer_normally():
    """正常跑完（最后一条有文字）⇒ 照旧返回答案，状态为 answered。"""
    from langchain_core.messages import AIMessage, HumanMessage

    from routing.api_v1_agent import summarize_agent_result

    done = {"messages": [HumanMessage(content="6*7=?"), AIMessage(content="42")]}
    out = summarize_agent_result(done)

    assert out["status"] == "answered"
    assert out["answer"] == "42"
    assert "pending_tool_calls" not in out


def test_plain_tool_message_is_not_mistaken_for_approval():
    """反向用例：最后一条是 ToolMessage（工具已执行完）**不算**待审批。

    没有这条，把"最后一条没有文字"一律判成待审批的实现也能让上面两条过 ——
    而那会把"工具刚跑完、正要生成最终答案"的正常中途态误报成待审批。
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from routing.api_v1_agent import summarize_agent_result

    mid = {
        "messages": [
            HumanMessage(content="6*7=?"),
            AIMessage(content="", tool_calls=[{"name": "calculator", "args": {},
                                               "id": "c1", "type": "tool_call"}]),
            ToolMessage(content="42", tool_call_id="c1"),
        ]
    }
    out = summarize_agent_result(mid)

    assert out["status"] != "pending_approval", "工具已执行完，不是待审批"
