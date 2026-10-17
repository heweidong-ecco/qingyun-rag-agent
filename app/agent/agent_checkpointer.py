
import os
from typing import TypedDict, List, Annotated, Optional
import operator

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
# RedisSaver 版本不兼容，暂时不用
# from langgraph.checkpoint.redis import RedisSaver
from core.llm_factory import make_llm   # model / api_key / base_url / max_tokens 的唯一落点
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig   # 节点要靠它把回调接进模型调用
# 审批门的语义从 `agent_graph` **引入**，不在这里抄一份 —— 抄一份正是  记的病根
#    （同一个判断两处实现、不一致时不报错 ⇒ 审批静默地永不触发）。 丙段。
#    `agent_graph.py` 末行在 **import 期**就跑 `validate_approval_config` ⇒ 白名单为空
#    会在这里**一并响亮地报**（不用再调一次）。
from agent.agent_graph import SENSITIVE_TOOLS, should_continue, human_approval  # noqa: F401

# ==================== 初始化模型 ====================
# 角色 = 「模型轴 fast」+「长度轴 agent(1024)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
llm = make_llm("fast", "agent")

# ==================== 定义工具 ====================
# 工具清单从 `mcp_server.TOOLS` **派生**，不在本地重复定义 —— 重复 ⇒ 漂移（
from tools.mcp_server import TOOLS as _MCP_TOOLS

# 别在这里再排除 `execute_python` —— 抠掉它**没挡住什么**：无隔离已由执行器容器解决，
#    无审批也**不是这两张图特有的**（别处照样拿得到）。该管的地方是 `SENSITIVE_TOOLS`。
#    （逐条理由**同 `agent_graph.py` 同一处**，别在这儿再抄一遍。）
tools = [t["func"] for t in _MCP_TOOLS]
#  —— 分派查这张表，别在 `tool_execute` 里再抄一遍名字。
#    本文件是那次「按 `"search"` 分派、而真名是 `duckduckgo_search`」bug 的**第二处**现场
#       （活路径 = `POST /agent/memory_chat`）。守卫 ⇒ `app/tests/test_tool_dispatch.py`
TOOLS_BY_NAME = {t.name: t for t in tools}

llm_with_tools = llm.bind_tools(tools)

# ==================== 定义 State ====================
class AgentState(TypedDict):
    messages: Annotated[List, operator.add]
    #、同 `agent_graph.AgentState`：记账要的两个人身份键，**由端点注入**
    #    （`POST /agent/memory_chat`），一律 `.get(..., "unknown")` 读。
    user_name: str
    thread_id: str
    #：**预算被拦的原因**，通往【端点层】的唯一通道 —— 端点据此回 **429**（非流式）/
    #    **error 帧**（流式）。与 `agent_graph.AgentState` / `agent_graph_advanced.AgentState`
    #    的同名键**是同一个契约**：存**原因**，拼文案统一由
    #    `api_v1_agent.agent_budget_intercept_message` 做（别在节点里拼）。
    budget_intercept: Optional[str]

# ==================== 定义节点 ====================
from billing.token_tracker import (          # noqa: E402  （原位保留，未挪动）
    check_token_budget, record_from_response, BUDGET_EXCEEDED_MSG,
    #：保住 `check_token_budget` 这个调用，**别改成 `check_token_budget_detail`**
    #    （见 `agent_graph.py` 同一处）—— 4 个测试文件拿"模块属性 `check_token_budget`"
    #    当 monkeypatch 缝，换掉 ⇒ 补丁静默失效、转去连真库。
)


def agent_decide(state: AgentState, config: RunnableConfig):
    """决策节点。

    **真流式的唯一条件 = 声明 `config` 并把它转发给模型的 `.stream`**。
       别改成 `async def` —— 同步的 `graph.invoke` 会当场抛
       `TypeError: No synchronous function provided to "agent"`，而 `/agent/memory_chat` 在用它。
       （同型实现见 `app/agent/agent_graph.py::agent_decide`，那份是  的样板。）
    """
    # 预算检查**必须在 `.stream()` 之前**（放之后钱已花，只能丢结果、拦不住）。
    user_name = state.get("user_name", "unknown")
    thread_id = state.get("thread_id", "unknown")
    if not check_token_budget(user_name, estimated_tokens=500):
        #    改前只塞一句话当答案，端点照常回 **HTTP 200** ⇒ 调用方**看不出被拒了**。
        #    这是**入口节点**，所以它既是"清零的那一处"、也是"写标志的那一处"
        #       —— 本出口写原因，**不写 `None`**。
        #    **不许在这里 `raise`**（§二 的 checkpoint 污染实测）。
        return {"messages": [AIMessage(content=BUDGET_EXCEEDED_MSG)],
                "budget_intercept": BUDGET_EXCEEDED_MSG}

    response = None
    for chunk in llm_with_tools.stream(state["messages"], config=config):
        # 用 `AIMessageChunk.__add__`（`+`）合并，**不是** `response.content += chunk.content`：
        #    后者会丢掉**碎片化**到达的 `tool_calls`（name 一块、args 几块）
        #    ⇒ `should_continue` 判不出 `"approval"` / `"tools"` ⇒ **审批门静默失效**。
        response = chunk if response is None else response + chunk
    # 统计 Token
    record_from_response(
        llm_with_tools, response, "agent_decision",
        user_name=user_name, thread_id=thread_id,
    )
    #    `budget_intercept` 是普通 state 键（last-write-wins + 落 checkpoint）
    #    ⇒ 只清一个出口的话，走**另一个**出口的那一轮会留着上一轮的值。
    #    守卫：`app/tests/test_budget_soft_return.py::test_agent_checkpointer_上一轮的标志不串轮`
    return {"messages": [response], "budget_intercept": None}

def tool_execute(state: AgentState):
    """执行节点。** `TOOLS_BY_NAME` 查表分派** —— 理由与实测见 `agent_graph.py` 同名处。

    **更正（丙段已修）**：本文件原先**没有审批门**
       （§遗留·2：「同一个仓里，一条路停下等人批，另一条直接执行」）。
       现在 `build_checkpointer_agent` 带 `interrupt_before=["approval"]`，
       路由/白名单**从 `agent_graph` 引入**（不是抄一份）。
       ⇒ **能走到本节点的 `tool_calls` 都是非敏感的**（敏感的会先停在 `approval`）。
    """
    last_message = state["messages"][-1]
    tool_messages = []
    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool = TOOLS_BY_NAME.get(tool_name)
        result = tool.invoke(tool_args) if tool else f"未找到工具: {tool_name}"
        tool_msg = ToolMessage(content=str(result), tool_call_id=tc["id"], name=tool_name)
        tool_messages.append(tool_msg)

    return {"messages": tool_messages}

#    （只有两条路：`tool_calls` ⇒ "tools" / 否则 END）。已删除 —— 改用 `agent_graph` 那一份
#    （三条路，含 `"approval"`）。理由：审批的判据只能有**一处**，见文件顶部 import 处的注释。

# ==================== 流式白名单（B1）====================
# **可流节点名单放在【图模块里】，端点不许自己抄一份字面量**（理由见
#    `app/agent/agent_graph.py` 同名常量处 —— 一个名字两个来源必然漂移，而漂移是**静默**的）。
# 必须放在**模块级**（不能放进 `build_checkpointer_agent`）：端点是按
STREAMABLE_NODES = frozenset({"agent"})


# ==================== 构建图（支持选择 Checkpointer 后端） ====================
# ======= 支持 MemorySaver SqliteSaver RedisSaver 自主选择架构后端 =======
def build_checkpointer_agent(backend: str = "memory"): # 默认memory即MemorySaver
    workflow = StateGraph(AgentState)
    # 添加节点和边（与基础 Agent 一致：决策 → 工具 → 决策循环）
    workflow.add_node("agent", agent_decide)
    workflow.add_node("tools", tool_execute)
    workflow.add_node("approval", human_approval)
    workflow.set_entry_point("agent")
    # 映射必须**写全三条**：`should_continue` 现在会返回 `"approval"`，
    #    少了它 langgraph 会以 "unknown branch" 抛错（不是静默走默认）。
    workflow.add_conditional_edges(
        "agent", should_continue,
        {"approval": "approval", "tools": "tools", END: END},
    )
    # 审批通过后，从 "approval" 去 "tools" 执行（与 `agent_graph` 同）
    workflow.add_edge("approval", "tools")
    workflow.add_edge("tools", "agent")

    # 根据后端选择 Checkpointer
    if backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver
        db_path = os.path.join(os.path.dirname(__file__), "agent_history.db")
        checkpointer = SqliteSaver.from_conn_string(db_path)
    elif backend == "redis":
        #       redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        #    它**只被下面那行注释掉的代码用过** ⇒ 是死变量。
        # 新增,RedisSaver 版本不兼容问题还没解决，现在暂时不用
        # checkpointer = RedisSaver.from_conn_string(redis_url)
        checkpointer = MemorySaver()  # 暂以内存兜底，避免编译空图
    else:  # 默认 memory
        checkpointer = MemorySaver()

    # `interrupt_before=["approval"]` —— 进入审批节点**之前**暂停（与 `agent_graph` 同）。
    #    少了这个参数，`approval` 节点会被**直接走进去**（它什么也不做）⇒ 门形同虚设。
    return workflow.compile(checkpointer=checkpointer, interrupt_before=["approval"])

# 全局实例（可通过环境变量 AGENT_CHECKPOINT_BACKEND 切换）
backend = os.getenv("AGENT_CHECKPOINT_BACKEND", "memory")
checkpointer_agent = build_checkpointer_agent(backend=backend)