# `app/tools/tool_visualizer.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **部分可用** —— 记录与查询都在，但 存储是**进程内存**（重启即空） **只有 2 个模块**往里写（`api_v1_agent` 的两条 MCP 链 + `api_v1_rag.stream_search`，共 **3 处**调用点 —— 见 §🟡） |
| **对外提供** | `start_trace` · `finish_trace` · `record_tool_start` · `record_tool_end` · `record_agent_decision` · `get_trace` · `get_trace_of_any_owner` · `get_all_traces` · 数据类 `AgentTrace` / `ToolCallRecord` |
| **谁在用** | 写：`api_v1_agent.mcp_agent_chat` · `mcp_agent_chat_stream`（建轨迹）· **`api_v1_rag.stream_search`** · `agent_graph_advanced` 的两个节点（工具与决策）。<br>读：`/agent/trace/{thread_id}` · `/agent/traces` 两个端点<br>🆕：新页面 **`/trace`**（`app/static/web/trace.html`）读**本轴**画上半页，读**成本轴**画下半页 —— 同一个页面上**两条轴并排、互不相加** |
| **存储** | 模块级字典 `_traces`，**键 = `session_key(user_name, thread_id)`**（决策 9） |

## ✅ 做了什么

- **一次 Agent 任务的完整轨迹**：提问原文、每个工具调用的入参/结果/耗时/状态、
  Agent 的决策理由、最终输出、token 与花费。
- 🔴 **收口**：键由**裸 `thread_id`** 改成**拼了身份**，
  并给两个读端点加了归属判定 —— 改之前是三处跨用户可见（见下）。

## 🟡 做到哪 / 缺什么

| 缺什么 | 说明 |
|---|---|
| ⚠️ **重启即空** | `_traces` 是**进程内存** ⇒ 重启后 `/agent/trace/*` 一律"未找到"。与 `/agent/pending`（`pending_approvals`）同一形态 |
| ⚠️ **只有 2 个模块往里写**（**3 处**调用点） | `start_trace` 的调用点：`api_v1_agent.mcp_agent_chat` · `api_v1_agent.mcp_agent_chat_stream` · **`api_v1_rag.stream_search`**。<br>⛔ **其余花钱的链仍不建轨迹** ⇒ 那些端点上"查不到"是"**从来没建过**"，⛔ 不是"查不到"。<br>📌 **判据（可打印）**：`grep -rn '^[[:space:]]*start_trace(' app/ --include='*.py' \| grep -v '^app/test_'` ⇒ **现跑**（⛔ 别抄数 —— 别处也会加调用点，那时该改的是**别处**） |
| ⚠️ **无淘汰** | 字典只增不减 ⇒ 长跑进程内存单调增长（🔴 **未立账**） |
| ✅ **无 spec 这条欠账已清** | 原先记在 §遗留（「`tool_visualizer.py` 与 `cost_dashboard.py` 无 spec」）—— 本文件是它那一半 |

## ⚠️ 看代码会误判的地方

| 会以为 | 实际 |
|---|---|
| 🔴 **「`_traces[thread_id]` —— 键就是 `thread_id`」** | **不是**。键是 `session_key(user_name, thread_id)`（长度前缀，见 `app/access/specs/session_key.md`）。⚠️ **`AgentTrace.thread_id` 存的是【原值】** —— 响应里回显的是它，⛔ 不是键。**两个不同的东西共用一个名字** |
| 🔴 **「`/agent/traces` 是管理员看板」** | **默认不是** —— 它**只给调用者本人的**。看全量要 `include_all=True`，而那个开关**由端点按角色给**（`api_v1_agent.agent_trace_list` 里一行）。本模块**⛔ 不 import `permission`**，它不认识角色 |
| 🔴 **「`get_trace_of_any_owner` 是个普通的查询函数」** | ⛔ **它是 admin 旁路**（决策 2 的「读侧给 admin 例外」）。**按原值反查、不看属主** ⇒ ⛔ **别在普通路径上调**。普通路径一律走 `get_trace(user_name, thread_id)` |
| ⚠️ **「非属主查询会报『这个线程不属于你』」** | ⛔ **不会** —— 非属主与"真不存在"**返回同一个答复**（`未找到线程 …`）。这是**有意的**：否则那个错误本身就是"该线程存在"的 oracle |
| ⚠️ **「`get_all_traces()` 不带参数」** | 现在**必须传 `user_name`**（位置形参、无默认值）。⚠️ 与 `session_key` 同一取向：**漏传要 `TypeError`，⛔ 不是悄悄返回全量** |
| ⚠️ **「身份漏传会落到某个默认桶」** | 本模块**不会**（形参无默认值）。🔴 **但上游会** —— `agent_graph_advanced` 从 state 取身份用的是 `state.get("user_name", "default_user")` / `"unknown"` ⇒ 那些**默认值本身是 fail-open**，是  那一族的病，⛔ 不在本模块修 |
| ⚠️ **「追踪轴和花费轴是一回事，改了一处另一处跟上」** | ⛔ **两条轴**：本模块是**追踪**（进程内存）；`token_tracker.check_session_token_budget(user_name, thread_id)` 是**花费**（查 `token_usage_logs` 表）。⚠️ **花费轴本来就带 `user_name`** ⇒ **它没这个病，⛔ 别去"顺手统一"** |
| 🔴 **「用户的演示路径上也查得到轨迹」** | ✅ **【成立】**。对话页走 `/api/v1/rag/stream_search`（`api_v1_rag.py`）—— 改前那条链**⛔ 不调 `start_trace`** ⇒ 本轴的键**根本没建过**（当时的原话是"⛔ 多半查不到"）。<br>⚠️ **这句话在 之前【是错的】** —— 它正是 §62 记的那个洞：「**演示路径上，上半页【必然】是空的**」。<br>⇒ 现在凡"页面打开是空的"这类反馈，**分三种**：① 走的链**仍不建轨迹**（会建的只有 `/agent/mcp_chat` · `/agent/mcp_chat_stream` · `/rag/stream_search` 三条）② API 重启过 ③ thread_id 真没用过。<br>🔴 ** 强制要求的"空的时候要解释为什么"照旧生效**（而且**文案被迫改过一次** —— 见下）。<br>📌 判据：`app/static/js/trace.test.js::emptyTraceReason` 的三条分支 |
| 🔴 **「`/trace` 页那句『走的是 RAG 检索链、而只有 Agent 链会写轨迹』」** | ⛔ **它是【假话】**，页面文案已跟着改。🔴 **这正是"文案会过期"的一个实例**： 补上检索链的轨迹之后，那句话描述的事实**不存在了**。<br>⚠️ **页面里印假话比印"未找到"更糟** —— 看的人**没有理由怀疑它**，排查会被引到错方向。<br>⇒ 新文案给的是**两条都可能**的原因（① 那条链不建轨迹 ② 进程重启过），⛔ **不再把"检索链"说成必然**。📌 `app/static/js/trace.test.js` 那条用例的断言也跟着换了 —— ⛔ **旧断言（`/检索链/`）是那句假话的翻版，留着就是把假话钉死** |
| ⚠️ **「`record_tool_*` 找不到轨迹就是 bug」** | **不是** —— 查不到时**静默 `return`**（`if key not in _traces`）。设计如此：没建过轨迹的会话不记。⚠️ 但副作用是「**身份拼错 ⇒ 记录悄悄丢**」，⛔ 不报错 |

## 关联

- 📄 决策 ⇒  **决策 9**（本轴的归属口径与 `/agent/traces` 的语义）
- 📄 身份键 ⇒ `app/access/specs/session_key.md`（本模块的键**就是**它）
- 📄 待办 ⇒ （已修）· （同族，未修）
- 📄 端点 ⇒ `app/routing/specs/api_v1_agent.md` 的「看代码会误判」表
- 📌 判据 ⇒ `app/tests/test_trace_isolation.py`（13 例 · 全带**正向控制** · 三条证伪都做过）


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/tools/tool_visualizer.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ **内容不改写**；只清理内部编号 / 批次号这类工作日志坐标。

**两条轴别混**：本模块是**追踪**轴（进程内存）。
   `token_tracker.check_session_token_budget(user_name, thread_id)` 是**成本**轴 ——
   它**本来就带 `user_name`**，没这个病，**别去"顺手统一"它**。

**身份是必填形参、没有默认值** —— 与 `session_key`（决策 8-1）同一取向：
   **漏传要崩（`TypeError`），不是悄悄记到公共桶里。**
