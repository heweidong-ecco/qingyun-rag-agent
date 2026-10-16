"""真起 MCP 子进程、真走 stdio 协议 —— 本仓**唯一**一条 MCP 端到端守卫。

## 为什么必须有这一条

`app/tests/test_agent_repairs.py` 里那几条 MCP 用例**全都 monkeypatch 掉**了
`stdio_client` / `ClientSession` ⇒ 它们验的是**我们和假对象的契约**，不是「真协议跑不跑得通」。

⇒ **服务端 API 接错了，现有测试一条都不会红。**
这条守的就是那个面：**真子进程 × 真协议 × 真调用**。

**成本**（实测 2026-10-08）：单次调用 **1.8s** · `list_tools` **1.4s** ⇒ 本文件约 **3s**。
   不标 `integration` / `needs_db` —— 它**不碰网、不碰库**（别为了省 3 秒把它摘出 CI，
   那等于把**唯一**能抓 API 迁移错误的东西摘掉）。

它的价值证明：**迁移前它就绿**⇒ 迁移后若变红，**一定是迁移搞坏的**。
"""
import asyncio

#: 真调用的探针工具 —— 用 `calculator`：**纯本地、无网、无副作用**，且结果是确定的
PROBE_TOOL = "calculator"
PROBE_ARGS = {"expression": "6*7"}
PROBE_EXPECT = "42"


def test_served_tools_match_the_registry():
    """**服务端真的吐出来的工具**，必须与 `mcp_server.TOOLS` **逐名一致**。

    为什么这条能抓迁移错误：`list_tools` 处理器若是**没接上**（或还留着 1.x 的装饰器形态），
    客户端会拿到**空清单或直接报错** —— 而「注册了 7 个」与「服务真出 7 个」是**两件事**。

    反证（施工单  会做）：把 `on_list_tools=` 从 `Server(...)` 里拿掉 ⇒ **本用例红**。
    """
    import tools.mcp_server as mcp_server

    from agent.agent_graph_advanced import get_mcp_tools

    served = sorted(t.name for t in asyncio.run(get_mcp_tools()).tools)
    registered = sorted(t["func"].name for t in mcp_server.TOOLS)

    assert served == registered, (
        f"🔴 服务端吐出来的工具与注册表对不上\n  服务端={served}\n  注册表={registered}"
    )
    # 反向对照：别让一条"两边都是空集"把本用例洗成绿
    assert registered, "注册表是空的 —— 本用例会退化成空集合比对"


def test_real_call_round_trips_through_stdio():
    """真调一个工具，结果**原样回来** —— 端到端把 `call_tool` 那条线走通。"""
    from agent.agent_graph_advanced import call_mcp_tool

    out = asyncio.run(call_mcp_tool(PROBE_TOOL, PROBE_ARGS))

    assert out == PROBE_EXPECT, f"期望 {PROBE_EXPECT!r}，实际 {out!r}"


def _server_child_pids() -> list:
    """找出**真的** MCP 服务端子进程。

    **必须用 `pgrep -f`（匹配【完整】命令行），不能用 `ps -eo command=` 做子串匹配** ——
       `ps` 在 **Linux 上会按终端宽度【截断】命令行**：CI 的路径长
       （`/home/runner/work/<repo>/<repo>/app/tools/mcp_server.py`），截断后 `mcp_server.py` 被切掉
       ⇒ **本地（macOS）找得到、CI（Linux）找不到**。
       **实测**（2026-10-08）：本机 860 全绿，**CI 报 `assert []`**
       —— 正是本仓记过的那一类「**本地绿当成了不依赖**」
       （本仓的复盘记录（⛔ 不随产品仓提供））。

    同时排除**本进程**（`os.getpid`）—— 别把自己当成子进程杀了。
    """
    import os
    import subprocess

    out = subprocess.run(
        ["pgrep", "-f", "tools/mcp_server.py"], capture_output=True, text=True
    ).stdout
    pids = {int(tok) for tok in out.split() if tok.isdigit()}
    pids.discard(os.getpid())
    return sorted(pids)


def test_killed_subprocess_is_recovered_on_the_next_call():
    """**真把子进程杀了，之后要能自己回来**（的验证项之一）。

    ## 为什么这条**必须是真杀**（假对象测不出来）

    **它是【真的】抓出过一个 bug 的**（2026-10-08 -B 验证阶段）：改前 `_hold` 把
      「**连接断了**」和「**工具自己报错**」混在一个 `except Exception` ⇒ 子进程一死，
      holder task **可能照样活着**（它阻塞在 `tx.get` 上）⇒ `is_alive` 恒 True
      ⇒ **永远不重建** ⇒ 之后每次调用都拿同一条死会话（**一直** `MCPError: Connection closed`）。
      现场 ⇒ `docs/说明/mcp长驻会话-调研-20261008.md` 的续篇 /

    ## 它钉的**只有一件事**：**30 秒内必须自己回来**

    · **不再**钉「发现的那一次必须失败」—— **那是时序相关的**：有时 `async with`
      **自己退出了**（`is_alive` 变 False ⇒ 第一次调用就重建成功），有时没有（堆在队列上）。
      ⇒ 取反之后输出**照样会变**，但它**不稳定** ⇒ 那条断言**不是一把可靠的尺子**，
      不该放在这里。
    · **稳定可复现的那把尺子在** `app/tests/test_mcp_long_session.py::test_transport_loss_rebuilds_the_session`
      （假对象，**反证 3/3 稳定红**）。本用例是**真环境的兜底**：它保证"真的杀了也能回来"。

    **不自动重试**：工具调用可能已有副作用，重试 = at-least-once，
      `execute_python` 那种不能这么干 ⇒ 由**调用方**决定要不要再来一次。
    """
    import asyncio
    import os
    import signal
    import time

    from agent.agent_graph_advanced import aclose_mcp_session, call_mcp_tool

    async def _scenario():
        # **整个过程必须在【一个】事件循环里** —— `asyncio.run` 每次都会关掉 loop，
        #    而关 loop 会把长驻会话一起带走（holder task 被取消 + 子进程退出）。
        #    实测踩过：在循环外"先 call 再找子进程"，**永远找不到**（会话已经收了）。
        assert await call_mcp_tool(PROBE_TOOL, PROBE_ARGS) == PROBE_EXPECT

        pids = _server_child_pids()
        assert pids, "起了会话却找不到 MCP 子进程 —— 那这条用例没验到东西"

        for pid in pids:
            os.kill(pid, signal.SIGKILL)

        # **判据只钉一件事：最终能自己回来**（30 秒内）。
        #
        # **原本还钉了「发现的那一次必须失败」，已去掉 —— 它是【时序相关】的**：
        #    · 有时**堆在队列上没人发现**（`_hold` 阻塞在 `tx.get()`，那条死会话一直"活着"）
        #      ⇒ 得等修好之后才会「失败一次、然后重建」；
        #    · 有时 `async with` **自己退出了** ⇒ `is_alive()` 变 False ⇒ **第一次调用就重建**、
        #      直接成功。
        #    ⇒ 两种情况在"修复前/修复后"都能出现 ⇒ **那条断言不是一把稳定的尺子**
        #      （本仓原话：**取反之后输出照样变的东西，才算在测那件事**）。
        # **可稳定复现的那条尺子在** `app/tests/test_mcp_long_session.py::test_transport_loss_rebuilds_the_session`
        #    （假对象，**反证 3/3 稳定红**）。本用例是**真环境的兜底**。
        deadline = time.time() + 30
        out, last = None, None
        while time.time() < deadline:
            try:
                out = await call_mcp_tool(PROBE_TOOL, PROBE_ARGS)
                break
            except Exception as exc:                # noqa: BLE001
                last = exc

        assert out == PROBE_EXPECT, (
            f"🔴 子进程被杀之后**没有自愈**（30 秒内一直失败）：{type(last).__name__}: {last}"
        )
        await aclose_mcp_session()

    asyncio.run(_scenario())
