"""
LangGraph Agent 示例：基于图结构的智能助理
"""
import os
from typing import TypedDict, List, Annotated, Optional
import operator

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from core.llm_factory import make_llm   # ①b：model / api_key / base_url / max_tokens 的唯一落点
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig   # B1：节点要靠它把回调接进模型调用
#    四个 import **删了** —— 它们**只**服务于本文件原先自带的那两个 `@tool`。
#    那份已改成从 `mcp_server.TOOLS` 派生 ⇒ 留着就是 （第 ⑥ 道门会红）。
#    顺带消掉一处：`:221` 有个循环/局部变量也叫 `tool`，原先与这个 import 撞名。
#    `record_from_response` 是三张图共用的**唯一记账实现**，别在本文件里另抄一份取用量。
from billing.token_tracker import check_token_budget, record_from_response, BUDGET_EXCEEDED_MSG
#    `check_token_budget_detail`**（曾试过、已退回）。理由不是"它更好"，是这个**模块属性**
#    是 4 个测试文件在用的 monkeypatch 缝 —— `test_billing_wiring.py:384` 拿它把本图打成
#    "恒拦"并断言"末条 = `BUDGET_EXCEEDED_MSG` 且**一笔账都不记**"；`test_agent_stream_chains.py`
#    用它放行去测真流式。换掉 ⇒ 那些补丁**静默失效**、转而去连真库。

# ==================== 初始化模型 ====================
# 角色 = 「模型轴 fast」+「长度轴 agent(1024)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
llm = make_llm("fast", "agent")

# ==================== 定义工具 ====================
#    `search_tools.web_search` 的**重复定义删了** —— 它们和 `simple_tools` 那份**逐字等价**，
#    而重复定义的代价是**漂移**： 那个「按 `"search"` 分派、真名却是别的」的 bug
#    就是这么长出来的。⇒ 工具清单改为从 `mcp_server.TOOLS` **派生**
from tools.mcp_server import TOOLS as _MCP_TOOLS

#    只在**调用方起过轨迹**时才有数据（`tool_visualizer.record_*` 在没起过时是空操作）
#    ⇒ 这件事**不改变本图的任何返回值**，也不影响那些不起轨迹的调用者。
from tools.tool_visualizer import record_tool_start, record_tool_end

#    当初的理由**两条**，逐条处置：
#      ① 这张图里它**没有容器隔离** ⇒ **已由 解决** —— 现在是**硬化容器**
#         （只读根 · 无网 · 非 root · 无 cap · 5s/256MB/pids 限制）
tools = [t["func"] for t in _MCP_TOOLS]

#  —— 分派必须查这张表，不许再在 `tool_execute` 里抄一遍名字。
#    病根就是"名字写在两处"：抄的那份一旦对不上，落的是 `else` 分支（**如实报错、不崩溃**）
#    ⇒ 那个工具**从来没被执行过**，而接口一切正常（本仓 实测，）。
# 守卫 ⇒ `app/tests/test_tool_dispatch.py`
TOOLS_BY_NAME = {t.name: t for t in tools}

# 将工具绑定到模型，这样模型就知道可以调用哪些工具
llm_with_tools = llm.bind_tools(tools)

# ==================== 审批白名单（B4）====================
# 改前：**只要产生任意 tool_calls 就进审批** ⇒ 问一句"今天几号"也会停下来等人批。
#    硬门 D 要的是「**该被接管时被接管**」，不是「全都接管」。
# 它是**白名单**：没登记的工具**默认不敏感** ⇒ **新加的工具默认不过审批**。
SENSITIVE_TOOLS: frozenset[str] = frozenset(
    n.strip() for n in os.getenv("SENSITIVE_TOOLS", "web_search").split(",") if n.strip()
)


def validate_approval_config() -> None:
    """启动自检：白名单**不许为空**，且里面的名字**必须真的存在**。

    两种都是「**静默失效**」—— 审批永不触发，但**不会有任何报错**，
       要等人验收时才发现「接管从来没发生过」。⇒ 让它们在**启动时就报**。

    | 形态 | 什么时候发生 | 加进来的时间 |
    |---|---|---|
    | **空名单** | 有人在 `.env` 里把它**显式清空** | |
    | **名字不存在** | 手滑写了错名字 / 工具改名后没同步 | —— **本仓真的发生过**：默认值写成了变量名 `search_tool`，**活了三天** |

    **第二段是本函数存在的真正理由**： 只拦了空名单，
       于是**同一个失败**换了个形状（"名字全都对不上"）**绕过了它自己设的闸**。
       ⇒ 判据从「非空」升级为「**非空 且 名字真的在 `tools` 里**」。
    """
    if not SENSITIVE_TOOLS:
        raise EnvironmentError(
            "SENSITIVE_TOOLS 为空 —— 人工审批将永不触发（硬门 D 名存实亡）。"
            " 请在 .env 里写明需要审批的工具名（逗号分隔），例如 SENSITIVE_TOOLS=web_search"
        )

    known = {t.name for t in tools}
    unknown = SENSITIVE_TOOLS - known
    if unknown:
        raise EnvironmentError(
            f"SENSITIVE_TOOLS 里有不存在的工具名：{sorted(unknown)}"
            f" —— 它们**永远不会被审批**（硬门 D 名存实亡）。"
            f" 可用的工具名：{sorted(known)}。"
            " ⚠️ 这里要写**工具名**，⛔ 不是代码里的变量名。"
        )

# ==================== 定义 Agent 的状态 ====================
class AgentState(TypedDict):
    # 对话历史消息列表。operator.add 表示新消息会被追加到末尾，而不是覆盖。
    messages: Annotated[List, operator.add]
    #    `user_name` = 配额按谁算；`thread_id` = 这笔钱记到哪个会话。
    #    **由端点注入**（`api_v1_agent.py:198-201`），图自己推不出来。
    #    一律 `.get(..., "unknown")` 读 —— 旧调用方（`api_v1.py` / `api_v1_rag.py`）不传这两个键，
    #       用下标会当场 `KeyError` 把那些路径打挂。缺身份**只该漏记到 "unknown"，不该 500**。
    user_name: str
    thread_id: str
    #    —— 端点据此回 **429**（非流式）/ **error 帧**（流式）。
    #    与 `agent_graph_advanced.AgentState` 的同名键**是同一个契约**：存**原因**，
    #       拼文案由端点层的 `api_v1_agent.agent_budget_intercept_message` 统一做（别在节点里拼）。
    #    它**没有**挂 `operator.add` ⇒ **last-write-wins + 落 checkpoint**
    budget_intercept: Optional[str]

# ==================== 定义节点函数 ====================
def _last_human_text(messages) -> str:
    """最后一条**用户**消息的文本 —— 拿它当检索长期记忆的查询词。

    用 `isinstance(m, HumanMessage)` 判，不是"取 `messages[-1]`" —— 第一步时
       末条确实是人话，但**这个函数是按"最后一条用户消息"定义的**，语义不靠调用时机。
    取不到 ⇒ 回空串（`search_user_memory("")` 由 mem0 那边兜，不是这里抛）。
    """
    for m in reversed(list(messages or [])):
        if isinstance(m, HumanMessage):
            return str(getattr(m, "content", "") or "")
    return ""


def agent_decide(state: AgentState, config: RunnableConfig):
    """
    决策节点：调用模型，让它决定是回复文本还是调用工具。

    **B1：本节点必须"声明 `config` + 转发给模型的流式调用"** ——
       这是 `astream(..., stream_mode="messages")` 出不出 token 的**唯一条件**。
       实测（假模型探针，见 `app/tests/test_agent_sse.py` 文件头）：

       | 写法 | `astream(stream_mode="messages")` |
       |---|---|
       | 不接 `config` + `invoke` | **1 块**（整段，`on_llm_end` 吐的）＝**假流式** |
       | 接 `config` + `.stream(config)` | **N 块**、时间戳递增 ＝真流式 |

    **别只看"接口返回了 `text/event-stream` 就以为成了"** ——
       上面那两种写法**接口长得一模一样**，只有数块数才分得出来。

    **别把本节点改成 `async def`** —— 实测会让**同步的** `graph.invoke` 直接抛
       `TypeError: No synchronous function provided to "agent"`，而非流式路径
       （`/agent/langgraph_chat` · `api_v1.py` · `api_v1_rag.py`）**都在用它**。
       同步节点 + 同步 `.stream` 就能真流式，不需要 async。

    **聚合用 `AIMessageChunk.__add__`（LangChain 自带），别手拼 content** ——
       `tool_calls` 是**碎片化**到达的（name 一块、args 几块）。
       只拼 `content` 会把 `tool_calls` 丢掉 ⇒ `should_continue` 判不出 `"approval"`
       ⇒ **B4 人工审批静默失效**，而接口返回 `{"status": "answered"}` 一切正常。
       守卫 ⇒ `app/tests/test_agent_sse.py::test_agent_decide_preserves_tool_calls`
    """
    #    放在之后钱已经花了，只能丢弃结果、拦不住（同参照图 `agent_graph_advanced.py:329`）。
    #    两条链先前**连检查都没有** ⇒ 端点上的 B8 会话上限 / B11 全站熔断对本图等于不存在。
    user_name = state.get("user_name", "unknown")
    thread_id = state.get("thread_id", "unknown")
    # 预估本次调用消耗（经验值：决策通常消耗 200–500 tokens；与参照图同取 500）
    if not check_token_budget(user_name, estimated_tokens=500):
        # 返回 AIMessage 而非 `final_output` —— 本图的出口是 `should_continue` 读
        #    `messages[-1].tool_calls`（见 `:195`）：无 tool_calls ⇒ END。
        #    别忘了记账**在**这里也要有 —— 但拦下来的这次**没花钱**，不该记。
        #    改前只塞一句话当答案，端点照常回 **HTTP 200** ⇒ 调用方**看不出被拒了**。
        return {"messages": [AIMessage(content=BUDGET_EXCEEDED_MSG)],
                "budget_intercept": BUDGET_EXCEEDED_MSG}

    # ════════════════ 长期记忆注入（）════════════════
    # **本图原先一分记忆都不注入** ⇒ `/agent/langgraph_chat` 这条链压根没有"记忆"这一说，
    #    而「外部检索与工具Agent编排」那一页**要拿它当主链**、页面上写着"记忆"。
    messages = state["messages"]
    if not any(isinstance(m, ToolMessage) for m in messages):
        # **延迟导入**：`agent.memory_store` 一 import 就建 mem0 客户端（本地 Qdrant
        #    **单实例锁**）⇒ 不该让"只用本图"的调用者被迫背这口锅
        #    （同族写法 ⇒ `memory_store._lazy_get_embedding()`）。
        from agent.memory_store import memory_block
        block = memory_block(
            f"{user_name}:{state.get('memory_space', 'default')}",
            _last_human_text(messages),
        )
        if block:
            messages = [SystemMessage(content=block)] + list(messages)

    response = None
    for chunk in llm_with_tools.stream(messages, config=config):
        # 用 `+` 合并，不是 `response.content += chunk.content`：
        #    后者会丢掉 `tool_call_chunks`（见上面）。
        response = chunk if response is None else response + chunk
    # **别在这里加 `if response is None: response = AIMessage(content="")` 兜底** ——
    #    那是**不可达代码**：模型吐零块时，langchain 的 `BaseChatModel.stream()` **自己会**
    #    `raise ValueError("No generation chunks were returned")`
    record_from_response(
        llm_with_tools, response, "agent_decision",
        user_name=user_name, thread_id=thread_id,
    )
    # 返回一个AIMessage，LangGraph会自动将它追加到messages中
    #    `budget_intercept` 是普通 state 键（last-write-wins + 落 checkpoint）
    #    ⇒ 只清一个出口的话，走**另一个**出口的那一轮会留着上一轮的值
    #    ⇒ 预算恢复之后那一轮**正常的提问**照样回 429。
    return {"messages": [response], "budget_intercept": None}

def tool_execute(state: AgentState):
    """
    执行节点：解析模型的工具调用请求，执行工具，并返回ToolMessage。

    ** `TOOLS_BY_NAME` 查表，不许再写 `if tool_name == "search"`**。
       改前那样写是把工具名**抄了第二份**，而抄的那份写的是 `"search"`、真名是
       `duckduckgo_search` ⇒ **永远落 `else`** ⇒ 搜索工具**从来没被执行过**，
       模型收到的却是一条正常的 `未找到工具: duckduckgo_search`（**不报错**）。
       同型 bug 在本仓是**第二次**：`plan_execute.py:101-110` 记着上一回（prompt 写 `search`、
       注册表里叫 `web_search`）。⇒ 病根不是"写错了"，是"**名字有两个来源**"。
       守卫 ⇒ `app/tests/test_tool_dispatch.py`
    """
    last_message = state["messages"][-1]
    tool_messages = []
    #    这两个键在图里一律 `.get(…, "unknown")` 读 —— 缺身份**只该漏记，不该 500**
    #       （旧调用方 `api_v1.py` / `api_v1_rag.py` 不传这两个键）。
    user_name = state.get("user_name", "unknown")
    thread_id = state.get("thread_id", "unknown")

    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]

        #    **没 `start_trace` 过的线程上，这两个调用是【空操作】**
        #       （`tool_visualizer` 里 `if key not in _traces: return`）
        #       ⇒ 对 `api_v1.py` / `api_v1_rag.py` 那些不建轨迹的调用者**逐字无影响**。
        #    `record_tool_start` **必须排在工具真执行之前** —— `record_tool_end` 靠
        record_tool_start(tool_name, tool_args, user_name, thread_id)

        # 按名字查表执行。三个工具都传 **dict**（`t.invoke(tool_args)`）——
        #    旧的 `if` 分支对搜索传的是裸字符串 `tool_args["query"]`，换表后统一成 dict。
        tool = TOOLS_BY_NAME.get(tool_name)
        result = tool.invoke(tool_args) if tool else f"未找到工具: {tool_name}"
        # 查不到名字这一条按 **error** 记（工具**没执行成功**）——
        #    不照抄 `agent_graph_advanced` 那处的"一律 success"（那是它自己的历史包袱）。
        if tool is None:
            record_tool_end(tool_name, result, user_name, thread_id, "error", "未知工具")
        else:
            record_tool_end(tool_name, result, user_name, thread_id, "success")

        # 生成ToolMessage
        tool_msg = ToolMessage(
            content=str(result),
            tool_call_id=tc["id"],
            name=tool_name
        )
        tool_messages.append(tool_msg)

    return {"messages": tool_messages}

def needs_approval(tool_calls: list) -> bool:
    """这轮 tool_calls 里**有没有**需要人工审批的（B4）。

    判据是「**有任何一个**」⇒ 混合调用（本地工具 + `web_search`）**整体**审批，
       不能"挑着执行"—— 那等于给敏感调用开了个绕过口子。
    """
    names = {tc.get("name") for tc in tool_calls or []}
    return bool(names & SENSITIVE_TOOLS)


def should_continue(state: AgentState):
    """
    路由函数（B4 起是**三条**路，不再是两条）：
      * 没有 tool_calls         ⇒ END（模型给出了最终答案）
      * 有 tool_calls **且含敏感** ⇒ "approval"（停下来等人批）
      * 只有**非敏感** tool_calls ⇒ "tools"（直接执行，不再无谓地等人）
    """
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None) or []
    if not tool_calls:
        return END
    if needs_approval(tool_calls):
        return "approval"
    return "tools"

#  新增部分 人工审批节点
def human_approval(state: AgentState):
    """
    人工审批节点：什么也不做，只是一个用于暂停的中断点。
    """
    # 这里可以打印或记录日志，方便调试
    print("流程已暂停，等待人工审批...")
    return {}

# ==================== 流式白名单（B1 · ）====================
# **：可流节点名单放在【图模块里】，端点不许自己抄一份字面量。**
#    写在这里（不是 `build_agent_graph()` 函数体里）是因为**端点要按 `agent_graph.STREAMABLE_NODES` 取**
#    —— 函数体里的是局部名，外面拿不到。
STREAMABLE_NODES = frozenset({
    "agent",     # 决策节点：`agent_decide` 已声明 `config` 并把流转发给模型
})


# ==================== 构建图 ====================
def build_agent_graph():
    """构建并编译 LangGraph Agent 图（带人工审批）"""
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("agent", agent_decide)
    workflow.add_node("tools", tool_execute)
    # 新增审批节点
    workflow.add_node("approval", human_approval)

    # 设置图的入口点
    workflow.set_entry_point("agent")
    # 条件边：从 "agent" 出发，由 should_continue 决定去哪（B4 起是三条路）
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "approval": "approval",  # 含**敏感**工具 ⇒ 进审批节点（interrupt_before 在此暂停）
            "tools": "tools",        # 只有**本地**工具 ⇒ 直接执行，无人值守也能跑完
            END: END                 # 模型给了最终答案 ⇒ 结束
        }
    )

    # 审批通过后，从 "approval" 节点去 "tools" 节点执行工具
    workflow.add_edge("approval", "tools")
    # 工具执行完后，回到 "agent" 继续思考
    # 添加普通边：工具执行完后，总是回到"agent"节点继续思考
    workflow.add_edge("tools", "agent")

    # 编译图
    memory = MemorySaver()  # 用于持久化状态
    # 关键：interrupt_before=["approval"] 告诉 LangGraph 在进入审批节点前暂停
    return workflow.compile(checkpointer=memory, interrupt_before=["approval"])

# 启动自检：白名单为空 ⇒ 直接起不来（别让它"静默地永不触发"，那是验收时才发现的失败）
validate_approval_config()

# 创建全局 graph 实例
agent_graph = build_agent_graph()