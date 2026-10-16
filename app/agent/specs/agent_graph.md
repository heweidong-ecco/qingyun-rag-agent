# `app/agent/agent_graph.py`

| 项 | 内容 |
|---|---|
| **状态** | ⚰️ **遗留 / 未经裁决** —— **6 套 Agent 实现之一**<br>✅ 2026-10-01：`llm`（`:21`）接上 `MAX_TOKENS_AGENT`<br>✅ 2026-10-02（`①b`）：该 `llm`（现于 `:25`）**改走 `llm_factory.make_llm("fast", "agent")`**。⚠️ **模型轴是 `fast`**（不是 chat）—— 这是改动前的实际取值，收口时**原样保留**<br>✅ 2026-10-03：审批触发条件**从「任意 tool_calls」改成「工具白名单」**<br>✅ **2026-10-03：`calculator` 的 `eval(expression)` 换成 `safe_math.calculate`** —— 本文件 **185 → 189 行**（多出的行是解释为什么不许改回去的注释）<br>🔵 **2026-10-03：`agent_decide` 改成【流式可透传】的** —— 声明 `config: RunnableConfig` + 改用 `llm_with_tools.stream(…, config=config)` 逐块聚合。189 → **222 行**。📄 <br>🔴 **2026-10-03：两条既有 bug 结掉 + 搜索工具换掉** —— ①`tool_execute` 改成**查 `TOOLS_BY_NAME` 表**分派；②`SENSITIVE_TOOLS` 默认值改成真工具名 `web_search`，且 `validate_approval_config()` **加第二段硬拦**（名字不存在 ⇒ 拒绝启动）；③搜索工具 `DuckDuckGoSearchRun` → `search_tools.web_search`。222 → **258 行**（多出的行是"病根是名字有两个来源"的解释 + 新硬拦）。📄 <br>🔵 **2026-10-04（剩余 4 条链）**：258 → **273 行** —— 新增模块级 `STREAMABLE_NODES` 及其说明注释（见下条）<br>⚠️ **行数口径**：本仓一律用  的数（= **真实行数**）。`wc -l` 对本文件**少算 1**（它末行没有换行符）⇒ 两边会差 1，⛔ **不是笔误**<br>⚠️ **行号口径**：非历史条目的行号是 **2026-10-04之后**的 `grep -n` 实测值；**带日期条目里括注的行号是【当时】的值**（带「**现于**」的除外），两者对不上**不是笔误**<br>⚠️ ** /3⛔ 没动过本文件** —— 队列与续跑都落在 `api_v1_agent.py`（见下）<br>🔵 **2026-10-04（剩余 4 条链）：新增模块级 `STREAMABLE_NODES`**（现于 `:264`，现为 `frozenset({"agent"})`）—— **白名单住在图模块里**，端点只准按 `agent_graph.STREAMABLE_NODES` 取，⛔ 不许自己抄一份字面量（的教训：**一个名字两个来源必然漂移，而漂移是静默的**）。<br>⚠️ **为什么必须放模块级**：放进 `build_agent_graph()` 函数体就是**局部名** ⇒ 端点 `agent_graph.STREAMABLE_NODES` 直接 `AttributeError`（本批**真的踩过一次**才改过来）。📌 守卫 `app/tests/test_agent_stream_chains.py::test_chain_a_whitelist_is_the_graph_module_s_own` |<br>⚠️ **`agent_decide` 的节点实现本批一字未动**（早就改好）—— 本批改的是**另外三张图**<br>🔴 **2026-10-04：`agent_decide` 接上【预算拦 + 记账】** —— 278 → **308 行**。改前本图的 LLM 调用点**既不查预算、也不记账**（`record_usage` / `check_token_budget` 在本文件 **0 命中**）⇒ 走 `/agent/langgraph_chat` 的用户**一分钱不花**，而端点上的 B8 会话上限 / B11 全站熔断对本图**等于不存在**（守卫读的计数器它从不写）。|
| **对外提供** | 路由 `/agent/langgraph_chat` · `/agent/approve` |
| **谁在用** | `api_v1_agent.py:18`（`from agent_graph import agent_graph, STREAMABLE_NODES`） |

## ✅ 做了什么

- 基础 LangGraph Agent：`agent` 决策节点 → `tools` 执行循环
- 工具：🔴 **2026-10-08 起全部【从 `mcp_server.TOOLS` 派生】**——
  本地那份 `web_search` / 计算器 / 日期**三个定义全删了**。
  ✅ **2026-10-08：原先的 `execute_python` 排除【已放开】**（作者同意「甲」·  附录）——
  本图工具表现在**与 `mcp_server.TOOLS` 逐名一致**（4 个）。
  ⚠️ 放开的两条理由：①「无容器隔离」**已由 解决**（硬化容器）；
  ②「无审批」**不是这两张图特有的** —— 别处（`plan_execute` / `..._learning` / 经 MCP 的
  `agent_graph_advanced` / `/agent/execute_code`）**同样拿得到且同样不在审批名单**
  ⇒ 抠掉它**并没挡住什么**，只制造不一致。
  ✅ **`SENSITIVE_TOOLS`（全局审批名单）那条【已裁】：`execute_python` 不进** ——
  作者裁「保持现状」（§二·11）。依据：**敏感 = 这个工具会把数据发到本机之外**。
  · ⭐ **工具名只有 `tools` 一个来源** —— `TOOLS_BY_NAME = {t.name: t for t in tools}`，`tool_execute` 查它，⛔ 不许再抄名字
  · ⚠️ **连带删掉的 import**：`datetime` / `safe_math.calculate` / `search_tools.web_search` / `langchain` 的 `tool`
    （它们只服务于那三个已删的定义 ⇒ 留着就是，第 ⑥ 道门会红）
- **人工审批**：`interrupt_before=["approval"]`（`:302`，审批节点本身 `human_approval` 在 `:241`）+ `/agent/approve` 端点
- 🔵 **流式（· 2026-10-03）**：`agent_decide`（`:120`）声明 `config: RunnableConfig`，
  并用 `.stream(…, config=config)` 逐块聚合 ⇒ `/agent/langgraph_chat/stream` 的
  `astream(stream_mode="messages")` 才拿得到 **token 级**的块。📄 裁定；
  ⚠️ **判据**（可打印）⇒ `app/tests/test_agent_sse.py`（12 例，纯离线，进 CI）
- 🔵 **审批白名单**（B4）：`SENSITIVE_TOOLS`（`:71`，读 env，默认 **`web_search`**）· `needs_approval()`（`:215`）· `validate_approval_config()`（`:76`，启动自检**两段**：空名单 ⇒ `raise`；**名字不存在 ⇒ 也 `raise`**）
- 🔴 **预算拦 + 记账（2026-10-04）**：`agent_decide`（`:120`）里 **`.stream()` 之前** 查
  `check_token_budget(user_name, estimated_tokens=500)`，**之后** 用
  `record_from_response(llm_with_tools, response, "agent_decision", …)` 记一笔。
  · `AgentState` 新增 `user_name` / `thread_id`（`:108`）—— **由端点注入**（`api_v1_agent.py:198`），图自己推不出来
  · ⚠️ **拦在调用【之前】** —— 放在 `.stream()` 之后钱已经花了，只能丢结果、拦不住
  · ⚠️ **被拦下那次不记账** —— 没花钱就没有账（与参照图 `agent_graph_advanced.py` 一致）
  · 📄 裁定；📌 判据 ⇒ `app/tests/test_billing_wiring.py`（AST 形状 + 行为，14 例）· `app/tests/test_token_budget_hookup.py`（`record_from_response` 单元，5 例）
- 🔴 **被拦那一轮改【写进 state】（批 7 · 2026-10-05）**：改前软返回只返回
  `{"messages": [AIMessage(BUDGET_EXCEEDED_MSG)]}` ⇒ **HTTP 仍 200**，调用方在响应里**看不出被拒了**。
  · 现在**两个出口都显式给 `budget_intercept`**：软返回出口写**本轮的原因**、正常出口（`:205`）写 **`None`**（清零）
  · ⚠️ **清零必须在【入口节点】**（`agent_decide` 是 `set_entry_point("agent")`，每轮第一个跑）——
    它是**普通 state 键**（⛔ 没挂 `operator.add`）⇒ last-write-wins + **落 checkpoint**
    不清零的话**上一轮被拦**会让**下一轮正常提问也回 429**
  · ⚠️ ⛔ **别改成在节点里 `raise`** ——  实测：会污染 checkpoint（留下没人回答的
    `tool_calls`）⇒ 那个 thread **从此每轮必 500**
  · 📄 裁定；📌 判据 ⇒ `app/tests/test_budget_soft_return.py`（21 例，含**变异自证 27/27**）

## 🟡 做到哪 / 缺什么

- ⚠️ **哪套 Agent 是"产品版本"——【未裁】**（属 **M5 的代际收敛**，见下）
- ✅ ~~**触发条件口径是错的**~~ ⇒ **2026-10-03 起【已修】**（B4）：`should_continue`（`:225`）现在是**三条路**（`approval` / `tools` / `END`），只有命中白名单才停
- ✅ ~~**`calculator` 是任意代码执行**~~ ⇒ **2026-10-03 起【已修】**：改走 `app/tools/safe_math.py`（AST 白名单求值 + 三道闸）。📄 见 **`app/tools/specs/safe_math.md`**
- ✅ ~~**零测试覆盖**~~ ⇒ **2026-10-03 起有 `app/tests/test_approval_trigger.py`**（**8 例**，纯离线）+ **`app/tests/test_tool_dispatch.py`**（**9 例**，含 2 条子进程起服自检）。⚠️ **覆盖范围只有审批触发条件与工具分派** —— **图的其余部分（节点行为 / 状态流转）仍无测试**
- ✅ ~~** 未做**~~ ⇒ **2026-10-03 起【已做】**：待接管队列在 **`app/agent/pending_approvals.py`**，出口是 **`GET /agent/pending`**（`api_v1_agent.py`）。📄 见 **`app/agent/specs/pending_approvals.md`**<br>⚠️ **但队列【不在这个模块里】** —— `agent_graph.py` 只负责"停下来"；"谁停下来了、从哪儿看"是 `api_v1_agent.py` 记账。⛔ 别在本模块找队列
- ✅ ~~** 未做**~~ ⇒ **2026-10-03 起【已做】**：`/agent/approve` 加了 `edited_answer`（改写后提交），**续跑形状被 `app/tests/test_approval_resume.py` 钉住**（`invoke(None, config)` = 从 checkpoint 继续）<br>⚠️ **同样不在本模块里** —— 改动落在 `api_v1_agent.py`。⛔ 本模块里**没有**续跑代码
- ⚠️ **硬门 D 三段齐了，但【没有端到端验收过】** ——  只钉了接线与语义（假图），**"上下文真的连续"要真 LLM + 真 `MemorySaver` 跑一遍**才算（联网花钱）
- ✅ ~~**DuckDuckGo 本机不通**（`search_tools.py:48` 注明实测 `duckduckgo.com` 完全不通）⇒ 这条链上的搜索会失败~~
  ⇒ **2026-10-03 起【已修】**：本文件改用 **`search_tools.web_search`（Bing 版）**。
  ⚠️ **但仍⛔ 不声明"搜索能用了"** —— 那要真联网，本机不可验证；本 DEC 只声明**"分派走对了"**（离线可验）。
- ✅ ~~🔴🔴 **`SENSITIVE_TOOLS` 的默认值【匹配不到任何真实工具】⇒ 审批其实【永不触发】**~~
  ⇒ **2026-10-03 起【已修】**。改前实测（跑真服务时撞）：
  真实工具名 = `['calculator', 'date_today', 'duckduckgo_search']`，而默认白名单写的是
  **`search_tool`**（**变量名**，⛔ 不是工具名）⇒ **交集为空** ⇒ **启动自检过得去，功能却从未生效过**。
  · ✅ **两处都补了**：默认值改成真工具名 `web_search`（`:71`），
    且 `validate_approval_config()`（`:76`）**加了第二段硬拦** —— 名字不在 `tools` 里 ⇒ **拒绝启动**。
  · ⚠️ **改前的失败形态正是硬门 D 说的那种**「**验收时才发现接管从来没发生过**」——
     只拦了"空名单"，**没拦"名字不存在"** ⇒ 同一个失败**换了个形状绕过了它自己的闸**。
  · 📌 可打印的判据（修复后必须**非空**）：
    `{t.name for t in agent_graph.tools} & agent_graph.SENSITIVE_TOOLS` ⇒ `{'web_search'}`
  · 📌 守卫 ⇒ `app/tests/test_tool_dispatch.py::test_shipped_default_whitelist_matches_a_real_tool`
    （**子进程**跑 —— 该常量是模块级读的，同进程改 env 无效）
- ✅ ~~🔴 **`tool_execute` 的分派名字也对不上** ⇒ **搜索工具永远执行不了**。（原 `:85`）~~
  ⇒ **2026-10-03 起【已修】**。改前它判 `if tool_name == "search"`，
  而真名是 `duckduckgo_search` ⇒ 落到 `else`、返回字面量 `"未找到工具: …"`。
  · ⚠️ **后果是模型侧可见的**：真服务实测模型会**反复重试搜索** ⇒ 这正是   那个**多轮聚合缺陷**的触发器。
  · ✅ **现在查表**（`TOOLS_BY_NAME.get(name)`，`:181`）—— 名字**只从 `tools` 派生**，⛔ 不再有第二份。
  · 📌 守卫 ⇒ `app/tests/test_tool_dispatch.py` **两道 AST**（分派里不许出现字符串字面量比较；
    全仓不许有第三个 `tool_execute` 抄名字）+ 一条**行为**（查不到时**如实**报 `未找到工具: xxx`，⛔ 不是 `KeyError`）
- ⚠️ **同型 bug 在本仓是【第二次】** —— `plan_execute.py:101-110` 记着上一回（prompt 写 `search`、
  注册表里叫 `web_search`）。⇒ 病根不是"写错了"，是**名字有两个来源**

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「这是个产品功能」 | 🟡 **它是 6 套并存实现之一** —— 另 5 套：`agent_checkpointer` · `agent_graph_advanced` · `agent_graph_advanced_learning` · `/ws/agent` 的内联 Executor · `plan_execute`。<br>🔴 **哪套留下【未裁】**（`docs/CODE_INVENTORY.md` §7 明说「2 代与 3 代的先后顺序**从代码判不出**」） |
| 「有 `tool_calls` ⇒ 停在审批」 | 🔴 **2026-10-03（B4）起【不再】** —— 只有命中 `SENSITIVE_TOOLS` 才停（`should_continue`，`:225`）；**非敏感工具直接跑完**，无人值守。⚠️ **`app/tests/test_agent_repairs.py:533` 与 `api_v1_agent.py:66` 的旧注释就在说这条**，已同步更正 |
| 「白名单 = 危险工具清单」 | ⚠️ **不是** —— 第一版只有 **`web_search`**（**外发数据**）。<br>✅ **2026-10-03 已结**：`calculator` 原先那句 `eval(expression)`（**任意代码执行**）**已换成 `safe_math.calculate`**（AST 白名单求值）。⚠️ **但它仍然【不在】白名单里** —— 换成安全求值之后，"不出网"这条判据重新成立。<br>🔴 **改前实测（不是推演）**：喂 `__import__('os').system('touch /tmp/x')` ⇒ **命令真的跑了，且返回 `'0'`**（模型收到的是一条正常的"答案是 0"）。📄 见 `app/tools/specs/safe_math.md` |
| 「`.env` 里设了 `SENSITIVE_TOOLS`」 | ⚠️ **设不设都能跑** —— 不设走**默认值 `web_search`**（`:71`）。⇒ 想加/减**必须显式改 `.env`**。<br>🔴 **两种都会被启动自检拦住**（`validate_approval_config`，`:76`，**2026-10-03  起是两段**）：① **改成空** ② **写了不存在的工具名**。<br>⚠️ 第三档（**部分**名字不认识）**没有单独拦** —— 那时**整个启动被拒**，⛔ 不是"放过认识的那些"。 |
| 「审批是"全都接管"」 | 🔴 **不是** —— 硬门 D 要的是「**该被接管时被接管**」。改前"问个日期也停"那条路**验收过不去** |
| 「搜索工具还是那个 DuckDuckGo」 | ✅ **2026-10-03 起本文件也换成了 `search_tools.web_search`** —— ⛔ 三份实现不齐的反例只剩 `api_v1_rag.py:746`（RAG 侧 `/ws/agent`，见  遗留·1） |
| 🔴 **「`SENSITIVE_TOOLS` 只是个开关，写个大概就行」** | ⛔ **它是【工具名】清单，⛔ 不是变量名** —— 写错一个字母的后果是**静默失效**（那个工具永不审批），**接口一切正常**。本仓**真的栽过**：默认值写成变量名 `search_tool`，**活了三天**。<br>✅ 现在写错**会在启动时炸**，并**列出可用工具名**。 |
| 🔴 **「`agent_decide` 加个 `config` 参数只是顺手接一下」** | ⛔ **它是真流式的【唯一条件】** —— 不声明、或不转发进模型的**流式**调用，`astream(stream_mode="messages")` **只会吐 1 块**（整段，`on_llm_end` 发的）。<br>⚠️ 而**接口看上去完全正常**：照样 `text/event-stream`、照样有 `data:` 帧 —— **前端逐字显示是前端自己切的**。<br>📌 判据（可打印）：`app/tests/test_agent_sse.py::test_graph_streams_one_chunk_per_token`（数**块数**，⛔ 不看 header） |
| 🔴 **「节点这么重，该改成 `async def` 吧」** | ⛔ **别改** —— 实测（探针⑧）会让**同步的** `graph.invoke()` 直接抛 `TypeError: No synchronous function provided to "agent"`，<br>而非流式路径（`/agent/langgraph_chat`）**在用它**。<br>✅ **同步节点 + 同步 `.stream(config)` 就能真流式**，⛔ 不需要 async。守卫 ⇒ `test_non_streaming_invoke_still_works`<br>🔴 **2026-10-05 勘误**：本行原写「（`/agent/langgraph_chat` · `api_v1.py` · `api_v1_rag.py`）**都在用它**」—— **后两个不成立**：<br>它们各只有一句 `from agent_graph import agent_graph`（`api_v1.py:54` · `api_v1_rag.py:66`），**全文再 0 处引用**（`grep -c "agent_graph\." ` 两处都是 **0**）⇒ 是**死导入**，⛔ 不是消费者。<br>📌 判据（可打印）：`grep -rn "agent_graph" app/routing/api_v1.py app/routing/api_v1_rag.py` ⇒ 各 **1 行**（且都是 `import`）。<br>⚠️ **本图真正的非流式消费者只有** `/agent/langgraph_chat`（`api_v1_agent.py:235`）+ `/agent/approve` 的续跑。<br>⛔ **那两个死导入本批【没删】**（删除是"收拾仓库"，要单独裁）—— 只把话说对。 |
| ⚠️ **「聚合流式块，用 `content += ` 拼起来就行」** | ⛔ **会丢掉 `tool_calls`** —— 它是**碎片化**到达的（name 一块、args 几块）。<br>丢了 ⇒ `should_continue` 判不出 `"approval"` ⇒ **B4 审批静默失效**，而接口返回 `{"status":"answered"}` 一切正常。<br>✅ 必须用 LangChain 自带的 `AIMessageChunk.__add__`（`+`）。守卫 ⇒ `test_agent_decide_preserves_tool_calls` |
| 🔴 **「图里记账是 `token_tracker` 的事，节点不管」** | ⛔ **反了** —— `token_tracker` 只提供 `record_usage` / `record_from_response`，**"哪个节点调了 LLM"只有图自己知道**。<br>改前本图 **`record_usage` / `check_token_budget` 0 命中** ⇒ 走 `/agent/langgraph_chat` **一分钱不记**，端点上的 B8 会话上限 / B11 全站熔断**对本图等于不存在**（守卫读的计数器它从不写）。<br>✅ **每个调 LLM 的节点，必须同时有 `check_token_budget`（前置）与 `record_from_response`（后置）** —— `app/tests/test_billing_wiring.py` 会按**最内层函数**逐个数，漏一个就红。 |
| 🔴 **「`user_name` / `thread_id` 是可选装饰，缺了会 KeyError」** | ⛔ **缺了不报错，静默记成 `"unknown"`** —— 一律 `.get(…, "unknown")` 读。**历史上有过不传这两个键的调用方**（本图原先的调用点已随  补齐），用下标会把那种路径当场打挂。<br>⚠️ **两种错各有代价**：用下标 ⇒ **500**（太响）；不记 ⇒ **额度漏算**（太静）。本图选后者。<br>🔴 **2026-10-05 勘误**：本行原写「旧调用方（`api_v1.py` / `api_v1_rag.py`）不传这两个键」—— **那两处根本不调本图**（死导入，见上一行）⇒ 举错了例子。论点不变，例子已撤。 |
| 🔴 **「记账要的是 `session_key(user_name, thread_id)`」** | ⛔ **要的是 `thread_id` 原值** —— `session_key()` 是 **checkpoint 键**，与账目无关。端点上写 `sess` 是**错的**（修的就是这处）。<br>📌 判据：真库 `token_usage_logs.thread_id` 必须等于**请求里传的那个**（端到端实测过）。 |
| 🔴 **「被预算拦下来那次也该记一笔（记 0 tokens）」** | ⛔ **不记** —— **拦在 LLM 调用【之前】⇒ 没花钱 ⇒ 没有账**。记一笔 0 会污染 `token_usage_logs` 的计数（它是额度权威源）。 |
| 🔴 **「`budget_intercept` 是给 LLM 看的提示词一部分」** | ⛔ **反了** —— 它是**端点层**的通道：图里只写**原因**，拼成给调用方的话术由 `api_v1_agent.agent_budget_intercept_message()` 统一做（⛔ 别在节点里拼整句）。<br>⚠️ **它必须被【入口节点每轮清零】** —— 普通 state 键 + 落 checkpoint ⇒ 不清零 = 上一轮被拦会让下一轮正常提问也 429。 |
| 🔴 **「`budget_intercept=None` 与"没这个键"是一回事」** | ⛔ **端点读的是 `.get()`** ⇒ 两者都判成"没被拦"。**但只有显式 `None` 才能把上轮的值冲掉** —— 这就是为什么**两个出口都得给值**（软返回给原因、正常出口给 `None`），⛔ 不是"只给一个出口就行"。 |

## 🔴 2026-10-13· 本图补了两样：**记忆注入** + **轨迹记录**

📄 **为什么补这两样、以及当时撞出来的东西** ⇒ （⛔ 本文件只记**现在是什么样**）。

| 补了什么 | 落在哪 | ⚠️ 看代码会误判的地方 |
|---|---|---|
| **长期记忆注入**（读） | `agent_decide` 开头那一段 · 取法 `agent.memory_store.memory_block()` | 🔴 **只在【本任务的第一步】注入一次**（判据 = `state["messages"]` 里还没有 `ToolMessage`）⇒ **一次任务一次 embedding**。⚠️ 它**不写回 `state`** ⇒ **第二步以后模型看不到那段记忆**（有意的取舍；写回 = 改 checkpoint 内容，那是另一件事）。⛔ 缺省（没有记忆）时**一个字节都不变** —— 行为与改前逐字一致。 |
| **轨迹记录** | `tool_execute` 里 `record_tool_start` / `record_tool_end`（照 `agent_graph_advanced.py:408/436`） | 🔴 **没 `start_trace` 过的线程上它是【空操作】**（`tool_visualizer` 里 `if key not in _traces: return`）⇒ 对 `api_v1.py` / `api_v1_rag.py` 那些不起轨迹的调用者**逐字无影响**。⚠️ 查不到工具名那一条按 **`error`** 记（⛔ 不照抄高级图那处的"一律 success"）。 |
| 端点的**起表 / 收表** | `api_v1_agent.langgraph_chat`（改前**它不写轨迹**） | 🔴 **停在审批点时⛔ 不收表**（故意的：批准后 `tool_execute` 会**继续往同一条轨迹里记** ⇒ 页面重取就能看见续跑那几步）。代价：`duration_ms` 取 `end_time or now` ⇒ 那一格变成**"从发起到现在"**。 |

📌 **判据** ⇒ `app/tests/test_memory_injection_wiring.py`（**4 条**，每条都配了反证：把注入拿掉 / 让它没有记忆 /
把门改成放行 ⇒ **各自当场红**）· 本图别的行为由 `test_billing_wiring.py` / `test_agent_sse.py` 那几份兜着。

⚠️ **改这里时要连带看的**：`summarize_agent_result()` 那条「末条带 `tool_calls` ⇒ 停在审批点」的判据
**只对"带审批节点的图"成立** —— 往本图上加**新的**中间态出口时，回来重看它（它自己那段注释写着这条）。

## 关联

 的 /（🔴 2026-10-10 起**唯一清单**在那儿）· `docs/原理/架构.md` §1.3
`后端补齐清单` （硬门 D 触发条件）· **`app/agent/specs/pending_approvals.md`**（队列 —— ⚠️ **队列不本模块里**）·
`app/routing/specs/api_v1_agent.md` 的「实施计划 ②」
