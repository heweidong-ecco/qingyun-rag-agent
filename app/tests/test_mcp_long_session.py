"""长驻会话的口径守卫。

**全离线**：monkeypatch 掉 `stdio_client` / `ClientSession`，不起子进程。
   （"真起子进程"那件事由 `app/tests/test_mcp_protocol_e2e.py` 管，两件事别混。）

**本批的全部收益压在前两条上**：
   ① **`initialize` 只跑一次**（那 ~2 秒几乎全在这儿）
   ② **actor 按 loop 认领**（本仓大量调用点是 `asyncio.run`，每次一个新 loop）
"""
import asyncio
import contextlib


class _FakeTextContent:
    def __init__(self, text):
        self.text = text


class _FakeResult:
    def __init__(self, text):
        self.content = [_FakeTextContent(text)]


def _install_fakes(monkeypatch, m, events):
    """装假的 stdio_client / ClientSession，把**事件顺序**记下来。"""

    class _FakeSession:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            events.append("session:enter")
            return self

        async def __aexit__(self, *exc):
            events.append("session:exit")

        async def initialize(self):
            events.append("initialize")

        async def list_tools(self):
            events.append("list_tools")
            return "TOOLS"

        async def call_tool(self, name, arguments):
            events.append(f"call_tool:{name}")
            return _FakeResult(f"got:{arguments.get('expression')}")

    @contextlib.asynccontextmanager
    async def _fake_stdio(params):
        events.append("stdio:enter")
        try:
            yield (None, None)
        finally:
            events.append("stdio:exit")

    monkeypatch.setattr(m, "stdio_client", _fake_stdio)
    monkeypatch.setattr(m, "ClientSession", _FakeSession)


def test_initialize_runs_once_across_many_calls(monkeypatch):
    """**本批的全部收益就在这一条**：会话开一次、`initialize` 只跑一次。

    反证：把 actor 换回"每次调用自开自关" ⇒ `initialize` 会变成 3 次 ⇒ 本用例红。
    """
    import agent.agent_graph_advanced as m

    events = []
    _install_fakes(monkeypatch, m, events)

    async def _run():
        outs = [await m.call_mcp_tool("calculator", {"expression": str(i)}) for i in range(3)]
        await m.aclose_mcp_session()
        return outs

    outs = asyncio.run(_run())

    assert outs == ["got:0", "got:1", "got:2"]
    assert events.count("initialize") == 1, f"会话被重复初始化了：{events}"
    assert events.count("stdio:enter") == 1, f"子进程被重复起了：{events}"
    assert events.count("call_tool:calculator") == 3, f"工具调用被吞了：{events}"
    assert events[-1] == "stdio:exit", f"退出时机不对：{events}"


def test_session_is_rebuilt_for_a_new_event_loop(monkeypatch):
    """**两次 `asyncio.run` 用两个 loop** ⇒ actor 必须**认 loop**。

    本仓大量调用点就是 `asyncio.run(...)`（枚举：`grep -rn "asyncio.run(m\\.call_mcp_tool" app/test_*.py`），
       而 actor 的 `Queue` / `Future` **绑在创建它的那个 loop 上** ⇒ 不能跨 loop 复用。

    反证：去掉按 loop 认领 ⇒ 第二次会拿到"属于已关闭 loop 的队列" ⇒ 本用例红。
    """
    import agent.agent_graph_advanced as m

    events = []
    _install_fakes(monkeypatch, m, events)

    async def _one():
        return await m.call_mcp_tool("calculator", {"expression": "1"})

    assert asyncio.run(_one()) == "got:1"
    assert asyncio.run(_one()) == "got:1"
    # 两个 loop ⇒ 两套会话 ⇒ initialize 两次
    assert events.count("initialize") == 2, f"第二个 loop 复用了上一个 loop 的死会话？{events}"


def test_concurrent_calls_are_serialized_through_one_session(monkeypatch):
    """并发调用**串行**过同一个会话（MCP 单会话是请求-响应）—— 不许交叉 / 不许开第二个。"""
    import agent.agent_graph_advanced as m

    events = []
    _install_fakes(monkeypatch, m, events)

    async def _run():
        rs = await asyncio.gather(
            *[m.call_mcp_tool("calculator", {"expression": str(i)}) for i in range(5)]
        )
        await m.aclose_mcp_session()
        return rs

    rs = asyncio.run(_run())

    assert rs == [f"got:{i}" for i in range(5)]
    assert events.count("initialize") == 1, f"并发时开了不止一个会话：{events}"
    assert events.count("call_tool:calculator") == 5


def test_a_failing_call_does_not_kill_the_session(monkeypatch):
    """**一个请求失败，不该弄死整个会话** —— 那是现状都没有的脆弱性。

    反证：把 `_hold` 里的 `except` 去掉 ⇒ 第一次失败就把 holder task 带走 ⇒ 后续调用全挂 ⇒ 本用例红。
    """
    import agent.agent_graph_advanced as m

    events = []
    _install_fakes(monkeypatch, m, events)

    orig = m.ClientSession.call_tool
    boom = {"armed": True}

    async def _maybe_boom(self, name, arguments):
        if boom["armed"]:
            boom["armed"] = False
            raise RuntimeError("工具炸了")
        return await orig(self, name, arguments)

    monkeypatch.setattr(m.ClientSession, "call_tool", _maybe_boom)

    async def _run():
        with_raise = None
        try:
            await m.call_mcp_tool("calculator", {"expression": "1"})
        except RuntimeError as exc:
            with_raise = str(exc)
        second = await m.call_mcp_tool("calculator", {"expression": "2"})
        await m.aclose_mcp_session()
        return with_raise, second

    with_raise, second = asyncio.run(_run())

    assert with_raise == "工具炸了", "第一次失败应当如实冒泡给调用方"
    assert second == "got:2", f"一次失败把会话弄死了 —— 第二次拿不到结果：{second!r}"
    assert events.count("initialize") == 1, "会话被重启了 —— 本该活着"


def test_transport_loss_rebuilds_the_session(monkeypatch):
    """**传输断了 ⇒ 下一次调用必须重建会话**。

    **这一条是【真杀子进程】才照出来的**（2026-10-08 -B 验证阶段）：
       改前 `_hold` 把「**连接断了**」和「**工具自己报错**」混在一个 `except Exception` 里，
       于是子进程被杀之后 **holder task 照样活着** ⇒ `is_alive` 恒 True ⇒ **永远不重建**
       ⇒ 之后每一次调用都拿同一条死会话，**一直** `MCPError: Connection closed`。
       实测复现：调一次⇒ `kill -9` 子进程 ⇒ 再调 ⇒ **一直不恢复**。

    反证：把 `_is_transport_gone` 那支去掉（回到"只 set_exception 就继续"）
       ⇒ `initialize` 只会是 1 次 ⇒ **本用例红**。
    """
    from mcp import MCPError
    from mcp.types import CONNECTION_CLOSED

    import agent.agent_graph_advanced as m

    events = []
    _install_fakes(monkeypatch, m, events)

    armed = {"on": True}
    orig = m.ClientSession.call_tool

    async def _die(self, name, arguments):
        if armed["on"]:
            armed["on"] = False
            raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")
        return await orig(self, name, arguments)

    monkeypatch.setattr(m.ClientSession, "call_tool", _die)

    async def _run():
        try:
            await m.call_mcp_tool("calculator", {"expression": "1"})
        except MCPError:
            pass
        # **紧接着**再调（不等待）—— 这是真实场景，也是当年会报
        #    `RuntimeError: MCP 会话起不来：None` 的那个窗口
        second = await m.call_mcp_tool("calculator", {"expression": "2"})
        await m.aclose_mcp_session()
        return second

    assert asyncio.run(_run()) == "got:2"
    assert events.count("initialize") == 2, f"传输断了却没重建会话：{events}"
    assert events.count("stdio:enter") == 2, f"没起新的子进程：{events}"


def test_get_mcp_tools_goes_through_the_same_session(monkeypatch):
    """`get_mcp_tools` 与 `call_mcp_tool` **共用同一个会话**（别各开一个）。"""
    import agent.agent_graph_advanced as m

    events = []
    _install_fakes(monkeypatch, m, events)

    async def _run():
        tools = await m.get_mcp_tools()
        await m.call_mcp_tool("calculator", {"expression": "1"})
        await m.aclose_mcp_session()
        return tools

    assert asyncio.run(_run()) == "TOOLS"
    assert events.count("initialize") == 1, f"两处各开了一个会话：{events}"
    assert events.count("stdio:enter") == 1
