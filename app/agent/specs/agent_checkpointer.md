# `app/agent/agent_checkpointer.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **地基在，但零测试 · 且有一处忽略配置**（117 → **121 行**，2026-10-03 ；🔵 2026-10-04  后为 **171 行**；🔴 2026-10-04  后为 **192 行** —— `wc -l` 报 **191**，末行没有换行符，⛔ 差 1 不是笔误）<br>⚠️ **行数口径**：用 `scripts/spec_status.sh` 的数（= **真实行数**）。`wc -l` 对本文件**少算 1**（末行没有换行符）⇒ 两边会差 1，⛔ **不是笔误**<br>⚠️ **行号口径**：本 spec 的行号是 **2026-10-04之后**的 `grep -n` 实测值。⚠️ 历史条目里带括注的大多是**当时**的值 —— 带「**现于**」的是**今天的值**；两者对不上**不是笔误**（+  两次改动共下移了几十行）<br>✅ 2026-10-01：`llm`（现于 `:29`）接上 `MAX_TOKENS_AGENT`<br>✅ 2026-10-02（`①b`）：该 `llm`（现于 `:29`）**改走 `llm_factory.make_llm("fast", "agent")`** —— `model`/`api_key`/`base_url`/`max_tokens` 不再写在本地。⚠️ `bind_tools`（现于 `:51`）是**返回值必须是裸 `ChatOpenAI`** 的原因之一；⚠️ 另一个原因（记账要读 `model_name`）**已随  搬进 `token_tracker.record_from_response`**（本文件不再自己取模型名）<br>✅ **2026-10-03：`calculator` 的 `eval(expression)` 换成 `safe_math.calculate`**（`:33`；新增 import 在 `:17`）。⚠️ **本模块的 `calculator` 是活的** —— 走 `/agent/memory_chat` 那条链<br>🔴 **2026-10-03：两处修掉** —— ① `tool_execute`（现于 `:116`）改成**查 `TOOLS_BY_NAME` 表**分派（改前判 `if tool_name == "search"`、而真名是 `duckduckgo_search` ⇒ **搜索永远落 `else`**）；② 搜索工具换成 `search_tools.web_search`（`tools` 在 `:45`，表在 `:49`）。⚠️ **本模块的分派 bug 是"第二处现场"**（第一处在 `agent_graph.py`），且这条是**活路径**（`POST /agent/memory_chat`）。📄 <br>🔵 **2026-10-04：`agent_decide` 改【流式可透传】** —— 声明 `config: RunnableConfig` + 换 `llm_with_tools.stream(…, config=config)` 逐块 `+` 聚合；新增模块级 `STREAMABLE_NODES = frozenset({"agent"})`（本文件的 `tools` / `approval` **不在**白名单里：它们不调 LLM，且 `tools` 返回的 `ToolMessage` 会被当成"新消息"发出来）。⚠️ **节点必须保持【同步】** —— `/agent/memory_chat` 走同步 `graph.invoke()`，改 `async def` 当场 `TypeError: No synchronous function provided to "agent"`（实测）。🔴 **顺带撞出一个既有 bug（⛔ 当时未修）**：那个 `hasattr(response, "usage")` 判据**恒为 False**（真消息只有 `usage_metadata`，没有 `.usage`）⇒ **本端点的记账从来没执行过**（配额形同虚设）。⛔ **修它 = 开始拦人，是行为变更** ⇒ 要单独裁。📄 勘察 §8.5<br>🔴 **2026-10-04：那个恒假判据【已修】** —— `agent_decide`（现于 `:67`）改成 **`.stream()` 之前**查 `check_token_budget`（`:79`）、**之后**调 `record_from_response(…, "agent_decision", …)`（`:110`）；`AgentState`（`:54`）新增 `user_name` / `thread_id`（`:58` / `:59`，**由端点注入**）。171 → **192 行**。<br>⚠️ **这是【行为变更】**：`/agent/memory_chat` 的配额**从形同虚设变成真生效** —— 超预算的请求现在会被**拒**（返回 `BUDGET_EXCEEDED_MSG`），而改前一律放行。|<br>🔴 **它现在有测试了**：`app/tests/test_agent_stream_chains.py::test_real_chain_b_node_streams_one_chunk_per_token`（真图真节点出块）+ 既有的 `test_memory_chat_approval.py`（审批门） |
| **对外提供** | `build_checkpointer_agent()` · `checkpointer_agent` |
| **谁在用** | `api_v1_agent.py` → 路由 **`/agent/memory_chat`**（含 `check_session_token_budget` / `circuit` 两道门 + `session_key` 拼键）<br>🆕 `/agent/approve` —— **按登记表里的 `graph` 字段**路由到本图续跑（丙段） |

## ✅ 做了什么

- LangGraph Agent + **状态持久化**：`MemorySaver`（默认）/ `SqliteSaver`（`AGENT_CHECKPOINT_BACKEND=sqlite`）
- ⭐ **它是硬门 D（人工接管）的地基** —— `docs/现状核对` 判定「**地基是真的**」
- 🆕 **2026-10-03（丙段）**：**接上人工审批门** ——
  新增 `approval` 节点 + `interrupt_before=["approval"]`，路由改成**三路**
  （`should_continue`：含敏感工具 ⇒ `"approval"` / 只有本地工具 ⇒ `"tools"` / 无调用 ⇒ `END`）。
  ⚠️ **语义从 `agent_graph` 引入，⛔ 不是复制** —— 见「看代码会误判」第 2 行
- 工具：🔴 **2026-10-08 起与 `agent_graph.py` 同款 —— 全部【从 `mcp_server.TOOLS` 派生】**，
  本地那份 `web_search` / 计算器 / 日期**三个定义全删了**。
  ✅ **2026-10-08：原先的 `execute_python` 排除【已放开】**（需求方同意「甲」）——
  理由与逐条处置**同 `agent_graph.md` 同一处**（⛔ 别在这儿再抄一遍）。本图工具表现在也与
  `mcp_server.TOOLS` 逐名一致（4 个）。
  ✅ **`SENSITIVE_TOOLS`（全局审批名单）那条【已裁】：`execute_python` 不进** ——
  需求方裁「保持现状」（`docs/待办总表.md` §二·11）—— ⛔ 同 `agent_graph.md`，这儿不重抄依据。
  · ⭐ **工具名只有 `tools` 一个来源** —— `TOOLS_BY_NAME`，`tool_execute` 查它
  · ⚠️ **删了 import 后⛔ 别误删 `from agent_graph import SENSITIVE_TOOLS, should_continue, human_approval # noqa: F401`**
    —— 那一行是**结构性守卫**要的（`test_memory_chat_approval.py` 断言两模块的白名单**是同一个对象**）
- 🔴 **预算拦 + 记账（2026-10-04）**：`agent_decide` 里 **`.stream()` 之前** 查
  `check_token_budget(user_name, estimated_tokens=500)`，**之后** 用
  `record_from_response(llm_with_tools, response, "agent_decision", …)` 记一笔。
  · `AgentState` 新增 `user_name` / `thread_id` —— **由端点注入**（`api_v1_agent.py`）
  · 🔴 **它替换的是【一段恒假的旧代码】** —— 改前那个 `hasattr(response, "usage")` **恒为 False**
    （真属性名是 `usage_metadata`，`.usage` 不存在）⇒ 本链**有史以来第一笔账**是  的  实测写下的（**626 tokens**）。
    ⚠️ **旧块整段留在注释里作墓碑** —— 见「看代码会误判」表。
  · 🔴 **`purpose` 从 `"query_rewrite"` 改成 `"agent_decision"`** —— 改前那个值**与事实不符**（这个节点是**决策**，不是改写），
    且真库里 `query_rewrite` **0 条** ⇒ **无历史数据要迁**。⚠️ **行为变更**（统计口径跟着变）。
  · 📄 裁定 ⇒ ；📌 判据 ⇒ `app/tests/test_billing_wiring.py`（AST + 行为，14 例；其中 `-k behavior` 那条是**本 bug 的行为层墓碑**）
- 🔴 **被拦那一轮改【写进 state】（批 7 · 2026-10-05）**：改前软返回返回
  `{"messages": [AIMessage(BUDGET_EXCEEDED_MSG)]}` ⇒ **HTTP 仍 200**，`/agent/memory_chat` 的调用方
  **看不出被拒了**。· 现在**两个出口都显式给 `budget_intercept`**：软返回出口（`:99`）写**本轮原因**、
  正常出口（`:137`）写 **`None`**（清零 —— 入口节点 `agent_decide` 每轮第一个跑）。
  ⚠️ **本文件的返回形状是"孤例"** —— 它返 `AIMessage`（`should_continue` 读 `messages[-1].tool_calls`），
  ⛔ **不是 `final_output`** ⇒ 本批**只加键、⛔ 没动返回形状**（动了会动到本图路由）。
  · 📄 裁定 ⇒ ；📌 判据 ⇒ `app/tests/test_budget_soft_return.py`（21 例）

## 🟡 做到哪 / 缺什么

- 🆕 ✅ **有测试了**（2026-10-03 丙段）：`app/tests/test_memory_chat_approval.py` ——
  **审批门端到端**（真编图、真走 `should_continue`，只有 LLM 是假的 ⇒ 不联网）
  · ⚠️ 覆盖面**只有审批门那一块**，其余仍是 `docs/说明/测试.md` §六 **#3** 的"零覆盖"
- 🔴 **`backend=="redis"` 分支忽略 `REDIS_URL`**（见下）
- ✅ ~~🔴 **`record_usage(model="qwen-turbo")` 与实际调用的模型不符**（`:58`）~~
  **2026-10-01 已修**（`🅗 S4` +）⇒ 改成 `getattr(llm, "model_name", None) or getattr(llm, "model", "unknown")`
  （**照抄 `plan_execute.py:154`**）。✅ **同批已给 `MODEL_PRICING` 补上 `deepseek-v4-flash`** ——
  只改这里不补价会落到兜底价（**看不出来是兜底**）。
  📄 另两处在 `app/agent/specs/agent_graph_advanced.md` ⚠️②（`:316` `:364`）—— **三处已一起改**。
- ✅ ~~🔴 **`hasattr(response, "usage")` 恒假 ⇒ 记账从未执行**~~ ⇒ **2026-10-04 起【已修】**：
  真属性名是 `usage_metadata`；改用 `record_from_response`（**三张图共用的唯一实现**）。
  ⚠️ **`purpose` 同批从 `query_rewrite` 改成 `agent_decision`**（原值与节点事实不符，真库 0 条 ⇒ 无迁移）。
- ⚠️ **它也是 6 套 Agent 之一** —— 哪套是产品版本**【未裁】**（M5）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「这个模块跟 `agent_graph.py` 一样有审批」** | ✅ **2026-10-03（丙段）起【有】了** —— `build_checkpointer_agent()` 带 `interrupt_before=["approval"]`，路由/白名单**从 `agent_graph` 引入**（`SENSITIVE_TOOLS` / `should_continue` / `human_approval`，⛔ 不是抄一份）。<br>⚠️ **改之前是"没有"**： §遗留·2 记的「同一个仓里，一条路停下等人批，另一条直接执行」**已就此关闭**。<br>🔴 **但续跑要配对**：停下来的会话在 `/agent/approve` 靠**登记表里的 `graph` 字段**路由到本图（需求方 2026-10-03 裁）—— ⛔ 只加门不改 approve，会话会**永远放行不了** |
| 🔴 **「`should_continue` 还是本文件自己那份」** | ⛔ **不是** —— 2026-10-03 起本文件**删掉了自己那份两路版本**，改用 `agent_graph` 的**三路**版本。⚠️ 理由：审批判据只能有**一处**，抄一份 =  记的病根（两处实现、不一致时**不报错**）。⇒ ⛔ 别在本文件里"补一个本地版" |
| 「支持 Redis 后端」 | 🔴 **`:178-182` 把 `redis_url` 注释掉了，直接回落 `MemorySaver()`（`:184`）** ⇒ **写 `redis` 也拿不到 Redis 持久化**（进程重启即丢） |
| 「有检查点 ⇒ 对话能跨重启」 | 🔴 **默认 `memory` 是【内存态】** ⇒ **重启即丢**；只有显式设 `AGENT_CHECKPOINT_BACKEND=sqlite` 才落盘 |
| 「成本记账是准的」 | 🟡 **Token 口径准，金额【2026-10-01 起也准了】** —— 模型名已改为从对象取，且 `MODEL_PRICING` 已补 DeepSeek 条目（`🅗 S4`+）<br>⚠️ **但仍是"近似"** —— 单价**不区分缓存命中**（拿不到命中/未命中拆分）⇒ 按未命中价记 ⇒ **偏高估** |
| 🔴 **「`.usage` 和 `usage_metadata` 差不多，写哪个都行」** | ⛔ **差得多** —— `AIMessage` / `AIMessageChunk` 上**只有 `usage_metadata`**，`.usage` **不存在**（`hasattr` **恒为 False**）。<br>写错一个名字的后果是**静默失效**：记账整段被 `if` 跳过，**接口一切正常**，只是**一分钱不记**。本仓**真的栽过**：这条链从建立起就**从未记过一笔**（才写下第一笔）。<br>✅ **统一走 `token_tracker.record_from_response`** —— ⛔ 别在节点里自己 `getattr(response, "usage*")`（那正是两个名字的来源）。 |
| 🔴 **「`purpose` 随便填一个就行」** | ⛔ **它会进 `token_usage_logs`，是统计口径** —— 改前本节点填的是 `"query_rewrite"`，而它干的是**决策**（改前真库 `query_rewrite` **0 条**是巧合，⛔ 不是"没人用"）。<br>✅  起统一成 `"agent_decision"`（与 `agent_graph.py` · 参照图一致）。 |
| 🔴 **「被预算拦下那次也会记一笔」** | ⛔ **不记** —— 拦在 LLM 调用**之前** ⇒ **没花钱 ⇒ 没有账**。 |
| 🔴 **「被拦那轮返回的是 `AIMessage`，所以调用方看到的就是那句预算话术」** | ⛔ **2026-10-05 批 7 起不再是** —— 图里**同时写 `budget_intercept`**（`:99`），端点层读到非空 ⇒ **429**（流式 ⇒ error 帧）。⚠️ **`AIMessage` 那条形状本身没改**（`should_continue` 靠着它判路由）。 |
| 🔴 **「`budget_intercept` 只在被拦时写一下就行」** | ⛔ **正常出口（`:137`）也必须写 `None`** —— 它是**普通 state 键 + 落 checkpoint** ⇒ 只清一个出口的话，走另一个出口的那一轮会**留着上一轮的值**，预算恢复后**正常提问照样 429**。📄  ·  §五 |

## 关联

`ROADMAP` 待办  · `docs/说明/测试.md` §六 · `后端补齐清单` **B5/B6**（硬门 D 队列与续跑）·
****（工具名分派勘误 —— 本模块是**第二处现场**）·
** 丙段**（审批门 + `/agent/approve` 按图路由）·
****（**三条链不记账** —— 本模块那条恒假判据（`hasattr(response, "usage")`）在此修掉；配额从**形同虚设**变**真生效**）·
****（**图内预算软返回的出口形状** —— 2026-10-05 批 7：被拦那一轮写 `budget_intercept`，端点转 429）·
`app/agent/specs/agent_graph.md`（同款工具与分派表的出处 —— **审批语义也从它引入**）·
`app/agent/specs/pending_approvals.md`（**停下来的那个会话记在哪**）·
`app/access/specs/session_key.md`（**checkpoint 键怎么拼**）
