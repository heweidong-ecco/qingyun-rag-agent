# `app/agent/agent_graph_advanced_learning.py`

> ⚠️ **正文里那些 `:NNN` 行号停在 / 10-08 那几批** —— 之后本模块又长了几十行，
> 那些行号**整体偏小**（实测：`supervisor` 在 `:461`，正文写的是 `:413`；
> `build_advanced_agent()` 在 `:457`，正文写的是 `:409`）。
> ⛔ **别拿它们去 `sed -n`** —— 用 `grep -n "def <名字>" app/agent/agent_graph_advanced_learning.py` 现找。

> 🆕 **本 spec 建于 ** —— 此前本模块**没有 spec**（里一直有它）。
> 建它的直接起因： 剩余 4 条链要做真流式，而**「这条链里哪些节点该流、哪些绝不能流」这个判断
> 只存在于本模块内部**（⛔ 读代码推不出来 —— 见下方 ⭐ 节的表）。

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用，且是生产链** —— ✅ **记账于 补齐**（见下），⚠️ **两个分支本来就无字可流**（见下）<br>🔴 **：6 个 LLM 调用点全部接上【预算拦 + 记账】** —— 改前本文件 `record_usage` / `check_token_budget` **0 命中** ⇒ 走 `/agent/advanced_chat` 的花销在 `token_usage_logs` 里**完全看不见**。**。每个节点都是 **`.stream`/`.invoke` 之前**查 `check_token_budget(user_name, estimated_tokens=500)`、**之后**调 `record_from_response(…)`；`AgentState` 新增 `thread_id`（`user_name` 已有）—— **由端点注入**，子图与父图共用 `AgentState` ⇒ 身份自动流入子图。<br>⚠️ **`purpose` 五处取值**（`answer_generation` ×3 · `query_rewrite` ×1 · `agent_decision` ×2 —— 见「看代码会误判」表末行）<br>⚠️ **超预算的返回形状【按节点出口不同】** —— 见下方 ⚠️：`supervisor` 那处**必须给 `intent`**，⛔ 否则 `route_by_intent` 读 `state["intent"]` 当场 `KeyError`。<br>📄 裁定；📌 判据 ⇒ `app/tests/test_billing_wiring.py`（AST 精确点名**本文件 6 条** + `-k behavior` 那条断言恰好 **2 笔**）<br>🔵 **（剩余 4 条链）：4 个该流的节点改成【真流式】** —— `search_summarize`（`:143`）· `translate_execute`（`:237`）· `agent_decide`（`:279`）· `chat_node`（`:463`）都声明 `config: RunnableConfig` + 换 `.stream(…, config=config)` 逐块 `+` 聚合 ⇒ **本图**（多出的行是"为什么这 4 个流、那 2 个不流"的注释 + 白名单常量 + 每个节点的改法说明）。<br>⚠️ **本图 4 个节点【全是同步的】** ⇒ 走**同步** `.stream(config=config)`；⛔ **别照抄链 C**（`agent_graph_advanced.py` 那两个是 `async` ⇒ 用 `astream`），⛔ **也别把本图节点改成 `async def`** —— 同步的 `graph.invoke` 会当场 `TypeError: No synchronous function provided to "agent"`（实测）。<br>新增模块级 `STREAMABLE_NODES`（`:399`）；**为什么必须放模块级**（放进 `build_advanced_agent` 就是局部名 ⇒ 端点 `AttributeError`）写在 `:376-398` 的注释里。<br>🔴 **链 A 走 `astream` 时必须开 `subgraphs=True`** —— 本图 5 个子图，**不开它一个字都流不出来**（⚠️ 且 `meta["langgraph_node"]` 报的是**子图内层**名，见 ⚠️）。⚠️ **`+` 聚合在本图同样【必须】**：`tool_calls` 碎片化到达，只拼 `content` ⇒ react 子图拿不到 `tool_calls` ⇒ **工具永远不执行**，而接口一切正常。<br>📌 守卫 `app/tests/test_agent_stream_chains.py`（含 `test_real_chain_a_node_streams_one_chunk_per_token` · `test_chain_a_filters_out_supervisor_and_calc_execute`）<br>🔴 **（批 7）：6 处预算软返回全部收口**——**：每处**同时写 `budget_intercept`**；`supervisor` 的**软返回出口**由 `**cleared` 翻转成"写本轮原因"、**正常出口**（`:512`）写 `None`。<br>⚠️ **顺带堵掉"钱花在闸之前"**：`supervisor` 的记忆检索（`search_user_memory`）**下移到预算门之后**——改前被拒的那一轮照样花一次 embedding。📄 §四·`🅕`<br>🔴 **：本图【两处】记忆检索改 fail-open** —— `inject_memories_to_prompt`（`:116`）与**入口节点 `supervisor`**（`:497`）。<br>⚠️ **入口那处是这次新照出来的**（它**直调** `search_user_memory`，⛔ 不走那个函数）—— 只改函数，`/agent/advanced_chat` 在 embedding 取不到时**照旧整条 500**，而没有任何门会红。见 ⚠️<br>⚠️ **两处里的 `except AssertionError: raise` ⛔ 不是冗余** —— 探针报的是 `AssertionError`，它是 `Exception` 的子类，少了那两行会被**静默降级**。<br>📌 守卫 `app/tests/test_memory_injection_failopen.py`（**4 条**，带正控；**变异自证** 1 / 1 / 2 红） |
| **对外提供** | `build_advanced_agent`（`:457`，返回**编译好的图**，带 `MemorySaver`）<br>· 5 个 `create_*_subgraph`（`:130` / `:181` / `:214` / `:230` / `:267`）<br>· 模块级 `llm`（`:26`）· `llm_search`（`:85`）· `llm_calc`（`:86`）· `llm_date`（`:87`）· `llm_with_tools`（`:79`）<br>· 工具 `tools`（**从 `mcp_server.TOOLS` 派生**）· `TOOLS_BY_NAME`（按名字取**共享对象**，🔴 加）<br>⚠️ **本文件自带的 `calculator` / `date_today` 已于 删除**<br>· `inject_memories_to_prompt`（`:116`）· `AgentState`（`:90`）· 🆕 `STREAMABLE_NODES`（`:399`） |
| **谁在用** | `api_v1_agent.py:34` import → `:538` **模块级建图**（`advanced_agent = build_advanced_agent`）→ `POST /agent/advanced_chat`（`:540` 定义 · `:563` `advanced_agent.invoke(...)`）<br>🆕 **`POST /agent/advanced_chat/stream`**（`:585`， · ）—— 同一张图，走 `astream(..., subgraphs=True)`（`:653`/`:669`），汇总取自 `aget_state`（`:636`） |
| **规模** | **623 行**（现跑；⚠️ 本文件用 `wc -l` 会得 **622** —— **末行没有换行符**，⛔ 差 1 不是笔误）<br>⚠️ **这一格曾停在 540** —— 之后的几批（等）改了代码没同步，实测改前已是 **593**。

## ✅ 做了什么

- **一张「主管路由 + 5 个部门子图」的图**（`build_advanced_agent`，`:409`）：
  `supervisor`（`:413`，判意图）→ `route_by_intent`（`:513`）→ 六选一 →
  `search_dept` / `calc_dept` / `date_dept` / `translate_dept` / `react_dept` / `chat` → `END`
- **5 个子图各自独立编译**（`:178` / `:211` / `:226` / `:262` / `:374`）：搜索（2 节点）· 计算（1）· 日期（1）· 翻译（1）· **ReAct（3 节点 + 循环）**
- **长期记忆注入**：`supervisor`（**现于 `:497`**）与 `inject_memories_to_prompt`（`:116`）都查 mem0；
  `user_id = f"{user_name}:{memory_space}"`
  · 🔴 **批 7：`supervisor` 的那次检索【下移到预算门之后】**
    —— 改前它在门**之上** ⇒ **预算已被拒的那一轮照样打一次 DashScope embedding**，结果**当场被丢弃**。
    ⚠️ 发现方式：用例**本机绿、CI 红**（本机 `.env` 有真 key ⇒ **在花真钱**）。
  · 🔴 **：两处都改成 fail-open** —— 检索失败只**少一段上下文**，⛔ 不打挂
    `/agent/advanced_chat`（改前是裸调用 ⇒ embedding 取不到即**整条 500**）。
    ⚠️ **必须是【两处】**：`supervisor` 是**直调** `search_user_memory`，**⛔ 不在**
    `inject_memories_to_prompt` 里面 ⇒ 只改那个函数**盖不到它** ——  的账原先就漏了这处（见 ⚠️⑨）。
    📌 守卫 `app/tests/test_memory_injection_failopen.py`
- **工具表从 MCP 注册表派生**（`:64`）—— **单一事实源**（作者裁「乙」；理由与实测见 `:38-62`）
- **REACT 子图有收尾节点**（`summarize`，`:345`）—— 补的，此前 `final_output` 永远留占位串
- **`calculator` 走 `safe_math.calculate`**（`:30`）——，⛔ 不许改回 `eval`
- 🔴 **预算拦 + 记账**：**6 个 LLM 调用点全部接上**（见下方节点表倒数第二列）——
  每个节点 **调用之前** 查 `check_token_budget(user_name, estimated_tokens=500)`、**之后** 调
  `record_from_response(…)`。`AgentState` 新增 `thread_id`（`user_name` 已有）⇒ 子图与父图共用同一 `AgentState`，
  身份自动流入。⚠️ **超预算的返回形状按节点出口不同**（见 ⚠️⑦）。
  📄 裁定；📌 判据 ⇒ `app/tests/test_billing_wiring.py`
- 🔴 **被拦那一轮改【写进 state】（批 7 · ）**：本图 **6 处软返回全部**改成
  **同时写 `budget_intercept`**（`search_summarize:176` · `calc_execute:217` · `translate_execute:275`
  react 的 `agent_decide:330` · **`supervisor:470`（入口）** · `chat_node:534`）⇒ 端点回 **429 / error 帧**。
  · **入口 `supervisor` 的正常出口（`:512`）必须写 `state["budget_intercept"] = None`** —— 它是
    **原地改 state 再整个返回**（⛔ 不是返回增量）⇒ 清零写成赋值。（：不清零 ⇒ 上轮被拦会让下轮正常提问也 429）
  · ⚠️ **`supervisor` 的软返回【必须】继续给 `intent="CHAT"`** —— `route_by_intent` 读 `state["intent"]`，少了当场 `KeyError` = **500**（见 ⚠️⑦）
  · 📄 裁定；📌 判据 ⇒ `app/tests/test_budget_soft_return.py`（21 例，**变异自证 27/27**）
- 🔵 **真流式**：4 个节点（`:143` / `:237` / `:279` / `:463`）声明 `config: RunnableConfig`
  并用 `.stream(…, config=config)` 逐块聚合 ⇒ `/agent/advanced_chat/stream` 的
  `astream(…, subgraphs=True)` 才拿得到 **token 级**的块。📄 裁定 ⇒  · 白名单 ⇒ `STREAMABLE_NODES`（`:399`）
  ⚠️ **节点保持同步** —— 改 `async def` 会当场 `TypeError`（见状态栏）

## 🟡 做到哪 / 缺什么

- ✅ ~~🔴 **本模块的 LLM 调用【一处都不记账】**~~
  ⇒ **【已修】**：6 个调用点全部接上 `check_token_budget` + `record_from_response`。
  ⚠️ **改前判据（可打印，已作废）**：`grep -c record_usage app/agent/agent_graph_advanced_learning.py` ⇒ **0**；
  **改后**同一个 grep 仍可能 **0** —— 因为实现走的是 `record_from_response`（**三张图共用的唯一入口**），
  ⛔ **别拿 `grep -c record_usage` 当"记没记账"的判据**（这条本身就是"两个名字"的病根）。
  正确的可打印判据 ⇒ `python -m pytest app/tests/test_billing_wiring.py -q`。
  📌 **同族**：`app/routing/api_v1_rag.py` 仍是 0（那个已在它的 spec 里登记，⛔ 本轮未动）。
  ⚠️ **端点上那两道闸**（`check_session_token_budget` / `circuit(global_key())`，`api_v1_agent.py` 的 `advanced_agent_chat`）
  **只是"拦"，不是"记"** —— 别把两者读成一回事。**这次补的是"记"**（以及每个节点自己的"拦"）。
- ⚠️ **`MemorySaver()`**（`:450`）是**进程内存** ⇒ 重启即丢（与 `/agent/langgraph_chat`·`/agent/mcp_chat` 同）。
- ✅ ~~**`calculator` / `date_today` 在本文件里是【第二份定义】**~~ ⇒ 🔴 **已删**
  —— 那两份「同名不同对象」的副本**整个删掉了**。子图节点现在改为
  **`TOOLS_BY_NAME["calculator"]` / `["date_today"]`**（`TOOLS_BY_NAME` 在 `:67` 附近，
  取的是 `mcp_server.TOOLS` 里那**同一批对象**）。
  ⭐ **判据（对象同一，⛔ 不是"名字一样"）**：
  `app/tests/test_tool_registry_single_source.py::test_all_graphs_share_the_same_tool_objects`（用 `is` 断言）。
  ⚠️ **子图形状没动** —— 只换了 `.invoke()` 的目标。
- ⚠️ **`supervisor` 直接改 `state` 再整个返回**（`:381-382`）——
  与本仓别处的 `return {...}` 风格不同，读的时候容易看漏它**确实**写了 `intent`。

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **① 这是"进阶示例"** | ⛔ **它是 `POST /agent/advanced_chat` 的图本体** —— 模块 docstring 第 1 行写着「LangGraph **进阶示例**」。<br>**判据**：看谁 import 它（`api_v1_agent.py:34`）⇒ 名字与自述都不可信（同 `agent_graph_advanced.py` 的 ⚠️①）。 |
| 🔴 **② 「调 LLM 的节点都能流」** | ⛔ **6 个调 LLM 的节点里只有 4 个该流** —— 见下表。**这张表就是本 spec 存在的理由**：<br>它**⛔ 读代码推不出来**（要同时知道「这个节点的 LLM 输出是什么角色」+「答案最终从哪来」）。 |
| ⚠️ **③ 「流式失败 ⇒ 一个字都流不出来」** | ⛔ **反了**：`calc_dept` / `date_dept` **两条分支本来就无字可流** —— 它们的答案来自**工具返回值**，不来自 LLM。<br>⇒ 用户会**一直等到最后一帧**才看到 `answer`。**这是设计如此，⛔ 不是 bug。** |
| ⚠️ **④ 子图节点的名字** | **父图里**它们叫 `search_dept` / `react_dept`…，而 `stream_mode="messages"` 的 `meta["langgraph_node"]`<br>报的是**子图内层**名（`search_summarize` / `agent`…）—— ⚠️ **前提是开了 `subgraphs=True`**；<br>不开，要么只拿到子图的**返回值**（1 块整段 · 外层名），要么**一块都没有**。<br>📄 实测六剧本表 ⇒ `qingyun-rag-agent-TODO待办/探针-真流式与子图.py` · 勘察 §8.3 |
| ⚠️ **⑤ `should_continue` 是本仓唯一那个** | ⛔ **不是** —— 本文件 `:278` 那个是**局部函数**，返回 **`"tools"` / `END`**；<br>`agent_graph.py:195` 那个返回 **`"approval"` / `"tools"` / END**；`agent_graph_advanced.py:430` 那个返回 **`"tools"` / `"chat"`**。<br>**同名 · 三个语义 · 三个作用域。** |
| ⚠️ **⑥ 路由是"容错"的** | ⛔ **是【精确匹配】的**（`:513`）：`intent.content.strip()` 与 `"SEARCH"` 等**逐字相等**才命中，<br>**否则一律落 `chat`**（兜底）。⇒ 模型多吐一个句号（`REACT。`）就**静默走错分支**、且**不报错**。<br>⚠️ 这是**真实风险**，不是理论 —— 提示词（`:430`）只写「只返回一个单词」，**没有输出约束**。<br>🔵 ** 之后这条更值得注意**：`chat_node` 是兜底 ⇒ **大多数请求走的其实是这条分支**（其 docstring 已写明） |
| 🔴 **⑦ 「超预算了，6 个节点都 `return {"final_output": …}` 就完事」** | ⛔ **`supervisor` 那处必须【同时】返回 `intent`**（`:469`：`{"intent": "CHAT", "final_output": BUDGET_EXCEEDED_MSG, "budget_intercept": BUDGET_EXCEEDED_MSG}`）—— <br>因为它的下游是 `route_by_intent`（`:513`），**读 `state["intent"]`**：只给 `final_output` ⇒ 当场 `KeyError`。<br>⚠️ **其余 5 处出口不同**：`chat_node` / `search_summarize` / `calc_execute` / `translate_execute` 的出口是 `final_output`；<br>REACT 的 `agent_decide`（`:311`）出口是 `messages`（`{"messages": [AIMessage(...)]}`）—— 它的下游 `should_continue` 读 `messages[-1].tool_calls`。<br>⇒ **同一件事（拦），返回形状按【节点在图里的位置】定，⛔ 不能一刀切。**<br>🔴 **批 7 追加**：**6 处都还要加 `budget_intercept`**—— 但 **`intent` 照样一个都不能少**。 |
| 🔴 **⑦b 「被拦那轮 `supervisor` 写 `None` 就对了」** | ⛔ **反了** —— `supervisor` 是**入口节点**，**它自己就是拦截点** ⇒ 被拦那一轮必须写**本轮原因**。写 `None` 等于**把这次拦截藏起来**（那正是要治的病）。<br>✅ **`None` 只该出现在【正常出口】**（`:512`，原地改 state 再返回）。⚠️ 改前它写的是 `**cleared`（显式清零）—— 那个写法在"拦截只有 `tool_execute` 一种"的年代是对的，**语义翻转**。 |
| 🔴 **⑦c 「被拒那轮反正要返回预算话术，记忆检索放前面无所谓」** | ⛔ **钱已经花了** —— `search_user_memory`（`:497`）/ `inject_memories_to_prompt` 会**真打一次 DashScope embedding**，而取回的记忆**当场被丢弃**（走不到 `messages`）。<br>✅ 批 7 已把两条都**下移到预算门之后**。📌 判据 `app/tests/test_budget_soft_return.py::_no_memory`（**一调就炸**，⛔ 不是挡成 passthrough）。 |
| 🔴 **⑨ 「改了 `inject_memories_to_prompt` 就等于把本图的记忆检索改完了」** | ⛔ **漏了一处** —— `supervisor` 是**直调** `search_user_memory`（`:497`），**⛔ 不走** `inject_memories_to_prompt`（`:116`）<br>⇒ 只改那个函数，`/agent/advanced_chat` 在 embedding 取不到时**照旧整条 500**，而**没有任何门会红**。<br>🔴 ** 的账原先就是这么写的**（只列了两个 `inject_memories_to_prompt`）—— 修的时候**逐处扫调用点**才照出来。<br>⚠️ **同族**：这就是本仓「第二层闸 / 漏一处就没人红」那一族（`grep -rn "search_user_memory" app/agent/` 数**调用点**，⛔ 不是数 `def`）。<br>🔴 **两处里的 `except AssertionError: raise` 也⛔ 不是冗余** —— 见 `agent_graph_advanced.md` ⚠️⑨（探针报的是 `AssertionError`，它是 `Exception` 的子类）。<br>📌 守卫 ⇒ `app/tests/test_memory_injection_failopen.py`（**4 条**，每条带**正控**；**变异自证**：拆入口那处守卫 ⇒ 1 红 · 拆 `except AssertionError` ⇒ 1 红 · 拆两处函数内的 ⇒ 2 红）。 |
| 🔴 **⑧ 「记账就是 `record_usage`，grep 它就能查有没有记账」** | ⛔ **本模块走的是 `record_from_response`**（三张图共用的**唯一入口**，`token_tracker.py`）—— <br>`grep -c record_usage` 在本文件**仍然是 0**（改前改后都是）⇒ **拿它当判据会得出"还是没记"的错误结论**。<br>✅ 正确判据：`python -m pytest app/tests/test_billing_wiring.py -q`（它按**最内层函数**逐个点数）。 |

### 🔴 ⭐ 节点表：**6 个调 LLM 的节点，只有 4 个该流**

| 节点 | 行（`def`） | 调什么 LLM | 该流？ | 记账 `purpose` | 为什么 |
|---|---|---|---|---|---|
| `supervisor` | `:413` | `llm.invoke(classify_prompt)`（`:451`） | ❌ | `agent_decision`（`:454`） | 输出是**路由词** `SEARCH`/`CALCULATOR`/… —— 流给用户 = 答案前面先蹦一个 `SEARCH` |
| `chat_node` | `:463` | `llm.stream(…, config=config)`（`:486`） | ✅ | `answer_generation`（`:488`） | 就是答案。⚠️ **它是兜底分支** ⇒ 大多数请求走这条 |
| `search_summarize` | `:143` | `llm_search.stream(…, config=config)`（`:164`） | ✅ | `answer_generation`（`:167`） | 一句话总结 = 答案 |
| `calc_execute` | `:185` | `llm_calc.invoke(extract_prompt)`（`:196`） | ❌ | `query_rewrite`（`:201`） | **只提取表达式**（`6*7`）；真答案 `42` 来自 `calculator` 工具（`:204`），**⛔ 不是 LLM 输出** |
| `translate_execute` | `:237` | `llm.stream(…, config=config)`（`:250`） | ✅ | `answer_generation`（`:252`） | 译文 = 答案 |
| `agent`（REACT 子图） | `:279` | `llm_react_with_tools.stream(…, config=config)`（`:305`） | ✅ | `agent_decision`（`:310`） | 答案 + `tool_calls` |

另有 **2 个节点不调 LLM**（**本来就无字可流**，**也⛔ 不记账 —— 没花钱**）：`date_execute`（`:218`，纯工具）、
`summarize`（`:345`，只把最后一条消息的 `content` 搬进 `final_output`）。

⚠️ **行号口径**：本表为 **（记账）之后**的实测值（`grep -n "def <节点名>"` 可复核）。
⛔ **别拿本文档早先版本的旧行号去找** —— （流式）与 （记账）**各改过一次**，
每次每个节点的下移量**都不同**（：+3 ~ +62；：再 +10 ~ +90）。

⚠️ **记账列**：`purpose` **不是随手填的标签**，它进 `token_usage_logs`、是统计口径。
本表 6 个值 = `answer_generation` ×3 · `query_rewrite` ×1 · `agent_decision` ×2。
⚠️ `calc_execute` 那个 `query_rewrite` 与链 3（`agent_checkpointer`）改前填的**同名** —— 但那条已改成 `agent_decision`
（它的节点确实是决策）。**本处保留 `query_rewrite` 是对的**：这个节点真在"把问题改写成表达式"。

⇒ 落成 `STREAMABLE_NODES`（**`:399`，放在图定义旁边，⛔ 别让端点自己抄一份**）：
```python
{"chat", "search_summarize", "translate_execute", "agent"}
```
⚠️ **`supervisor` 的字必须在过滤时丢掉** —— 而它会**真的出现在流里**（它也是个调 LLM 的节点），
实测见 `app/tests/test_agent_stream_chains.py::test_chain_a_filters_out_supervisor_and_calc_execute`。

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/api_v1_agent.md` | **唯一入口** `POST /agent/advanced_chat`（`:434`）· 🆕 流式版 `POST /agent/advanced_chat/stream`（`:474`）· 那两道前置闸（B8 / B11） |
| `app/routing/specs/sse.md` | ⚠️ **本链的流式骨架** —— `sse_stream(graph_message_text(nodes=…))`；🔴 **链 A 是唯一必须 `subgraphs=True` 的一条** |
| `app/agent/specs/agent_graph_advanced.md` | ⚠️ **同一族的对照** —— 那边也有 ⚠️「名字骗人」与「同名不同义」两条 |
| `app/agent/specs/agent_checkpointer.md` · `app/agent/specs/agent_graph.md` | 另外两套图的 `agent/tools/approval` 节点（**同名碰撞**，见 ⚠️⑤） |
| `app/billing/specs/token_tracker.md` | 🔴 **本模块现在【调用】它了**（：`check_token_budget` + `record_from_response`）—— 改前一条都不调，见 §🟡 第一条 |
| `app/tools/specs/safe_math.md` | `calculator` 的求值实现 |
| `qingyun-rag-agent-TODO待办/硬门A-Agent端流式勘察-20261003.md` §8.1 | ⭐ **「6 个只有 4 个该流」那张表最早落在这里** |
