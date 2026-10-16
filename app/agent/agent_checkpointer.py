
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
# `SENSITIVE_TOOLS` **必须留在这个导入行** —— 它不是死导入，`ruff` 判不了：
#    `app/tests/test_memory_chat_approval.py` 的结构性守卫写作
#    `assert ac.SENSITIVE_TOOLS is ag.SENSITIVE_TOOLS`（`ac`/`ag` 是那两个模块的**别名**
#    全仓 grep `agent_checkpointer.SENSITIVE_TOOLS` **搜不到**）。守卫语义 =「白名单只能有一份」。
# 行尾那条 `noqa` 指令是收尾：**本注释里不写那个井号**，写了 ruff 会把注释本身
#    当成一条 noqa 指令，在 stderr 上打一句 "Invalid `noqa` directive"（实测踩过）。
#    不挂基线是因为基线的键是 `(文件, 规则)`：挂一条 = 本文件**所有** F401 放行，
#    行尾 `noqa` 只放行**这一行**，粒度更细、理由就在行边上。
#    代价：**哪天那条守卫被删了、`SENSITIVE_TOOLS` 真成了死导入，这里也不会报** ——
#          但在基线里同样不会报（同一个盲区），故不构成反对理由。
from agent.agent_graph import SENSITIVE_TOOLS, should_continue, human_approval  # noqa: F401

# ==================== 初始化模型 ====================
# 角色 = 「模型轴 fast」+「长度轴 agent(1024)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
llm = make_llm("fast", "agent")

# ==================== 定义工具 ====================
# 工具清单从 `mcp_server.TOOLS` **派生**，不在本地重复定义 —— 重复 ⇒ 漂移（
#    同 `agent_graph.py` 的处理）。LLM 看到的是 `simple_tools` 那份（实现逐字等价、
#    docstring 更详细）。`eval`没丢：实现在 `app/tools/safe_math.py`，
#    守卫在 `test_safe_math_wiring.py`。
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
    #    没挂 `operator.add` ⇒ last-write-wins + 落 checkpoint ⇒ **必须每轮清零**，否则
    #    上一轮被拦会让下一轮的**正常提问**也返回 429。清零落在**入口节点** `agent_decide`
    #    （`set_entry_point("agent")` ⇒ 每轮必经），**它的两个出口都要带上**。
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
        # 2026-10-05（批 7）：**同时置 `budget_intercept`** ——
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
        # 并且**必须遍历【所有】块**，不许跳过 `content` 为空的块 ——
        #    provider 把 `usage_metadata` 挂在**最后一块**（`content=''`）上（实测，
        #）⇒ 跳过它，下面那段记账就没了，
        #    而接口一切正常。
        response = chunk if response is None else response + chunk
    # 统计 Token
    # **2026-10-04修掉了本仓最难看的一个 bug。原先这里写的是**：
    #
    #     if hasattr(response, "usage"):
    #         record_usage(..., prompt_tokens=response.usage.prompt_tokens,
    #                           completion_tokens=response.usage.completion_tokens,
    #                           purpose="query_rewrite", ...)   # 还漏传 user_name/thread_id
    #
    #     **那个判据恒为 False** —— 真 `AIMessage` / `AIMessageChunk` 都**没有** `.usage`
    #     属性（真名是 `usage_metadata`）⇒ **这段记账从写下那天起就没执行过一次**。
    #     墓碑 ⇒ `app/tests/test_token_budget_hookup.py::test_does_not_record_on_the_old_wrong_attribute`
    #     （拿一个**只有 `.usage`** 的假对象来调，`record_from_response` 必须返回 `False`）。
    #
    # **`purpose` 从 `"query_rewrite"` 改成 `"agent_decision"`** —— 这是个**对话端点**，
    #    记成"查询改写"是错的。真库 `token_usage_logs` 里 `query_rewrite` **0 条**
    #    （正是因为它从没跑过）⇒ **无历史数据要迁移**。
    # 修它 = **行为变更**：`/agent/memory_chat` 的配额从"形同虚设"变成"真的生效"。
    #    裁定与理由 ⇒  §八。
    record_from_response(
        llm_with_tools, response, "agent_decision",
        user_name=user_name, thread_id=thread_id,
    )
    # 2026-10-05（批 7）：**正常出口也要清零**——
    #    `budget_intercept` 是普通 state 键（last-write-wins + 落 checkpoint）
    #    ⇒ 只清一个出口的话，走**另一个**出口的那一轮会留着上一轮的值。
    #    守卫：`app/tests/test_budget_soft_return.py::test_agent_checkpointer_上一轮的标志不串轮`
    return {"messages": [response], "budget_intercept": None}

def tool_execute(state: AgentState):
    """执行节点。** `TOOLS_BY_NAME` 查表分派** —— 理由与实测见 `agent_graph.py` 同名处。

    **2026-10-03 更正（丙段已修）**：本文件原先**没有审批门**
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

# 2026-10-03（丙段）：本文件原先在这里**自己写了一份** `should_continue`
#    （只有两条路：`tool_calls` ⇒ "tools" / 否则 END）。已删除 —— 改用 `agent_graph` 那一份
#    （三条路，含 `"approval"`）。理由：审批的判据只能有**一处**，见文件顶部 import 处的注释。

# ==================== 流式白名单（B1 · 2026-10-04）====================
# **：可流节点名单放在【图模块里】，端点不许自己抄一份字面量**（理由见
#    `app/agent/agent_graph.py` 同名常量处 —— 一个名字两个来源必然漂移，而漂移是**静默**的）。
# 必须放在**模块级**（不能放进 `build_checkpointer_agent`）：端点是按
#    `agent_checkpointer.STREAMABLE_NODES` 取的，函数体里的是局部名，外面拿不到。
# `tools` / `approval` **不在**里面：它们不调 LLM（无字可流），
#    而 `tools` 返回的 `ToolMessage` 会被当成"新消息"发出来 ⇒ 混进正文（实测）。
STREAMABLE_NODES = frozenset({"agent"})


# ==================== 构建图（支持选择 Checkpointer 后端） ====================
# ======= 支持 MemorySaver SqliteSaver RedisSaver 自主选择架构后端 =======
def build_checkpointer_agent(backend: str = "memory"): # 默认memory即MemorySaver
    workflow = StateGraph(AgentState)
    # 添加节点和边（与基础 Agent 一致：决策 → 工具 → 决策循环）
    workflow.add_node("agent", agent_decide)
    workflow.add_node("tools", tool_execute)
    # 2026-10-03（丙段）：审批节点 —— 与 `agent_graph` 同形（三条路由 + `interrupt_before`）。
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
        # 2026-09-20 删（D1/pyflakes：局部变量赋值后从未使用）：此处原有
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