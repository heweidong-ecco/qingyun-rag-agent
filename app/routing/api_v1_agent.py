"""
API v1 路由集中定义
所有 /api/v1 前缀的接口在此管理。
"""
import asyncio
from loguru import logger
from fastapi import APIRouter, Depends, Query
from core.exceptions import ErrorCode, AppException
from routing.deps import get_current_user_hybrid

#：SSE 骨架 —— 本仓**所有**流式端点共用一份（含那 5 条实测约束的顺序）。
# 2026-10-04（批 3）：`anyio` / `json` / `StreamingResponse` / `logger` / `track_stream_cancel`
#    这 5 个 import **已从这个文件删掉** —— 它们此前**只被** `/agent/langgraph_chat/stream`
#    那一段内联生成器用到，现在那一段整块搬进了 `app/routing/sse.py`。
#    删它们不是"顺手清理"：留着就是**没人用的 import**（pyflakes 会报）。
from routing.sse import DONE_FRAME, graph_message_text, sse_frame, sse_response, sse_stream

from agent.agent_graph import agent_graph, STREAMABLE_NODES
# （2026-10-04）：四条流式链的**可流节点白名单**从**图模块**取
#    （端点不许自己抄一份字面量 ——  的教训：一个名字两个来源必然漂移，
#     而漂移是**静默**的）。所以这里要的是**模块对象**，不是常量本身。
import agent.agent_checkpointer as agent_checkpointer
import agent.agent_graph_advanced as agent_graph_advanced
import agent.agent_graph_advanced_learning as agent_graph_advanced_learning
from agent.pending_approvals import (
    list_pending, register, resolve,
    find_by_raw_thread_id,            # 丙段：按原 thread_id 反查**属主**
    approval_round_cap,               # 轮次上限
)
# 2026-10-06（§3.2）：审批留痕。必须是**模块级名字绑定** ——
#    测试靠 `monkeypatch.setattr(api_v1_agent, "record_decision", …)` 换掉它。
#    别改成函数体里 `from approval_audit import …`：那样 patch 会失效、用例变**假绿**。
from agent.approval_audit import record_decision, summarize_tool_calls, list_decisions
# 角色—— `/agent/approve` 的「本人或 admin」判据走这里，
# 别在本文件另写 `user_name == "admin"`（那就又多一处口径）。
from access.permission import UserRole, get_user_role
from langchain_core.messages import HumanMessage, ToolMessage
# 多分支路由（意图分类）高级 Agent：定义在 agent_graph_advanced_learning.py
from agent.agent_graph_advanced_learning import build_advanced_agent
from agent.plan_execute import plan_task, execute_plan, BudgetExceededError
from agent.agent_checkpointer import checkpointer_agent
from agent.memory_store import add_user_memory, search_user_memory
# 2026-10-13· **本文件第一批 `response_model`** —— 起因是
#    `app/tests/test_response_model_declared.py` 那道门：「**凡被前端调用的端点**必须声明返回形状」
#    （`⑥ 实施`把「外部检索与工具Agent编排」那一页接线时，下面这十几条**当场进了 `called` 集合**）
#    那道门的 `PENDING`（豁免清单）**只装门立起来之前就存在的老端点** ⇒ 不能往里塞新的。
from routing.schemas import (
    CostRecordsResponse,   # 2026-10-13：PENDING 那三条欠账
    TokenBudgetResponse,   # 同上
    AgentApprovalHistoryResponse,
    AgentApproveResponse,
    AgentChatResponse,
    AgentInterceptsResponse,
    AgentMcpChatResponse,
    AgentMemoryAddResponse,
    AgentMemorySearchResponse,
    AgentPendingContextResponse,
    AgentPendingResponse,
    AgentPlanExecuteResponse,
    AgentToolHealthResponse,
    AgentToolsResponse,
    AgentToolVersionsResponse,
    AgentTraceCostResponse,
    AgentTraceResponse,
)
# 2026-09-21 注释（N13 · **# 可扩展能力**）：`browser_tools` 依赖未安装的 chromium
#    ⇒ 两个端点每调必 500。装好 chromium 后连同下面两个端点一起取消注释。
# from browser_tools import fetch_webpage, screenshot_webpage
from tools.code_executor import execute_python
from tools.tool_health import run_health_check, get_tool_health, UNHEALTHY, _tool_health
from tools.mcp_server import TOOLS_DEFINITION
from billing.token_tracker import (
    check_token_budget, get_token_budget_info, check_budget_warning,
    get_pool_budget_info,
    get_user_summary, get_purpose_summary, get_thread_summary, get_recent_usage,
    get_user_history, generate_monthly_report,
    check_budget_before_call, estimate_tool_cost,
    TOOL_ESTIMATED_COST, PURPOSE_ESTIMATED_COST,
    get_intercept_count, check_session_token_budget,       # B8（①b）：会话级上限
    get_user_overview,                # B13（①b）：**读库**的全时总览
    get_global_daily_token_usage,     # B13（①b）：全站日级用量（B10 的数）
    thread_cost_breakdown,            #）：Trace 页**成本轴**的逐笔明细
    get_daily_tokens_by_purpose, split_daily_tokens,   # 批 4：今日按用途拆成 LLM / Embedding / 其它
)
# B13（①b）：全站日级**上限**常量。与 `get_global_daily_token_usage`
#    **成对使用** —— 只给"已用"不给"上限"，客户端算不出"全站还剩多少"。
#    别把 1_000_000 写死在这里（裁过这个值，改它只该改 `token_config.py`）。
from billing.token_config import GLOBAL_DAILY_TOKEN_LIMIT
# B11（①b）：全站日级熔断。
# 与 B8 **并列**，别把两者合并成一个函数 —— 维度不同（B8 按会话 / B11 按全站），
#    合并后一改就会同时动到两层。（与  各裁各的范围）
from billing.breaker import circuit, global_key
# 会话键（丙段）：**把身份拼进 checkpoint / 会话 id**。
# 为什么必须拼：`thread_id` 在**本文件 6 条端点上**默认就是 `"default"`
#    ⇒ 两个用户都用默认值 ⇒ 共用一个 checkpoint 桶，而消息 reducer 是
#    `Annotated[List, operator.add]`（append）⇒ **模型看得到别人的对话**。
#    只在**传进图 / 传进待接管队列**时拼 —— 响应里回显的仍是调用方传进来的**原值**。
from access.session_key import session_key
# （2026-10-04）：**5 条 Agent 链的留痕**落到 Redis `chat_history`。
#    为什么是这里而不是 checkpoint：`chat_history` 与 LangGraph checkpoint 是**两套互不相通的存储**，
#       而  决策 3 把 `status` 的落点定在 `chat_history` ⇒ 改前 5 条链**一个字都不留**
#       （判据：`grep -n "persist_turn\|append_chat_history" app/routing/api_v1_agent.py` ⇒ 改前零命中）。
#    代价（选它时就知道）：Agent 链的下一轮 prompt 来自 **checkpoint**，不读 `chat_history`
#       ⇒ 给它写的是**日志型数据**（今天无读者）。反方向有一条真影响：`api_v1_rag.py` 在
#       **前端没传历史时**读这条键 ⇒ 此后 Agent 链的轮次会进 RAG 的 prompt（=「统一会话」的意图）。
from core.cache import persist_turn
# 记录工具 开始追踪 结束追踪
from tools.tool_visualizer import (
    start_trace, finish_trace, get_trace, get_all_traces,
    get_trace_of_any_owner,     # N4：admin 例外用的旁路（普通路径别用）
)
# MCP Client 高级 Agent（会话池版）及动态工具列表
from agent.agent_graph_advanced import mcp_agent, get_mcp_tools


router = APIRouter(prefix="/api/v1")


#: 预算拦截时给调用方看的那句话的**前缀**（批 7）。
#: **由端点层拼、不由图里拼** —— 图写进 `budget_intercept` 的是**原因**，
#:    拼文案只有这一个地方。理由见 `agent_budget_intercept_message` 的 docstring。
BUDGET_INTERCEPT_PREFIX = "预算拦截，本轮未继续执行："


def agent_budget_intercept_message(why: str) -> str:
    """把图写下的**拦截原因**拼成给调用方看的那句话（**唯一**的拼法）。

    ## 为什么必须有这个函数（别在各端点再拼一遍）

    9 条端点（4 非流式 + 4 流式 + `/agent/approve`）都要在读到 `budget_intercept` 时
    回一句话。各拼各的 ⇒ 同一次拦截在不同端点上说法不同，而**调用方看不出这是同一件事**。

    ## 文案是「**本轮**」口径，不是「本次工具调用未执行」

    那句话是 （2026-10-05 批 2）留下的，当时**只有**工具触发那一种拦截 ——
    对它是准的。**批 7新增的 10 处软返回里，绝大多数根本没有工具调用**
    （四张图的**入口节点**在调 LLM **之前**就拦下了：`agent_decide` / `supervisor`）
    ⇒ 旧文案对它们**不准**（说了一个没发生的事）。

    ⇒ 改成「本轮未继续执行」—— 对**两种**拦截都成立。
    代价（选了就要认）：`/agent/mcp_chat` 那条 429 的中文**跟着变了**。
       而"哪个工具没执行"这个信息**没有丢** —— `check_multilevel_budget` 的 reason 里本来就带
       （形如「工具 `x` 将超出…」）。裁定。

    **`why` 为空时也要能调**（`.get` 可能拿到 `None`）—— 返回一句兜底，不返回空串：
       空的 `{"error": ""}` 跑出去，调用方**比现在还看不懂**。
    """
    why = (why or "").strip()
    if not why:
        # 图写了标志却没写原因 = 写入方的 bug。别静默变成空串 —— 给一句能看懂的话。
        return BUDGET_INTERCEPT_PREFIX.rstrip("：") + "（未提供原因）"
    return f"{BUDGET_INTERCEPT_PREFIX}{why}"


def summarize_agent_result(result: dict) -> dict:
    """把 LangGraph 的返回态整理成对调用方**有意义**的形状。

    2026-09-20 修（契约缺陷）:
      这张图带 `interrupt_before=["approval"]` —— **停在审批点时，最后一条消息是
      "只带工具调用、没有文字"的 AIMessage**（实测:`AIMessage content=''
      tool_calls=[{'name': 'calculator', …}]`）。
      而端点原先直接取 `result["messages"][-1].content` ⇒ **返回 200 + 空答案**，
      调用方**完全看不出"正在等人工审批"**（明明有配套的 `/agent/approve`）。
      实测复现:POST /agent/langgraph_chat?question=请计算6*7 → `{"answer": ""}`。

    判据是「**最后一条消息带 `tool_calls`**」—— 只看这一条。
      · **这条判据为什么成立**（B4 之后**理由变了**，别照旧理解）：
        图带 `interrupt_before=["approval"]` ⇒ 停在审批点时，末条消息**必带** tool_calls。
        而**非敏感**的 tool_calls（`calculator` / `date_today`）**不会出现在本函数的输入里** ——
        它们直接跑 `tools → agent → … → END`，**从不停在图中间**。
        ⇒ 「末条带 tool_calls」在这里**仍然等价于**「停在审批点」。
      · **但等价性依赖上面那一句**：若将来有**别的**路径把"跑了一半的图"喂进本函数
        （流式返回 / 调试端点 / 某个非敏感工具提前返回），这条判据就会**误报 `pending_approval`**。
        ⇒ **改图的路由时，回来重看这里。**
      · **不能**再加 `and not content`：真实 LLM 常见"既写文字又调工具"
        （"我来帮你算一下。" + tool_calls），那种形态同样在等审批，
        加了这个条件就会误报 `answered` ⇒ 调用方照样不知道要去 `/agent/approve`
        （**原缺陷原样保留**，2026-09-20 由合并前评审指出并加了用例）。
      · **也**不会把中途态误报：`ToolMessage` **没有 `tool_calls` 属性**，
        所以"工具刚跑完、正要生成最终答案"那一态天然被排除。
    """
    messages = result.get("messages") or []
    if not messages:
        return {"status": "answered", "answer": ""}
    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    content = getattr(last, "content", "") or ""
    if tool_calls:
        return {
            "status": "pending_approval",
            # 保留模型已经写出的文字（真实 LLM 常"先说一句再调工具"）——
            #    但 `status` 明确告诉调用方：**这还不是最终答案**，工具尚未执行。
            "answer": content,
            "pending_tool_calls": [
                {"name": tc.get("name"), "args": tc.get("args")} for tc in tool_calls
            ],
        }
    return {"status": "answered", "answer": content}


def _tool_rulings(pending_calls: list, ruling_text: str) -> list:
    """把「人工对卡住的工具调用的裁定」变成**配对回答** —— 每个 `tool_call_id` 一条 `ToolMessage`。

    **为什么必须是 `ToolMessage`**（2026-10-04 端到端验收实测，改前塞的是别的类型）：

    **① 结构**：`interrupt_before=["approval"]` 停在审批点时，state 末尾是
      **一条带 `tool_calls` 的 `AIMessage`**。此刻再往 state 里 append 一条别的类型，
      就永久留下了「`tool_calls` 后面没有配对 `ToolMessage`」的**非法结构**
      真模型下一轮直接 400：
        `An assistant message with 'tool_calls' must be followed by tool messages
         responding to each 'tool_call_id'.`
      **假图 / 单测看不出来** —— 只有真模型的 API 才校验这个结构（本仓栽过：21 条单测全绿）。

    **② 路由**：`update_state` 会**按消息类型推断"这次更新来自哪个节点"**（`as_node`），
      推断结果决定了**接下来跑哪个节点**：
      · 塞 `AIMessage` ⇒ 被当成 **`agent` 节点**的输出 ⇒ 条件边重算 ⇒ **图当场 END**
        ⇒ `tools` / `agent` **一个都不跑**（实测：`approve` 耗时 **0.017s**、`answer` = 输入原文）。
      · 塞 `ToolMessage` ⇒ `as_node` 被推成 **`agent`**（= 最后跑过的那个节点）⇒ 条件边重算时
        末条是 `ToolMessage`（没有 `tool_calls`）⇒ 还是 **END**（实测，别凭直觉）。
      ⇒ **两条都要做**：塞 `ToolMessage` **且** 显式传 `as_node="tools"`
        （调用点见 `approve_agent_action`）—— 这样下一步正好是 `agent`
        ⇒ **模型真的被叫醒**，看到人工的裁定后生成最终答复（这才是"续跑"）。
        实测对照（`/tmp/probe_asnode.py`）：不传 `as_node` ⇒ `next=`；传 `"tools"` ⇒ `next=('agent',)`。

    `pending_calls` 为空 ⇒ 返回空列表（`update_state` 随之退化成"不改 state"）。
    判据 ⇒ `app/tests/test_approval_resume.py` §⑤（真图 + 假 LLM，核「state 里没有孤儿 `tool_calls`」）。
    """
    return [
        ToolMessage(content=ruling_text, tool_call_id=c["id"], name=c.get("name"))
        for c in (pending_calls or []) if c.get("id")
    ]


# ==================== 以下是 Agent 接口 ====================
# ==================== AgentGraph 接口 ====================
@router.post("/agent/langgraph_chat", response_model=AgentChatResponse)
async def langgraph_chat(
    question: str,                    # 这是一个查询参数
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),   # 这也是一个查询参数
    user_name: str = Depends(get_current_user_hybrid), # 这是依赖注入
):
    """
    使用 LangGraph Agent 进行对话。
    thread_id 用于区分不同的对话会话。

    本图带人工审批节点。若返回 `status="pending_approval"`，说明**工具还没执行**，
    要用返回的 `pending_tool_calls` 走 `/agent/approve`（带同一个 `thread_id`）继续。
    """
    # 2026-10-13：**本端点开始写轨迹轴**（改前不写）。
    #    为什么补：这一条是「外部检索与工具Agent编排」那一页的**主链**，而那一页要展示
    #    「**每一步打了谁 · 花了多久 · 回了什么**」—— 那些数只有轨迹轴有。
    #    **位置照 `/agent/mcp_chat`（`:1664`）**：排在下面两道门**之前** ——
    #       被拒的那次也**留得下一条痕迹**（否则"被拦"在轨迹上完全看不）。
    #    收表的动作**只在【答完】那一支**（见下面 `finish_trace` 那里）。
    start_trace(user_name, thread_id, question)

    # B8 · 会话级 token 上限—— 触顶动作 = **直接拒绝**（要素② 已裁）
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）。与上一段**并列、都要过**：
    # B8 管"这个会话花了多少"，这段管"全站今天花了多少"。
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段断言点 #1：**图收到的键**（不是 `thread_id`）—— 守卫 `app/tests/test_session_isolation.py`
    sess = session_key(user_name, thread_id)

    result = agent_graph.invoke(
        # 2026-10-04：`user_name` / `thread_id` 必须**进 state** ——
        #    图里靠它们记账（缺了**不报错**，只是静默记成 "unknown"，配额拦不住具体的人）。
        #    这里的 `thread_id` 是**原值**，不是下面 config 里的 `sess`：
        #       `sess = session_key(...)` 是 **checkpoint 的键**，两者用途不同
        #       （参照 `/agent/mcp_chat`，见  §三）。
        {"messages": [HumanMessage(content=question)],
         "user_name": user_name, "thread_id": thread_id},
        config={"configurable": {"thread_id": sess}}
    )
    # 2026-10-05（批 7）：图内软返回的**出口形状** —— 图里预算不够时，
    #    入口节点在 state 上留下 `budget_intercept`（改前它只塞一句"预算用完了"**当答案**，
    #    端点照常回 **HTTP 200** ⇒ 调用方**看不出被拒了**）。
    # **必须在 `summarize_agent_result` 之前 `raise`**：被拦的这一轮**没有停在审批点**
    #    ⇒ 下面 B5 那一支不许 `register`（否则队列里多一条**永远批不了**的假待办）。
    # 别在节点里 `raise` —— 会把 checkpoint 留成非法序列（§二 实测）。
    # 裁定。
    if result.get("budget_intercept"):
        raise AppException(ErrorCode.QUOTA_EXCEEDED,
                           agent_budget_intercept_message(result["budget_intercept"]))

    summary = summarize_agent_result(result)

    # B5 · 待接管队列—— `MemorySaver` 反查不出"谁卡住了"，只能在这里记账。
    # `else` 那支不是可省的：**本轮没卡住 ⇒ 清掉上一次的登记**，
    #    否则同一个 thread 一旦卡过一次，就会永远留在队列里**变成假待办**。
    # 记账也用 `sess`（不是裸 `thread_id`）—— 否则两个人用同一个 `thread_id`
    #    会在**队列里也串号**，`/agent/approve` 会拿着别人的 key 去续跑（丙段）。
    if summary.get("status") == "pending_approval":
        register(sess, user_name, summary.get("pending_tool_calls") or [],
                 raw_thread_id=thread_id, graph="agent_graph")
        # 2026-10-13：停在审批点 ⇒ **不收表**。
        #    理由：这条任务**还没跑完** —— 批准之后 `agent_graph.tool_execute` 会**继续往
        #    同一条轨迹里记**（`record_*` 只认"这条 key 在不在"，不看有没有收过表）
        #    ⇒ 页面在批准后重取轨迹，就能看见续跑那几步。
        #    代价（如实说）：`duration_ms` 取 `end_time or time.time - start_time`
        #       （`tool_visualizer.AgentTrace.to_dict`）⇒ 没收表时它是**"到现在为止"**，
        #       而不是"这一轮"的时长。页面据此**标明它是"从发起那次算到现在"**。
    else:
        resolve(sess)
        # 答完了 ⇒ 收表（`total_tokens` / `total_cost` 本处取不到 ⇒ 保持 0）。
        # **这就是那个"恒为 0"的来源**（未记，见  §六·2）——
        #    页面**不许拿它当"本次花费"**，花费一律走 `/agent/trace/{thread_id}/cost`。
        finish_trace(user_name, thread_id, summary.get("answer") or "")

    return {
        "question": question,
        "thread_id": thread_id,
        "requested_by": user_name,
        **summary,
    }

@router.post("/agent/langgraph_chat/stream")
async def langgraph_chat_stream(
    question: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    user_name: str = Depends(get_current_user_hybrid),
):
    """`/agent/langgraph_chat` 的**流式**版本。SSE 逐 token 返回。

    ## 帧格式（与 `/rag/stream_search` 一致，便于前端复用）

    | 帧 | 何时 |
    |---|---|
    | `data: {"content": "…"}` | **每个 token 一帧** |
    | `data: {"thread_id": …, "status": …, "answer": …, "pending_tool_calls": […]}` | 收尾**一帧汇总** |
    | `data: [DONE]` | 结束哨兵 |

    那一帧**汇总**不是可有可无的：`status="pending_approval"` 是**唯一**告诉调用方
       "工具还没执行、要带同一个 `thread_id` 去 `/agent/approve`"的地方。
       **砍掉它 = 前端只会看到一个戛然而止的半截答案**，而 HTTP 返回 200。

    ## 判据是「**逐字出现**」，不是「有 `text/event-stream`」

    **假流式**（整段一次到、前端再切字符）**照样有 `text/event-stream`、照样有 `data:` 帧**。
    ⇒ 判据钉在**后端出块**：`app/tests/test_agent_sse.py::test_graph_streams_one_chunk_per_token`。
    ⇒ 「时间戳递增」那一条 TestClient 测不出（拿到的是已缓冲的整段），
      用**真服务 + 真 HTTP** 验：
       `curl -N … | while IFS= read -r line; do echo "$(date +%T.%3N)  $line"; done`

    ## 三条接线**必须与 `/agent/langgraph_chat` 保持一致**（别只做一半）

    1. **B8 会话级上限 + B11 全站日级熔断** —— 两条都要过（在**进生成器之前**，
       否则触顶会变成"HTTP 200 + 流到一半断掉"，调用方看不出是被限额拒了）；
    2. **B5 待接管队列** —— 停在审批点时要 `register`，否则 `/agent/pending` 里**找不到它**
       （`MemorySaver` 没有"列出全部 thread"的 API，这个登记是**唯一**的入口）；
    3. **`summarize_agent_result`** —— 状态口径只有它一处，别在这里另写一套判断。
    """
    # B8 · 会话级 token 上限—— 触顶动作 = 直接拒绝（与 `/agent/langgraph_chat` 同）
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）—— 与上一段**并列、都要过**
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段：**checkpoint / 待接管队列**用拼过身份的键；响应里回显的仍是原 `thread_id`。
    #    与 `check_session_token_budget`（上面，用原值）**是两条轴**，别合并。
    sess = session_key(user_name, thread_id)

    ENDPOINT = "agent_langgraph_chat_stream"   # Prometheus 的 label

    # 2026-10-04（剩余 4 条链 · 批 3）：**内联生成器整个换成 `sse.sse_stream` 骨架**。
    #    帧序、编码、取消语义**逐帧等价**（判据：`app/tests/test_agent_sse.py` +
    #    `app/tests/test_cancel_propagation.py` **全绿且一行断言都没改**）。
    #    原来这个文件里那段 `try / except / except / finally` 的**顺序约束没有消失** ——
    #    它们搬进了 `app/routing/sse.py`（那 5 条实测约束的落点，见该模块 docstring）。
    #    别把"骨架里没有"读成"不需要"：`X-Accel-Buffering` · 同步收尾排在 `await` 前
    #    `shield=True` 关流，三件都还在，只是现在**只有一份**。
    async def _complete(collected):
        """收尾尾巴（正常跑完才进）：**汇总帧 + `[DONE]`**。

        形参 `collected`（已经发出去的那些文本块）**有意不用** —— 理由见下。
        """
        # **状态必须取自【图的最终状态】，不是"把 `agent` 节点的流式块攒起来"。**
        #
        #    这一条是 **2026-10-03 在真服务上跑出来的**（Step 4），不是想出来的：
        #    问一句会触发搜索的话，模型因工具报「未找到工具」而**重试了好几轮**。
        #    原先攒块 ⇒ 攒出的消息**带着上一轮的 `tool_calls`** ⇒ `summarize_agent_result`
        #    判成 `pending_approval` ⇒ **接口报"在等人工审批"，而图其实早就跑完了**
        #    （前端会一直等一个**永远不会来**的批准）。
        #    攒块还会让两轮的 name 粘成 `"date_todayduckduckgo_search"` 这种串
        #    （那是**当时的工具名**； 已把搜索换成 `web_search` ⇒ 别拿这串当真名）。
        #
        #    一轮就能跑完的场景**盖不住**它 ⇒ 守卫用**两轮**的假图：
        #       `app/tests/test_agent_sse.py::test_status_comes_from_final_state_not_from_streamed_chunks`
        #
        #    这样与 `/agent/langgraph_chat` 的口径**完全一致**（它也喂 `result` 整份 state），
        #       状态判定只有 `summarize_agent_result` 一处，不在这里另写一套。
        #
        #    骨架把 `collected` 递过来，**这不是"攒块"的口子** —— 它就是上面被否掉的那条路。
        #       要状态，就 `aget_state`。
        state = await agent_graph.aget_state({"configurable": {"thread_id": sess}})

        # 2026-10-05（批 7）：图内软返回的**出口形状** —— 同 `/agent/mcp_chat/stream`，
        #    但那边的 **429 这里发不出去**：响应头**已经发出去了**（HTTP 200 + `text/event-stream`）
        #    ⇒ 只能改发一帧 `{"error": …}`。
        #    **不许照旧发汇总帧** —— 那帧里有 `answer`，读起来就是"这轮正常答完了"。
        #    排在 `summarize_agent_result` **之前**：被拦的这一轮**没停在审批点**
        #       ⇒ 下面 B5 那一支不许 `register`（否则队列里多一条**永远批不了**的假待办）。
        why = (state.values or {}).get("budget_intercept")
        if why:
            msg = agent_budget_intercept_message(why)
            yield sse_frame({"error": msg}, ensure_ascii=False)
            yield DONE_FRAME
            # 挪进了这一支（别在末尾再写一处 `persist_turn`）—— 本轮**没答成**。
            persist_turn(user_name, question, msg, thread_id=thread_id, status="error")
            return

        summary = summarize_agent_result(state.values or {})

        # B5 · 待接管队列—— 与 `/agent/langgraph_chat` 同款：
        # `else` 那支不是可省的：本轮没卡住 ⇒ 清掉上一次的登记，
        #    否则同一个 thread 卡过一次就**永远留在队列里变成假待办**。
        if summary.get("status") == "pending_approval":
            register(sess, user_name, summary.get("pending_tool_calls") or [],
                     raw_thread_id=thread_id, graph="agent_graph")
        else:
            resolve(sess)

        yield sse_frame({"thread_id": thread_id, "requested_by": user_name, **summary}, ensure_ascii=False)
        yield DONE_FRAME
        #  · 留痕（`status="done"`）。**只写 `answered`** ——
        #    停在审批点时 `summary["answer"]` 是**模型已经写出来的那半句（非空）**
        #    ⇒ 不 gate 就会把「等审批的半截」写成 `done`，**正是本 DEC 的 status 要防的那类假信号**。
        #    （停在审批点是图的【正常】暂停，不属  的射程 ⇒ 本轮登记为边界、不实现别的 status。）
        #    位置：排在**帧之后** —— 与 `/rag/stream_search` 的 `_complete` 同款。
        #       redis 出问题时客户端**已经**拿到完整收尾，不会看到"答案被 error 帧顶掉"。
        if summary.get("status") == "answered":
            persist_turn(user_name, question, summary.get("answer") or "", thread_id=thread_id, status="done")

    return sse_response(sse_stream(
        # 上游**必须返回一个可 `aclose` 的句柄**（这里是个 lambda，返回 `astream` 对象）——
        #    骨架要拿它去关流（客户端断开后不关 ⇒ 图**继续跑完** = 继续调模型 = 继续烧钱）。
        #    别退回"在 `async for` 里内联调用"：那样拿不到句柄，关不掉。
        lambda: agent_graph.astream(
            # 2026-10-04：同 `/agent/langgraph_chat` —— 身份必须进 state。
            #    流式这条尤其容易漏：`lambda` 里的 state 写在别处，肉眼扫端点函数体看不出来
            #       ⇒ `app/tests/test_billing_wiring.py` 专门下钻 `lambda` 查这两个键。
            {"messages": [HumanMessage(content=question)],
             "user_name": user_name, "thread_id": thread_id},
            config={"configurable": {"thread_id": sess}},
            stream_mode="messages",
        ),
        endpoint=ENDPOINT,
        # 只转发**可流节点**出的块（名单在 `agent_graph.STREAMABLE_NODES`，不在这里抄一份）。
        #    图里还有 `tools` / `approval` 节点，不过滤的话它们吐的消息会**混进正文**
        #    （实测：`tools` 节点的 `ToolMessage` 内容会作为一块出现）。
        # 空 content 的块**必须跳过**（`tool_call` 的碎片 content 就是空的，一次调用来 2–3 个）
        #    —— `graph_message_text` 一并挡了（见 `app/routing/sse.py`）。
        extract=lambda item: graph_message_text(item, nodes=STREAMABLE_NODES),
        on_complete=_complete,
        #：**取消与异常两条出口**都把**已经流出去的那半截**补存进历史（不是直接丢）。
        #    必须是**同步**的 —— 骨架会在 `await aclose` **之前**调它，
        #       这正是"晚切（用户已看到字再点停止）也存得下"的原因。
        #    这里**只能用 `collected`**：取消/异常时图正跑到一半，**没有最终状态可查**
        #       （与 `_complete` 那条"答案取自 `aget_state`"不矛盾 —— 那条管 `done`）。
        on_incomplete=lambda collected, status: persist_turn(
            user_name, question, "".join(collected), thread_id=thread_id, status=status,
        ),
    ))

# ==================== 属于AgentGraph 接口下  新增的： AgentGraph 人工审批接口 ====================


@router.post("/agent/approve", response_model=AgentApproveResponse)
async def approve_agent_action(
    thread_id: str,
    approved: bool,
    edited_answer: str = None,
    owner: str = None,
    user_name: str = Depends(get_current_user_hybrid),
):
    """
    人工审批接口：批准 / 拒绝 / **改写后提交**。

    **续跑走的是 `agent_graph.invoke(None, config)`** —— `None` = **从 checkpoint 继续**，
       **不是**新开一轮。改成喂新消息 = 上下文断裂，**而接口返回看着一模一样**。
       守卫 ⇒ `app/tests/test_approval_resume.py`（假图钉住调用形状）。

    `edited_answer`：**人工把答案改过之后再放行**。
      · 不给 ⇒ 按原样续跑（行为与改动前一致）
      · 给了 ⇒ 先 `update_state` 把改写推成**一组 `ToolMessage`**，再从 checkpoint 续跑
        **必须进 state、不能只当返回值吐出去** —— 只放响应里，**后续节点看不到这个改写**。
        **形状是 `ToolMessage`（每个卡住的 `tool_call_id` 一条）+ 显式 `as_node="tools"`**
           —— **不是 `AIMessage`、也不是 `HumanMessage`**。理由（结构合法 + 图还往下走）
           逐条写在 `_tool_rulings` 的 docstring 里，**改前那版（`AIMessage`）是错的**，
           两种错都实测过（真模型 400 / 图当场 END）。2026-10-04 硬门 D 端到端验收后改。
      · **只有"批准"时才生效**；拒绝时给了也会被忽略（拒绝的语义是"别做了"）。

    **返回第三态 `status="pending_approval"`**：批了/拒了，但模型**又要**一个敏感工具
       ⇒ 图**再次**停在审批点 ⇒ 这里**重新登记**（不再无条件注销 —— 那会让会话变**孤儿**：
       `/agent/pending` 查不到、再批报"没有等待审批的任务"、同 thread 再问 500）。
       调用方要按**与首次触发时相同**的方式处理它（再走一遍本接口）。

    **轮次上限（作者 2026-10-05 裁「上限 3 轮」）**：上面那条"重新登记"
       **自带一个上限** —— 登记时记着 `rounds`，每"放行后又停" `+1`；到上限（`approval_round_cap`，
       默认 **3**，env `MAX_APPROVAL_ROUNDS` 可改）就**不再登记**，改为注入一条
       "已达上限、请直接作答"的 `ToolMessage` 并**续跑**，让图自己收尾：
       · 收尾成功 ⇒ `status="approved"/"rejected"` + **`forced_finish=True`** + `rounds`
       · 连收尾提示都拦不住（模型仍要敏感工具）⇒ `status="error"` + `rounds`，该轮终止、**不入队**
       没有上限的后果：模型可**无限**要求敏感工具、人工就得无限批（实测 3 次收敛，但**没有任何机制阻止 30 次**）。

    **2026-10-06（裁定 6）起多一个可选 `owner`**：`thread_id` 的默认值在
       10 条 agent 端点上都是 `"default"` ⇒ 两个用户都不传就**撞车** ⇒ 本端点会走上面那条
       「对应多条待审批会话」而**谁也批不了**。`owner` 用来**收窄候选**。
       **只是收窄，不是授权** —— 传了它照样走归属校验（给了却一条不匹配 ⇒ 落在「0 条」那支）。
       默认 `None` ⇒ **退回现有行为**（丙段"按 raw 反查属主"的语义**没动**）。

    **2026-10-06（§3.2）起每次真的裁决都留痕**（`approval_audit.record_decision`）——
       记 `owner` / `actor` / `decision` / `edited` / `rounds` / `reason`。
       写库失败**只打日志**（fail-open）⇒ 不许让本端点 500。
    """
    # B8 · 会话级 token 上限+ B11 · 全站日级熔断（`①b`）。
    # 2026-10-05（批 7）补 —— 本端点此前是全仓**唯一**既无 B8 也无 B11 的
    #    **烧钱**端点（R1.3 由中间件覆盖 ⇒ 不是"零门"，但续跑照样烧钱）。
    #    **两条并列、都要过**（别合并）—— 与其余 11 条端点同款。
    #    用的是**调用方** `user_name`，**不是属主 `owner`**（属主要等查完队列才知道）。
    #       这是一条**新裁决**：谁发请求谁被限，与其余端点一致。
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段：**先按【原 thread_id】查队列定属主，再按属主拼键**。
    #    别按调用方拼 —— admin 会拼出自己那个不存在的桶 ⇒ **永远批不了别人的**（硬门 D 死掉）。
    candidates = find_by_raw_thread_id(thread_id)
    if owner is not None:                     # ← 裁定 6：先按 owner 收窄候选（不是授权）
        candidates = [c for c in candidates if c["user_name"] == owner]
    if not candidates:
        # 队列是**唯一**入口（`MemorySaver` 反查不出"谁卡住了"）⇒ 这里没有 = 没有可批的。
        #    已知代价：`AGENT_CHECKPOINT_BACKEND=sqlite` 重启后（图在盘上、队列在内存）
        #    会走到这一支 —— 但那种会话**本来就已经是孤儿**（见 `pending_approvals` 模块 docstring）。
        return {"status": "error", "message": "当前没有等待审批的任务"}

    if len(candidates) > 1:
        # 同一个原 thread_id 被多个人用过 ⇒ **光凭它定不了是哪一条**（这正是隔离问题本身）。
        # 别"挑第一条" —— 那等于随机批一个人的会话。
        owners = sorted({c["user_name"] for c in candidates})
        return {
            "status": "error",
            "message": (f"thread_id={thread_id!r} 对应多条待审批会话（属主：{owners}）—— "
                        f"请先看 `/agent/pending` 确认是哪一个"),
        }

    owner_name = candidates[0]["user_name"]
    # 归属校验：**本人或 admin**（admin 那条不是可省的 —— 队列本来就跨用户）。
    #    局部名是 `owner_name` 而**不是** `owner`：`owner` 现在是本函数的**可选形参**
    #       （裁定 6，用来收窄候选）⇒ 直接叫 `owner` 会**把形参就地覆盖掉**，
    #       而那个值下面还要用（重新入队时按属主登记）。
    if user_name != owner_name and get_user_role(user_name) != UserRole.ADMIN:
        return {"status": "error", "message": f"无权审批：该会话属于 {owner_name}"}

    # 用**登记时拼好的**那个键（`candidates[0]["thread_id"]`），**不在这里重拼** ——
    #    重拼 = 又多一处"拼法"口径，两处一旦不一致，`resolve()` 会静默清不掉（幂等、不报错）。
    sess = candidates[0]["thread_id"]
    config = {"configurable": {"thread_id": sess}}

    # 按**登记的图**路由（作者 2026-10-03 裁）：`/agent/langgraph_chat` ⇒ `agent_graph`，
    #    `/agent/memory_chat` ⇒ `checkpointer_agent`。
    #    写死 `agent_graph` 的后果：memory_chat 那条会话**永远放行不了**
    #    （门关了却没有钥匙 —— 比不加门还糟）。
    # 字典**在这里现建**（不是模块级常量）：模块级常量会把图对象**早绑定**，
    #    测试里 `monkeypatch.setattr(m, "agent_graph", …)` 就换不掉了。
    GRAPHS = {"agent_graph": agent_graph, "checkpointer_agent": checkpointer_agent}
    graph_name = candidates[0].get("graph", "agent_graph")
    target = GRAPHS.get(graph_name)
    if target is None:
        # 别「猜一个」 —— 猜错就是往**别的图**上写状态。
        return {
            "status": "error",
            "message": f"登记表里的图名不认识：{graph_name!r}（已知：{sorted(GRAPHS)}）",
        }

    # 获取当前图的状态
    current_state = target.get_state(config)

    if current_state.next != ("approval",):
        # B5：图没停在审批点 ⇒ **注册表里若有这个 thread，那是陈的** ⇒ 顺手清掉。
        # 这一支也要清 —— 否则"/agent/pending 说有，批的时候说没有"，对不上。
        resolve(sess)
        return {"status": "error", "message": "当前没有等待审批的任务"}

    # 卡在审批点的那条消息带了哪些工具调用 ⇒ 它们的 `tool_call_id` **必须**有人配对回答。
    #    别用 `getattr(..., "tool_calls", None) or []` 糊过去：真的取不到就说明"没停在审批点"，
    #       而上面那句已经在拦它了 —— 这里的取值失败**不该被静默吞掉**。
    pending_calls = (
        getattr(current_state.values["messages"][-1], "tool_calls", None) or []
    )

    # 留痕（§3.2）：**只记"真的落到图上的裁决"**。
    #    位置是刻意的 —— 必须在上面那道「图没停在审批点 ⇒ 登记陈了」的守卫**之后**：
    #       那一条发生在归属校验**通过之后**，把它也算成一次裁决就是**假留痕**
    #       （§3.2 只列了三条"不记"，这是第四条，见具体化 C）。
    #    `owner_name` 是**会话的**、`user_name` 是**动手的**（admin 接管时**必然不同**）
    #       —— 别合成一个字段。
    #    `edited` 的真实语义是「**代替模型给出了这次工具调用的结果**」，不是"改写了答案"。
    #    **两道 fail-open，不是重复**：`record_decision` 内部那道拦的是"写库失败"；
    #       外面这道拦的是**参数绑定失败**（它发生在**进函数之前** —— 日后签名漂移
    #       ⇒ `TypeError` ⇒ 裸调用会把审批打成 500，而留痕是**旁路**）。
    try:
        record_decision(
            owner=owner_name,
            actor=user_name,
            decision="approved" if approved else "rejected",
            edited=bool(approved and edited_answer is not None),
            raw_thread_id=thread_id,
            graph=graph_name,
            rounds=candidates[0].get("rounds", 1),
            reason=summarize_tool_calls(pending_calls),
        )
    except Exception as e:
        print(f"[ApprovalAudit] 留痕写入失败（已忽略）: {e}")

    if approved:
        if edited_answer is not None:
            # B6 修正（2026-10-04 · 端到端验收）：人工改写**回填成 `ToolMessage`**，
            #    不再是 `AIMessage` —— 两个理由（结构合法 · 图还能往下走）见 `_tool_rulings`。
            target.update_state(config, values={"messages": _tool_rulings(
                pending_calls,
                f"【人工接管】该工具**未被执行**。人工给出的结论：{edited_answer}")},
                as_node="tools")       # 见 `_tool_rulings` 的「② 路由」——不传就会直接 END
        else:
            # 原样放行：`values=None` ⇒ 不改 state，图继续前进到 approval 节点，然后去 tools
            target.update_state(config, values=None)
        result = target.invoke(None, config)
    else:
        # 拒绝：同样**回填 `ToolMessage`**（不再是 `HumanMessage` —— 同型缺陷，见 `_tool_rulings`）。
        # 措辞里**不许出现 `edited_answer`**：拒绝的语义是"别做了"，不是"按我说的做"。
        target.update_state(config, values={"messages": _tool_rulings(
            pending_calls,
            "【人工接管】该工具已被人工**拒绝执行**，请不要再调用它 —— "
            "直接告知用户这次操作被拒绝了。")},
            as_node="tools")           # 同上：不传 `as_node` 图就会直接 END（模型不参与）
        result = target.invoke(None, config)

    # 2026-10-05（批 7）：**续跑的这一轮也可能被预算拦下** —— `agent` 是入口节点、
    #    每轮都查预算，而放行后图正要从 `approval` 回到它。不认这个标志的话，下面那些
    #    `answer` 拿到的就是那句"预算用完了"的**伪答案**（HTTP 200 + 一个看着跑完了的状态）。
    #    **先 `resolve` 再 `raise`**：软返回 = 图这一轮**真跑完了**（不是停在审批点）
    #       ⇒ 不注销就留在队列里变成一条**永远批不了**的假待办。
    #    判据：`app/tests/test_budget_soft_return.py::test_approve_续跑那一轮被拦时回429`
    if result.get("budget_intercept"):
        resolve(sess)
        raise AppException(ErrorCode.QUOTA_EXCEEDED,
                           agent_budget_intercept_message(result["budget_intercept"]))

    final_message = result["messages"][-1]

    # B5/B6 修正（2026-10-04 · 端到端验收）：**只有"真的走完了"才许 `resolve`**。
    #    改前是**无条件**注销 ⇒ 放行后若模型**又**要求敏感工具，图**再次**停在审批点，
    #    而队列里已经没有它了 ⇒ **孤儿会话**：`/agent/pending` 查不到、
    #    再 `/agent/approve` 报「当前没有等待审批的任务」、同 thread 再问 ⇒ 400。
    #    实测（2026-10-04 真服务）：`approve` 返回 `answer=""`（末条 = 只有 tool_calls 的 AIMessage）
    #    且放行后 `count=0` —— 正是这个形态。
    #    判据 ⇒ `app/tests/test_approval_resume.py::test_resume_that_stops_again_is_re_registered`
    after = target.get_state(config)
    if after.next == ("approval",):
        # 又停在审批点 ⇒ 按**轮次上限**分两路（作者 2026-10-05 裁「上限 3 轮」）。
        new_calls = list(getattr(after.values["messages"][-1], "tool_calls", None) or [])
        rounds = candidates[0].get("rounds", 1)
        if rounds >= approval_round_cap():
            # 触顶：**不再入队**（否则模型可无限要求敏感工具、人工无限批）。
            #    改为注入一条「已达上限，请直接作答」的裁定并**续跑**，让图自己收尾。
            target.update_state(config, values={"messages": _tool_rulings(
                new_calls,
                "【人工接管】已达人工审批次数上限，该工具**未被执行**。"
                "请不要再调用任何敏感工具，直接根据你已有的信息回答用户。")},
                as_node="tools")           # 同前：不传 `as_node` 图会直接 END（模型不参与）
            forced = target.invoke(None, config)
            # 2026-10-05（批 7）：**这是本端点的【第二个】`invoke`**，同样要认标志 ——
            #    只认头一个的话，走到这条路的用户会拿到 `forced_finish=True` +
            #    `answer="预算用完了"`（看着像"模型被强制收口了"，其实是根本没跑）。
            #    判据：`app/tests/test_budget_soft_return.py::test_approve_触顶强制收尾那一轮被拦时回429`
            if forced.get("budget_intercept"):
                resolve(sess)
                raise AppException(ErrorCode.QUOTA_EXCEEDED,
                                   agent_budget_intercept_message(forced["budget_intercept"]))
            if target.get_state(config).next == ("approval",):
                # 连"请收尾"都拦不住 ⇒ 认输：返回 error 且**不再入队**（不再无限循环）。
                #    这条出口会让该 thread 停在审批点（与"孤儿"同病）——但它是**有界的**；
                #       不这么做，"封顶"就退化成了"换个姿势继续无限循环"。
                resolve(sess)
                return {
                    "status": "error",
                    "thread_id": thread_id,
                    "message": (f"人工审批已达上限（{approval_round_cap()} 轮），"
                                "模型仍要求敏感工具 —— 该轮已终止，⛔ 未重新入队。"),
                    "rounds": rounds,
                }
            resolve(sess)
            return {
                "status": "approved" if approved else "rejected",
                "thread_id": thread_id,
                "answer": forced["messages"][-1].content,
                "requested_by": user_name,
                "forced_finish": True,      # 触顶强制收尾（调用方可据此提示"模型被强制收口"）
                "rounds": rounds,
            }
        # 未到上限 ⇒ **重新入队**（按**属主**登记，不是按调用方 —— 批的人可能是 admin）。
        # `raw_thread_id` 用**请求里那个原值**（不是拼过的 `sess`）—— `register` 的契约要求原值。
        register(sess, owner_name, new_calls, raw_thread_id=thread_id, graph=graph_name,
                 rounds=rounds + 1)
        return {
            "status": "pending_approval",       # 第三态：批了，但它**又**停下来了
            "thread_id": thread_id,
            "answer": final_message.content,
            "pending_tool_calls": [
                {"name": tc.get("name"), "args": tc.get("args")} for tc in new_calls
            ],
            "requested_by": user_name,
            "rounds": rounds + 1,
        }

    # 走到这里 = 图**真的走完了** ⇒ 注销（否则它会**永远留在队列里**；`resolve` 幂等，重复调不抛）。
    resolve(sess)

    return {
        "status": "approved" if approved else "rejected",
        "thread_id": thread_id,
        "answer": final_message.content,
        "requested_by": user_name,
    }


@router.get("/agent/pending", response_model=AgentPendingResponse)
async def list_pending_approvals(
    user_name: str = Depends(get_current_user_hybrid),
):
    """列出**当前等待人工接管**的会话（硬门 D 的入口）。

    **为什么要有这个端点**：`MemorySaver` **没有"列出全部 thread"的 API**
    ⇒ 没有它，**接管事件在界面上根本找不到**（硬门 D 判据③ 的反例正是这个）。
    数据来自 `app/agent/pending_approvals.py`（**进程内存** —— 重启即空，见其 spec）。

    **本端点只读**，不改任何状态；批准/拒绝走 `POST /agent/approve`。

    **2026-10-03（丙段）起每行多两个字段**（**加性**，老调用方不受影响）：
      · `raw_thread_id` —— 调用方传的**原值**（`thread_id` 现在是**拼过身份**的键）
      · `graph` —— 这条会话停在**哪张图**上（`agent_graph` / `checkpointer_agent`）
        ⇒ **批量批的时候要看它**：`/agent/approve` 按它选图。

    **2026-10-06（§二·发现③）起：本人默认只看自己的；admin 看全量。**
      改动前是 `list_pending` 原样返回 ⇒ **任何登录用户都能看到所有人的待批会话**
         （含别人的 `tool_calls` 与 `raw_thread_id`）。同族的 `/agent/traces` 在  修过，它没跟上。
      **过滤放在这里，不改 `pending_approvals.list_pending`** ——
         那个函数的职责就是"交出这个进程级队列"，往里加 `user_name=None` 只会造出
         「忘了传 = 静默全量」的默认值（口径）。
    """
    rows = list_pending()
    if get_user_role(user_name) != UserRole.ADMIN:      # admin 例外，显式一行（照 :1943）
        rows = [r for r in rows if r["user_name"] == user_name]
    return {"count": len(rows), "items": rows, "requested_by": user_name}


def _serialize_messages(messages: list) -> list:
    """把图里的消息序列转成 **JSON 可发的**形状（§3.1）。

    `content` **可能是 list**（多模态 parts）⇒ **原样带上**，别假定是 `str`、
       也别 `str` 糊成一坨 —— 前端有 `messageText` 负责归一。
       真的碰到既不是 `str`/`list`/`None` 的东西时，**降级成 `str`**：
       宁可显示得难看，也不能让整条端点 500。
    """
    out = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if content is not None and not isinstance(content, (str, list)):
            content = str(content)
        item = {
            "type": getattr(msg, "type", None) or type(msg).__name__,
            "content": content,
        }
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            item["tool_calls"] = [
                {"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")}
                for tc in tool_calls
            ]
        for attr in ("name", "tool_call_id"):
            value = getattr(msg, attr, None)
            if value is not None:
                item[attr] = value
        out.append(item)
    return out


@router.get("/agent/pending/context", response_model=AgentPendingContextResponse)
async def pending_approval_context(
    thread_id: str = Query(..., min_length=1),
    owner: str = Query(None),
    user_name: str = Depends(get_current_user_hybrid),
):
    """取出**某个卡住的会话的完整上下文**（硬门 D 演示那一栏， §3.1）。

    **为什么必须有它**：`/agent/pending` 返回的只有登记表那 7 个字段 ——
       没有 messages、没有用户问的原话。没有本端点，操作员就是**在不知道前因的
       情况下放行**（而那正是"人工接管"要避免的事）。

    定位逻辑与 `/agent/approve` **同构**（不另起一套）：反查候选 → 归属校验 →
       按**登记的图**取 state。两条都拒绝时用 **200 + `{"status":"error"}`**，
       **不改 404** —— 与 `/agent/approve` 一致比"我认为更规范"重要。

    `owner` **只是【收窄候选】，不是授权** —— 传了它**照样**要走归属校验。
       （给了 `owner` 却一条都不匹配 ⇒ 落在「0 条」那支。）
       别把它读成"下游收拼过的键"：`/agent/approve` 收 raw 的语义没动（丙段）。

    `thread_id` **必填、无默认值** —— 与那 10 条 agent 端点的 `Query("default", …)` **不同**，
       是有意的：本端点的输入来自 `/agent/pending` 的输出，**它一定带着 raw_thread_id**；
       给个 `"default"` 默认值只会让"忘了传"静默变成本不该命中的那个会话。
    """
    candidates = find_by_raw_thread_id(thread_id)
    if owner is not None:                     # ← 裁定 6：先按 owner 收窄候选
        candidates = [c for c in candidates if c["user_name"] == owner]

    if not candidates:
        return {"status": "error", "message": "当前没有等待审批的任务"}

    if len(candidates) > 1:
        owners = sorted({c["user_name"] for c in candidates})
        return {
            "status": "error",
            "message": (f"thread_id={thread_id!r} 对应多条待审批会话（属主：{owners}）—— "
                        f"请指定 owner 收窄"),
        }

    row = candidates[0]
    # 归属校验：**本人或 admin** —— 与 `/agent/approve` 同一条（admin 那条不是可省的）。
    if user_name != row["user_name"] and get_user_role(user_name) != UserRole.ADMIN:
        return {"status": "error", "message": f"无权查看：该会话属于 {row['user_name']}"}

    # 字典**在这里现建**（不是模块级常量）：模块级常量会把图对象**早绑定**，
    #    测试里 `monkeypatch.setattr(m, "agent_graph", …)` 就换不掉了。
    GRAPHS = {"agent_graph": agent_graph, "checkpointer_agent": checkpointer_agent}
    graph_name = row.get("graph", "agent_graph")
    target = GRAPHS.get(graph_name)
    if target is None:
        return {
            "status": "error",
            "message": f"登记表里的图名不认识：{graph_name!r}（已知：{sorted(GRAPHS)}）",
        }

    state = await target.aget_state({"configurable": {"thread_id": row["thread_id"]}})

    return {
        "status": "ok",
        "owner": row["user_name"],
        "graph": graph_name,
        "rounds": row.get("rounds", 1),
        "next": list(state.next),
        "messages": _serialize_messages(state.values.get("messages", [])),
        "requested_by": user_name,
    }

# ==================== 高级图LangGraph进阶 接口：包含---条件边、循环、子图  ====================


advanced_agent = build_advanced_agent()

@router.post("/agent/advanced_chat")
# 新增Mem0 灵活 独立隔离的记忆空间，memory_space，默认：default
async def advanced_agent_chat(
    question: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    memory_space: str = "default",
    user_name: str = Depends(get_current_user_hybrid),
):
    """使用多分支路由的高级 Agent 进行对话"""
    # B8 · 会话级 token 上限
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）。与上一段**并列、都要过**：
    # B8 管"这个会话花了多少"，这段管"全站今天花了多少"。
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段断言点：图收到的键 = 拼过身份的（不是裸 `thread_id`）
    sess = session_key(user_name, thread_id)

    result = advanced_agent.invoke(
        {
            "messages": [HumanMessage(content=question)],
            "user_name": user_name,
            "memory_space": memory_space,
            # 2026-10-04：本端点**原先只传 `user_name`**，没有 `thread_id`
            #    ⇒ 6 个节点的记账会全部落到 `"unknown"` 这个会话桶里
            #    （账记上了、但归不到具体哪个会话）。传**原值**，不是 `sess`。
            "thread_id": thread_id,
        },
        config={"configurable": {"thread_id": sess}}
    )
    # 2026-10-05（批 7）：图内软返回的**出口形状**。本图**6 个出口**都会写
    #    `budget_intercept`（不只是入口 `supervisor`）⇒ 这里一处收口。
    #    判据：`app/tests/test_budget_soft_return.py::test_advanced_agent_每个软返回出口都置标志`
    if result.get("budget_intercept"):
        raise AppException(ErrorCode.QUOTA_EXCEEDED,
                           agent_budget_intercept_message(result["budget_intercept"]))

    return {
        "question": question,
        "answer": result.get("final_output", "处理完成"),
        "intent": result.get("intent", "unknown"),
        "thread_id": thread_id,
        "memory_space": memory_space,
        "requested_by": user_name,
    }


@router.post("/agent/advanced_chat/stream")
async def advanced_agent_chat_stream(
    question: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    memory_space: str = "default",
    user_name: str = Depends(get_current_user_hybrid),
):
    """`/agent/advanced_chat` 的**流式**版本。SSE 逐 token 返回。

    ## 帧格式（与另外三条 agent 流式链**同一套**，便于前端复用）

    | 帧 | 何时 |
    |---|---|
    | `data: {"content": "…"}` | **每个 token 一帧** |
    | `data: {"thread_id": …, "answer": …, "intent": …, "memory_space": …}` | 收尾**一帧汇总** |
    | `data: [DONE]` | 结束哨兵 |

    ## 两条**必须知道**的现状（别当 bug 去"修"）

    1. **CALC / DATE 两个意图【一个字都流不出来】。** 它们的答案来自**工具返回值**
       （`calculator` / `date_today`），不是 LLM 输出 ⇒ 那两条分支上，用户会一直等到
       **最后一帧汇总**才看到 `answer`。别为了"看起来也在流"把 `calc_execute` 的
       **表达式提取过程**放出去 —— 那是中间产物，不是答案。
       节点对照表（6 个调 LLM 的节点里只有 4 个该流）
       `app/agent/specs/agent_graph_advanced_learning.md` 的 节
    2. **`supervisor` 的路由词（`SEARCH`/`CALCULATOR`/…）不会出现** —— 它**也是**调 LLM 的
       节点，会**真的进到流里**，靠 `STREAMABLE_NODES` 白名单挡掉（不是"它不产生块"）。

    ## 三条接线与 `/agent/advanced_chat` 保持一致（别只做一半）

    1. **B8 会话级上限 + B11 全站日级熔断** —— 两条都要过，且必须在**进生成器之前**
       （否则触顶会变成"HTTP 200 + 流到一半断掉"，调用方看不出是被限额拒了）；
    2. `sess = session_key(user_name, thread_id)` —— 图收到的键**拼身份**，响应仍回显**原值**；
    3. **汇总取自图的最终状态**（`aget_state`），不是"把流过的块攒起来"。
    """
    # B8 · 会话级 token 上限
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）—— 与上一段**并列、都要过**
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段：**checkpoint 键**拼身份（不是裸 `thread_id`）；响应里回显的仍是原值。
    sess = session_key(user_name, thread_id)
    ENDPOINT = "agent_advanced_chat_stream"

    async def _complete(collected):
        """收尾尾巴：**汇总帧 + `[DONE]`**（状态只从 `aget_state` 取 —— 见 docstring 第 3 条）。"""
        state = await advanced_agent.aget_state({"configurable": {"thread_id": sess}})
        values = state.values or {}

        # 2026-10-05（批 7）：图内软返回的**出口形状** —— 同
        #    `/agent/langgraph_chat/stream`（本图有 **6 个**软返回出口，这里一处收口）。
        why = values.get("budget_intercept")
        if why:
            msg = agent_budget_intercept_message(why)
            yield sse_frame({"error": msg}, ensure_ascii=False)
            yield DONE_FRAME
            persist_turn(user_name, question, msg, thread_id=thread_id, status="error")
            return

        yield sse_frame({
            "thread_id": thread_id,
            "answer": values.get("final_output", "处理完成"),
            "intent": values.get("intent", "unknown"),
            "memory_space": memory_space,
            "requested_by": user_name,
        }, ensure_ascii=False)
        yield DONE_FRAME
        #  · 留痕（`status="done"`）。答案取 `values.get("final_output") or ""` ——
        #    与**上面那一帧**的占位串 `"处理完成"` **有意不同**：占位串是给前端看的兜底，
        #    而把一句假的"处理完成"存进历史，下一轮 prompt 会把它当成**真的回答内容**。
        #    本链**没有** `status` 口径（不走 `summarize_agent_result`）⇒ 无审批点可停，不设 gate。
        persist_turn(user_name, question, values.get("final_output") or "", thread_id=thread_id, status="done")

    return sse_response(sse_stream(
        lambda: advanced_agent.astream(
            {
                "messages": [HumanMessage(content=question)],
                "user_name": user_name,
                "memory_space": memory_space,
                # 2026-10-04：同 `/agent/advanced_chat` —— 传**原值**，不是 `sess`。
                "thread_id": thread_id,
            },
            config={"configurable": {"thread_id": sess}},
            stream_mode="messages",
            # **`subgraphs=True` 不可省**（实测，链 A 独有）：
            #    这张图里 5 个部门**全是子图**。不开它 ⇒ 要么只拿到子图节点的**返回值**
            #    （1 块整段 · **外层**名 `search_dept`），要么**一块都没有**
            #    ⇒ 前端看到的是"半天没反应，然后整段蹦出来" = **假流式**。
            #    开了它，`item` 的形状变成 `(namespace, (chunk, meta))` ——
            #       `graph_message_text` 已归一化，端点别再解一次。
            subgraphs=True,
        ),
        endpoint=ENDPOINT,
        # 白名单来自**图模块**（`agent_graph_advanced_learning.STREAMABLE_NODES`）——
        #    端点**一个字面量都不抄**（见文件头 import 处的理由）。
        extract=lambda item: graph_message_text(
            item, nodes=agent_graph_advanced_learning.STREAMABLE_NODES),
        on_complete=_complete,
        #：取消 / 异常两条出口的留痕（同步 · 排在 `await aclose` 之前）。
        on_incomplete=lambda collected, status: persist_turn(
            user_name, question, "".join(collected), thread_id=thread_id, status=status,
        ),
    ))

# ==================== Plan-and-Execute:AgentGraph 接口 ====================


class _ThreadTokenBridge:
    """链 D 专用：把「**同步线程**里产生的 token」搬进**事件循环**（2026-10-04）。

    **为什么需要它**：`plan_task` 是**同步**函数、跑在 `asyncio.to_thread` 里
       （`/agent/plan_execute` 一直如此 —— 直接在 async 端点里调会**阻塞整个事件循环**）。
       ⇒ token 是在**别的线程**上产生的，而 `asyncio.Queue` **不是线程安全的**，
         直接往里放会偶发丢数据/乱序 ⇒ 走 `loop.call_soon_threadsafe(...)`。

    **本类【不】放进 `app/routing/sse.py`** —— 共享层（按设计）**不引入线程依赖**，
       它只认「一个有 `__anext__` / `aclose` 的对象」（见 `app/routing/sse.py` 的 段）。

    **`aclose` 必须非阻塞 + 协作式**：
       `push` 只置一个"别发了"的标志。**绝不能 `await thread.join`** ——
       Python 的线程**杀不掉**（没有 safe thread kill），join 会把"客户端断开"
       变成"服务端一直挂着等这次规划跑完"，**恰恰是本任务要修的那个病**。
    """

    _DONE = object()

    def __init__(self, loop):
        self._loop = loop
        self._queue: "asyncio.Queue" = asyncio.Queue()
        # 一个标志管两件事（"别再发了"）：`finish` / `fail` / `aclose` 都要置它。
        self._stop_pushing = False
        # 供端点挂后台任务（取消时一并取消它，免得留个孤儿协程）
        self.task = None

    # ---------- 工作线程侧（别在这里 await 任何东西） ----------
    def push(self, token: str) -> None:
        if self._stop_pushing:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, token)

    def finish(self) -> None:
        """正常结束：投递哨兵，让 `async for` 收尾（⇒ 骨架接着跑 `on_complete`）。"""
        self._stop_pushing = True
        self._loop.call_soon_threadsafe(self._queue.put_nowait, self._DONE)

    def fail(self, exc: BaseException) -> None:
        """出错：把异常投递过去，让 `async for` 在**事件循环侧**抛出 ⇒ 走骨架的错误路径。"""
        self._stop_pushing = True
        self._loop.call_soon_threadsafe(self._queue.put_nowait, exc)

    # ---------- 事件循环侧 ----------
    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is self._DONE:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        """客户端断开时由骨架调用（`app/routing/sse.py` 的 finally，已用 `shield` 护住）。"""
        self._stop_pushing = True
        # 排空：把还在队列里的东西丢掉，别让等着的一方再等
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(self._DONE)
        # 取消后台任务（**不是**等它结束）：规划线程本身杀不掉，但我们不该留一个
        #    没人再读它产出的协程挂着。
        if self.task is not None and not self.task.done():
            self.task.cancel()


@router.post("/agent/plan_execute", response_model=AgentPlanExecuteResponse)
async def agent_plan_execute(
    goal: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),   # B8 补：本端点原先**没有** thread_id
    user_name: str = Depends(get_current_user_hybrid),
):
    """完整的 Plan-and-Execute 流程"""
    # B8 · 会话级 token 上限—— 触顶直接拒绝
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）。与上一段**并列、都要过**：
    # B8 管"这个会话花了多少"，这段管"全站今天花了多少"。
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 1. 规划：将用户目标分解为步骤清单
    # 2026-09-20 修：此处原是一个**字符串字面量**（`"""任务规划接口：…"""`）——
    #    函数在上一行**已经有 docstring** ⇒ 它是**空操作**；
    #    且它描述的是「规划」这一**子步骤**，不是整个函数的职责。
    #    ⇒ 改成**普通注释**（保留信息、去掉误导）。
    # 2026-09-21 改（§十四 · ③-a/③-b），两件事一起：
    #
    #  ① **丢到线程里跑** —— `plan_task` / `execute_plan` 是**同步**的，直接在 async 端点里调
    #     会**阻塞事件循环**（一个慢请求卡住**整个 API**）。
    #     以前执行是「LLM 模拟」所以很快；**N15 真调工具之后会真发网络请求、真跑代码**，
    #     阻塞的代价从"理论问题"变成"现实问题"。
    #     同型先例：`/agent/fetch_webpage` 那两个端点也是因为这个才用 `asyncio.to_thread`。
    #
    #  ② **接住 `BudgetExceededError`** —— `plan_execute` 现在**查预算并记账**（③-b）。
    #     预算耗尽要报 **QUOTA_EXCEEDED**，而不是漏成 500。
    #     用 `AppException` 是**路由层**抛的，能被全局处理器接住
    #     （中间件里抛的接不住 —— 见 `CLAUDE.md` 的那条警告；这里是路由层，安全）。
    try:
        plan = await asyncio.to_thread(plan_task, goal, user_name)
        execution_result = await asyncio.to_thread(execute_plan, plan, goal, user_name)
    except BudgetExceededError as e:
        raise AppException(
            ErrorCode.QUOTA_EXCEEDED,
            f"今日 Token 预算已用完，无法执行本任务。{e}",
        )

    return {
        "goal": goal,
        "plan": plan,
        "execution_result": execution_result,
        "requested_by": user_name,
    }


@router.post("/agent/plan_execute/stream")
async def agent_plan_execute_stream(
    goal: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    user_name: str = Depends(get_current_user_hybrid),
):
    """`/agent/plan_execute` 的**流式**版本。SSE 逐块返回。

    ## 帧格式（与另外三条 agent 流式链**同一套**）

    | 帧 | 何时 |
    |---|---|
    | `data: {"content": "…"}` | **规划段**逐块 |
    | `data: {"goal": …, "thread_id": …, "plan": […], "execution_result": "…"}` | 收尾**一帧汇总** |
    | `data: [DONE]` | 结束哨兵 |

    ## 两条**必须知道**的现状（都摘自作者 2026-10-04 的裁定）

    1. **流内文本是【正在生成的 JSON 片段】，不是人读终稿。**
       `plan_task` 的提示词明确要求严格 JSON 输出（`:216-222`），下游还要 `json.loads`。
       ⇒ 前端的正确用法 = 当**"规划中"指示器**；**终稿只看最后一帧汇总**。
       **不许把流到的 JSON 直接渲染成计划**（模型可能吐不完整/非法 JSON）。
    2. **只有「规划段」在流** —— `execute_plan`（执行段）**不流**。
       ⇒ 规划段之后是**一长段静默**（N 步 × 每步 2 次 LLM），然后才是末帧。
       **别让读者以为执行段也在流**。

    ## 两条与 `/agent/plan_execute` 不同（**是流式的固有代价，不是疏漏**）

    * **预算耗尽从 4xx 变成 error 帧**：非流式那条会把 `BudgetExceededError` 转成
      `QUOTA_EXCEEDED`（4xx）。这里响应头**已经发出去了**（HTTP 200 + `text/event-stream`），
      改不了状态码 ⇒ 只能发一帧 `{"error": …}`。**这正是 B8/B11 两道闸必须在
      【进生成器之前】跑的原因** —— 它们能拦的那部分仍然是"根本没开始流"的干净 4xx。
    * **取消时规划线程停不下来**：Python 线程杀不掉 ⇒ 只能"不再发"。见 `_ThreadTokenBridge`
      的 docstring（不许 `await thread.join`）。
    """
    # B8 · 会话级 token 上限—— 这两道闸能拦的，是"根本没开始流"的那部分
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    ENDPOINT = "agent_plan_execute_stream"
    # 本端点**不用** `session_key`：`plan_execute` **没有图、没有 checkpoint** ——
    #    `thread_id` 只是回显给调用方看的。别为了"跟另外三条统一"而凭空拼一个键。
    bridge = _ThreadTokenBridge(asyncio.get_running_loop())
    result_holder: dict = {}

    async def _run():
        """跑「规划 → 执行」，把规划段的 token 通过 `bridge` 送出去。

        **`bridge.finish` 放在【两段都跑完之后】**（不是规划一跑完就 finish）——
           那样骨架会在执行段还在跑时就收尾并发末帧，而 `result_holder` 里**还没有**
           `execution_result` ⇒ 汇总帧里是空的。现在这样：流一直开着，执行段**静默**，
           末帧**一定**带着完整结果（与上面 docstring 第 2 条一致）。
        """
        try:
            plan = await asyncio.to_thread(plan_task, goal, user_name,
                                           on_token=bridge.push)
            execution_result = await asyncio.to_thread(execute_plan, plan, goal, user_name)
        except asyncio.CancelledError:
            # 客户端断开 ⇒ 骨架已经把 bridge 关了。别把"取消"当成"上游出错"塞进队列。
            raise
        except BaseException as exc:        # noqa: BLE001 —— 任何失败都要让等待的一方醒过来
            bridge.fail(exc)
        else:
            result_holder["plan"] = plan
            result_holder["execution_result"] = execution_result
            bridge.finish()

    def _open_upstream():
        """在**事件循环里**被骨架调用（`sse_stream` 的 `open_upstream`）⇒ 起任务合法。

        必须**留下任务引用**（挂在 `bridge.task` 上）——
           不然任务可能被 GC 掉，或者断开后没人取消它。
        """
        bridge.task = asyncio.create_task(_run())
        return bridge

    async def _complete(collected):
        yield sse_frame({
            "goal": goal,
            "thread_id": thread_id,
            "plan": result_holder.get("plan"),
            "execution_result": result_holder.get("execution_result", ""),
            "requested_by": user_name,
        }, ensure_ascii=False)
        yield DONE_FRAME
        #  · 留痕（`status="done"`）。答案取**执行结果**，不是流出去的规划段 JSON 片段
        #    （那半截 `{"step"` 本就不是人读终稿，见本函数上方）。
        #    与上面那一帧**同一个键**（`result_holder["execution_result"]`）⇒ 帧与历史不会对不上。
        persist_turn(user_name, goal, result_holder.get("execution_result", ""), thread_id=thread_id, status="done")

    return sse_response(sse_stream(
        _open_upstream,
        endpoint=ENDPOINT,
        # `extract=None`：桥吐出来的**就是文本**（不是图的消息块）⇒ 原样发。
        #    别给它套 `graph_message_text`（那要 `(chunk, meta)`，会 ValueError）。
        on_complete=_complete,
        #：取消 / 异常两条出口的留痕。**提问变量是 `goal`**（本端点没有 `question`）。
        #    **已知毛刺（登记，本轮不修）**：中途取消时存下去的是**半截 JSON**（`{"step"` 这种）
        #       —— 它的流本就不是人读终稿。**照实存**，不许为了好看去 `json.loads` 那半截（可能非法）。
        on_incomplete=lambda collected, status: persist_turn(
            user_name, goal, "".join(collected), thread_id=thread_id, status=status,
        ),
    ))


# ==================== 测试类 ====================
# ==================== checkpointer 测试 ====================


@router.post("/agent/memory_chat")
async def memory_chat(
    question: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    user_name: str = Depends(get_current_user_hybrid),
):
    """带持久化记忆的 Agent 对话接口。

    **2026-10-03（丙段）起带人工审批**：若返回 `status="pending_approval"`，
       说明**工具还没执行**，要带同一个 `thread_id` 走 `/agent/approve` 继续。
       改动前本端点**没有审批门** —— 同一个仓里，`/agent/langgraph_chat` 停下等人批、
       而这里**直接执行**（§遗留·2）。
    """
    # B8 · 会话级 token 上限
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）。与上一段**并列、都要过**：
    # B8 管"这个会话花了多少"，这段管"全站今天花了多少"。
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段断言点：图收到的键 = 拼过身份的（不是裸 `thread_id`）
    # 本端点就是  丙段点名的那个 —— 两个人用默认 `thread_id` 曾共用一个桶。
    sess = session_key(user_name, thread_id)
    config = {"configurable": {"thread_id": sess}}
    result = checkpointer_agent.invoke(
        # 2026-10-04：身份必须进 state —— 本图此前**连记账都没有**
        #    （判据 `hasattr(response,"usage")` 恒假，见 `agent_checkpointer.agent_decide`）。
        #    `thread_id` 传**原值**，不是 `config` 里的 `sess`（那是 checkpoint 的键）。
        {"messages": [HumanMessage(content=question)],
         "user_name": user_name, "thread_id": thread_id},
        config=config
    )
    # 2026-10-05（批 7）：图内软返回的**出口形状**（同 `/agent/langgraph_chat` 那段，
    #    含"必须排在 `summarize_agent_result` 之前"的理由）。
    if result.get("budget_intercept"):
        raise AppException(ErrorCode.QUOTA_EXCEEDED,
                           agent_budget_intercept_message(result["budget_intercept"]))

    # 丙段：本端点现在**会停在审批点**（`checkpointer_agent` 的 `interrupt_before=["approval"]`）。
    #    ⇒ 状态判定**复用 `summarize_agent_result`**，别再在这里另写一套
    #      （它已经是"停没停在审批点"的**唯一**口径，见其 docstring）。
    summary = summarize_agent_result(result)

    # B5 · 待接管队列 —— 与 `/agent/langgraph_chat` 同款：卡住 ⇒ 登记；没卡住 ⇒ 清掉上一次的。
    # `graph="checkpointer_agent"` **必须写**：`/agent/approve` 靠它决定续跑哪张图
    #    （写上之前，approve 只会去问 `agent_graph` ⇒ 这个会话**永远放行不了**）。
    if summary.get("status") == "pending_approval":
        register(sess, user_name, summary.get("pending_tool_calls") or [],
                 raw_thread_id=thread_id, graph="checkpointer_agent")
    else:
        resolve(sess)

    return {
        "question": question,
        "thread_id": thread_id,
        "requested_by": user_name,
        **summary,
    }


@router.post("/agent/memory_chat/stream")
async def memory_chat_stream(
    question: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    user_name: str = Depends(get_current_user_hybrid),
):
    """`/agent/memory_chat` 的**流式**版本。SSE 逐 token 返回。

    ## 帧格式（与另外三条 agent 流式链**同一套**）

    | 帧 | 何时 |
    |---|---|
    | `data: {"content": "…"}` | **每个 token 一帧** |
    | `data: {"thread_id": …, "status": …, "answer": …, "pending_tool_calls": […]}` | 收尾**一帧汇总** |
    | `data: [DONE]` | 结束哨兵 |

    那一帧**汇总**不是可有可无的：`status="pending_approval"` 是**唯一**告诉调用方
       "工具还没执行、要带同一个 `thread_id` 去 `/agent/approve`"的地方。
       **砍掉它 = 前端只会看到一个戛然而止的半截答案**，而 HTTP 返回 200。

    ## 三条接线与 `/agent/memory_chat` 保持一致（别只做一半）

    1. **B8 会话级上限 + B11 全站日级熔断**（在**进生成器之前**）；
    2. **B5 待接管队列** —— 停在审批点时要 `register`，否则 `/agent/pending` 里**找不到它**
       （`MemorySaver` 没有"列出全部 thread"的 API，这个登记是**唯一**的入口）；
       `graph="checkpointer_agent"` **必须写** —— `/agent/approve` 靠它决定续跑哪张图。
    3. **`summarize_agent_result`** —— 状态口径只有它一处，别在这里另写一套判断。
    """
    # B8 · 会话级 token 上限
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）—— 与上一段**并列、都要过**
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段：**checkpoint / 待接管队列**用拼过身份的键；响应里回显的仍是原 `thread_id`。
    sess = session_key(user_name, thread_id)
    ENDPOINT = "agent_memory_chat_stream"

    async def _complete(collected):
        """收尾尾巴：**登记/清队列 + 汇总帧 + `[DONE]`**（状态只从 `aget_state` 取）。"""
        state = await checkpointer_agent.aget_state({"configurable": {"thread_id": sess}})

        # 2026-10-05（批 7）：图内软返回的**出口形状** —— 同
        #    `/agent/langgraph_chat/stream`（含"不许照旧发汇总帧"与"排在 `summarize` 之前"
        #    两条理由），只是换了一张图的 `aget_state`。
        why = (state.values or {}).get("budget_intercept")
        if why:
            msg = agent_budget_intercept_message(why)
            yield sse_frame({"error": msg}, ensure_ascii=False)
            yield DONE_FRAME
            persist_turn(user_name, question, msg, thread_id=thread_id, status="error")
            return

        summary = summarize_agent_result(state.values or {})
        # B5 · 与 `/agent/memory_chat` 同款：`else` 那支不是可省的 ——
        # 本轮没卡住 ⇒ 清掉上一次的登记，否则同一个 thread 卡过一次就**永远留在队列里变成假待办**。
        if summary.get("status") == "pending_approval":
            register(sess, user_name, summary.get("pending_tool_calls") or [],
                     raw_thread_id=thread_id, graph="checkpointer_agent")
        else:
            resolve(sess)
        yield sse_frame({"thread_id": thread_id, "requested_by": user_name, **summary}, ensure_ascii=False)
        yield DONE_FRAME
        #  · 留痕（`status="done"`）。**只写 `answered`** —— 理由与
        #    `/agent/langgraph_chat/stream` 那条**逐字相同**：审批点的 `answer` 是那半句**非空**的
        #    模型输出，不 gate 会被写成 `done`（假信号）。停审批点**登记为边界、本轮不实现**。
        #    **别动上面 `register`/`resolve` 的顺序**（B5 待接管队列）。
        if summary.get("status") == "answered":
            persist_turn(user_name, question, summary.get("answer") or "", thread_id=thread_id, status="done")

    return sse_response(sse_stream(
        lambda: checkpointer_agent.astream(
            # 2026-10-04：同 `/agent/memory_chat` —— 身份必须进 state。
            {"messages": [HumanMessage(content=question)],
             "user_name": user_name, "thread_id": thread_id},
            config={"configurable": {"thread_id": sess}},
            stream_mode="messages",
        ),
        endpoint=ENDPOINT,
        # 白名单来自**图模块**；这里**不开** `subgraphs` —— 本图**没有子图**。
        extract=lambda item: graph_message_text(
            item, nodes=agent_checkpointer.STREAMABLE_NODES),
        on_complete=_complete,
        #：取消 / 异常两条出口的留痕（同步 · 排在 `await aclose` 之前）。
        on_incomplete=lambda collected, status: persist_turn(
            user_name, question, "".join(collected), thread_id=thread_id, status=status,
        ),
    ))

# ==================== Men0 添加记忆管理接口 测试 ====================

# 搜索 新增改动，灵活使用user_id进行用户不同功能的记忆空间隔离。
@router.post("/agent/memory/add", response_model=AgentMemoryAddResponse)
async def add_memory(
    content: str,
    memory_space: str = "default",  # 新增：记忆空间名称
    user_name: str = Depends(get_current_user_hybrid),
):
    # 组合出唯一的 user_id
    user_id = f"{user_name}:{memory_space}"
    add_user_memory(user_id, content)
    return {
        "status": "added",
        "content": content,
        "memory_space": memory_space,
    }
# 搜索 新增改动，灵活使用user_id进行用户不同功能的记忆空间隔离。
@router.get("/agent/memory/search", response_model=AgentMemorySearchResponse)
async def search_memory(
    query: str,
    memory_space: str = "default",  # 新增：记忆空间名称
    user_name: str = Depends(get_current_user_hybrid),
):
    """搜索用户长期记忆"""
    # 组合出唯一的 user_id
    user_id = f"{user_name}:{memory_space}"
    memories = search_user_memory(user_id, query)
    return {
        "query": query,
        "memories": memories,
        "memory_space": memory_space,
    }


# ==================== 单独的浏览器工具测试接口 ====================
#
# 2026-09-21 整块注释（N13 · 作者裁「挂起 + 直接注释掉 + 标『# 可扩展能力』」）
#
#   **# 可扩展能力 —— 装上 chromium 后取消下面的注释即可启用。**
#
#   为什么挂起（两条都实测过，缺一不可）：
#     ① 本仓**任何部署方式都没装 chromium**（`app/Dockerfile` / `docker-compose.yml`
#        都没有 `playwright install`）⇒ 不只是"本机的问题"
#     ② 本机**缓存里的 chromium 是旧 build（1228，556 MB）**，而 playwright 1.62 要 build **1234**
#        ⇒ 版本不匹配，**装了旧的也照样跑不了**
#   实测报错原文：
#     `BrowserType.launch: Executable doesn't exist at .../chromium_headless_shell-1234/...`
#
#   为什么**不是**留一个会 500 的端点：那正是本仓反复在防的
#     「**看起来能用、其实不能用**」。宁可不暴露。
#   代价：`ROUTES` 由 **14 → 12**（在 PR 里显式声明过）。
#
#   重新启用时**必须一起做**（否则会踩回 2026-09-20 修过的那个坑）：
#     · 取消 `app/tools/mcp_server.py` 里 `TOOLS` 的两行 + `browser_tools` 的 import
#     · 取消 `app/tools/tool_health.py` 里 `TEST_ARGS_MAP` 的两项
#     · 取消 `app/routing/api_v1_agent.py` 顶部的 `from browser_tools import ...`
#     · 把 `app/tests/test_agent_repairs.py` 里那两条 skip 掉的用例恢复
#   保留下来的知识（**别丢**）：这两个端点**必须**用 `asyncio.to_thread` 跑 ——
#     `browser_tools` 是 Playwright **同步** API，直接在 async 端点里调会报
#     `It looks like you are using Playwright Sync API inside the asyncio loop.`（实测）。
#     对应用例现在 skip 着，重新启用时取消 skip。
#
# @router.post("/agent/fetch_webpage")
# async def agent_fetch_webpage(
#     url: str,
#     user_name: str = Depends(get_current_user_hybrid),
# ):
#     """使用Playwright获取网页文本内容
#
#     2026-09-20 修:必须丢到**线程**里跑 —— `browser_tools` 用的是 Playwright
#        **同步** API，而本端点是 async ⇒ 直接在事件循环所在线程里调会报
#        `playwright._impl._errors.Error: It looks like you are using Playwright
#        Sync API inside the asyncio loop.`（实测）
#        不能把 `browser_tools` 改成 async —— 它同时被 **MCP server 的同步路径**
#           调用（`create_mcp_tool_handler` 里是同步 `tool_func.invoke`）。
#        回归测试:`app/tests/test_agent_repairs.py::test_fetch_webpage_is_not_invoked_on_the_event_loop`
#     """
#     result = await asyncio.to_thread(fetch_webpage.invoke, {"url": url})
#     return {"url": url, "content": result, "requested_by": user_name}
#
# # ==================== 新增“网页截图”工具测试接口 ====================
# # 添加一个“网页截图”工具（使用page.screenshot()），让Agent能把网页保存为图片。
#
# @router.post("/agent/screenshot_webpage")
# async def agent_screenshot_webpage(
#     url: str,
#     user_name: str = Depends(get_current_user_hybrid),
# ):
#     """使用Playwright截取网页并保存为图片
#
#     2026-09-20 修:同上（`fetch_webpage` 那条的孪生兄弟）——
#        同步 Playwright 必须丢到线程里跑。**两个端点一起修**，防止只改一个。
#        回归测试:`app/tests/test_agent_repairs.py::test_screenshot_webpage_is_not_invoked_on_the_event_loop`
#     """
#     result = await asyncio.to_thread(screenshot_webpage.invoke, {"url": url})
#     return {"url": url, "result": result, "requested_by": user_name}

# ==================== 新增 代码执行器 工具 测试接口 ====================


@router.post("/agent/execute_code")
async def agent_execute_code(
    code: str,
    user_name: str = Depends(get_current_user_hybrid),
):
    """在安全沙箱中执行Python代码"""
    result = execute_python.invoke({"code": code})
    return {"code": code, "result": result, "requested_by": user_name}

# ==================== Agent 工具 健康检查 测试接口 ====================


@router.get("/agent/tool_health", response_model=AgentToolHealthResponse)
async def agent_tool_health(
    user_name: str = Depends(get_current_user_hybrid),
):
    """查看所有工具的健康状态"""
    return {"tools": _tool_health, "requested_by": user_name}

@router.post("/agent/tool_health/refresh")
async def agent_tool_health_refresh(
    user_name: str = Depends(get_current_user_hybrid),
):
    """手动刷新工具健康检查"""
    await run_health_check()
    return {"tools": _tool_health, "requested_by": user_name}


# ==================== Agent 工具 版本查询 接口 ====================
# 查看所有工具及其版本号

@router.get("/agent/tool_versions", response_model=AgentToolVersionsResponse)
async def agent_tool_versions(
    user_name: str = Depends(get_current_user_hybrid),
):
    """查看所有工具及其版本号"""
    versions = {
        name: defn["version"]
        for name, defn in TOOLS_DEFINITION.items()
    }
    return {"tool_versions": versions, "requested_by": user_name}

# ==================== Agent 工具 健康检查与工具列表的联动查询 接口 ====================

@router.get("/agent/available_tools", response_model=AgentToolsResponse)
async def agent_available_tools(
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取当前健康且可用的工具列表（已自动过滤不健康工具）"""
    healthy_tools = []
    unhealthy_tools = []
    
    for tool_def in TOOLS_DEFINITION.values():
        tool_name = tool_def["name"]
        health = get_tool_health(tool_name)
        
        if health == UNHEALTHY:
            unhealthy_tools.append(tool_name)
        else:
            healthy_tools.append({
                "name": tool_name,
                "description": tool_def["description"],
                "version": tool_def.get("version", "unknown"),
            })
    
    return {
        "healthy_tools": healthy_tools,
        "unhealthy_tools": unhealthy_tools,
        "total": len(healthy_tools) + len(unhealthy_tools),
        "requested_by": user_name,
    }

# ==== 升级版 Agent MCP Client 新增 Token预算检查依赖和查询 接口 ====================


# 预算检查依赖（注入到需要控制成本的接口中）
async def check_budget(
    user_name: str = Depends(get_current_user_hybrid),
):
    """检查当前用户的Token预算是否充足"""
    if not check_token_budget(user_name):
        info = get_token_budget_info(user_name)
        raise AppException(
            ErrorCode.QUOTA_EXCEEDED,
            f"今日Token预算已用完。已使用 {info['used_today']} / {info['daily_budget']} tokens。"
        )
    return user_name

# 新增：Token预算查询接口
@router.get("/agent/token/budget", response_model=TokenBudgetResponse)
async def agent_token_budget(
    user_name: str = Depends(get_current_user_hybrid),
):
    """查看当前用户的 Token 预算信息 + **全站**日级额度。

    2026-10-03（`①b`）**加了 `global_*` 三个字段** ——
    在那之前，/ 的全站日级额度（超了**所有人**吃 429）**没有任何出口**：
    `get_global_daily_token_usage` 全仓只被 `breaker` 调过。
    ⇒ 看不见它逼近，只能等 429。

    **两套口径，别读混**：
      · `daily_budget` / `used_today` / `remaining` = **本用户**（`R1.3`，`QuotaMiddleware` 用它）
      · `global_*` = **全站合计**（`R1.4`，/ 用它）
      两者不是同一个上限的两半。
    """
    info = get_token_budget_info(user_name)
    global_used = get_global_daily_token_usage()
    # 2026-10-11（批 4）：把**今日**的用量按用途拆成 LLM / Embedding / 其它 ——
    #    在这之前页头只有一个总数，两者混着看不出所以然。
    #    与上面 `used_today` **同一个窗口**（`created_at >= CURRENT_DATE`）⇒ 和应当对得上。
    #    查不到就回 `{}` ⇒ 那三格是 0.0；**页面据此画 `—` 还是 `0` 由页面决定**（见 `cred.js`）。
    split = split_daily_tokens(get_daily_tokens_by_purpose(user_name))
    # 2026-10-11（批 6 · 两个额度池 ·）：**按池**的上限与剩余。
    #    「已用」用的是上面 `split` 那两个数（**按 purpose** 分），
    #       而**拦截是按【路径】**的 —— 两者不总是一致，那是有意的近似（见 `billing/quota_pools.py` 模块头）。
    #    旧的键**一个不动**（`daily_budget` / `used_today` / `remaining` 现在 = **总计**，
    #       它本来就是"总"，语义没变）；这里是**纯新增**。
    budgets = get_pool_budget_info(user_name)
    llm, emb = budgets["llm"], budgets["embedding"]
    return {
        "user_name": user_name,
        **info,
        **split,
        # 「已用」= 上面 `split` 里的 `llm_tokens_today` / `embedding_tokens_today`（不重复给）
        "llm_daily_limit": llm["limit"],
        "llm_remaining_today": round(llm["remaining"], 2),
        "embedding_daily_limit": emb["limit"],
        "embedding_remaining_today": round(emb["remaining"], 2),
        "global_daily_limit": GLOBAL_DAILY_TOKEN_LIMIT,
        "global_used_today": round(global_used, 2),
        # `get_global_daily_token_usage` 查库失败是 fail-open（返回 0.0）
        #    ⇒ 那种情况下这里会显示"全站还剩满额"。与同族取舍一致，不加特判。
        "global_remaining": round(max(0, GLOBAL_DAILY_TOKEN_LIMIT - global_used), 2),
        "requested_by": user_name,
    }

# ==================== 升级版 Agent MCP Client 工具 测试接口 ====================

@router.post("/agent/mcp_chat", response_model=AgentMcpChatResponse)
async def mcp_agent_chat(
    question: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    memory_space: str = "default",
    # 在需要控制成本的接口中使用
    # 新增 Token预算检查依赖和查询
    # user_name: str = Depends(get_current_user_hybrid),
    user_name: str = Depends(check_budget),  # 改为使用预算检查
):
    """使用 MCP Client 的 Agent 对话接口（请求级隔离）

    2026-09-20 修：这段 docstring 原先**躺在 `start_trace` 之后**（函数体第二句），
       是**空操作** —— 函数本身**没有 docstring**。已上移到签名正下方。
    """
    # 记录工具 开始追踪（带身份 —— 追踪轴也是按人分的，见 `tool_visualizer` 模块头）
    start_trace(user_name, thread_id, question)

    # B8 · 会话级 token 上限
    # 放在 `start_trace` **之后**：超限被拒时，追踪里仍留得下这次尝试的痕迹。
    #    本端点原有的 `check_budget` 依赖判的是【用户**日**预算】，与会话级是**两个东西**，并存。
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）。与上一段**并列、都要过**：
    # B8 管"这个会话花了多少"，这段管"全站今天花了多少"。
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段断言点：**checkpoint 键**拼身份（不是裸 `thread_id`）。
    # 但下面 state 里的 `thread_id` **保持原值** —— 它喂的是【追踪/花费】那条轴
    #    （`agent_graph_advanced.py:246/268` → `record_tool_*`），
    #    那条轴的**读**端点 `/agent/trace/{thread_id}`（`:1133`）用的也是原值。
    #    两条轴别混：混了就得连追踪的读写一起改（那是另一件事，不是丙段）。
    #    **那条轴本身有跨用户可见的洞** ⇒ 已立账  的。
    sess = session_key(user_name, thread_id)

    result = await mcp_agent.ainvoke(
        {
            "messages": [HumanMessage(content=question)],
            "user_name": user_name,
            "memory_space": memory_space,
            "thread_id": thread_id,     # 注入 thread_id（追踪轴，保持原值）
        },
        config={"configurable": {"thread_id": sess}}
    )

    # 2026-10-05：预算**硬**拦截 —— 图里被拦下的工具会在 state 上留
    #    `budget_intercept`（`agent_graph_advanced.tool_execute`）⇒ 在这里变成 **429**。
    #    改前是**软**拦截：图正常跑完、HTTP 200，那句"预算拦截"只有 LLM 看得见
    #       ⇒ **调用方在响应里看不出"被拒了"**。
    #    别改成去认末条 `ToolMessage` 的中文文案 —— 那是**子串判据**，
    #       `plan_execute` 的  刚把同型写法清掉（改一个字就静默失效 / 工具正文碰巧含那四个字就误判）。
    #    `finish_trace` 在这条路上**不写**（追踪里留一条"开了没结束"）——
    #       与上面 B8 / B11 两道门**一致**，是"被拒也要留下尝试痕迹"的有意为之。
    #    **2026-10-05（批 7）文案改了**：从「本次**工具调用**未执行」改成
    #       「**本轮**未继续执行」（`agent_budget_intercept_message`）。理由：本批新增的 8 处
    #       软返回**根本没有工具调用**（入口节点在调 LLM 之前就拦下了）⇒ 旧句对它们不准。
    #       **改既有判据不许静默** —— 声明与代价。
    if result.get("budget_intercept"):
        raise AppException(ErrorCode.QUOTA_EXCEEDED,
                           agent_budget_intercept_message(result["budget_intercept"]))

    final_message = result["messages"][-1]

    # 记录工具 结束追踪
    finish_trace(user_name, thread_id, final_message.content)

    # 预算提醒
    warning_info = check_budget_warning(user_name)

    return {
        "question": question,
        "answer": final_message.content,
        "thread_id": thread_id,
        "requested_by": user_name,
         "budget_warning": warning_info["message"] if warning_info["warning"] else None,
    }


@router.post("/agent/mcp_chat/stream")
async def mcp_agent_chat_stream(
    question: str,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),
    memory_space: str = "default",
    # 依赖**与 `/agent/mcp_chat` 一致**（`check_budget`，不是 `get_current_user_hybrid`）——
    #    它判的是【用户**日**预算】，是本端点原有的一道门，与会话级  **并存**（两个东西）。
    user_name: str = Depends(check_budget),
):
    """`/agent/mcp_chat` 的**流式**版本。SSE 逐 token 返回。

    ## 帧格式（与另外三条 agent 流式链**同一套**）

    | 帧 | 何时 |
    |---|---|
    | `data: {"content": "…"}` | **每个 token 一帧** |
    | `data: {"thread_id": …, "answer": …, "budget_warning": …}` | 收尾**一帧汇总** |
    | `data: [DONE]` | 结束哨兵 |

    ## 与 `/agent/mcp_chat` 保持一致的四处

    1. **`start_trace` 在最前**（在 B8 **之前**：超限被拒时，追踪里仍留得下这次尝试的痕迹）；
    2. **`check_budget` 依赖 + B8 会话级 + B11 全站日级** —— 三道门都要过；
    3. **`sess` 拼身份**，但注入 state 的 `thread_id` **保持原值** ——
       它喂的是**追踪 / 花费**那条轴（`/agent/trace/{thread_id}` 读的也是原值）。
       **两条轴别混**（混了就得连追踪的读写一起改，那是另一件事）；
    4. `finish_trace` + `check_budget_warning` 移到**收尾**（它们要在有最终答案之后才做）。
    """
    # 记录工具 开始追踪
    start_trace(user_name, thread_id, question)

    # B8 · 会话级 token 上限
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # B11 · 全站日级熔断（`①b`）
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 丙段：**checkpoint 键**拼身份（不是裸 `thread_id`）
    sess = session_key(user_name, thread_id)
    ENDPOINT = "agent_mcp_chat_stream"

    async def _complete(collected):
        """收尾尾巴：**结束追踪 + 预算提醒 + 汇总帧 + `[DONE]`**。

        `answer` 取自**图的最终状态**（最后一条消息），不是攒流过的块 ——
           与 `/agent/mcp_chat`（它取 `result["messages"][-1].content`）**同源**。
        """
        state = await mcp_agent.aget_state({"configurable": {"thread_id": sess}})
        values = state.values or {}
        messages = values.get("messages") or []
        answer = getattr(messages[-1], "content", "") if messages else ""
        # 记录工具 结束追踪
        finish_trace(user_name, thread_id, answer)

        # 2026-10-05：预算**硬**拦截 —— 同 `/agent/mcp_chat`，但那边的 429 这里发不出去：
        #    响应头**已经发出去了**（HTTP 200 + `text/event-stream`）⇒ 只能改发一帧 `{"error": …}`
        #    （与 `/agent/plan_execute/stream` 同一口径）。
        #    **不许照旧发汇总帧** —— 那帧里有 `answer`，读起来就是"这轮正常答完了"，
        #       而这正是软拦截在流式这条路上的形态（改前实测：`answer` = "预算拦截：…"）。
        why = values.get("budget_intercept")
        if why:
            # **2026-10-05（批 7）文案改了**（同 `/agent/mcp_chat`，理由见那一处）：
            #    从「本次**工具调用**未执行」改成「**本轮**未继续执行」；
            #    且**改走共享纯函数**（不再就地拼）⇒ 帧与历史**同源**，不会对不上。
            msg = agent_budget_intercept_message(why)
            yield sse_frame({"error": msg}, ensure_ascii=False)
            yield DONE_FRAME
            #：留痕排在任何 `yield` 之前的那条约束这里**不适用**（本支不会异常），
            #    但"三条出口都要留痕"照办 —— 这条算 `error`（本轮没答成）。
            persist_turn(user_name, question, msg, thread_id=thread_id, status="error")
            return

        # 预算提醒
        warning_info = check_budget_warning(user_name)
        yield sse_frame({
            "thread_id": thread_id,
            "answer": answer,
            "budget_warning": warning_info["message"] if warning_info["warning"] else None,
            "requested_by": user_name,
        }, ensure_ascii=False)
        yield DONE_FRAME
        #  · 留痕（`status="done"`）。答案与上面那一帧**同一个来源**
        #    （图的最终状态末条消息），不是攒流过的块。
        #    **别动 `finish_trace` 的顺序**（记录工具要的是"追踪先结束"）——
        #       本句排在最后，不碰它。
        persist_turn(user_name, question, answer, thread_id=thread_id, status="done")

    return sse_response(sse_stream(
        lambda: mcp_agent.astream(
            {
                "messages": [HumanMessage(content=question)],
                "user_name": user_name,
                "memory_space": memory_space,
                "thread_id": thread_id,     # 注入 thread_id（追踪轴，保持原值）
            },
            config={"configurable": {"thread_id": sess}},
            stream_mode="messages",
        ),
        endpoint=ENDPOINT,
        # 白名单来自**图模块**；本图**没有子图** ⇒ 不开 `subgraphs`。
        extract=lambda item: graph_message_text(
            item, nodes=agent_graph_advanced.STREAMABLE_NODES),
        on_complete=_complete,
        #：取消 / 异常两条出口的留痕（同步 · 排在 `await aclose` 之前）。
        on_incomplete=lambda collected, status: persist_turn(
            user_name, question, "".join(collected), thread_id=thread_id, status=status,
        ),
    ))

# ==================== 升级版 Agent MCP Client  动态获取当前可用的工具列表 接口 ====================
@router.get("/agent/mcp_tools_dynamic")
async def agent_mcp_tools_dynamic(
    user_name: str = Depends(get_current_user_hybrid),
):
    """通过 MCP Client 动态获取当前可用的工具列表"""
    tools_result = await get_mcp_tools()
    # 2026-09-20 修:`get_mcp_tools` 返回的是 **`ListToolsResult`**（列表在 `.tools`），
    #    **不是列表本身** —— 与本文件另一处（`get_llm_with_mcp_tools`）是**同一个 bug 的
    #    两个入口**，先前只修了那一个 ⇒ 本路由 `for t in tools` 必
    #    `AttributeError: 'tuple' object has no attribute 'name'` ⇒ **100% 500**；
    #    且 `len(tools)` 同样不对。
    #    回归测试:`app/tests/test_agent_repairs.py::test_mcp_tools_dynamic_handles_list_tools_result`
    _tools = tools_result.tools
    return {
        "tools": [
            {
                # 左边那个 `"inputSchema"` 是**本接口的响应键**（对外契约，不动）；
                #    右边那个是 **mcp `Tool` 的字段名**（2.x 起是 `input_schema`）。
                #    2026-10-08：改前两边同名，改后**必须不一样** —— 别顺手统一。
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema
            }
            for t in _tools
        ],
        "total": len(_tools),
        "requested_by": user_name,
    }

# ==== 升级版 Agent MCP Client 添加 Token统计 查询  接口 ====================


@router.get("/agent/token/usage")
async def agent_token_usage(
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取当前用户的 Token 使用统计"""
    summary = get_user_summary(user_name)
    return {
        "user_name": user_name,
        "summary": summary,
        "requested_by": user_name,
    }

@router.get("/agent/token/purpose")
async def agent_token_purpose(
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取按用途分类的 Token 使用统计"""
    return {
        "purpose_summary": get_purpose_summary(),
        "requested_by": user_name,
    }

@router.get("/agent/token/recent")
async def agent_token_recent(
    limit: int = 20,
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取**你自己**最近的 Token 使用记录。

    **2026-10-08：加了 `truncated`**（`frontend/README.md` §六 红线②「被截断必须说出来」）——
       改前它**取了 N 条就走了**，调用方**分不清"就这么多"与"被截了"**。
    判法同 `/agent/approvals/history`：**多要一条** —— 多要的那条**只当探针，不返回**
       （不拿 `len == limit` 猜：那在"正好 N 条、后面没有了"时会误报截断）。

    **2026-10-09：本端点此前【收了 `user_name` 却从没用它】** ——
       函数体写的是 `get_recent_usage(limit + 1)`，而那读的是 `_usage_records`
       （**进程内所有人共用的一张表**）⇒ **它返回的是所有人的记录，每条还带别人的 `user_name`**。
       端点的 docstring 一直写着"获取最近的 Token 使用记录"、**没有一个字说这是全站的**
       ⇒ 调用方**看不出它越权**（本仓最恨的那种形态）。
       ⇒ 现已把身份**传下去**（`get_recent_usage(limit + 1, user_name)`）。
       守卫 ⇒ `app/tests/test_token_recent_scope.py`（两个用户各记一笔 ⇒ 查一个只回他自己的）。
    """
    rows = get_recent_usage(limit + 1, user_name)
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    return {
        "recent_usage": rows,
        "count": len(rows),
        "truncated": truncated,
        "requested_by": user_name,
    }

@router.get("/agent/token/thread")
async def agent_token_thread(
    thread_id: str = None,
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取按线程维度的 Token 使用统计（不传 thread_id 则返回所有线程）"""
    summary = get_thread_summary(thread_id)
    return {
        "thread_id": thread_id if thread_id else "all",
        "summary": summary,
        "requested_by": user_name,
    }

# ====  Token统计 全部代码 Token花费可视化接口  接口 ====================


@router.get("/agent/cost/overview")
async def agent_cost_overview(
    user_name: str = Depends(get_current_user_hybrid),
):
    """
    花费总览：展示当前用户的 Token 消耗和费用概况。**读库 · 全时累计**。

    2026-10-03（`①b`）**改了口径**，两处都改了：

    | | 改前 | 改后 |
    |---|---|---|
    | 数据源 | `get_user_summary` / `get_purpose_summary` —— **进程内存**，重启归零 | `get_user_overview` —— **读 `token_usage_logs`** |
    | 窗口 | 本进程启动以来 | **全时累计** |
    | `by_purpose` 范围 | **全站**（那两个内存函数都不分用户） | **本人**（与 `total_*` 一致） |

    **为什么必须改**：改前它自称"最直观的『花了多少钱』查询接口"，
    但实测 admin 在库里有 **4216 tokens**、它答 `0` —— **不报错、界面照常出数**。
    实跑记录 ⇒ `app/billing/specs/token_tracker.md` 的 `①b`  段。

    **"今天花了多少"不归本接口** —— 那是 `R1.3` 口径，
       去 `/agent/token/budget`（或看板）。本接口答的是"一共"。
    """
    overview = get_user_overview(user_name)

    return {
        "user_name": user_name,
        "total_cost": overview["total_cost"],
        "total_tokens": overview["total_tokens"],
        "total_calls": overview["calls"],
        "by_purpose": overview["by_purpose"],
        "requested_by": user_name,
    }
# ====  Token统计 历史查询 接口 ====================


@router.get("/agent/token/history")
async def agent_token_history(
    days: int = 30,
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取用户最近的 Token 消耗历史趋势"""
    history = get_user_history(user_name, days)
    return {
        "user_name": user_name,
        "days": days,
        "history": history,
    }

# ====  Token统计 月度报告  接口 ====================


@router.get("/agent/cost/monthly_report")
async def agent_monthly_report(
    year: int = None,
    month: int = None,
    user_name: str = Depends(get_current_user_hybrid),
):
    """
    生成用户指定月份的 Token 花费报告。
    
    参数:
        year: 年份（可选，默认当前年）
        month: 月份（可选，默认当前月，1-12）
    """
    report = generate_monthly_report(user_name, year, month)
    report["requested_by"] = user_name
    return report

# ====  Token统计 预算检查查询接口  接口 ====================

@router.get("/agent/budget/check")
async def agent_budget_check(
    tool_name: str = None,
    purpose: str = None,
    user_name: str = Depends(get_current_user_hybrid),
):
    """检查当前用户是否有足够预算执行指定操作"""
    allowed, reason = check_budget_before_call(
        user_name=user_name,
        tool_name=tool_name,
        purpose=purpose
    )
    info = get_token_budget_info(user_name)
    return {
        "allowed": allowed,
        "reason": reason,
        "budget_info": info,
        "estimated_cost": estimate_tool_cost(tool_name) if tool_name else None,
        "requested_by": user_name,
    }

@router.get("/agent/budget/estimates")
async def agent_budget_estimates(
    user_name: str = Depends(get_current_user_hybrid),
):
    """查看所有工具的单次调用预估成本"""
    return {
        "tool_estimates": TOOL_ESTIMATED_COST,
        "purpose_estimates": PURPOSE_ESTIMATED_COST,
        "requested_by": user_name,
    }

# ====  Token统计 拦截统计查询：按用户 接口 ====================


@router.get("/agent/budget/intercepts", response_model=AgentInterceptsResponse)
async def agent_budget_intercepts(
    user_name: str = Depends(get_current_user_hybrid),
):
    """查看预算拦截统计"""
    return {
        **get_intercept_count(user_name),
        "requested_by": user_name,
    }


@router.get("/agent/approvals/history", response_model=AgentApprovalHistoryResponse)
async def agent_approval_history(
    limit: int = 50,
    offset: int = 0,
    user_name: str = Depends(get_current_user_hybrid),
):
    """裁决历史 —— **接管页下半栏就读它**，也就是硬门 D「证真」那栏的可视证据（§3.2）。

    **可见性镜像 `/agent/traces`（`:1943`）**：本人默认只看自己的，**admin 看全量**。

    与 `/agent/pending` 不同：那条队列是**进程内存**、重启即空；本表在 **PG** 里，
       **重启后照样查得到**。⇒ 重启之后「历史查得到、会话找不到」是**预期行为，不是 bug**。

    `owner` 那个 `None` 是**显式传**的（admin 那条路）—— `list_decisions` 的 `owner`
       **故意不给默认值**，就是为了让"我忘了传"**当场 `TypeError`**，而不是静默变成
       "查所有人"（口径）。
    """
    owner_filter = None if get_user_role(user_name) == UserRole.ADMIN else user_name
    events = list_decisions(owner=owner_filter, limit=limit + 1, offset=offset)

    # **`has_more` 用「多取一条」判**（`frontend/README.md` §六）——
    #    多要的那一条**不返回**，只用来回答"后面还有没有"。
    #    **不用 `count(*)`**：多一次全表计数，而且它与"这一页满没满"是两件事。
    #    **更不许让前端拿 `count == limit` 去猜** —— 那在"正好一整页、后面没有了"时
    #       会显示一个**点不动的下一页**，而且不报错。
    has_more = len(events) > limit
    if has_more:
        events = events[:limit]

    return {
        "events": events, "count": len(events), "has_more": has_more,
        "limit": limit, "offset": offset, "requested_by": user_name,
    }

# ====  Token统计 花费明细查询 接口 ====================


@router.get("/agent/cost/records", response_model=CostRecordsResponse)
async def agent_cost_records(
    days: int = 7,
    limit: int = 100,
    offset: int = 0,
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取最近N天的花费明细记录（`offset` 分页 —— 2026-10-11 · 刀 9）

    **`offset` 是 2026-10-11 加的**：改前只有 `limit`（硬截 100 条），
       页面上**只能说"被截断了"，翻不了页**。
    **`limit` 的取值必须与 `app/static/js/cost.js` 那一侧的页大小同口径** ——
       两边各写一个数就会"翻一页跳两页"（那**不报错**，只是页码对不上内容）。
    """
    try:
        from core.db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT user_name, thread_id, model, purpose, prompt_tokens, completion_tokens,
                              total_tokens, total_cost, tool_name, created_at
                       FROM cost_records
                       WHERE user_name = %s
                         AND created_at >= CURRENT_DATE - %s
                       ORDER BY created_at DESC
                       LIMIT %s OFFSET %s""",
                    # 多要一条（判"还有下一页"的探针）；`offset` 夹到非负 ——
                    #    负数 offset 在 PG 里**直接报错**（不是回落到 0），那会变成 500。
                    (user_name, days, limit + 1, max(0, offset))
                )
                rows = cur.fetchall()
                records = [
                    {
                        "user_name": r[0],
                        "thread_id": r[1],
                        "model": r[2],
                        "purpose": r[3],
                        "prompt_tokens": r[4],
                        "completion_tokens": r[5],
                        "total_tokens": r[6],
                        "total_cost": round(r[7], 4),
                        "tool_name": r[8],
                        "created_at": str(r[9])
                    }
                    for r in rows
                ]
        # 2026-10-08：`truncated` =「多要的那条真拿到了」——
        #    判法同 `/agent/approvals/history`，不拿 `len == limit` 猜
        #    （那在"正好 N 条、后面没有了"时会误报）。`frontend/README.md` §六 红线②。
        truncated = len(records) > limit
        if truncated:
            records = records[:limit]
        # 2026-10-11（刀 9）：**`truncated` 保留，不删** ——
        #   「被截断必须说出来」那半条不因为加了翻页而消失；它就是那道门要的机器可读信号。
        # 同一次里给 `has_more`：**名字不同、含义也不同** ——
        #   `has_more` =「**下一页有东西**」（翻得到）；`truncated` =「**这一页后面还有**」。
        #   这一页翻不动时两者会同时为真，别把其中一个当成另一个的别名。
        return {"records": records, "count": len(records), "truncated": truncated,
                "has_more": truncated, "offset": max(0, offset), "limit": limit,
                "requested_by": user_name}
    except Exception:
        # `as e` 已去掉 —— 异常对象交给 `logger.exception` 自己取（`sys.exc_info`），
        #    留着不用反而会被 `ruff` 的  报（本仓的静态检查门会红）。
        # 2026-10-10（CodeQL `py/stack-trace-exposure` · 真问题）：
        #    **不把异常原文回给调用方** —— 本端点**任何已认证用户都能打**
        #    （`Depends(get_current_user_hybrid)`，**不是** admin-only），
        #    而 `str(e)` 会把 **SQL 片段 / 连接串 / 文件路径**一起带出去。
        #    **本仓「失败要说出来」那条不变**（熔断/失败不许退化成白屏）——
        #    仍然**说**，只是**不再说内部细节**：细节进服务端日志，调用方拿到的是可读的失败原因。
        logger.exception("查询成本记录失败（调用方 {}）", user_name)
        return {"error": "查询成本记录失败，请稍后重试（详细信息见服务端日志）"}
    
# ====  添加轨迹追踪查询 接口 ====================


@router.get("/agent/trace/{thread_id}", response_model=AgentTraceResponse)
async def agent_trace_detail(
    thread_id: str,
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取**本人**在指定线程上的执行轨迹详情。

    **2026-10-03（N4）**：改之前**不判属主** —— 任何登录用户拿一个
       `thread_id` 就能读到别人的提问原文与工具结果。
    非属主与"真不存在"**返回同一个答复**（不给"存在但不属于你"这个 oracle）。
    """
    trace = get_trace(user_name, thread_id)
    if trace is None and get_user_role(user_name) == UserRole.ADMIN:
        # admin 例外**显式一行**（决策 2 / 决策 8-3）——
        #    不是靠"不过滤"顺带实现的。
        trace = get_trace_of_any_owner(thread_id)
    if trace is None:
        return {"error": f"未找到线程 {thread_id} 的执行轨迹"}
    return {"trace": trace, "requested_by": user_name}


@router.get("/agent/trace/{thread_id}/cost", response_model=AgentTraceCostResponse)
async def agent_trace_cost(
    thread_id: str,
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取**本人**在指定线程上的**逐笔花费明细**（成本轴）。

    **2026-10-06新增。为什么另开一条，而不是塞进
       `/agent/trace/{thread_id}`**：那一条读的是 `tool_visualizer` 的**进程内存**（追踪轴），
       这一条查的是 PG `token_usage_logs`（成本轴）。**两条轴的数据源、粒度、
       写入方全不同**，合一个响应只会让人以为它们能按步对齐 —— **对不上**（没有共同的 step id）。

    **与上面那条的第二个差别：本端点【不进 `route-auth-baseline.txt`】**
       —— 它带 `Depends(get_current_user_hybrid)`，是真有鉴权的。

    **属主过滤**：`thread_cost_breakdown` 内部走 `WHERE user_name = %s AND thread_id = %s`。
       **别图省事去调 `token_tracker.get_thread_cost`** —— 那个函数**没有用户条件**。
       （它本身不是洞：全仓唯一调用点是本人的线程预算检查。但用在这里就是越权。）

    **0 条回 `200` + 空 `items`，不是 404** —— 与 `/agent/trace/{thread_id}`、
       `/agent/approve` 一致：不给"这个 thread_id 存在但不属于你"这个 oracle
       （非属主与"真不存在"**返回同一个答复**）。
    """
    data = thread_cost_breakdown(
        user_name,
        thread_id,
        include_all=get_user_role(user_name) == UserRole.ADMIN,   # admin 例外，显式一行（照 :2122）
    )
    return {**data, "thread_id": thread_id, "requested_by": user_name}


@router.get("/agent/traces")
async def agent_trace_list(
    user_name: str = Depends(get_current_user_hybrid),
):
    """获取执行轨迹摘要列表 —— **默认只有本人的**；admin 看全量。"""
    return {
        "traces": get_all_traces(
            user_name,
            include_all=get_user_role(user_name) == UserRole.ADMIN,   # admin 例外，显式一行
        ),
        "requested_by": user_name,
    }