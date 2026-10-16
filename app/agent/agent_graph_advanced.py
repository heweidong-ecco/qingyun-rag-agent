"""
Agent 图（集成 MCP Client）
LangGraph 进阶示例：多分支路由与子图协作
"""
from typing import TypedDict, List, Annotated, Optional
import asyncio
import operator
import os
from loguru import logger

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from core.llm_factory import make_llm   # ①b：model / api_key / base_url / max_tokens 的唯一落点
from langchain_core.messages import AIMessage, ToolMessage,SystemMessage
from langchain_core.runnables import RunnableConfig   # B1：节点要靠它把回调接进模型调用
from billing.token_tracker import record_usage
# 新增 预估消耗的前置检查
from billing.token_tracker import check_token_budget
# 工具调用前插入预算检查 Token预算
from billing.token_tracker import check_multilevel_budget

# ==================== 导入 MCP Client ====================
import anyio
from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CONNECTION_CLOSED

# ==================== 定义全局 State ====================
class AgentState(TypedDict):
    messages: Annotated[List, operator.add]
    intent: str  # 存储用户意图（search/calculator/date）
    final_output: str  # 存储最终回复
    user_name: str          # 新增：当前对话的用户名
    memory_space: str       # 新增：当前使用的记忆空间
    thread_id: str          # 新增：当前会话的 thread_id
    # **它是「拦没拦住」通往【端点层】的唯一通道** —— 端点据此回 429 / error 帧。
    # **别拿末条 `ToolMessage` 的中文文案当判据**（那是子串判据，`plan_execute` 的
    #    刚把同型写法清掉：改一个字的措辞就静默失效，工具正文里恰好出现那四个字就误判）。
    budget_intercept: Optional[str]

# ==================== 初始化模型 ====================
#    同型问题 `plan_execute.py:70-76` 早就修过，**这里漏了**。
#    ⇒ **没 timeout = 上游挂了就一起挂着**；而 SDK 默认 `max_retries=2`
#      ⇒ 一次失败**静默重试 2 次、烧 3 倍额度**，可这条链上本来就带 token 预算检查。
AGENT_LLM_TIMEOUT = 60
# 角色 = 「模型轴 chat」+「长度轴 agent(1024)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
#    `timeout` / `max_retries` 走 `make_llm` 的 `**extra` 透传（本处不再自己写 model/max_tokens）。
llm = make_llm(
    "chat", "agent",
    timeout=AGENT_LLM_TIMEOUT,       # S12
    max_retries=1,                   # S12：不用 SDK 默认的 2（会烧 3 倍额度）
)
# ==================== 导入 长期记忆Mem0 模块 ====================
from agent.memory_store import search_user_memory

def inject_memories_to_prompt(original_prompt: str, state: AgentState) -> str:
    """
    检索相关长期记忆，并将其注入到 Prompt 开头。
    如果检索不到，则返回原始 Prompt。
    """
    user_name = state.get("user_name", "default_user")
    memory_space = state.get("memory_space", "default")
    
    # 从消息列表中提取用户查询
    messages = state.get("messages", [])
    if messages:
        user_query = messages[-1].content
    else:
        user_query = ""

    user_id = f"{user_name}:{memory_space}"
    #    同一条道理：记忆是「有更好」的锦上添花，检索失败（key 不对 / 网络不通 /
    #    mem0 或向量库挂了…）只该**少一段上下文**，不许把整条链打挂。
    #    改前这里是**裸调用** ⇒ `/agent/mcp_chat`（走本文件的 `mcp_agent`）在 embedding
    #       取不到时**整条 500**；本机看不见（有真 key ＋ Redis 里有 embedding 缓存）。
    try:
        memories = search_user_memory(user_id, user_query)
    except AssertionError:
        # **这个不许吞**：`test_budget_soft_return._no_memory` 会把检索换成一个
        #    「一调就炸」的探针（它守的是「钱不许花在预算闸之前」），炸出来的正是
        #    `AssertionError` ⇒ 落进下面那个分支就会被**静默降级**，那条守卫当场失效。
        raise
    except Exception as e:                  # noqa: BLE001 —— 见上：**任何**失败都只该降级
        # **留痕，不是静默吞掉** —— 静默的话，"记忆一直没生效"没有任何人会发现。
        logger.warning(
            f"[memory] 长期记忆检索失败 ⇒ 本次不注入（fail-open）：{type(e).__name__}: {e}")
        memories = []

    if memories:
        memory_text = "\n".join(memories)
        enhanced_prompt = f"""以下是与用户相关的长期记忆，请参考这些信息来个性化你的回答：
{memory_text}

{original_prompt}"""
        return enhanced_prompt
    return original_prompt

# ==================== MCP Client 连接管理 ====================
# 曾经的方案是「`_mcp_session` 全局单例，所有请求共享同一个会话」，它有两个毛病：
#   并发阻塞：全局单例是同步的，一个请求正在调用工具时，另一个请求必须等待。
#   状态污染：不同用户的调用可能互相影响。
"""
MCP Client 连接管理（**单 task 自开自关**版）

🔴 2026-09-20：**会话池已移除** —— 它不是在"修 bug"，而是把一个
   **从来没真正生效、且架构上不可行**的机制拆掉。两条证据：
   ① **它没在生效**：`initialize_pool()` **无任何调用方**（`main.py` 不调它，
      成功启动日志里「MCP 会话池已初始化」**0 次**）；`release_mcp_session()` /
      `close_all_sessions()` 也**零调用方** ⇒ 实际行为一直是
      "每次调用新建一个会话、用完不归还"。
   ② **它不可行**：`stdio_client` 基于 **anyio**，其 cancel scope 要求
      「**进入与退出在同一个 task**」。池化的生命周期天然跨 task
      （启动 task 建 · 请求 task 用 · 归还 task 关）⇒ 实测报
      `RuntimeError: Attempted to exit cancel scope in a different task than
      it was entered in`，**并让应用启动直接失败**
      （`Application startup failed. Exiting.`）。
      ⚠️ 那比原来的缺陷更糟：原来是"应用能跑、只是 MCP 健康检查挂"。

   ⇒ 现在的契约：**谁调用，谁在【自己这个 task 内】把会话开出来、用完关掉。**
   （代价：每次调用起一个 MCP 子进程。本仓的工具调用量下可接受；
     要复用会话，得上 HTTP/SSE transport —— 那是另一个方案。）

   回归测试：`app/tests/test_agent_repairs.py::test_call_mcp_tool_keeps_session_lifecycle_inside_one_task`
"""
#    （本文件 :8 与 :27-28 已经有了）—— 已删除。`sys` / `asynccontextmanager` /
#    `Path` 是本段新增、别处没有，故只补这三个。
# **更正**：那句「**本文件 :8 已经有 `asyncio`**」**是假的** ——
#    去重时把**唯一**那一处也删掉了，而当时**没有任何代码用它**，所以一直没暴露。
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# MCP server 脚本的**绝对**路径 —— 锚在本文件旁边，**与 CWD 无关**。
_MCP_SERVER_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "mcp_server.py"


@asynccontextmanager
async def mcp_session():
    """在**当前 task 内**开一个 MCP 会话，并保证退出时关掉。

    `command` 必须是**当前解释器**(`sys.executable`) —— 裸 `"python"` 会落到
       系统 python，而它**没有本仓依赖**，MCP server 起不来。

    server 脚本路径必须**锚在模块自身位置**，不能写 `"app/tools/mcp_server.py"` ——
       那是**相对 CWD** 的：本仓起服务的姿势是 `cd app && uvicorn main:app`，
       从 `app/` 看它解析成 `app/app/tools/mcp_server.py`（**不存在**）⇒ 子进程起不来
       ⇒ 6 个工具全 unhealthy（报 `ExceptionGroup: unhandled errors in a TaskGroup`，
       子异常还被 tool_health 的 except 吞掉，**看不出真因**）。
       实测对照（同一次调用，只换 cwd）:仓库根 → `2`；`app/` → TaskGroup 异常。
       回归测试:`app/tests/test_agent_repairs.py::test_mcp_server_path_does_not_depend_on_cwd`
    """
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(_MCP_SERVER_SCRIPT)],
        # 🔴 **它在本地【永远看不出来】** —— 本机 `localhost:6379` 恰好是对的
        env={**os.environ},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ==================== 长驻会话 · actor 模式（· -B）====================
# **为什么是 actor，不是会话池**（栽过的那次记在上面那段 docstring 里）：
#    `stdio_client` 基于 anyio，其 cancel scope 要求「**进入与退出在同一个 task**」。
#    · 池化 = 「启动 task 建 · 请求 task 用 · 归还 task 关」⇒ **横跨 3 个 task** ⇒ 炸
class _McpSessionActor:
    """持有唯一一个长驻 MCP 会话；调用方**投请求、等 future**，一个字节都不碰 session。"""

    def __init__(self):
        self._loop = asyncio.get_running_loop()
        self._tx: "asyncio.Queue" = asyncio.Queue()
        self._ready: "asyncio.Future" = self._loop.create_future()
        self._task: "asyncio.Task | None" = None
        self._dead_reason: str | None = None
        self._restarts = 0

    @property
    def loop(self):
        return self._loop

    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return
        # **把队列/ready 【捕获】成局部量，交给 holder task** —— 别让它在自己体内读 `self.*`。
        #    这一条是【实测撞出来的】：旧 holder 退出时若去读 `self._tx` / `self._ready`，
        #    读到的**已经是被新一代换掉的那两个** ⇒ 它的 `finally` 会把**新会话的启动**
        #    打成 `RuntimeError: MCP 会话起不来`。
        tx = asyncio.Queue()
        ready = self._loop.create_future()
        self._tx, self._ready = tx, ready
        self._task = asyncio.create_task(self._hold(tx, ready), name="mcp-session-holder")
        await ready            # 等 initialize 完成 —— 别让第一个请求自己去撞握手
        # 起来了就把计数清零 —— 限流管的是「**连续**起不来」，不是"这一辈子只能崩 5 次"
        self._restarts = 0

    async def _hold(self, tx, ready) -> None:
        """**整个会话生命周期都在这个 task 里** —— 这是那道坎的解法，别把 session 漏出去。

        **`tx` / `ready` 是【构造那一刻捕获进来的局部量】，不许在体内读 `self._tx` / `self._ready`**：
           旧 holder 退出时那两个**可能已经被新一代换掉** ⇒ 它的 `finally` 会把**新会话的启动**打死。
           （实测现象：`kill -9` 子进程后**紧接着**再调 ⇒ `RuntimeError: MCP 会话起不来：None`。）
        """
        try:
            async with mcp_session() as session:
                if not ready.done():
                    ready.set_result(True)
                while True:
                    item = await tx.get()
                    if item is None:
                        break
                    name, args, fut = item
                    if fut.done():
                        continue        # 调用方已经不要了（超时/取消）⇒ 别白跑
                    try:
                        if name == "__list_tools__":
                            fut.set_result(await session.list_tools())
                        else:
                            fut.set_result(await session.call_tool(name, args))
                    except Exception as exc:        # noqa: BLE001
                        fut.set_exception(exc)
                        if _is_transport_gone(exc):
                            # **传输断了 ⇒ 会话作废，出循环**（`async with` 退出
                            #    `is_alive()` 变 False ⇒ 下次调用重建）。
                            #    **不能只是 set_exception 就继续** —— 那样 holder task
                            #    会带着一条死会话一直活着（实测踩过，见 `_is_transport_gone`）。
                            self._dead_reason = f"{type(exc).__name__}: {exc}"
                            break
                        # **工具自己的错不该弄死整个会话** —— 那是"每次自开自关"都没有的
                        #    脆弱性：长驻之后，一次工具异常会波及**之后所有**调用。
        except Exception as exc:                    # noqa: BLE001
            self._dead_reason = f"{type(exc).__name__}: {exc}"
        finally:
            if not ready.done():
                ready.set_exception(
                    RuntimeError(f"MCP 会话起不来（{type(self).__name__}）：{self._dead_reason}")
                )
            # **退出时把【自己这条】队列里还压着的请求失败掉** —— 别让它们悬着。
            #    并发的调用会排在队列里；holder task 一旦走人，那些 future
            #    **再也不会有人 set** ⇒ 调用方**永远挂着**（比报个错糟得多）。
            #    **只清 `tx`（我这条），不碰 `self._tx`**（那可能已经是下一代的了）。
            while True:
                try:
                    _, _, pending = tx.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if pending is not None and not pending.done():
                    pending.set_exception(
                        RuntimeError(f"MCP 会话已结束：{self._dead_reason}")
                    )

    async def _request(self, name, args=None):
        # 会话没了（崩了 / 被关了）⇒ **重建**，别让调用方拿到一个死队列。
        # **判据要带 `_dead_reason`**：holder task 从"发现传输断了"到"真的结束"之间
        #    有一小段窗口 —— 那时 `is_alive()` 还是 True，但**已经没人读队列了**。
        #    只看 `is_alive()` 会把请求投进一个死队列 ⇒ **永远挂着**。
        if self._dead_reason is not None or not self.is_alive():
            self._restarts += 1
            if self._restarts > 5:
                raise RuntimeError(
                    f"MCP 会话反复起不来（已试 {self._restarts} 次）：{self._dead_reason}"
                )
            self._task = None
            self._dead_reason = None
            await self.start()      # 队列/ready 由 `start` **新建并捕获** —— 别在这里也建一份
        fut = self._loop.create_future()
        await self._tx.put((name, args, fut))
        return await fut

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = await self._request(tool_name, arguments)
        # 结果是一个 Content 列表，提取文本内容
        if result.content:
            return result.content[0].text
        return "工具返回了空结果"

    async def list_tools(self):
        return await self._request("__list_tools__")

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._tx.put(None)
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None


#: **按 loop 认领**：`asyncio.run` 每次都开一个新 loop，而 actor 的 `Queue` / `Future`
#: **绑在创建它的那个 loop 上** ⇒ 不能跨 loop 复用（本仓大量调用点就是 `asyncio.run`）。
#: **「传输没了」这一类异常** —— **别与"工具自己报错"混为一谈**。
def _is_transport_gone(exc: BaseException) -> bool:
    """这条异常是不是在说「**传输断了**」（区别于「工具自己报错」）。"""
    if isinstance(exc, (
        anyio.ClosedResourceError,
        anyio.BrokenResourceError,
        anyio.EndOfStream,
        BrokenPipeError,
        ConnectionError,
    )):
        return True
    if isinstance(exc, MCPError):
        return exc.code == CONNECTION_CLOSED
    return False


_ACTOR: "_McpSessionActor | None" = None


async def _get_actor() -> "_McpSessionActor":
    """拿（必要时建）**当前 loop** 的那个 actor。"""
    global _ACTOR
    loop = asyncio.get_running_loop()
    if _ACTOR is None or _ACTOR.loop is not loop:
        _ACTOR = _McpSessionActor()
    if not _ACTOR.is_alive():
        await _ACTOR.start()
    return _ACTOR


async def aclose_mcp_session() -> None:
    """显式关掉长驻会话（`main.py` 的 `graceful_shutdown` 会调它）。

    **不调它也不会漏子进程**（loop 关时 holder task 会走到 `finally`），
       但**显式关**能让"什么时候释放那个 84 MB"是可预期的。
    """
    global _ACTOR
    if _ACTOR is not None:
        await _ACTOR.stop()
        _ACTOR = None


async def get_mcp_tools():
    """通过 MCP Client 获取所有可用工具（与 `call_mcp_tool` **共用同一个长驻会话**）"""
    actor = await _get_actor()
    return await actor.list_tools()


async def call_mcp_tool(tool_name: str, arguments: dict) -> str:
    """通过 MCP Client 调用工具。

    **：改走【长驻会话】** —— 改前每次调用自开自关，
    实测 **~2 秒/次**（2.42 / 2.09 / 1.93），成本几乎全在**起子进程 + 重新 import langchain**。
    现在由 `_McpSessionActor` 的 **holder task 独占**那个 `async with`，本函数只投请求、等结果
    ⇒ 实测 **4–7 毫秒**（首次 ~22ms，含握手）。`docs/说明/mcp长驻会话-调研-20261008.md`

    **别改回"从池里取"** —— 见 `_McpSessionActor` 上面那段（anyio cancel scope）。
    """
    actor = await _get_actor()
    return await actor.call_tool(tool_name, arguments)

# ==================== 工具执行节点（通过 MCP Client） ====================
# ==================== tools 节点（保持原有逻辑） ====================
# 新增 工具调用时记录轨迹
from tools.tool_visualizer import record_tool_start, record_tool_end,record_agent_decision
async def tool_execute(state: AgentState):
    """执行节点：通过 MCP Client 调用工具。

    ：**预算拦截从"软"变"硬"**。

    **改前**：被拦时只塞一条 `ToolMessage` 就 `continue` ⇒ 图照常往下走
    ⇒ `/agent/mcp_chat` **HTTP 200**，只有 **LLM 自己**看得见那句"预算拦截"
    ⇒ 「预算拦住了」这句话**只对 LLM 成立**（本文件 spec 的 ③）。

    **改后**：被拦 ⇒ ① 照旧答满本轮的 `ToolMessage`（**序列必须合法**：
    带 `tool_calls` 的 `AIMessage` 后面少一条回应，真 provider 直接 400）
    ② 在 state 上写 `budget_intercept` ⇒ 端点层据此回 **429 / error 帧**。

    **别改成在这里 `raise`**（哪怕它看着更"硬"）—— 实测会把 checkpoint 留成
    `next=('tools',)` + 一条**没人回答**的 `AIMessage(tool_calls)` ⇒ **那个 thread 从此废掉**。
    实测输出与备选评估。
    `record_tool_end` 在**被拦这条路上仍然不写**（追踪里留一条"开了没结束"）——
    这与改前**一字不差**（原来 `continue` 也不写），不是引入的回归。
    """
    last_message = state["messages"][-1]
    tool_messages = []
    user_name = state.get("user_name", "unknown")
    thread_id = state.get("thread_id", "unknown")
    budget_intercept = None

    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]

        # 记录工具调用开始
        record_tool_start(tool_name, tool_args, user_name, thread_id)

        # ===== 多级预算检查 =====
        allowed, reason = check_multilevel_budget(
            user_name=user_name,
            thread_id=thread_id,
            tool_name=tool_name
        )
        if not allowed:
            tool_msg = ToolMessage(
                content=f"⚠️ 预算拦截：{reason}\n请等待预算重置或联系管理员提升额度。",
                tool_call_id=tc["id"],
                name=tool_name
            )
            tool_messages.append(tool_msg)
            # **继续跑完这一轮的所有 tool_call**（不是 `break`）：本轮有 3 个工具调用，
            #    只答 2 个 ⇒ 那条 `AIMessage` 悬着 ⇒ 下一轮进来必 400。
            #    拦截是"这一轮都别做了"，但不做的**每一条都要留下回应**。
            budget_intercept = reason
            continue

        # 通过 MCP Client 调用工具。
        #    （`app/tools/tool_cache.py`），在 **MCP server 进程里**生效 ⇒ 四条执行路径共享同一份。
        #    **可见行为变了**：以前命中会返回 `"{结果}\n[缓存命中]"`，现在**没有这个后缀**。
        result = await call_mcp_tool(tool_name, tool_args)

        # 记录工具调用结束（成功状态；原代码在此误记录为“未找到工具”错误）
        record_tool_end(tool_name, result, user_name, thread_id, "success")

        tool_msg = ToolMessage(content=str(result), tool_call_id=tc["id"], name=tool_name)
        tool_messages.append(tool_msg)

    out = {"messages": tool_messages}
    # **只在被拦时写**这个键 —— 清零**只有一个地方**（`agent_decide` 开头）。
    #    "谁能改它"保持单一来源，才讲得清"什么时候它是可信的"。
    if budget_intercept is not None:
        out["budget_intercept"] = budget_intercept
    return out

# ==================== 动态绑定工具到模型 ====================
async def get_llm_with_mcp_tools():
    """获取绑定了 MCP 工具的 LLM 实例。

    修（依赖漂移）:此前写的是 `for mcp_tool in tools:` —— 而
       `session.list_tools` 返回的是 **`ListToolsResult`**（列表在 `.tools`），
       **不是列表本身**。直接遍历它 ⇒ pydantic 模型迭代出的是 **(key, value) 元组**
       ⇒ `AttributeError: 'tuple' object has no attribute 'name'`
       ⇒ `/agent/mcp_chat`（三代）**500**。
       **去掉了这里的版本号**（原写「`mcp 1.30.0` 的」）——
       结论与 mcp 版本无关（2.x 仍是 `ListToolsResult` / `.tools`），而**版本号会过期**：
       本仓  的教训就是"别把话说死在一个会变的东西上"。
       回归测试:`app/tests/test_agent_repairs.py::test_get_llm_with_mcp_tools_unpacks_list_tools_result`
    """
    tools_result = await get_mcp_tools()
    # 将 MCP 工具列表转换为 LangChain 能理解的格式
    langchain_tools = []
    for mcp_tool in tools_result.tools:
        langchain_tools.append({
            "name": mcp_tool.name,
            "description": mcp_tool.description,
            #    这是个**只读属性名变了**的坑，别读成"整个类型换了"：
            #      · **构造**仍然两种都能写（`Tool(inputSchema=…)` 也行 —— `populate_by_name`）
            #      · **读属性**只认**字段名** `input_schema` ⇒ 写 `.inputSchema` 会
            #        `AttributeError: 'Tool' object has no attribute 'inputSchema'. Did you mean: 'input_schema'?`
            "parameters": mcp_tool.input_schema
        })
    return llm.bind_tools(langchain_tools)

# ==================== 流式白名单（B1 · ）====================
# **：可流节点名单放在【图模块里】，端点不许自己抄一份字面量**（理由见
#    `app/agent/agent_graph.py` 同名常量处 —— 一个名字两个来源必然漂移，而漂移是**静默**的）。
# 必须放在**模块级**（不能放进 `build_mcp_agent`）：端点是按
STREAMABLE_NODES = frozenset({
    "agent",   # 决策节点：答案 + `tool_calls`（那个「多轮」场景的主角）
    "chat",    # 兜底对话节点：**最终答案就是它生成的** ⇒ 不流它，流式端点等于白开
})


# ==================== 构建图 ====================
def build_mcp_agent():
    workflow = StateGraph(AgentState)

    # ==================== chat_node：兜底对话节点 ====================
    async def chat_node(state: AgentState, config: RunnableConfig):
        """处理不需要工具调用的直接对话，或工具调用完成后的最终总结

           `config` 转发下去（：声明 `config` + 转发，是出不出 token 的**唯一条件**）。
           **别照抄** `agent_graph.py` / `agent_checkpointer.py` 那两处 —— 那两处是**同步**
           节点、用同步 `.stream`；这里改成同步会阻塞事件循环。
        """
        user_name = state.get("user_name", "unknown")

        # 预算检查必须在**任何花钱的调用之前** —— 不只是 `llm.invoke`：
        #    下面那句 `inject_memories_to_prompt` 会去打一次 DashScope embedding（**也是花钱的**）。
        #       仍然花掉一笔 embedding，而结果**当场被丢弃**（走不到 `messages`）。
        #       这是的判据在 CI（dummy key）里**真打网络**才暴露出来的。
        if not check_token_budget(user_name, estimated_tokens=500):
            #    改前只把它当一句正常答案返回，端点照常回 **HTTP 200** ⇒ 调用方看不出被拒了。
            #    `chat_node` **不是入口节点** ⇒ 清零不在这里（在 `agent_decide`）。
            #    **不许在这里 `raise`**（§二 的 checkpoint 污染实测）。
            return {
                "final_output": "今日Token预算已用完，请明天再试。",
                "messages": [AIMessage(content="今日Token预算已用完，请明天再试。")],
                "budget_intercept": "今日Token预算已用完，请明天再试。",
            }

        # 构建带记忆注入的 system prompt —— **必须排在门【之后】**：它要花一次 embedding 的钱。
        system_prompt = "你是一个智能助理，请直接回答用户的问题。"
        # 导入长期记忆mem0模块
        system_prompt = inject_memories_to_prompt(system_prompt, state)

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        # B1：真流式（`astream` + 转发 `config`）。 用 `+` 聚合（`AIMessageChunk.__add__`），
        #    不是 `content +=` —— 那会丢掉碎片化的 `tool_calls`。
        # **必须遍历【所有】块**，不许跳过 `content` 为空的块：provider 把
        #    `usage_metadata` 挂在**最后一块**（`content=''`）上（实测，`探针-流式与记账.py`）
        response = None
        async for chunk in llm.astream(messages, config=config):
            response = chunk if response is None else response + chunk

        # 新增 统计 Token 消耗
        if hasattr(response, "usage_metadata"):
            usage = response.usage_metadata
            record_usage(
                #    ⇒ `token_usage_logs.cost` 按**错的单价**记 ⇒ 连带 `MAX_THREAD_COST`（元）也判错。
                #    改从对象取（照抄 `plan_execute.py:154` 的写法）。
                model=getattr(llm, "model_name", None) or getattr(llm, "model", "unknown"),
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                purpose="answer_generation",
                user_name=state.get("user_name", "unknown"),
                thread_id=state["thread_id"],   # 从 state 获取
            )
        
        return {
            "final_output": response.content,
            "messages": [response]
        }
    # ==================== agent_decide 节点（保持原有逻辑） ====================
    # 定义 agent_decide 节点（异步版本，动态绑定工具）
    async def agent_decide(state: AgentState, config: RunnableConfig):
        """决策节点（改真流式）。

        与 `chat_node` 同一条：**async 节点用 `astream` + 转发 `config`**。
        **`+` 聚合在这里是【必须】的，不是风格问题**：`tool_calls` 是碎片化到达的，
           只拼 `content` 会把它们丢掉 ⇒ `should_continue` 判不出 `"tools"`
           ⇒ 直接跳去 `chat` 出最终答案，**工具永远不会被执行**，而接口一切正常。
        """
        # 为什么必须有这一句：`budget_intercept` 是**普通 state 键**
        # （没挂 `operator.add`）⇒ **last-write-wins + 落 checkpoint**
        # ⇒ 不清零的话，"上一轮被拦"会让**下一轮不需要工具的正常提问也返回 429**。
        cleared = {"budget_intercept": None}

        # 预算检查必须在 invoke **之前** —— 放在之后钱已经花了，只能丢弃结果、拦不住
        #    （上移；）
        # 预估本次调用消耗（经验值：决策通常消耗200-500 tokens）
        user_name = state.get("user_name", "unknown")
        if not check_token_budget(user_name, estimated_tokens=500):
            #     当时写的是 `**cleared`（= 置 `None`）—— 那是因为当年**只有**
            #       工具触发那一种拦截（写在 `tool_execute` 里），入口节点这一处
            #       的任务只是"别让上轮的值串过来"。**新增的正是这一种**
            #       它必须自己写原因，不能再是 `None`。
            return {
                "final_output": "今日Token预算已用完，请明天再试。",
                "messages": [AIMessage(content="今日Token预算已用完，请明天再试。")],
                "budget_intercept": "今日Token预算已用完，请明天再试。",
            }

        llm_with_tools = await get_llm_with_mcp_tools()
        # B1：真流式 —— 同 `chat_node`（`astream` + `config`，`+` 聚合遍历所有块）。
        response = None
        async for chunk in llm_with_tools.astream(state["messages"], config=config):
            response = chunk if response is None else response + chunk
        # 记录决策过程
        if hasattr(response, "tool_calls") and response.tool_calls:
            for tc in response.tool_calls:
                record_agent_decision(user_name, state["thread_id"], {
                    "type": "tool_decision",
                    "tool_name": tc["name"],
                    "arguments": tc["args"],
                    "reasoning": response.content if hasattr(response, "content") else "",
                })
        # 新增 统计 Token 消耗
        # 在 agent_decide 节点中调用 record_usage 时，传入 tool_name 和 tool_args
        if hasattr(response, "usage_metadata"):
            usage = response.usage_metadata
            # 检查是否有工具调用
            tool_name = None
            tool_args = None
            if hasattr(response, "tool_calls") and response.tool_calls:
                tool_name = response.tool_calls[0]["name"]
                tool_args = response.tool_calls[0]["args"]
            
            record_usage(
                #    记**实际被调用的那个对象**（`:345` 调的是 `llm_with_tools`）。
                #    实测 `llm.bind_tools(...)` 后 `.model_name` 仍是 `deepseek-v4-flash`。
                model=getattr(llm_with_tools, "model_name", None)
                or getattr(llm_with_tools, "model", "unknown"),
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                purpose="agent_decision",
                user_name=state.get("user_name", "unknown"),
                thread_id=state.get("thread_id", "unknown"),
                tool_name=tool_name,
                tool_args=tool_args,
            )
        # `cleared` 是**另一个出口也要带**的（见本节点开头那段）—— 别只留这一处。
        return {**cleared, "messages": [response]}


    # ==================== 路由函数 ====================
    def should_continue(state: AgentState):
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        # 不需要工具时，去 chat_node 生成最终答案
        return "chat"

    def after_tools(state: AgentState):
        """：被预算拦下 ⇒ **本轮到此为止**（不回 `agent`）。

        改前是 `add_edge("tools", "agent")` **无条件**回 `agent` —— 被拦之后
        `agent` 会**再调一次同一个工具**、再被拦一次 …直到撞上递归上限。
        实测（改动前）：被拦的那一轮直接
        `GraphRecursionError: Recursion limit of 25 reached`
        —— 且那是在**已经判定"没钱了"之后**又白白烧了十几轮 LLM。
        守卫：`app/tests/test_budget_hard_intercept.py::test_被拦时图在tools之后直接结束不回agent`
        """
        return "blocked" if state.get("budget_intercept") else "continue"

     # ==================== 注册节点 ====================
    workflow.add_node("agent", agent_decide)
    workflow.add_node("tools", tool_execute)
    workflow.add_node("chat", chat_node)
    workflow.set_entry_point("agent")
    # 条件路由：需要工具 → tools，不需要工具 → chat
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", "chat": "chat"})
    
    # tools 执行完后，回到 agent 继续判断（可能需要更多工具，也可能直接去 chat）。
    workflow.add_conditional_edges(
        "tools", after_tools, {"blocked": END, "continue": "agent"})
    # chat 节点执行完后，结束
    workflow.add_edge("chat", END)

    return workflow.compile(checkpointer=MemorySaver())

# 全局实例
mcp_agent = build_mcp_agent()
