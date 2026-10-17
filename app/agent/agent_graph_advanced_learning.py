"""
LangGraph 进阶示例：多分支路由与子图协作
"""
from typing import TypedDict, List, Annotated, Optional
import operator
from loguru import logger

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from core.llm_factory import make_llm   # ①b：model / api_key / base_url / max_tokens 的唯一落点
from tools.search_tools import web_search   # **留** —— `:149` 的搜索子图节点直接 `.invoke` 它
#    它们只服务于本文件原先自带的那两个 `@tool`（已删、改为取共享对象）⇒ 留着就是 F401。
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage,SystemMessage
from langchain_core.runnables import RunnableConfig   # B1：节点要靠它把回调接进模型调用
#    那个口子（`/agent/advanced_chat` 与它的 `/stream` 都走这张图）。
#    `record_from_response` 是三张图共用的**唯一记账实现**，别在本文件里另抄 6 份取用量。
from billing.token_tracker import check_token_budget, record_from_response, BUDGET_EXCEEDED_MSG
#    别改成 `check_token_budget_detail` —— 见 `agent_graph.py` 同一处的长注释
#    （"模块属性 `check_token_budget`"是 4 个测试文件的 monkeypatch 缝）。

# ==================== 初始化模型 ====================
# 角色 = 「模型轴 chat」+「长度轴 agent(1024)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
llm = make_llm("chat", "agent")

# ==================== 定义工具 ====================
#    它们与 `mcp_server.TOOLS` 里那两个**同名不同对象**（实现逐字等价 ⇒ 当前无害，
#    但属"重复定义" —— 本文件下面那句注释自己就登记过它）。
#    **未改子图的形状** —— 只把子图节点 `.invoke` 的目标换成**共享对象**（见 `TOOLS_BY_NAME`）。


from tools.mcp_server import TOOLS as _MCP_TOOLS

tools = [t["func"] for t in _MCP_TOOLS]

#    取的是**共享对象**（原先拿的是本文件自带的同名副本）。与 `agent_graph.py` 同款。
#    它**不是**分派表 —— 分派那两条在 `agent_graph` / `agent_checkpointer`（守卫 `test_tool_dispatch.py`）。
TOOLS_BY_NAME = {t["func"].name: t["func"] for t in _MCP_TOOLS}

#    上面那个列表**已经包含**这两个工具 ⇒ 加了之后**各出现两次**
#    `llm_react.bind_tools(tools)` 发给 LLM 时被拒：
#      `openai.BadRequestError: 400 - 'Tool names must be unique.'`
#    ⇒ **3 代 Agent 的 REACT 分支 100% 500**。

# 重新绑定工具到模型
llm_with_tools = llm.bind_tools(tools)



# 为每个工具创建模型实例（用于子图）
# 三个都 = 「模型轴 chat」+「长度轴 agent(1024)」，与上面的 `llm` 同角色。
llm_search = make_llm("chat", "agent")
llm_calc = make_llm("chat", "agent")
llm_date = make_llm("chat", "agent")

# ==================== 定义全局 State ====================
class AgentState(TypedDict):
    messages: Annotated[List, operator.add]
    intent: str  # 存储用户意图（search/calculator/date）
    final_output: str  # 存储最终回复
    user_name: str          # 新增：当前对话的用户名
    memory_space: str       # 新增：当前使用的记忆空间
    #    **由端点注入**（`api_v1_agent.py` 的 `/agent/advanced_chat` 与它的 `/stream`），
    #       一律 `.get(..., "unknown")` 读。
    #    子图（search/calc/date/translate/react）与本图**共用同一个 `AgentState`**
    #       父图的 `thread_id` 会流进子图节点，不必逐个子图再注入一次。
    thread_id: str
    #    —— 端点据此回 **429**（非流式）/ **error 帧**（流式）。
    #    与另外三张图的同名键**是同一个契约**：存**原因**，拼文案由
    #       `api_v1_agent.agent_budget_intercept_message` 统一做（别在节点里拼）。
    #    没挂 `operator.add` ⇒ last-write-wins + 落 checkpoint ⇒ **必须每轮清零**。
    budget_intercept: Optional[str]

# ==================== 创建 通用的“记忆注入”工具函数 ====================
#    与文件下方**逐字重复**，且下面那份在它之前从未被使用 ⇒ 删此处、保留下方。

def inject_memories_to_prompt(original_prompt: str, state: AgentState) -> str:
    """
    检索相关长期记忆，并将其注入到 Prompt 开头。
    如果检索不到，则返回原始 Prompt。
    """
    user_name = state.get("user_name", "default_user")
    memory_space = state.get("memory_space", "default")
    user_query = state["messages"][-1].content

    user_id = f"{user_name}:{memory_space}"
    #    记忆是「有更好」的锦上添花，检索失败（key 不对 / 网络不通 / mem0 或向量库挂了…）
    #    只该**少一段上下文**，不许把整条链打挂。改前这里是**裸调用**
    #    ⇒ `/agent/advanced_chat` 在 embedding 取不到时**整条 500**。
    #    **本文件的 `supervisor`（入口节点）另有一处**，同款处置 —— 见那一处的注释。
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


# ==================== 创建子图（搜索研究部门） ====================
def create_search_subgraph():
    """
    搜索子图：执行搜索 -> 总结搜索结果 -> 返回
    这是一个独立的图，有自己的节点和流程。
    """
    subgraph = StateGraph(AgentState)

    def search_execute(state: AgentState):
        """执行搜索"""
        query = state["messages"][-1].content
        result = web_search.invoke(query)
        return {"messages": [AIMessage(content=f"搜索原始结果：{result[:500]}")]}

    def search_summarize(state: AgentState, config: RunnableConfig):
        """总结搜索结果

           这是 `astream(stream_mode="messages")` 出不出 token 的**唯一条件**。
           本文件所有节点**都是同步的**，别改成 `async def`：同步的 `graph.invoke`
           （`/agent/advanced_chat` 走的就是它）会当场抛 `TypeError`。
        """
        #    这是**子图节点**：`user_name` / `thread_id` 由父图经共用 `AgentState` 流进来。
        user_name = state.get("user_name", "unknown")
        thread_id = state.get("thread_id", "unknown")
        if not check_token_budget(user_name, estimated_tokens=500):
            #    改前只塞一句话当答案，端点照常回 **HTTP 200** ⇒ 调用方**看不出被拒了**。
            #    **不许在这里 `raise`**（§二 的 checkpoint 污染实测）。
            #    本图（`advanced_agent`）的**清零在 `supervisor`**（它是入口节点），
            #       不在本节点 —— 这些是**子图节点**，不是"每轮必经"。
            return {"final_output": BUDGET_EXCEEDED_MSG,
                    "budget_intercept": BUDGET_EXCEEDED_MSG}

        raw = state["messages"][-1].content
        summary_prompt = f"请用一句话总结以下信息：{raw}"
        # `+` 聚合（`AIMessageChunk.__add__`），不是 `content +=`。
        # **遍历【所有】块**，不跳空 `content` —— `usage_metadata` 挂在**最后一块**上
        #    （provider 行为）。跳过它 ⇒ 下面那句记账**静默失效**，而接口一切正常。
        summary = None
        for chunk in llm_search.stream([HumanMessage(content=summary_prompt)], config=config):
            summary = chunk if summary is None else summary + chunk
        record_from_response(
            llm_search, summary, "answer_generation",
            user_name=user_name, thread_id=thread_id,
        )
        return {"final_output": summary.content}

    subgraph.add_node("search_execute", search_execute)
    subgraph.add_node("search_summarize", search_summarize)
    subgraph.set_entry_point("search_execute")
    subgraph.add_edge("search_execute", "search_summarize")
    subgraph.add_edge("search_summarize", END)

    return subgraph.compile()

# ==================== 创建子图（计算器部门） ====================
def create_calculator_subgraph():
    """计算器子图"""
    subgraph = StateGraph(AgentState)

    def calc_execute(state: AgentState):
        """从用户消息中提取表达式并计算"""
        user_name = state.get("user_name", "unknown")
        thread_id = state.get("thread_id", "unknown")
        if not check_token_budget(user_name, estimated_tokens=500):
            #    改前只塞一句话当答案，端点照常回 **HTTP 200** ⇒ 调用方**看不出被拒了**。
            #    **不许在这里 `raise`**（§二 的 checkpoint 污染实测）。
            #    本图（`advanced_agent`）的**清零在 `supervisor`**（它是入口节点），
            #       不在本节点 —— 这些是**子图节点**，不是"每轮必经"。
            return {"final_output": BUDGET_EXCEEDED_MSG,
                    "budget_intercept": BUDGET_EXCEEDED_MSG}

        query = state["messages"][-1].content
        # 使用简单 prompt 提取表达式
        extract_prompt = f"提取以下问题中的数学表达式，只返回表达式，不要其他内容：{query}"
        expression = llm_calc.invoke([HumanMessage(content=extract_prompt)])
        # 记账 —— `purpose="query_rewrite"`：这一步产出的**不是给用户的答案**，
        #    而是喂给 `calculator` 工具的**规整后的输入**（真答案 `42` 来自工具，见 `STREAMABLE_NODES` 那张表）。
        #    别改成 `answer_generation`：那会让"这一步是什么角色"在账面上失真。
        record_from_response(
            llm_calc, expression, "query_rewrite",
            user_name=user_name, thread_id=thread_id,
        )
        result = TOOLS_BY_NAME["calculator"].invoke(expression.content)
        return {"final_output": result}

    subgraph.add_node("calc_execute", calc_execute)
    subgraph.set_entry_point("calc_execute")
    subgraph.add_edge("calc_execute", END)

    return subgraph.compile()

# ==================== 创建子图（日期部门） ====================
def create_date_subgraph():
    """日期子图"""
    subgraph = StateGraph(AgentState)

    def date_execute(state: AgentState):
        result = TOOLS_BY_NAME["date_today"].invoke("")
        return {"final_output": result}

    subgraph.add_node("date_execute", date_execute)
    subgraph.set_entry_point("date_execute")
    subgraph.add_edge("date_execute", END)

    return subgraph.compile()

# ==================== 新增翻译子图（日期部门） ====================
# 因为翻译功能是直接调用大模型llm，不需要定义python函数脚本实现翻译功能，所以不用使用tool函数定义工具。
def create_translate_subgraph():
    """
    翻译子图：将用户输入翻译成英文。
    如果用户指定了目标语言，则翻译成对应语言（此处简化为英文）。
    """
    subgraph = StateGraph(AgentState)

    def translate_execute(state: AgentState, config: RunnableConfig):
        """执行翻译（改真流式 —— 译文本就是答案 ⇒ 该流）"""
        user_name = state.get("user_name", "unknown")
        thread_id = state.get("thread_id", "unknown")
        if not check_token_budget(user_name, estimated_tokens=500):
            #    改前只塞一句话当答案，端点照常回 **HTTP 200** ⇒ 调用方**看不出被拒了**。
            #    **不许在这里 `raise`**（§二 的 checkpoint 污染实测）。
            #    本图（`advanced_agent`）的**清零在 `supervisor`**（它是入口节点），
            #       不在本节点 —— 这些是**子图节点**，不是"每轮必经"。
            return {"final_output": BUDGET_EXCEEDED_MSG,
                    "budget_intercept": BUDGET_EXCEEDED_MSG}

        query = state["messages"][-1].content
        # 简单粗暴地翻译成英文
        prompt = f"请将以下内容翻译成英文，只输出翻译结果：\n\n{query}"
        # 同 `search_summarize`：同步 `.stream` + 转发 `config` + `+` 聚合所有块。
        result = None
        for chunk in llm.stream([HumanMessage(content=prompt)], config=config):
            result = chunk if result is None else result + chunk
        record_from_response(
            llm, result, "answer_generation",
            user_name=user_name, thread_id=thread_id,
        )
        return {"final_output": result.content}

    subgraph.add_node("translate_execute", translate_execute)
    subgraph.set_entry_point("translate_execute")
    subgraph.add_edge("translate_execute", END)

    return subgraph.compile()

# ==================== 新增 ReAct 子图：用来执行子图是否需要循环，====================
# 循环的作用是如果用户提出的问题需要多个工具的调用就需要ReAct模式[思考-行动-思考]的循环思考模型，
# 这里的ReAct作为内部子图的思考动作来执行，这就是子图功能的强大，可以通过子图在内部嵌套各种的思考模型和功能，
def create_react_subgraph():
    """
    ReAct 子图：能自主调用工具的通用任务处理部门。
    内部包含 agent 节点和 tools 节点，形成一个循环。
    """
    subgraph = StateGraph(AgentState)

    # 为子图单独绑定工具的模型
    # 角色 = 「模型轴 chat」+「长度轴 agent(1024)」。
    llm_react = make_llm("chat", "agent")
    llm_react_with_tools = llm_react.bind_tools(tools)

    def agent_decide(state: AgentState, config: RunnableConfig):
        """决策节点：调用模型，让它决定是回复文本还是调用工具。

           **`+` 聚合在这里是【必须】的**：`tool_calls` 是碎片化到达的，只拼 `content`
           会让本子图的 `should_continue` 判不出 `"tools"` ⇒ **工具永远不会被执行**，
           而 `summarize` 照样写出 `final_output`（看着像正常回答）。
        """
        #    超预算时**必须返回 AIMessage、不能只写 `final_output`** ——
        #       本子图的出口是 `should_continue` 读 `messages[-1].tool_calls`：
        #       给一条**没有 tool_calls** 的消息 ⇒ 走 `END` ⇒ 由 `summarize` 把它的文本
        #       落成 `final_output`（那条边见 `add_conditional_edges` 的 `{END: "summarize"}`）。
        user_name = state.get("user_name", "unknown")
        thread_id = state.get("thread_id", "unknown")
        if not check_token_budget(user_name, estimated_tokens=500):
            #    **不改返回形状**（本子图的出口是 `should_continue` 读 `messages[-1].tool_calls`）。
            return {"messages": [AIMessage(content=BUDGET_EXCEEDED_MSG)],
                    "budget_intercept": BUDGET_EXCEEDED_MSG}

        # 构建基础 system prompt
        system_prompt = "你是一个能使用工具的智能助理。请根据用户需求自主调用工具完成任务。"

        # 注入长期记忆
        system_prompt = inject_memories_to_prompt(system_prompt, state)
        # 将 system prompt 和消息列表合并
        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = None
        for chunk in llm_react_with_tools.stream(messages, config=config):
            response = chunk if response is None else response + chunk
        # 记账。这是**循环节点**（`tools` ⇒ `agent`）—— 每一轮**各记一笔**，
        #    这正是要的：循环 N 轮就花 N 次钱。
        record_from_response(
            llm_react_with_tools, response, "agent_decision",
            user_name=user_name, thread_id=thread_id,
        )
        return {"messages": [response]}
    # 工具执行节点现在只需一行核心逻辑
    from tools.mcp_server import TOOL_HANDLERS

    def tool_execute(state: AgentState):
        last_message = state["messages"][-1]
        tool_messages = []

        for tc in last_message.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]

            # 从 MCP Server 的处理器中查找工具
            handler = TOOL_HANDLERS.get(tool_name)
            if handler:
                result = handler(tool_args)
            else:
                result = f"未找到工具: {tool_name}"

            tool_msg = ToolMessage(content=str(result), tool_call_id=tc["id"], name=tool_name)
            tool_messages.append(tool_msg)

        return {"messages": tool_messages}
    

    def should_continue(state: AgentState):
        """路由函数：检查最后一条消息是否包含tool_calls。"""
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    def summarize(state: AgentState):
        """收尾节点：把最后一条消息的文本落成 `final_output`。

        补（第 9 个依赖漂移之外的缺陷）:
          本子图原先**只有 agent / tools 两个节点、没有任何节点写 `final_output`**,
          而端点读的是 `api_v1_agent.py` 的 `result.get("final_output", "处理完成")`
          ⇒ **走 REACT 意图时永远返回占位串「处理完成」**（其余四个子图都写了）。
          实测复现:`POST /agent/advanced_chat?question=帮我规划…三步计划`
          → `{"answer":"处理完成","intent":"REACT"}`。
          它此前**测不出来** —— 先是 mem0 那道 500、后是工具重名那道 400,
             REACT 分支根本走不到底。**修好前面两个才把它暴露出来。**
          回归测试:`app/tests/test_agent_repairs.py::test_react_subgraph_sets_final_output`
        """
        last = state["messages"][-1]
        return {"final_output": getattr(last, "content", "") or ""}

    subgraph.add_node("agent", agent_decide)
    subgraph.add_node("tools", tool_execute)
    subgraph.add_node("summarize", summarize)
    subgraph.set_entry_point("agent")
    subgraph.add_conditional_edges(
        "agent",
        should_continue,
        # 原本这里是 `{..., END: END}` —— 直接结束就没有节点能写 final_output。
        {"tools": "tools", END: "summarize"}
    )
    subgraph.add_edge("tools", "agent")
    subgraph.add_edge("summarize", END)

    return subgraph.compile()

# ==================== 流式白名单（B1）====================
# **可流节点名单放在【图模块里】，端点不许自己抄一份字面量**（理由见
#    `app/agent/agent_graph.py` 同名常量处 —— 一个名字两个来源必然漂移，而漂移是**静默**的）。
# 必须放在**模块级**（不能放进 `build_advanced_agent`）：端点是按
STREAMABLE_NODES = frozenset({
    "chat",
    "search_summarize",
    "translate_execute",
    "agent",     # 子图**内层**名 —— `meta["langgraph_node"]` 报的就是它，不是 `react_dept:agent`
})


# ==================== 构建主图 ====================
from agent.memory_store import search_user_memory
def build_advanced_agent():
    workflow = StateGraph(AgentState)

    # 1. 添加主管节点（大脑）
    def supervisor(state: AgentState):
        """分析用户意图，决定路由方向（增强版：注入长期记忆mem0）"""
        user_query = state["messages"][-1].content

        #    **超预算时【必须】给 `intent`** —— `route_by_intent` 读的是 `state["intent"]`
        #        （`:423`）。少了它，langgraph 会当场 `KeyError: 'intent'` ⇒ **500**，
        #        而不是一句"预算用完了"。给 `"CHAT"` ⇒ 落到兜底分支 `chat_node`，
        #        而 `chat_node` 自己的守卫也会拦下（同一个人、同样的额度）
        if not check_token_budget(state.get("user_name", "unknown"), estimated_tokens=500):
            #    **`intent="CHAT"` 一个都不能少** —— `route_by_intent` 读 `state["intent"]`，
            #       少了它当场 `KeyError` = **500**，而不是一句"预算用完了"。
            return {"intent": "CHAT", "final_output": BUDGET_EXCEEDED_MSG,
                    "budget_intercept": BUDGET_EXCEEDED_MSG}

        # 从 Mem0 检索相关记忆（user_id 从 state 或配置中获取，这里先写死示例）
        # 排在门【之后】：`search_user_memory` 会去打一次 embedding（**花钱的**）。
        user_name = state.get("user_name", "default_user")
        memory_space = state.get("memory_space", "default")
        # 构建 Mem0 的 user_id
        user_id = f"{user_name}:{memory_space}"
        #    `inject_memories_to_prompt`（是**直调**），所以那一处的处置**盖不到这里**。
        #    漏了它 = `/agent/advanced_chat` 照旧整条 500（同一个 embedding 失败）。
        #    `except AssertionError: raise` 那句不是多余 —— 见上面那个函数的同款说明
        #       （`test_budget_soft_return._no_memory` 的探针就落在这一处）。
        try:
            memories = search_user_memory(user_id, user_query)
        except AssertionError:
            raise
        except Exception as e:              # noqa: BLE001 —— 见上：**任何**失败都只该降级
            logger.warning(
                f"[memory] 长期记忆检索失败 ⇒ 本次不注入（fail-open）：{type(e).__name__}: {e}")
            memories = []

        # 构建包含记忆的上下文
        memory_context = ""
        if memories:
            memory_context = "\n用户相关记忆：\n" + "\n".join(memories)

        classify_prompt = f"""分析以下用户请求，只返回一个单词表示意图：
- 如果需要搜索、查资料、了解新闻 → SEARCH
- 如果需要数学计算 → CALCULATOR
- 如果询问日期时间 → DATE
- 如果需要翻译文本 → TRANSLATE
- 其他复杂问题 → REACT

{memory_context}
用户请求：{user_query}
意图："""

        intent = llm.invoke([HumanMessage(content=classify_prompt)])
        # 记账放在 `state["intent"] = …` **之前**：下面那行是**改 state 本身**（不是返回增量），
        #    这里插在它前面只是为了读起来清楚，两者无依赖。
        record_from_response(
            llm, intent, "agent_decision",
            user_name=state.get("user_name", "unknown"),
            thread_id=state.get("thread_id", "unknown"),
        )
        state["intent"] = intent.content.strip()
        #    `budget_intercept` 是普通 state 键（last-write-wins + 落 checkpoint）
        #    ⇒ 只清一个出口的话，走**另一个**出口的那一轮会留着上一轮的值
        #    ⇒ 预算恢复之后那一轮**正常的提问**照样回 429。
        #    本节点是**原地改 `state` 再整个返回**（不是返回增量）⇒ 清零也写成赋值。
        state["budget_intercept"] = None
        return state

    # 2. 添加对话节点（简单聊天）
    def chat_node(state: AgentState, config: RunnableConfig):
        """兜底对话节点（改真流式）。

        它是**默认落点**：`route_by_intent` 是**精确匹配**的，模型多吐一个句号
           （`REACT。`）就静默落到这里 ⇒ **这条分支的流式体验 = 大多数请求的体验**。
        """
        #    这一处**不只是"漏记"** —— 它是 `supervisor` 超预算时的**落点**（见 `supervisor` 注释）：
        #       这里的守卫是"那条路不花钱"的**第二道保证**，别因为它看着冗余就删。
        user_name = state.get("user_name", "unknown")
        thread_id = state.get("thread_id", "unknown")
        if not check_token_budget(user_name, estimated_tokens=500):
            #    改前只塞一句话当答案，端点照常回 **HTTP 200** ⇒ 调用方**看不出被拒了**。
            #    **不许在这里 `raise`**（§二 的 checkpoint 污染实测）。
            #    本图（`advanced_agent`）的**清零在 `supervisor`**（它是入口节点），
            #       不在本节点 —— 这些是**子图节点**，不是"每轮必经"。
            return {"final_output": BUDGET_EXCEEDED_MSG,
                    "budget_intercept": BUDGET_EXCEEDED_MSG}

        # 构建基础 system prompt
        system_prompt = "你是一个智能助理，请直接回答用户的问题。"

        # 注入长期记忆
        system_prompt = inject_memories_to_prompt(system_prompt, state)

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        # 同族写法：同步 `.stream` + 转发 `config` + `+` 聚合所有块。
        response = None
        for chunk in llm.stream(messages, config=config):
            response = chunk if response is None else response + chunk
        record_from_response(
            llm, response, "answer_generation",
            user_name=user_name, thread_id=thread_id,
        )
        return {"final_output": response.content}

    # 3. 编译子图
    search_subgraph = create_search_subgraph()
    calc_subgraph = create_calculator_subgraph()
    date_subgraph = create_date_subgraph()
    translate_subgraph = create_translate_subgraph()
    react_subgraph = create_react_subgraph()

    # 4. 添加节点
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("chat", chat_node)
    workflow.add_node("search_dept", search_subgraph)
    workflow.add_node("calc_dept", calc_subgraph)
    workflow.add_node("date_dept", date_subgraph)
    workflow.add_node("translate_dept", translate_subgraph)
    workflow.add_node("react_dept", react_subgraph)

    workflow.set_entry_point("supervisor")

    # 5. 多分支条件路由
    def route_by_intent(state: AgentState):
        intent = state["intent"]
        if intent == "SEARCH": return "search_dept"
        elif intent == "CALCULATOR": return "calc_dept"
        elif intent == "DATE": return "date_dept"
        elif intent == "TRANSLATE": return "translate_dept"
        elif intent == "REACT": return "react_dept"
        else: return "chat" # chat 作为最终兜底

    workflow.add_conditional_edges("supervisor", route_by_intent, {
        "search_dept": "search_dept",
        "calc_dept": "calc_dept",
        "date_dept": "date_dept",
        "translate_dept": "translate_dept",
        "react_dept":"react_dept",
        "chat": "chat"
    })

    # 子图和对话节点执行完后，都走向结束
    workflow.add_edge("search_dept", END)
    workflow.add_edge("calc_dept", END)
    workflow.add_edge("date_dept", END)
    workflow.add_edge("translate_dept", END)
    # 添加 ReAct 子图到结束的边
    workflow.add_edge("react_dept", END)
    workflow.add_edge("chat", END)

    return workflow.compile(checkpointer=MemorySaver())