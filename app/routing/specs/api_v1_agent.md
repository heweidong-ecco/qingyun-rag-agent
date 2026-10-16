# `app/routing/api_v1_agent.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **可用；流式【5 条 · 对话链全齐】**（· 第一条 ⇒ **补足剩余 4 条**）—— 5 条"会逐字生成答案"的链**全部**有 SSE 版本<br>⚠️ **其余 29 条（查询 / 管理 / 记账类）仍全非流式** —— 它们产出的**不是逐字生成的文本**（token 用量 / 工具健康 / 预算 / 轨迹 / 记忆增删），**流式对它们没有意义**。⛔ **这一条是【的判断】，没走业务裁定**（硬门 A 要的是"**该流的流**"）<br>✅ **改造收口**：本文件下方有 **实施计划 **（人工接管 · **已完成**）与 （流式与取消 · **已完成** —— // 全部落地， 的最后 4 条链于 补齐）<br>✅ ：新增 **`GET /agent/pending`** ⇒ 路由 **28 → 29**<br>✅ ：`POST /agent/approve` 增加可选参数 **`edited_answer`** ⇒ **硬门 D 三段齐了**<br>🔵 ：新增 **`POST /agent/langgraph_chat/stream`**（SSE）⇒ 路由 **29 → 30**。📄 <br>✅ ：新加的这条流式路由**补上了 cancel 传播**（关图的流 + 记数）—— §遗留·3 自己点的那个洞**已堵**。📄 <br>🔴 **（丙段）· 三条口径变了**：<br> **进图的 checkpoint 键**由裸 `thread_id` 改成 **`session_key(user_name, thread_id)`**（4 张图 · 7 处）；⚠️ **响应仍回显原值**<br> **`/agent/approve` 加了归属校验**（**本人或 admin**）+ **按登记表里的 `graph` 字段路由**；⛔ 它**不再**直接吃 `agent_graph` 写死<br> **`/agent/memory_chat` 接上审批门**（`interrupt_before=["approval"]`）⇒ ** §遗留·2 关闭**<br>🔵 **（剩余 4 条链）· 一次加 4 条流式路由** ⇒ 路由 **30 → 34**：`advanced_chat/stream`（`:889`）· `plan_execute/stream`（`:1128`）· `memory_chat/stream`（`:1311`）· `mcp_chat/stream`（`:1711`）。<br>· 同时**把原有的 `langgraph_chat/stream`（`:282`）一起改成走新共享层 `app/routing/sse.py`** ⇒ 本文件里**不再有自己的 SSE 生成器**（逐帧等价，`test_agent_sse.py` + `test_cancel_propagation.py` 全绿且未改）。<br>· ⚠️ **每条都必须保留那两道前置闸**（`check_session_token_budget` + `circuit(global_key)`）—— 有两个 AST 守卫挖的是**端点函数体内部**（`app/tests/test_session_budget_wiring.py:72` · `app/tests/test_breaker_wiring.py:88`）⇒ **闸必须在函数体里，⛔ 不能挪进共享层**。<br>· 🔴 **链 D（`plan_execute/stream`）与 A/B/C **形态不同**：`plan_task` 是**同步函数**（跑在 `asyncio.to_thread` 里）⇒ 靠 **`_ThreadTokenBridge`**（`:1003`）把 token 从线程送回事件循环；且**它只流"规划段"**，之后是**一长段静默**（`execute_plan` 不流）—— ⛔ 别当成 bug。<br>🔵 **· 5 条对话链全部接上 `chat_history` 留痕** —— 三条出口各写一个 `status`：`done` / `cancelled` / `error`。<br>· 🔴 **改前 5 条链【一条历史都不写】**（`grep -rn "append_chat_history" app/routing/api_v1_agent.py` ⇒ **0**）—— 它们的"半路状态"由 **checkpointer** 持有，而 `chat_history` 是**另一套存储**（§一 就查的这件事）。<br>· 🔴 **`done` 的答案取自【图的最终状态】（`aget_state` / `summary`），⛔ 不是 `on_complete` 收到的 `collected`**（真服务撞过的同一个坑：`calc_execute` 那种分支**一个字都不流**）。<br>· 🔴 **停在审批点（`status == "pending_approval"`）⇒ 本轮【不写】**（链 A 与链 B 各带这个 gate）—— 那时 `answer` 里是**模型已写的那半句（非空）**，不 gate 就会被写成 `status="done"`，正是本 DEC 要防的假信号。<br>📌 守卫 `app/tests/test_agent_stream_chains.py`（**60 条** · 较评审收口时 **+13**，全是留痕那几条）· 骨架 ⇒ `app/routing/specs/sse.md`<br>🔴 **· 6 个端点的初始 state 补上【身份】**：`user_name` / `thread_id` **必须进 state**（图里的记账节点靠它们才知道"这笔钱记给谁、记到哪个会话"）。<br>· 改动点：`langgraph_chat`（`:248`）· `langgraph_chat/stream`（`:414`）· `memory_chat`（`:1280`）· `memory_chat/stream`（`:1391`）—— 各加 `{"user_name": user_name, "thread_id": thread_id}`；<br>· 🔴 **`advanced_chat`（`:863`）/ `advanced_chat/stream`（`:973`）原先【只传了 `user_name`】，没有 `thread_id`** ⇒ 那两张图的账**只记得到人、记不到会话**。补齐。<br>· ⚠️ **传的是【原值】，⛔ 不是 `sess`（`session_key(...)` 那个）** —— `session_key` 是 **checkpoint 键**，与账目无关；写混了账会记到拼接后的键上。<br>· ⚠️ **缺身份不报错**：一律 `.get(…, "unknown")` 读 ⇒ 静默记成 `"unknown"`（⛔ 不是 500）。<br>📄；📌 判据 ⇒ `app/tests/test_billing_wiring.py`（`ENDPOINTS` 6 条逐个查初始 state 有没有那两个键）<br>🔴 **（批 7）：9 条端点接上【图内拦截的出口形状】**—— 4 条非流式 ⇒ **429**（判在 `summarize_agent_result` 之前，⛔ 不许 `register`）· 4 条流式 ⇒ **error 帧 + `[DONE]` + `persist_turn(status="error")`**（⛔ 不发汇总帧）· `/agent/approve` ⇒ **补 + 两道门**（用调用方过门）+ **两个 `invoke` 都认标志**（命中先 `resolve` 再 429）。<br>⚠️ **改前这 8 条非-approve 端点会回 HTTP 200 + 一句"今日Token预算已用完"当答案** —— 调用方**看不出被拒了**。<br>⚠️ **`/agent/mcp_chat` 那条 429 的文案变了**—— 因为新增的软返回**根本没有工具调用**。📄 §三<br>🔵 **（接管页）· 一次加 2 条端点 ⇒ 路由 34 → 36**：<br>· 🆕 **`GET /agent/pending/context`（`:763`）** —— 待接管会话的**完整上下文**（`messages` 序列原样 + `owner`/`graph`/`rounds`/`next`）。可带**可选 `owner`**（裁定 6）收窄撞车的 `thread_id`；**多条候选 ⇒ 如实拒绝**，⛔ 不"挑第一条"。<br>· 🆕 **`GET /agent/approvals/history`（`:2021`）** —— 裁决历史（读 `app/agent/approval_audit.py` 的 `approval_events` 表）。⚠️ **与 `/agent/pending` 同一条可见性口径**：本人默认、admin 全量。<br>· 🔴 **`GET /agent/pending`（`:702`）的可见性变了** —— 此前**跨用户全量**，现在**本人默认 · admin 全量**（缺口）。⚠️ **收窄发生在【端点里】（两行过滤），`list_pending` 一行没动** —— 见 `app/agent/specs/pending_approvals.md`。<br>· 🔴 **`POST /agent/approve`（`:439`）加了两样**： 可选 **`owner`**（用来收窄撞车的 `thread_id`，**收窄 ≠ 授权**） 每次**真裁决**写一条留痕（`record_decision`，fail-open）。⚠️ **留痕写在那道"队列登记陈了"的守卫【之后】** ⇒ 它是**第四条不记的出口**（见「看代码会误判」表）。<br>· 📄 设计 ⇒ · 施工 ⇒ `qingyun-rag-agent-TODO待办/-20261006-接管页.md` · ⚠️ ** ⛔ 不等于硬门 D 翻 ✅**（要照四硬门原文逐栏对）<br>🆕 **（Trace 页）· 加 1 条只读端点 ⇒ 路由 36 → 37**：<br>· 🆕 **`GET /agent/trace/{thread_id}/cost`** —— 成本轴（`token_usage_logs`）的逐笔明细 + 整条线程合计。⚠️ **它与 `/agent/trace/{thread_id}`（追踪轴 · 进程内存）是【两条轴】**，页面上并排画、**⛔ 不合并**（没有共同的步 id）。<br>· 🔴 **0 条回 200，⛔ 不是 404**（`thread_id` 是用户自己填的）；**取数⛔ 不复用 `get_thread_cost`**（它没有归属条件）。📄 §三·A/§三·A' |
| **对外提供** | **36 个路由**（含 **5 条 SSE**：`/agent/langgraph_chat/stream` · `/agent/advanced_chat/stream` · `/agent/plan_execute/stream` · `/agent/memory_chat/stream` · `/agent/mcp_chat/stream`；其余 `/agent/approve` · `/agent/pending` · `/agent/pending/context` · `/agent/approvals/history` · `/agent/token/*` · `/agent/cost/*` …）· `summarize_agent_result()`（`:131`） |
| **谁在用** | 前端（🟡 **接管页已做** ⇒ `app/static/web/approvals.html`；对话页见 `app/static/specs/static_frontend.md`）· `test_public_paths.py` 等 · `app/tests/test_agent_stream_chains.py`（**60 条**，覆盖 4 条新链 + 三条出口的留痕） |
| **规模** | **2119 行**（口径 = **本仓口径**；`wc -l` 报 **2118** —— 本文件**末行没有换行符** ⇒ 少算 1，⛔ **不是笔误**，同 `agent_graph.py`）<br>⚠️ **别抄这个数** —— 它已被重取过**十次**：743 → 835 → 875 → 894 → 1006/1007 → 1552/1553（4 条新端点）→ 1692（`060`/`062` 累计）→ 1713/1714（6 处身份注入 +22 行）→ **2118/2119**（接管页：两个新端点 + 留痕接线 + 可见性收窄，+405 行）。<br>🔴 **更正**：上一版这里写的 **1553** 是 **/`060`/`062` 之前**的值，**一直没跟上** ⇒ 本条所谓"当天重取六次"对后三批改动**失效**。<br>🔴 **教训**：**这个数每批都会涨**，而上一版把它写进正文时**没有跟着重取行号**（同一个「抄了个会过期的数」的毛病）⇒ 见上方行号口径块。 |

> ✅ **行号口径（第二次全量重取 · 上一次 = ）**
>
> 本文件从 743 行长到 **2119 行**（十次重取）⇒ 正文里大量 `:NNN` 曾**整体下移**。
> **上一次**的实测反例：`:473` 指向 `pending_calls,`、`:843` 指向一个三引号、
> `:1198` 指向一个括号 —— 而它们**都声称**指向 `/stream` 路由。
> 🔴 **同一族又栽了一次**： 这一批（接管页）往本文件加了 **~400 行**（1714 → 2119），
> 而**没有任何一道门会发现**上一版那 35 处 `:NNN` 已经全部指偏 —— 是**收口时自己发现的**
> （判据：拿本文件里的每个 `:NNN` 去 `sed -n "${n}p"` 看**那一行真的是不是它**）。
> **两次都是逐条重取**，⛔ 不是按行数差做算术 —— **改动落进本文件 ⇒ 必须再重取一次**。
>
> 🔴 **本文件里的行号现在分两个口径，读之前先分清**：
> 1. **状态栏 · `## ✅ 做了什么` · `## 🟡 做到哪` · `## ⚠️ 看代码会误判` · `## 关联`**（= `🔵 实施计划 ②` **之前**的整段）= **描述【现在的代码】** ⇒ **已重取为 的真值**。
> 2. **`🔵 实施计划 ② / ③` 段落里的** = **计划 / 施工【当时】的快照** ⇒ **有意保留原样**（那是一份历史记录，改它等于篡改当时的账）。
> ⚠️ **判据是句子的时态，不是它在哪一段**：凡句子里写着「**现在 / 现状 / 已换成 / 现在的实际**」的，都在本次重取之列 —— 本次因此动了 勘误表（「本节写的 / **现在的实际**」）· 那句「🔴 现状」· 的「四条链」端点表。
>
> 📌 **要用行号请自己重取**（⛔ 别信任何一份文档里的数）：
> ```bash
> grep -n '^@router\.\(post\|get\)' app/routing/api_v1_agent.py          # 路由当前行号
> grep -n '"user_name": user_name' app/routing/api_v1_agent.py          # 身份注入点
> # ⭐ 最省事的那条：把本文件里每个 :NNN 都拿去 sed -n "${n}p" 对一眼（两次重取都用的它）
> sed -n '1,173p' app/routing/specs/api_v1_agent.md | grep -o ':\([0-9]\{2,4\}\)'
> ```
> 📄 上一次重取的来龙去脉 ⇒  §七 / §九 · 这一次 ⇒  §二·发现③ 那一批。

## ✅ 做了什么

- **对话链**：`langgraph_chat`(:212) · `advanced_chat`(:835) · `plan_execute`(:1074) · `memory_chat`(:1245) · `mcp_chat`(:1627)
  · 🔵 **B8**：这 5 条**全部接上会话级 token 上限**（`check_session_token_budget`），触顶抛 `QUOTA_EXCEEDED`
  · 🔵 ** 剩余 4 条链**：同一批端点**各配一条 SSE 版** ⇒ `.../stream`（`:889` / `:1128` / `:1311` / `:1711`），
    与 `langgraph_chat/stream`（`:282`）**共用 `app/routing/sse.py`**。⚠️ **四条链的"可流节点名单"各不相同**，
    且**住在各自的图模块里**（`STREAMABLE_NODES`）—— ⛔ 端点不许抄字面量（的教训：一个名字两个来源必然**静默**漂移）
- **人工审批**：`POST /agent/approve`（`:439`）—— 批准 / 拒绝 / **改写后提交**，靠 `agent_graph` 的 `interrupt_before`
  · 🔵 **改写后提交（· · 🔴 口径 由 修正）**：可选参数 **`edited_answer`（`:443`）**。
    **批准 ∧ 给了改写** ⇒ 先 `update_state` 推入**一组 `ToolMessage`**（每个卡住的 `tool_call_id` 一条，`_tool_rulings()`）
    **并显式传 `as_node="tools"`**，再 `invoke(None, config)` 续跑；
    **不给** ⇒ 走原来的 `update_state(values=None)`（行为与改动前一致）；**拒绝** ⇒ 给了也忽略（措辞不同）。
    🔴 **改前推的是 `AIMessage`** —— **那是错的**（理由见「看代码会误判」表那条）。
  · 🔴 **返回第三态 `status="pending_approval"`** —— 批了/拒了，但模型**又要**一个敏感工具 ⇒ 图**再次**停在审批点
    ⇒ 这里**重新登记**（⛔ 不再无条件注销）。调用方按**与首次触发相同**的方式再走一遍本接口。
  · 🔴 **轮次上限（· 作者裁「上限 3 轮」）** —— 上面那条"重新登记"**自带封顶**：
    登记时记 `rounds`（首次 **1**，每"放行后又停" **+1**）；到上限（**`approval_round_cap()`**，默认 **3**，
    env **`MAX_APPROVAL_ROUNDS`**）就**不再登记** ⇒ 注入一条"已达上限、请直接作答"的 `ToolMessage` 并续跑：
    · 收尾成功 ⇒ `status="approved"/"rejected"` + **`forced_finish=True`** + `rounds`
    · 收尾不住（模型仍要敏感工具）⇒ `status="error"` + `rounds`，该轮终止、⛔ **不入队**
    ⚠️ 没有它：模型可**无限**要求敏感工具、人工得无限批（实测 3 次收敛，但**没有机制阻止 30 次**）。
  · ⭐ **续跑形状被测试钉住**：`app/tests/test_approval_resume.py`（**16 条** · **纯离线 · 进 CI**）——
    `invoke` 必须是 **`None`**（= 从 checkpoint 继续，⛔ 不是新开一轮）、`config` 必须是**请求里那个 thread_id**
    · §⑤ 六条是**真图 + 假 LLM**，钉「state 里没有孤儿 `tool_calls`」/「模型真的被叫醒」/「又停下就重新入队」
    · 🆕 §⑥ 四条钉**轮次上限**：首次 = 1 · 每次 +1 · 触顶强制收尾（`forced_finish`）· 收尾不住则 `error` 且不入队
  · 🆕 **留痕 + 可选 `owner`（· ）**：
    · **`owner`（`:444`）是【收窄】不是【授权】** —— 它在 `find_by_raw_thread_id(thread_id)` **之后**过滤候选（`:505`），
      解决"两个人用同一个 `thread_id`"的撞车；**授权仍是那条"本人或 admin"（`:527`）**。⚠️ **收了窄就批不了别人的**（本意）。
    · **每次真裁决写一条** `record_decision(...)`（`:578`）：`owner`（**会话属主**，取 `owner_name` `:522`）/ `actor`（**动手的人** —— admin 接管时**必然不同**）·
      `decision` · `edited` · `rounds` · `reason`（工具摘要，`summarize_tool_calls`）。落到 **`approval_events`**（`app/agent/approval_audit.py`）。
      🔴 **两道 fail-open，⛔ 不是重复**：① `record_decision` **函数体内**兜住（写库失败不影响裁决）
      ② **调用点**再包一层 `try/except` —— 因为**参数绑定的失败发生在进函数之前**。
      📄 为什么：**"留痕写不进去"不许把"人已经批了"这件事一起弄丢**。
    · ⚠️ **它是【第四条不记的出口】**（见「看代码会误判」表那条"队列登记陈了"）。
    · 📌 判据 ⇒ `app/tests/test_approve_audit_wiring.py`（12 条，**假 `record_decision` 记下 `seen`** 再断言关键字）
- 🔵 **待接管队列（· ）**：新增 **`GET /agent/pending`** —— 列出**当前在等接管的会话**
  （事实来源 = 新模块 **`app/agent/pending_approvals.py`**，⛔ **不是从 checkpoint 反查** —— `MemorySaver` **没有"列出全部 thread"的 API**）。
  `langgraph_chat` 在拿到 `summary` 后**登记 / 注销**；⚠️ **`approve_agent_action` 改前【每条 return 前】都注销，起不是了** ——
  **只有图真的走完才 `resolve()`**（又停下 ⇒ 重新登记；⛔ 无条件注销会造**孤儿会话**，见「看代码会误判」表）。
- 🆕 **接管页的两个读端点（· ）**：
  · **`GET /agent/pending/context`（`:763`）** —— 给一条待接管会话**完整上下文**：`owner` / `graph` / `rounds` /
    `next`（停在哪个节点）/ **`messages` 序列原样**（`_serialize_messages` 归一成一个纯 dict 序列）。
    ⚠️ **它是"点开一行才调"的**（页面轮询只打 `/agent/pending`）—— 队列每条都带全部消息会让轮询白扛一大坨。
    · 候选来源 = `find_by_raw_thread_id(thread_id)`；可带**可选 `owner`（`:789`）**收窄；
    · 🔴 **`len != 1` ⇒ 如实拒绝**（0 条 / 多条**都**拒）—— ⛔ **不"挑第一条"**：那等于**随机**给一个人看别人的会话；
    · 🔴 **越权与"真不存在"答同一个 `error`**（有意的，否则那句错误本身是"该 thread 存在"的 oracle —— 与 `/agent/trace/{id}` 同口径）；
    · ⚠️ **四类拒绝全是 HTTP 200 + `{"status": "error"}`** —— 前端必须**读 body**，⛔ 别只看 `r.ok`。
  · **`GET /agent/approvals/history`（`:2021`）** —— 裁决历史（读 `app/agent/approval_audit.py` 的 `list_decisions`）。
🔴 **：这条端点【分页了】（`limit` + `offset` + 响应回 `has_more`）** ——
分页前它**硬写 `limit=50` 且界面不说明被截了**（静默截断）。
⚠️ **`has_more` 的判法是「多取一条」**：向 `list_decisions` 要 `limit+1`，多要的那条**只当探针、不返回**。
🔴 **⛔ 不许让前端拿 `count == limit` 猜** —— 那在"正好一整页、后面没有了"时
会显示一个**点不动的下一页**，而且不报错。<br>🔴 **这条是老规矩，口径没变**（原出处是旧的那份设计系统规范 §六 红线②，该文件 已删）—— 本仓用**门**钉着 ⇒ `app/tests/test_truncation_declared.py`。
    ⚠️ **`owner` 是【必填关键字参数】**（那条口径：⛔ 不给默认值 ⇒ "我忘了传"当场 `TypeError`，
    而不是静默退化成"查全量"）。端点按角色决定传谁：**本人传自己 · admin 传 `None`（= 全量）**。
- 🆕 **Trace 页的成本轴端点（· ）**：**`GET /agent/trace/{thread_id}/cost`** ——
  一条线程的**逐笔**花费明细（`items`）+ **整条线程的合计**（`total`）+ `truncated`。
  · **只读**；取数走 `token_tracker.thread_cost_breakdown(user_name, thread_id, include_all=…)`（**既有模块加函数，⛔ 没新建模块**）。
  · 🔴 **`include_all` 由端点按角色给**（`:2122` 那一行 `get_user_role(user_name) == UserRole.ADMIN`）—— 本文件的老口径，与 `/agent/traces` 一致。
  · 🔴 **0 条回 200 + 空 `items`，⛔ 不是 404** —— `thread_id` 是**用户自己填的**，404 会把"这条线程没花过钱"说成"这条线程不存在"；而且它与 `/agent/trace/{id}`（**有** 404 语义）是**两条不同的轴**。
  · 🔴 **⛔ 别复用旁边的 `get_thread_cost(thread_id)`** —— 它没有归属条件，看着像能复用，实为越权。理由见 `app/billing/specs/token_tracker.md`「看代码会误判」。
  · ⚠️ **页面画它、但⛔ 不与上半页（追踪轴）合并** —— 两轴没有共同的步 id，见  §二。
  · 📌 判据 ⇒ `app/tests/test_trace_cost.py`（15 · 假 pg）· `app/tests/test_trace_cost_db.py`（9 · `needs_db`）· 页面侧 `app/tests/test_trace_page.py`（7）
- **⭐ `summarize_agent_result()`（`:131`）** —— 把图的运行结果翻成 `{"status": "pending_approval"/"answered", …}`，
  并**把模型已写出的文字一并返回**（真实 LLM 常"先说一句再调工具"）
- **预算**：`check_budget` 依赖（`:1583`，抛 `AppException(QUOTA_EXCEEDED)`）· 6 个 `/agent/token/*` 查询路由
- 🔴 **图内拦截 → 端点的【出口形状】（批 7 · ）** —— **9 条端点**：
  · **非流式 4 条**（`langgraph_chat` · `memory_chat` · `advanced_chat` · `mcp_chat`）：拿到结果后判
    `result.get("budget_intercept")` ⇒ **非空 ⇒ `raise AppException(QUOTA_EXCEEDED, …)` = 429**。
    ⚠️ **必须在 `summarize_agent_result()` 之前判** —— 被拦那轮**没停在审批点**，⛔ **不许 `register`**
    （否则队列里多一条**永远批不了**的假待办）。
  · **流式 4 条**（各自的 `/stream`）：收尾处 `aget_state` ⇒ `values.get("budget_intercept")`
    发**一帧** `{"error": msg}` + `[DONE]` + `persist_turn(..., status="error")` + `return`（⛔ **不发汇总帧**）。
    ⚠️ 响应头**已经发出去了**（HTTP 200 + `text/event-stream`）⇒ **状态码改不了**，只能发 error 帧（与 `/agent/plan_execute/stream` 同口径）。
  · **第 9 条 `/agent/approve`**：**既补门**（+，进门处、**并列都要过**）**又补出口形状**
    （它的**两个** `invoke` 都认标志；命中 ⇒ **先 `resolve(sess)` 再 429** —— 被拦那轮**图真跑完了**，
    不注销就留一条假待办）。⚠️ **门用【调用方】`user_name`**（⛔ 不是属主）—— 与其余端点一致（谁发请求谁被限）。
  · 文案由一个**模块级纯函数** `agent_budget_intercept_message(why)`（`:101`）统一拼 —— ⛔ 别在节点里拼整句。
  · 📄 裁定；📌 判据 ⇒ `app/tests/test_budget_soft_return.py`（21 例，**变异自证 27/27**）
- 🔵 **成本可见两处（`①b` · ）**：
  · **`/agent/token/budget`（`:1596`）** 补上**全站日级**三个字段（`global_daily_limit` / `global_used_today` / `global_remaining`）——
  在此之前 / 的全站额度**只有入口没有出口**，超了所有人吃 429 却**界面上看不到逼近**；
  · **`/agent/cost/overview`（`:1905`）** 数据源从 **`get_user_summary`（进程内存）** 换成 **`get_user_overview`（读库）**，
  `by_purpose` 随之从**全站**变**本人**
- 🔵 **Agent 端 SSE —— 5 条（· ；补足 4 条）**：
  `POST /agent/{langgraph_chat, advanced_chat, plan_execute, memory_chat, mcp_chat}/stream`
  · **帧格式**（A/B/C 三条一致）：逐 token 一个 `data: {"content": "..."}`，最后一个是 `data: {…summary}`（含 `thread_id`/`status`/`answer`），
    再 `data: [DONE]`；出错则 `data: {"error": "..."}` + `[DONE]`。
    ⚠️ **链 D 是唯一例外**：流里是**正在生成的 JSON 片段**（`plan_task` 的输出被提示词要求是严格 JSON）
    **⛔ 前端不许把流到的文本直接渲染成计划**，只当"规划中"指示器；**终稿只看最后一帧**。
  · **它真流式靠的是【图模块】那一侧**（节点声明 `config` + 转发 `.stream/.astream(config)`）——
    ⛔ **本文件这边只负责"转发"**，接线错了接口**照样长得像流式**。📄 见 `app/agent/specs/agent_graph.md` 等 4 份
  · ⚠️ **每条链的接线各不相同，别互相照抄**：链 A 必须 **`subgraphs=True`**（5 个子图，不开**一个字都流不出来**）；
    链 C 的两个节点是 **`async`** ⇒ 图侧用 `astream`；A/B 的节点是**同步**的 ⇒ `stream`；
    链 D **不是图**（同步函数 + `asyncio.to_thread`）⇒ 靠 `_ThreadTokenBridge`（`:1003`）。
  · ⚠️ **两条前置闸**（`check_session_token_budget`  + `circuit(global_key())`）与各自**非流式版本同源**，
    且**必须在端点函数体里** —— 两个 AST 守卫挖的就是函数体内部（见 `sse.md`）。
  · ⭐ **判据**（可打印 · 全离线 · 进 CI）：`app/tests/test_agent_sse.py`（**15 例** —— 原 **12**，评审收口补了 3 条汇总帧 `requested_by`）· `app/tests/test_cancel_propagation.py`（**22 例**，其中 Agent 段 **7** 条）·
    🆕 `app/tests/test_agent_stream_chains.py`（**60 例** · 覆盖 4 条新链：逐 token / `aget_state` 汇总 / 断连关上游 / A 链白名单 /  留痕 13 条）
  · ⚠️ **`X-Accel-Buffering: no` 是必须的** —— 少了它，Nginx / Cloudflare 会把整段缓冲住 ⇒ **又变回假流式**（本仓要上 Cloudflare 隧道）。
    ✅ 现在它在共享层 `app/routing/sse.py` 的 `SSE_HEADERS` 里，**5 条一起有**
- **工具**：`/agent/tool_health` · `/agent/tool_versions` · `/agent/available_tools` · `/agent/mcp_tools_dynamic`
- 🔴 **身份注入**：6 个对话端点的**初始 state**里补上 **`user_name` / `thread_id`** ——
  图里的记账节点靠这两个键才知道"钱记给谁、记到哪个会话"。⚠️ **传【原值】**，⛔ 不是 `sess`（见顶部状态栏）。
  ⚠️ **改前 `advanced_chat` 那两条只传了 `user_name`** ⇒ 账记不到会话。
  📌 判据 ⇒ `app/tests/test_billing_wiring.py`（AST 查 6 条端点的 `initial_state` 关键字）

## 🟡 做到哪 / 缺什么

- ✅ ~~**29 个路由全非流式** ⇒ **硬门 A 的缺口**~~ ⇒ **【对话链全部补齐】**：
  5 条"会逐字生成答案"的链**全部**有 SSE 版（`/agent/langgraph_chat/stream` 于
  其余 4 条于 ）。⚠️ **剩下的 29 条是非流式，但它们不是"缺口"** ——
  它们是**查询 / 管理 / 记账类**（token 用量 · 工具健康 · 预算 · 轨迹 · 记忆增删），
  **产出的不是逐字生成的文本** ⇒ 流式无意义。⛔ **这句是【本批的判断】，没走业务裁定**（若作者认为还有该流的，这条要改）。
  ⚠️ **别只看条数**：`grep -c '^@router' app/routing/api_v1_agent.py` ⇒ **36**，其中 **5** 条带 `/stream`。
  硬门 A 要的是"**该流的流**"，⛔ **不是"流了几条算完"**。📄
  · ✅ ~~⚠️ **（cancel 传播到上游）**~~ ⇒ **【已做】** ——
  客户端断开 ⇒ 关掉图的流（`aclose()`）+ 记 `stream_cancelled_total`。📄
  · ✅ ~~**（半截答案怎么处理）【仍未做】**~~ ⇒ **已裁**：「**存**」，
    提问 + 半截 + 中断标记。⚠️ **Agent 端这边不用改** —— 它的半路状态由 checkpointer 持有
    · 🔵 **更正**：**"Agent 端这边不用改"这句话只在 那个口径下成立** ——
      它说的是"**取消时的半路状态有人存**"（确实有，在 checkpoint 里）；
      ⚠️ 但 **checkpointer 与 `chat_history` 是两套互不相通的存储** ⇒ 要求变成「**要一条人/脚本能读的对话历史**」时，
      Agent 链**一条都没有**。那是**另一件事**， 已补：**5 条链全接 `chat_history`**。
- ✅ ~~🔴 **没有「待接管队列」端点**~~ ⇒ **【有了】**：`GET /agent/pending`。
  ⚠️ **但队列背后是【进程内存】**（`app/agent/pending_approvals.py`）⇒ **重启即空** —— 见其 spec 里那条"已知限制"
  · 🔴🔴 **（缺口③）· 可见性收窄** —— 它**此前是跨用户全量**（谁调都看到所有人的待办，
    连同 `user_name`），现改为 **本人默认 · admin 全量**（`get_user_role(...) == UserRole.ADMIN` 显式一行）。
    ⚠️ **收窄落在【端点里】，`list_pending()` 一行没动** —— 见 `app/agent/specs/pending_approvals.md`。
    🔴 **代价（知道再选）**：**admin 才看得到全量**，而"接管页"本身没有管理员以外的用法时，
    这个收紧**看起来没变化** —— 它挡的是**直接拿 key 打 API 的非 admin**。
- ✅ ~~**（接管后续跑）未做**~~ ⇒ **【已做】**：`edited_answer` 改写后提交 + 续跑形状被 `test_approval_resume.py` 钉住。
  ⚠️ **但只是"接线与语义"层** —— **真跑一遍"上下文确实连续"（真 LLM + 真 MemorySaver）没有测**，
  那需要联网花钱（见该测试文件的 docstring：本文件测的是**接线**，不是模型质量）。
- 🔴 **`/agent/approve` 的参数是 query 不是 body**（`:441-444`，**含新的 `edited_answer`**）⇒ 前端联调会踩
- 🔴 **`/agent/cost/overview` 的三个总数曾经是【进程内存】**（修）——
  它**不报错、界面照常出数**，只是**重启后答 0**（实测库里有 4216 tokens、它答 0）。
  ⚠️ **同族的仍在**：`/agent/token/overview` · `/agent/thread/{id}/overview` · 看板第 2 格
  **都是内存口径**（语义 = "本进程"，**有意保留**，⛔ 别当 bug 删）
- ⬜ 零散的 `/agent/token/*` 与 `/agent/cost/*` 有重复嫌疑（**未核**；⚠️  已把口径说清：
  `/agent/token/budget` = **今天 + 还剩多少**，`/agent/cost/overview` = **全时一共**，两者**不重复**）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴🔴 **「`/agent/langgraph_chat/stream` 的结尾那个 `summary`，把流过 `agent` 节点的块攒起来算就行」** | ⛔ **不行，会算错**（**真服务**撞）。<br>**攒块 = 跨多轮累积** —— 模型因工具返回"未找到工具"而**重试**时，`agent` 节点会进**多次** ⇒ 攒出来的东西带着**上一轮的** `tool_calls` ⇒ `summarize_agent_result` 报 **`pending_approval`，而图其实已经跑完**（前端会**永远等一个不会来的审批**）。<br>⚠️ 实测症状：`tool_calls` 的 name 被**拼接**成 `"date_todayduckduckgo_search"`。<br>🔴 ：那次"反复重试"的**根因已修**（`tool_execute` 原来是按字面量 `"search"` 分派、真名是 `duckduckgo_search`）⇒ ⚠️ **但本行仍照旧成立** —— 只要端点还在拿流式块猜，**任何**多轮场景都会重演。<br>✅ **正确做法：从图的最终状态取** —— `await agent_graph.aget_state(config)` ⇒ `summarize_agent_result(state.values)`，与 `/agent/langgraph_chat` **完全同一套语义**。<br>📌 判据（可打印）：`app/tests/test_agent_sse.py::test_status_comes_from_final_state_not_from_streamed_chunks`（`_TwoRoundModel` 逼出第二轮） |
| 🔴 **「这条流式端点没有 cancel 处理」** | ✅ **【有了】** —— 客户端断开后**关掉图的流**（`finally` 里 `await stream.aclose`），并记 `stream_cancelled_total{endpoint="agent_langgraph_chat_stream"}`。<br>⚠️ **不关的代价是"图继续跑完"** = 继续调模型 = **继续烧钱**，而前端看起来一切正常（它只是不显示了）。<br>⚠️ **本轮之前这里确实是空的** —— 而且 §遗留·3 **自己点了名**。<br>🔴🔴 **补**：`finally` 里的**顺序**与**关流方式**当天下午被真服务推翻过一次 —— `await stream.aclose` **会被二次投递的取消打断**，排在它后面的收尾**一件都不跑**（本端点症状：**计数不涨、`[cancel]` 日志没有**）。<br>✅ 现在：**同步收尾提到任何 `await` 之前** + 关流包 `anyio.CancelScope(shield=True)`。⛔ **别改回去**（"关流是收尾动作、放最前面才干净"正是那个坏改法）。<br>✅ **更正**：本行原写「本端点取消时**一条历史都不留**」—— **不再成立**，
现在取消 / 异常两条出口**都会**往 `chat_history` 写一条带 `status` 的半截。⚠️ **它与 `/rag/stream_search` 的差别换了一条**：
RAG 那条的骨架**没有图**，答案只能从 `collected` 取；Agent 这边 `done` 的答案**取自图的最终状态**（见顶部 🔵 块）。<br>📌 判据（可打印）：`app/tests/test_cancel_propagation.py` ⇒ **22 passed**（RAG  · Agent）；真服务 = 计数 +1 且日志有 `[cancel]` 行 |
| 🔴 **「客户端断开得靠 `request.is_disconnected()` 自己轮询」** | ⛔ **不用，那是框架给的**（uvicorn 报 `spec_version 2.3` ⇒ Starlette 监听 `http.disconnect` 后**取消生成器**）。<br>⇒ 真正的缺口只有「**停下并关掉上游**」这一件；**自己加轮询 = 多余，且会掩盖真缺口** |
| 🔴 **「中间件日志里那个秒数 = 这条流的生成耗时」** | ⛔ **不是** —— 它记到**响应开始返回**为止。实测：`(0.019s)` 的那条客户端收了 **27KB**、`(0.004s)` 的那条 **3 秒后**才 cancel。<br>⇒ ⛔ 别拿它当"生成提前停了"的证据（第一版就这么误读过） |
| 🔴 **「`status=answered` 就是拿到最终答案了」** | ⚠️ **要看 `status`** —— 返回 `pending_approval` 时 `answer` 里是**模型"先说的一句"**，**工具还没执行**。`summarize_agent_result` 的 docstring（`:132-157`）专门讲了这点：**不能加 `and not content`**，否则这种形态会被**误报成 `answered`**。⚠️ **该 docstring 还写了这条判据"依赖什么、什么时候会失效"**（B4 后理由变了）—— 改图的路由时**要回去重看** |
| ⚠️ ~~🔴 **「审批已经能用了，硬门 D 算完成」**~~ | ✅ **改了口径**：**触发条件不再是「任意 `tool_calls`」**，而是**工具白名单**（`agent_graph.py` 的 `SENSITIVE_TOOLS`）⇒ **问个日期不再进审批**。<br>🔴 **但白名单里的名字当时写错了**（`search_tool` 是**变量名**）⇒ **交集恒空 ⇒ 审批其实【永不触发】**，**直到 才由 修掉**。⇒ ⚠️ **"改了口径"与"口径真的生效"是两件事** —— 前者当天就成立了，后者晚了三天。<br>✅ **（3）： 队列 与 续跑都【已做】** ⇒ **三段（什么时候停 / 停在哪看得到 / 批了怎么接着跑）齐了**。<br>⚠️ **但"齐了"≠"验收过"**： 只钉了**接线与语义**（`invoke(None)` + `edited_answer` 进 state），<br>**"上下文真的连续"没有端到端跑过**（要真 LLM + 真 `MemorySaver`，**联网花钱**）⇒ 验收演示时**要补那一步**。<br>🔴 **补上了 —— 一跑就【不通过】**：真服务跑三条出口，**证真① 过、证真② 不过**；三条出口（改写放行 / 原样放行 / 拒绝）**每一条都会把会话弄坏**（同 thread 再问 ⇒ **500**）。⚠️ **而当时单测 21 条全绿** —— 因为假图**不校验消息结构**、假 `invoke` **不会有"下一轮"**。<br>✅ **同已修**（A+B+C）⇒ 复跑 **22/22**。🔴 **教训写在 **：**硬门 D 的证真② 只有真服务跑得出来**（与 的"幽灵判据"同源：**以为门上挂着锁**）。<br>⚠️ **硬门 D 仍不标 ✅** —— 它的演示/反例里含**界面**（「点开后能看到完整上下文」「界面上找不到」），前端未开工。<br>🔵 **更新**：**界面那一块【做了】**（`app/static/web/approvals.html` + `GET /approvals` → 302），
   且后端三个缺口（上下文端点 / 留痕 / 可见性）一并补上。<br>🔴 **但硬门 D 依然【不】因本批翻 ✅** ——  §六·6 明写：**验收要照
   `qingyun-rag-agent-TODO待办/通用/四硬门-定义与验收标准.md` 硬门 D 的四栏原文逐条对**，⛔ 不是"这单跑完就算"。
   （本 spec 里的行文只是**实现记录**，验收结论不在它这儿。）<br>⚠️ **本条 之前写的是旧口径**，⛔ 别照旧理解 |
| ⚠️ **「`/agent/approve` 收 JSON body」** | ⛔ **不是** —— `thread_id` / `approved` / **`edited_answer`** **都是 query 参数**（`:441-444`） |
| 🔴🔴 **「`/agent/approve` 拿 `thread_id` 就能批」** | ⛔ **（丙段）起不能了** —— 它现在**先从待接管队列反查属主**，再判**本人或 admin**。<br>⚠️ **改之前它没有任何归属校验** —— 任何人拿一个 `thread_id` 就能**批准并续跑**那个会话。<br>🔴 **为什么不能"按调用方拼"**：`/agent/pending` 是**跨用户队列**（硬门 D）⇒ 按调用方拼，admin 会拼出 `admin:…`、属主是 `alice:…` ⇒ **admin 永远批不了别人的**。<br>⚠️ **代价（知道再选）**：**队列是唯一入口** ⇒ `AGENT_CHECKPOINT_BACKEND=sqlite` 重启后（图在盘上、队列在内存）会答"没有待审批任务"，而以前能批。📌 判据 ⇒ `app/tests/test_approve_ownership.py` |
| 🔴 **「`/agent/approve` 只认 `agent_graph`」** | ⛔ **两张图** —— `/agent/langgraph_chat` 走 `agent_graph`、**`/agent/memory_chat` 走 `checkpointer_agent`**（丙段给它加了审批门）⇒ approve **按登记表里的 `graph` 字段路由**。<br>⚠️ 写死 `agent_graph` 的后果：memory_chat 那条会话**永远放行不了**（**门关了却没有钥匙**，比不加门还糟）。作者 裁。📌 判据 ⇒ `app/tests/test_memory_chat_approval.py::test_approve_routes_to_checkpointer_graph` |
| 🔴 **「`memory_chat` 没有审批门」（的口径）** | ✅ **丙段起有了** —— `checkpointer_agent` 带 `interrupt_before=["approval"]`，敏感工具会停下。⚠️ **输出形状也变了**：新增 `status` / `pending_tool_calls`（与 `/agent/langgraph_chat` 一致，⛔ 不再返回 200 + 空答案） |
| ⚠️ **「`thread_id` 拼身份 = 全仓统一拼法」** | ⛔ **不是** —— checkpoint 轴用 `session_key()`（**长度前缀**，无歧义）；而 **`:1416`（`add_memory`）与 `:1432`（`search_memory`）那两处 `f"{user_name}:{memory_space}"`** 仍是**朴素拼接**（含 `:` 会有歧义）。⚠️ 两条轴**各有各的拼法**，丙段**只动了 checkpoint 那条** |
| 🔴 **「`edited_answer` 就是"把答案改一下再返回"」** | ⛔ **不止** —— 它**先写进 graph state**，**再从 checkpoint 续跑**。<br>⚠️ **差别在哪**：审批之后图**还要去 `agent`** ⇒ 只把改写当返回值吐出去，**后续节点看不到它**（改写等于没改） |
| 🔴🔴 **「留痕字段 `edited=true` = 人工改写了一次回答」** | ⛔ **不是** —— `edited` 的真实语义是「**代替模型给出了这次工具调用的结果**」（§一 裁定 4 明写：界面上的原话是「**代替模型执行这次工具调用**」）。<br>⚠️ **为什么容易读歪**：字段名 `edited` 谁都认得，而它修饰的**不是答案**，是**那条 `ToolMessage`**。<br>⚠️ **它由 `bool(approved and edited_answer is not None)` 算出来**（`:582`）—— **拒绝时永远 `False`**，哪怕调用方**真的传了** `edited_answer`（拒绝的语义是"别做了"，传了也忽略）。⛔ 别把它当"用户改过答案没有"。 |
| 🔴 **「每次调 `/agent/approve` 都会留一条痕」** | ⛔ **不会** —— 留痕**只记"真的落到图上的裁决"**，而它有**四条不记的出口**（前三条  §3.2，第四条见下）：<br>① `thread_id` 对不上任何登记 ⇒「当前没有等待审批的任务」· ② **多条候选**（撞车）⇒ 如实拒绝 · ③ **归属校验不过**（不是本人也不是 admin）⇒「无权审批」·<br>④ 🔴 **队列登记陈了**（`:554` —— **归属校验已过**，但 `get_state().next != ("approval",)` ⇒ 顺手 `resolve` 并答"没有等待审批的任务"）。<br>⚠️ **第 ④ 条是本批（具体化 C）才发现要补写的**：它**发生在归属校验之后**，所以最容易被当成"一次真裁决"记下来 —— 而那是一条**假留痕**（照它统计会得出"某人批了很多次，但其实现场什么都没发生"）。<br>⇒ **判据是位置**：`record_decision`（`:578`）**必须**排在那道 `return`（`:558`）**之后**。📌 守卫 ⇒ `app/tests/test_approve_audit_wiring.py` 里那条"图没停在审批点 ⇒ 不写留痕"。 |
| 🔴🔴 **「改写/拒绝时往 state 里塞 `AIMessage` 就行」**（**改前 spec 就是这么写的，已推翻**） | ⛔ **`AIMessage` 是错的**，`HumanMessage` 也是错的 —— **必须是 `ToolMessage`（按 `tool_call_id` 配对）且显式传 `as_node="tools"`**。<br>🔴 **为什么**（全部真机实测）：`interrupt_before=["approval"]` 停在审批点时，state 末尾是**一条带 `tool_calls` 的 `AIMessage`**，它**必须**由每个 `tool_call_id` 各一条 `ToolMessage` 闭合。<br>· 塞 `AIMessage`/`HumanMessage` ⇒ 那个配对**永远不闭合** ⇒ 真模型**下一轮直接 400**（`must be followed by tool messages`）—— **而单测看不出来**（假图不校验结构、假 `invoke` 不会有"下一轮"）。<br>· 塞 `AIMessage` 还有第二重错：`update_state` 会**按消息类型推 `as_node`** ⇒ `AIMessage` 被认成 `agent` 的输出 ⇒ **条件边重算 ⇒ 图当场 END**（实测 `approve` **0.017s**、`answer` = 输入原文、`tools`/`agent` 一个都没跑）。<br>· ⚠️ **反直觉**：**只塞对 `ToolMessage` 也不够** —— 不传 `as_node` 时它被推成 `agent` ⇒ **照样 END**（实测 `next=`）。⇒ **两件事都要做**。<br>📌 判据 ⇒ `app/tests/test_approval_resume.py` §⑤（真图 + 假 LLM）· 端到端 **22/22** |
| 🔴 **「批完就从待接管队列里注销了」** | ⚠️ **改前是这样的， 起不是** —— 只有**图真的走完**（`get_state().next != ("approval",)`）才 `resolve()`。<br>⚠️ **为什么**：模型放行后**又要**一个敏感工具是**常见行为**（实测连续 3 次）⇒ 无条件注销 = 图还停着、队列已空 = 🔴 **孤儿会话**（`/agent/pending` 查不到、「再批」报"没有等待审批的任务"、同 thread 再问 **500**）。<br>📌 判据 ⇒ `test_resume_that_stops_again_is_re_registered` · `test_resume_that_finishes_clears_the_queue` |
| 🔴 **「模型可以无限要求敏感工具，人工无限批」** | ⛔ **封顶**：`rounds` 到 **`approval_round_cap`**（默认 3 · env `MAX_APPROVAL_ROUNDS`）就**不再登记**，改注入"请直接作答"的 `ToolMessage` 收尾。<br>⚠️ **别读成"一律报错"** —— **先努力收尾**（成功 ⇒ `forced_finish=True` + 正常答案）；只有"连提示都拦不住"才 `status="error"`。📌 判据 ⇒ `app/tests/test_approval_resume.py` §⑥（4 条） |
| ⚠️ **「`rounds` 是给人看的」** | ⛔ **是封顶依据** —— `/agent/approve` 拿 `candidates[0]["rounds"]` 比上限。⚠️ **`register` 默认 `rounds=1`** ⇒ 只有"放行后又停"那条路会 `+1`（首登记本就该是 1）；**新登记点若不代表首次，必须显式传 `rounds`** |
| ⚠️ **「审批状态是持久化的」** | ⚠️ **默认不是** —— `agent_graph.py:300` 用的是 `MemorySaver()`（**进程内存**）⇒ **重启即丢**。只有设了 `AGENT_CHECKPOINT_BACKEND=sqlite` 才落盘 |
| 🔴 **「`check_budget` 就是会话上限」** | ⛔ **不是** —— `check_budget`（`:1583`）判的是**用户【每日】token 预算**。**会话级是另一个函数**（`check_session_token_budget`，B8 · ）。两者**并存**，⚠️ `/agent/mcp_chat` 上**两条都挂** |
| 🔴 **「端点上那几道预算门过了 ⇒ 这一轮就不会被预算拒」** | ⛔ **还会被拒，而且是【图里】拒的** —— 批 7之前，图内软返回**只塞一句话当正常答案**（HTTP 200）⇒ 调用方**看不出被拒了**。<br>✅ **现状**：图在 state 上写 `budget_intercept` ⇒ 端点转 **429 / error 帧**。<br>⚠️ **两道门都拦不住的那两种洞**（这是"为什么还要图内拦"的答案）：**① `0 < remaining < 500`** —— `QuotaMiddleware` 只在 `remaining <= 0` 时拒（**纯库存**判定），而图节点按 **500 预估**判 ⇒ 这批人**穿得过中间件**；**② 跑一半才烧穿** —— 中间件**只在入口查一次**，多节点图会把额度烧穿。<br>⚠️ **`/agent/approve` 原先【两道门都没有】** —— 它是全仓唯一既无 也无 的**烧钱**端点（R1.3 由中间件覆盖 ⇒ 不是"零门"）；现已一并补上，**用调用方 `user_name` 过门**（⛔ 不是属主）。 |
| 🔴 **「被拦那次也会 `register` 进待接管队列」** | ⛔ **不会，而且【不能】** —— 被预算拦下的这一轮**没有停在审批点** ⇒ 登记一条**永远批不了**的假待办。<br>⇒ 端点里那个 `if` **必须排在 `summarize_agent_result()` 之前**（`register` 在它之后）。<br>⚠️ **反过来**：`/agent/approve` 的两条路被拦时**必须 `resolve`** —— 那里图**真跑完了**（软返回），不注销同样留假待办。 |
| 🔴 **「会话上限没拦住 = 没生效」** | ⚠️ **先看 `thread_id` 是不是默认值** —— 会话 key = **`user_name` + `thread_id`**（决策二）。<br>4 个端点的 `thread_id` 默认 `"default"` ⇒ **同一个人的**多次默认调用**共用**一个桶（**不同人不会互相踩** —— 这正是决策二加 `user_name` 的原因）。<br>⚠️ 但**换个 `thread_id` 就是换个桶** ⇒ 这是**设计如此**，不是漏拦<br>✅ **两条轴终于一致了** —— **checkpoint 那条轴**原先按**裸 `thread_id`** 走（§二 根因），现在也拼 `user_name`（丙段）。⚠️ 但**拼法不同**（这条是 `f"{user}:{thread}"`，那条是长度前缀）⇒ ⛔ 别以为能互推 |
| ⚠️ **「`/agent/plan_execute` 一直有 `thread_id`」** | 🔴 **才补的**（B8）。此前它**没有**这个参数 ⇒ 老客户端不传也能跑（走默认值），**行为不变**；但**新加的这条上限**在它上面用的是 `"default"` 桶 |
| ⚠️ **「额度是按人算的」** | ⚠️ **两者都是，但维度不同**：`check_budget` = 人 × 日；`check_session_token_budget` = **人 × 会话 × 日**。⛔ 别把其中一个当另一个 |
| 🔴 **「`/agent/traces` 是管理员看板」** | ⛔ **默认只给你本人的**（之前**是**给所有人的 —— 见下一条）。看全量要 admin，而那个开关在端点里**显式一行**（`include_all=get_user_role(...) == UserRole.ADMIN`）。📌 判据 ⇒ `app/tests/test_trace_isolation.py` |
| 🔴 **「`/agent/trace/{thread_id}` 拿一个 id 就能查」** | ⛔ **要属主**（**决策 9** / N4）。改之前它**不判属主**，且轨迹存储**只按裸 `thread_id`** 做键 ⇒ 而本文件 4 个端点的 `thread_id` **默认就是 `"default"`** ⇒ **不传的人共用一个槽、后问的盖先问的**（⚠️ 与上面「会话上限」那条的桶碰撞**是同一个形状，但在另一条轴上**）。<br>⚠️ **非属主与"真不存在"答同一个**（`未找到线程 …`）—— **有意的**，否则那句错误本身就是"该 thread 存在"的 oracle |
| 🔴 **「`/agent/cost/overview` 答 0 ⇒ 没花过钱」** | ⛔ **先想想进程重启过没有** —— **之前**它读 `get_user_summary` = **进程内存**，**重启即归零、且不报错**。<br>✅ 现在读 **`get_user_overview`（库）**，扛得住重启。<br>⚠️ **但同族的三个仍然是内存**：`/agent/token/overview` · `/agent/thread/{id}/overview` · 看板第 2 格 —— **答 0 是真的 0，还是刚重启，看代码分辨不出来** |
| 🔴 **「`/agent/cost/overview` 和 `/agent/token/budget` 在报同一件事」** | ⛔ **不是，窗口不同**（特意划清的）：<br>`/agent/token/budget` = **今天 + 还剩多少**（`R1.3` 额度口径，**跨天自愈**）<br>`/agent/cost/overview` = **全时累计 + 一共多少**（字段名 `total_*`）。<br>⚠️ 两个口径**方向相反** ⇒ 若把 `overview` 的 SQL 加上 `created_at >= CURRENT_DATE`，它会**静默退化成"今天"**（数值偏小、不报错）。<br>📌 判据（可打印）：`app/tests/test_cost_visibility_db.py::test_overview_window_is_all_time_not_today` |
| ⚠️ **「`/agent/token/budget` 的数是【本人】的，那就跟全站无关」** | ⚠️ **它同时答两层**：本人（`daily_budget`/`used_today`/`remaining`）**和**全站（`global_daily_limit`/`global_used_today`/`global_remaining`）。<br>⚠️ **字段名不带 `global_` 前缀的那三个是本人的** —— ⛔ **别拿未加前缀的 `remaining` 当全站余量**。<br>📌 理由：**"还剩多少"必须能同时看到本人和全站**，否则看到 `999961` 也不知道那是谁的上限 |
| ⚠️ **「全站额度快满了，接口会给个预警」** | ⛔ **不会** —— 全站额度超了是**所有人吃 429**（熔断），**没有"快到阈值了"的软提示**。<br>✅ 现在能**看到逼近**（`global_remaining`），但**得自己去看** —— ⬜ 无主动告警， §遗留未列，本行仅备查 |
| 🔴 **「`/agent/trace/{id}` 与 `/agent/trace/{id}/cost` 是一条链的两段，能拼成一棵树」** | ⛔ **拼不成** —— 两条**不同的轴**：前者是**追踪轴**（`tool_visualizer._traces` · **进程内存** · 粒度 = **工具调用**），后者是**成本轴**（PG `token_usage_logs` · 粒度 = **模型调用**）。<br>🔴 **它们【没有共同的步 id】** ⇒ 一个 `agent_decision`（模型）与一个 `search`（工具）之间**没有可判定的对应**（§二 实测）。<br>⚠️ **能对上的只有 `(user_name, raw thread_id)` 这一对游标** —— 再往下就只能靠"时间接近"猜，而**猜出来的层级不报错**。<br>⇒ 页面（`/trace`）**并排画两条轴、⛔ 不相加**。📌 判据 ⇒ `app/static/js/trace.test.js`（两轴各有各的 `summarize*`，且 `summarizeCost` **⛔ 不把 rows 加起来**） |
| ⚠️ **「`/agent/trace/{id}/cost` 查不到线程 ⇒ 该回 404」** | ⛔ **回 200 + 空 `items`** —— `thread_id` 是**用户自己填的**（页面 URL 上带），404 会把**"这条线程没花过钱"**说成**"这条线程不存在"**。<br>⚠️ **同理⛔ 别拿它跟 `/agent/trace/{id}` 对齐**：那条**确实**有"未找到"的语义（因为它的键是 `session_key`，查的是**建过没建过**）。<br>🔴 还有一层：读侧一旦报"不存在"，**那个错误本身就是 oracle**（与上一条"越权与真不存在答同一个"同族）。 |

| ⚠️ **「流式端点与非流式兄弟返回的形状一样」** | ⚠️ **之前【不是】** —— 5 条流式端点的**汇总帧都少一个 `requested_by`**，而各自的非流式兄弟（`:278` / `:885` / `:1124` / `:1306` / `:1706`）**全都有**，且**没有任何用例报错**（**静默的形状不一致**：两侧其余字段一模一样，只有它无声没了）。<br>✅ **`b0b1835` 起 5 条汇总帧都带它**。⚠️ **注意落点**：在**汇总帧**上（终态），⛔ 不在逐 token 的 `{"content": …}` 帧上（增量）。<br>📌 判据（可打印）：`grep -c '"requested_by": user_name' app/routing/api_v1_agent.py` ⇒ **33**（改前 27；⚠️ 这个数**随后续批次往上走**，别当固定值）· `pytest app/tests/test_agent_sse.py app/tests/test_agent_stream_chains.py -k "requested_by or plan_execute_summary_carries"` ⇒ **5 passed** |

| 🔴🔴 **「Agent 链有 checkpointer ⇒ 对话历史不用管」** | ⛔ **两回事，两套存储**（§一 的核心发现）—— **checkpointer**（`MemorySaver`）存的是**图的运行状态**，只有**本文件自己**按 `session_key(user, thread_id)` 去读；**`chat_history`**（Redis）存的是**人/脚本能读的一问一答**，是**另一把键**。<br>⚠️ **改前 5 条 Agent 链一条 `chat_history` 都不写**（`grep -rn "append_chat_history" app/routing/api_v1_agent.py` ⇒ **0**）⇒ 「下一轮 prompt 读得到」这件事，**RAG 读得到、Agent 读不到**（除非走 checkpoint）。<br>✅ ** 5 条链都写**，每条轮次 `{role, content, status}`。<br>🔴 **由此多了一条【新行为】**：`/rag/stream_search` 在**前端没传历史时**读同一把键 ⇒ **Agent 链的轮次此后会进 RAG 的下一轮 prompt**。这是「统一会话」的意图，⛔ 但**它是一条行为变更**，别当无事发生。<br>📌 判据（可打印）：`grep -c "persist_turn" app/routing/api_v1_agent.py` ⇒ **12**（= 5 处 `on_complete` + 5 处 `on_incomplete` + import 与注释）· `grep -c "on_incomplete=" app/routing/api_v1_agent.py` ⇒ **5**（改前 0，那时只有 `on_complete` 有回调） |

| 🔴🔴 **「端点上已经有 `thread_id` / `user_name` 参数了 ⇒ 图里当然拿得到」** | ⛔ **拿不到** —— 端点的参数**不会自动进图的 initial state**，必须**显式写进** `graph.invoke(...)` 的那个 dict。<br>⚠️ **漏写的后果是【静默】的**：图里 `.get("user_name", "unknown")` ⇒ 账记成 `"unknown"`，**接口一切正常**，只是额度**漏算**。<br>🔴 **本仓真的漏了**：`advanced_chat` / `advanced_chat/stream` 改前**只传 `user_name`、没传 `thread_id`**。<br>📌 判据（可打印）：`app/tests/test_billing_wiring.py` 按端点逐个查 initial state 的关键字集合。 |

## 关联

`app/agent/specs/agent_graph.md`（审批节点的本尊）· `app/agent/specs/agent_checkpointer.md`
`app/routing/specs/api_v1_rag.md`（③ 的另一半）· `app/specs/main.md`
`app/billing/specs/token_tracker.md`（**`get_user_overview` 的本尊** + 内存/库两套口径的说明）·
**`app/agent/specs/pending_approvals.md`**（的队列模块）·
**`app/agent/specs/agent_graph.md`**（审批那一侧 · **也是 B1 流式的另一半**）·
🔴 ** 丙段**（**checkpoint 键拼身份** · **`/agent/approve` 归属校验 + 按图路由** · **`memory_chat` 审批门**）·
`/agent/pending` **可见性收窄为本人默认/admin 全量** · `/agent/approve` **加可选 `owner` + 写留痕**；
⚠️ **本批 ⛔ 不等于硬门 D 翻 ✅** —— §六·6 明写要照四硬门原文逐栏对）·
**`app/agent/specs/approval_audit.md`**（**留痕那张表的本尊** —— `approval_events` 的 `owner`/`actor` 两个身份）·
🔴 同时定下**本文件里两条 trace 端点分属两条轴**（`/agent/trace/{id}` = 追踪轴 · `…/cost` = 成本轴，**⛔ 不合并**）·
⚠️ 顺带核出 **`app/static/web/approvals.html` 的 4 条 URL 全少 `/api/v1`** ⇒ 页面 100% 不可用，**本批未修**）·
**`app/static/specs/static_frontend.md`**（**接管页那一侧** —— `approvals.html` / `approvals.js`）·
**`app/access/specs/session_key.md`**（**键就是它拼的** —— 丙段新增模块）·
`后端补齐清单` **B1 · B2 · B3 · B4 · B5 · B6 · B13**

---

# 🔵 实施计划 ② · **人工接管**（立 · **执行中**）

> ## 📊 进度
>
> | | Task | 状态 |
> |---|---|---|
> | ✅ | · 前置决策（白名单里放哪些工具） | **作者 已答** = ~~`{search_tool}`~~ → **`{web_search}`** · `.env SENSITIVE_TOOLS`；🔴 **标识符已由 勘误**） |
> | ✅ |  ·  触发条件改工具白名单 | **已落地**—— 判据：`pytest app/tests/test_approval_trigger.py -q` ⇒ **7 passed** |
> | ✅ |  ·  待接管队列（数据 + 端点） | **已落地** —— 判据：`pytest app/tests/test_pending_approvals.py app/tests/test_pending_approvals_wiring.py -q` ⇒ **13 passed** |
> | ✅ | · 接管后续跑（含改写后提交） | **已落地**（🔴 **口径 由 修正**）—— 判据：`pytest app/tests/test_approval_resume.py -q` ⇒ **12 passed**（原 6 条接线/语义 **+ §⑤ 六条真图用例**） |
> | ✅ | · **端到端验收**（补） | **已跑 · 首跑不通过 · 已修** —— 作者 裁的「下一件事」。真服务（真 DeepSeek + 真 `MemorySaver`）跑三条出口 ⇒ **证真① 过、证真② 不过** ⇒ 修（`ToolMessage` 回填 + `as_node="tools"` + 只在真走完时注销）⇒ **复跑 22/22** |
>
> ⇒ ** 的 5 个 Task（0–4）全部落地** ⇒ **硬门 D 三段齐了 + 后端侧端到端已验**。
> ⚠️ **但别读成"硬门 D 已验证"** —— 它的**演示/反例里含界面**（「点开后能看到完整上下文」「界面上找不到」），
> 前端按  🅱️ 未开工 ⇒ 现状 = **后端侧证真①②已过 · 界面侧待前端**。
> 🔴 **本次的教训**： 原来那 **6 条**（假图）**全绿**，而真服务**三条出口全坏** ——
> **"接线测过"与"端到端成立"之间隔着一整个真模型**（§3.1）。

> ### 🔴🔴 **勘误—— 读本节之前先读这一段**
>
> **本节正文是【当时的计划原文】，⛔ 别照抄。**
> 里面那个白名单名字 **~~`search_tool`~~ 是错的** —— 它是**变量名**，不是工具名
> （真名原为 `duckduckgo_search`，现随  换成 **`web_search`**）。
>
> | 本节写的 | 现在的实际 |
> |---|---|
> | `SENSITIVE_TOOLS` 默认 `"search_tool"` | **`"web_search"`**（`app/agent/agent_graph.py:71`） |
> | `validate_approval_config()` **只查"非空"** | **两段** —— 空名单 **+ 名字不存在**，都 `raise`（`:92` / `:100`） |
> | `tool_execute` 判 `if tool_name == "search"` | **查 `TOOLS_BY_NAME` 表**（`:202`），⛔ 不再有字面量 |
> | 工具 = `DuckDuckGoSearchRun()` | **`search_tools.web_search`**（Bing 版） |
>
> ⚠️ **后果**：按本节原文落地的那一版，**审批从来没触发过**（交集恒空）而**没有任何报错**。
> 📄 全文（含"为什么连测试文件自己都钉着错名字"）
> 📌 **现在 `agent_graph.py` 长什么样**，以 `app/agent/specs/agent_graph.md` 为准（本节是**记录**，不是规范）。

> **来源**：`后端补齐清单-待裁-20260929.md` 的 **B4 · B5 · B6**（**作者已裁：全部为「甲」**）。
> **三者是同一件事的三段**：**什么时候该停（B4）→ 停在哪看得到（B5）→ 批了怎么接着跑（B6）**。

**目标**：让硬门 D 从「**有地基、但语义是错的**」变成「**该转人工时才转，且转过去看得、批完接得上**」。

**架构**：B4 改 `agent_graph.py` 的**条件边**（`should_continue` 从"有没有 tool_calls"改成"在不在敏感白名单"）；
B5 新增一个**待接管注册表** + 一个查询端点；B6 给 `/agent/approve` 加**改写后提交**。

---

## · **前置决策**：白名单里放哪些工具？· ✅ **已答**

> `决策二` 已裁「**工具白名单**」，但**"白名单里放哪些工具"这个子问题还空着**。
> ⬜ **我不替你定** —— 但把判断材料摆出来：

| 工具 | 有没有**外部副作用** | 建议 |
|---|---|---|
| ⭐ ~~**`search_tool`**~~ → **`web_search`**（原 `DuckDuckGoSearchRun`， 换成 Bing 版） | 🔴 **有** —— **它把查询内容发到第三方** | ✅ **放进白名单**（🔴 **写 `web_search`，⛔ 不是 `search_tool`**） |
| `calculator` | ⛔ 无 —— 纯本地计算 | ❌ 不放 |
| `date_today` | ⛔ 无 —— 纯本地取时间 | ❌ 不放 |

> ### ⇒ 我的建议：**白名单第一版 = ~~`{search_tool}`~~ → `{web_search}`**
>
> **理由**：**"敏感操作"的现实定义就是"会对外产生副作用"** —— 而 ~~`search_tool`~~ **`web_search`**
> **会把用户的问题原文发给搜索引擎**。这**不是"贵不贵"的问题，是"数据出去了"的问题**。
> ⚠️ **判据（的原话）一条都没变，改的只是那个名字** —— 见本节顶部的勘误框。
> ⚠️ 而 `calculator` / `date_today` 是纯本地的，**审批它们只会让人烦**（现状就是这个问题）。
>
> ### 🔴 而「空白名单」这个状态**必须明确表态**（三方文档都指出来了）
>
> 若白名单为空 ⇒ `needs_approval` 恒 `False` ⇒ **审批永不触发**
> **硬门 D 变成"有地基但从不启用"** —— 那是**验收上过不去**的。
> ⇒ **不许让它悄悄为空。** 见  的 Step 4（启动时校验）。

- [x] **Step 1**：把「白名单第一版放哪些工具」的裁定写进 `后端补齐清单` 的 **B4 · ✍️ 裁**栏，再开工。

---

## · 触发条件改成「工具白名单」· ✅ **已落地**

> ⚠️ **落地时与计划有两处不同**（都记在  与 `CHANGELOG`）：
> ① **多写了 2 条测试**（计划 5 条 → 实际 7 条）：`validate_approval_config()` 的**正反两条** ——
>    只测"空了会炸"**可能误报**（判据写反、恒炸）。
> ② **顺手修了三处"改完就成假话"的注释/spec** —— 其中 `summarize_agent_result` 的 docstring
>    **判据的【理由】变了、结论没变**：改前「有 `tool_calls` ⇒ 一定停在审批」，
>    改后「**非敏感的 `tool_calls` 不会出现在返回态里**」⇒ 重写了它**为什么还成立**、**什么时候会失效**。


**Files:**
- Modify: `app/agent/agent_graph.py`（`should_continue` `:95-103` · 条件边 `:130-137`）
- Test: `app/tests/test_approval_trigger.py`（新建）
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `SENSITIVE_TOOLS: frozenset[str]` —— 从 env `SENSITIVE_TOOLS`（**逗号分隔的工具名**）
- Produces: `needs_approval(tool_calls: list[dict]) -> bool`
- ⚠️ `should_continue` 的返回值**从 2 种变 3 种**：`"approval"` / `"tools"` / `END`

- [x] **Step 1: 写失败测试**

```python
# app/tests/test_approval_trigger.py
"""硬门 D 的触发条件（B4）。

🔴 改前的现状：**只要产生任意 tool_calls 就进审批** ⇒ 问一句"今天几号"也会停下来等人批。
   那条路在验收上是**过不去**的 —— 硬门 D 要的是「**该被接管时被接管**」，不是「全都接管」。

⚠️ 不碰 DB / Redis / 网络 —— 只测路由函数的返回值。
"""
import os

os.environ.setdefault("SENSITIVE_TOOLS", "search_tool")

from agent_graph import needs_approval, should_continue           # noqa: E402


def _calls(*names):
    return [{"name": n, "args": {}} for n in names]


def test_local_only_tools_do_not_need_approval():
    """🔴 本条对应"问个日期也进审批"那个现状 —— 它必须**不**触发。"""
    assert needs_approval(_calls("date_today")) is False
    assert needs_approval(_calls("calculator")) is False


def test_external_side_effect_tool_needs_approval():
    """`search_tool` 会把问题发到第三方 ⇒ 是敏感操作。"""
    assert needs_approval(_calls("search_tool")) is True


def test_mixed_calls_need_approval():
    """只要**有任何一个**敏感 ⇒ 整体审批（不能"挑着执行"）。"""
    assert needs_approval(_calls("date_today", "search_tool")) is True


def test_no_tool_calls_ends_the_graph():
    """没有 tool_calls ⇒ 结束，⛔ 不是"去审批"。"""
    from langchain_core.messages import AIMessage
    from langgraph.graph import END

    assert should_continue({"messages": [AIMessage(content="答案是 42")]}) == END


def test_unknown_tool_is_not_sensitive_by_default():
    """⛔ 白名单是**白名单** —— 没登记的工具**不**进审批。

    ⚠️ 这条是**故意的取舍**：默认"不敏感"意味着**新加的工具默认不过审批**。
       要它过，就得改 `SENSITIVE_TOOLS`（env）或改本文件。
       📌 若哪天裁定改成"默认敏感"，**改这一条 + 写明理由**，别悄悄改。
    """
    assert needs_approval(_calls("some_new_tool")) is False
```

- [x] **Step 2: 跑，确认失败**

```bash
python -m pytest app/tests/test_approval_trigger.py -q
```
预期：`ImportError: cannot import name 'needs_approval'`

- [x] **Step 3: 实现（`app/agent/agent_graph.py`）**

在 `search_tool = DuckDuckGoSearchRun()` **之前**加：

```python
# ==================== 人工审批的【触发条件】（B4 · 改）====================
# 🔴 改前：**只要产生任意 `tool_calls` 就进审批** ⇒ 问一句"今天几号"也会停下来等人批。
#
# 现在：**只有【会对外产生副作用】的工具**才需要审批。
#   判据：「敏感」= **这个工具会把数据发到本机之外 / 产生不可撤销的外部效果**。
#   ⇒ `search_tool`（把问题原文发给 DuckDuckGo）**算**；
#      `calculator` / `date_today`（纯本地）**不算**。
#
# ⚠️ 从 env 读，逗号分隔。**默认值是 `search_tool`** —— 见  的裁定。
SENSITIVE_TOOLS = frozenset(
    n.strip() for n in os.getenv("SENSITIVE_TOOLS", "search_tool").split(",") if n.strip()
)


def needs_approval(tool_calls: list) -> bool:
    """这一批工具调用里，**有没有任何一个**需要人工审批。

    ⚠️ 有任何一个敏感 ⇒ **整批都要批** —— ⛔ 不能"挑着执行敏感之外的"。
       理由：这批调用是**模型一次决定的**，拆开执行会让它看到的执行结果与它设想的不一致。
    """
    names = {tc.get("name") for tc in tool_calls or []}
    return bool(names & SENSITIVE_TOOLS)
```

把 `should_continue`（`:95`）改成：

```python
def should_continue(state: AgentState):
    """路由函数：没有工具调用 ⇒ 结束；有 **敏感** 工具调用 ⇒ 先审批；否则直接执行。

    ⚠️ 返回值有 **3 种**了（改前只有 2 种）—— 条件边的映射表要跟着改（见 `build_agent_graph`）。
    """
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None) or []
    if not tool_calls:
        return END
    if needs_approval(tool_calls):
        return "approval"
    return "tools"
```

把条件边（`:130-137`）改成：

```python
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "approval": "approval",   # 敏感工具 ⇒ 先停，等人批
            "tools": "tools",         # 非敏感 ⇒ 直接执行（这是本次改动的关键）
            END: END,
        }
    )
```

- [x] **Step 4: 加"白名单不许为空"的启动校验**

在 `build_agent_graph()` **之前**加：

```python
def validate_approval_config():
    """启动时校验审批配置 —— ⛔ 不许让白名单【悄悄为空】。

    为什么必须有这条：白名单为空 ⇒ `needs_approval` 恒 False ⇒ **审批永不触发**
    **硬门 D 变成"有地基但从不启用"** —— 而**没有任何报错**，验收时才发现。
    ⇒ 让它**启动就报**，别等到验收。

    📌 同型前科：本仓 `embedding_client.py` 的模块级 `OpenAI(api_key=...)`
    —— key 为空会**炸掉整条 import 链**（待办）。
    ✅ **（批 6）那条已修**（改惰性 + 缺 key 点名）；
    ⚠️ 本条（启动校验）**不受影响、仍然要** —— 它拦的是「该配的没配」。
      那条是"**意外**为空就炸"；本条是"**该配的东西没配**就炸"，**方向相反、目的一样**。
    """
    if not SENSITIVE_TOOLS:
        raise EnvironmentError(
            "SENSITIVE_TOOLS 为空 ⇒ 人工审批永远不会触发，硬门 D 名存实亡。\n"
            "请在 .env 里配置至少一个会对外产生副作用的工具名，例如：\n"
            "    SENSITIVE_TOOLS=search_tool"
        )
```

然后在 `agent_graph = build_agent_graph()` 那行**之前**调用 `validate_approval_config()`。

- [x] **Step 5: 跑测试**

```bash
python -m pytest app/tests/test_approval_trigger.py -q                        # → 5 passed
python -m pytest app/ -m "not integration and not needs_db" -q            # → 全绿
```

⚠️ **可能红**：`app/tests/test_agent_repairs.py` 里有关于审批语义的用例（`:55-64` 那段注释就是它留下的）。
**红是预期的** —— 改的就是这个语义。**逐条看**：把"问日期会进审批"那类断言**改掉并写明新口径**，
⛔ **不要为了让测试过而回退实现**。

- [x] **Step 6: 补 `.env.example` + CHANGELOG + 提交**

```bash
# .env.example 加一行（带注释说明它是干什么的）：
#   SENSITIVE_TOOLS=search_tool    # 需要人工审批的工具（逗号分隔）
git add app/agent/agent_graph.py app/tests/test_approval_trigger.py .env.example CHANGELOG.md
git commit -m "feat(硬门D): B4 —— 审批触发改成敏感工具白名单，问日期不再进审批"
```

---

## · 待接管队列（数据 + 端点）· ✅ **已落地**

> ### ✅ 落地结果
>
> | 计划怎么写 | 实际怎么做 | 差异 |
> |---|---|---|
> | 6 个 Step | **6 个全做了** | — |
> | 4 条测试 | **7 条** | ➕ 3 条：**排序契约**（"卡得最久排最前"是 docstring 里写了却没人守的契约）· **启动警告的正反两条**（只测"设了会响"会误报） |
> | （计划没提接线守卫） | **➕ 新增 `app/tests/test_pending_approvals_wiring.py`（6 条）** | 🔴 **接线是这个模块最可能静默坏掉的地方**（漏一处 ⇒ 队列**永远空**或**永远有假待办**，且**都不报错**）⇒ 按本仓 `*_wiring.py` 既有做法补上 |
> | （计划没提） | **➕ 那 6 条守卫逐条自证** | ⚠️ 它们是**写在实现之后**的（"tests-after"）⇒ **绿了不证明测的是对的东西**。⇒ 逐条把接线拆掉、确认对应测试**真会红**、再还原（脚本见 verdict：6/6 红了，还原后 6 passed） |
>
> **判据（可打印）**：
> ```bash
> venv/bin/python -m pytest app/tests/test_pending_approvals.py app/tests/test_pending_approvals_wiring.py -q
> # ⇒ 13 passed
> venv/bin/python -m pytest app/ -m "not integration and not needs_db" -q
> # ⇒ 237 passed, 3 skipped, 22 deselected, 0 failed   （本轮之前 224 ⇒ +13）
> ```
> **顺手改的口径**：路由 **28 → 29**（`待办总表` · `后端补齐清单` 三处「28 个 agent 路由全部非流式」同步改）。


**Files:**
- Create: `app/agent/pending_approvals.py` + `app/agent/specs/pending_approvals.md`（⛔ 新建模块必须同时建 spec）
- Create: `app/tests/test_pending_approvals.py`（无 marker）
- Modify: `app/routing/api_v1_agent.py`（新端点 + 在 `langgraph_chat` 里登记）

**Interfaces:**
- Produces: `register(thread_id, user_name, tool_calls) -> None` · `resolve(thread_id) -> None` · `list_pending() -> list[dict]`
- Produces: `GET /agent/pending` —— 返回 `[{"thread_id", "user_name", "since", "tool_calls"}]`

> ### 🔴 一个**必须写在 spec 里**的耦合
>
> **没法从 `MemorySaver` 里"列出所有卡住的会话"** —— 它只按 thread_id 取，**没有"列出全部"的 API**。
> ⇒ **必须自己建一张注册表**，在对话返回 `pending_approval` 时登记、在 `/agent/approve` 后注销。
>
> ⚠️ **而这张注册表放哪，必须与 checkpoint 后端一致**：
> · 默认 `MemorySaver`（`agent_graph.py:148`）⇒ 状态本来就在内存 ⇒ 注册表放内存**是一致的**
> · 但若设了 `AGENT_CHECKPOINT_BACKEND=sqlite` ⇒ **状态落盘了、注册表还在内存**
>   **重启后：图还在等审批，队列里却查不到** ⇒ 会话变孤儿
> ⇒ **本计划 v1 只支持内存**，并在 `pending_approvals.py` 顶部**写明这个限制** + 启动时若检测到 sqlite 后端就**警告**。

- [x] **Step 1: 写失败测试**

```python
# app/tests/test_pending_approvals.py
"""待接管队列（B5）。⚠️ 不碰 DB / Redis —— 纯内存注册表。"""
import pending_approvals as pa


def test_register_then_list():
    pa.clear()                                   # 测试隔离
    pa.register("t1", "admin", [{"name": "search_tool", "args": {"q": "x"}}])
    rows = pa.list_pending()
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "t1"
    assert rows[0]["user_name"] == "admin"
    assert "since" in rows[0]


def test_resolve_removes_it():
    """批完必须注销 —— 否则它会**永远留在队列里**，变成假待办。"""
    pa.clear()
    pa.register("t2", "admin", [])
    pa.resolve("t2")
    assert pa.list_pending() == []


def test_resolve_unknown_thread_is_a_noop():
    """重复注销 / 注销不存在的 ⇒ ⛔ **不许抛异常**（`/agent/approve` 会调到）。"""
    pa.clear()
    pa.resolve("never-registered")               # 不应抛


def test_register_twice_keeps_latest():
    """同一 thread 再次登记 ⇒ 更新，⛔ 不产生两条。"""
    pa.clear()
    pa.register("t3", "admin", [{"name": "a", "args": {}}])
    pa.register("t3", "admin", [{"name": "b", "args": {}}])
    rows = pa.list_pending()
    assert len(rows) == 1 and rows[0]["tool_calls"][0]["name"] == "b"
```

- [x] **Step 2: 跑，确认失败**（`ModuleNotFoundError: pending_approvals`）

- [x] **Step 3: 实现 `app/agent/pending_approvals.py`**

```python
"""待接管会话的注册表（B5）。

⚠️ **为什么需要它**：`MemorySaver`（`agent_graph.py:148`）**没有"列出所有 thread"的 API**
   ⇒ **没法从 checkpoint 里反查"谁卡在审批"** ⇒ 只能自己记账。

🔴 **已知限制（v1）**：本表在**内存**里 ⇒ **进程重启即清空**。
   与默认的 `MemorySaver` **是一致的**（那个也在内存）。
   ⛔ **但若设了 `AGENT_CHECKPOINT_BACKEND=sqlite`**：图的状态落盘了，本表还在内存
      **重启后图仍在等审批，而队列里查不到** ⇒ 会话变孤儿。启动时会给警告（见 `warn_if_backend_mismatch`）。
"""
import threading
import time

_lock = threading.Lock()
_pending: dict[str, dict] = {}


def register(thread_id: str, user_name: str, tool_calls: list) -> None:
    """登记一个卡在审批的会话。同一 thread 重复登记 ⇒ 覆盖。"""
    with _lock:
        _pending[thread_id] = {
            "thread_id": thread_id,
            "user_name": user_name,
            "tool_calls": list(tool_calls or []),
            "since": time.time(),
        }


def resolve(thread_id: str) -> None:
    """会话已不再等待审批 ⇒ 注销。⚠️ 注销不存在的**是正常的**（幂等），不抛异常。"""
    with _lock:
        _pending.pop(thread_id, None)


def list_pending() -> list[dict]:
    """按"卡住时间"升序 —— **卡得最久的排最前**（最该先处理）。"""
    with _lock:
        return sorted((dict(v) for v in _pending.values()), key=lambda r: r["since"])


def clear() -> None:
    """仅供测试隔离用。"""
    with _lock:
        _pending.clear()


def warn_if_backend_mismatch(logger=None) -> None:
    """checkpoint 落盘了、而本表在内存 ⇒ 重启后队列会丢。启动时提醒。"""
    import os
    if os.getenv("AGENT_CHECKPOINT_BACKEND") == "sqlite":
        msg = ("AGENT_CHECKPOINT_BACKEND=sqlite ⇒ 图状态会落盘，"
               "但待接管队列(pending_approvals)仍在内存 ⇒ 重启后队列会丢、会话变孤儿。"
               "见 app/agent/specs/pending_approvals.md")
        (logger.warning if logger else print)(msg)
```

- [x] **Step 4: 建 `app/agent/specs/pending_approvals.md`**（把上面那段「已知限制」原样搬进去 —— 它正是"看代码会误判"的那类）

- [x] **Step 5: 接端点（`app/routing/api_v1_agent.py`）**

```python
from pending_approvals import list_pending, register, resolve


@router.get("/agent/pending")
async def list_pending_approvals(
    user_name: str = Depends(get_current_user_hybrid),
):
    """列出**当前等待人工接管**的会话（硬门 D 的入口）。

    判据（`通用/四硬门 §2` 的 L2 分水岭）：**能被前端当作一个独立可点的入口**。
    """
    rows = list_pending()
    return {"count": len(rows), "items": rows, "requested_by": user_name}
```

在 `langgraph_chat`（`:130`）里，拿到 `summary` **之后**：

```python
    if summary.get("status") == "pending_approval":
        register(thread_id, user_name, summary.get("pending_tool_calls") or [])
    else:
        resolve(thread_id)      # 本轮没卡住 ⇒ 清掉上一次的登记（否则会残留成假待办）
```

在 `approve_agent_action`（`:112`）里，**每条 return 之前**调 `resolve(thread_id)`。

- [x] **Step 6: 跑 + 提交**

---

## · 接管后续跑（含"改写后提交"）· ✅ **已落地**

> ### ✅ 落地结果
>
> | 计划怎么写 | 实际怎么做 | 差异 |
> |---|---|---|
> | 4 个 Step | **4 个全做了** | — |
> | 2 条测试 | **6 条** | ➕ 4 条：**`thread_id` 必须是请求里那个**（`None` 只保证"是续跑"，保证不了"续的是**这一条**"）· **改写必须是 `AIMessage`**（用 `HumanMessage` 会把"人给的结论"当**新输入**再答一遍）· **拒绝时不许写改写**（反面：只测"批准时会写"会漏掉这个）· **没停在审批点不许 `invoke`** |
> | 2 条里 1 条就该绿 | **确实绿了** | ⚠️ `invoke(None)` 与"早退不 invoke"这两条**改前就绿** ⇒ 它们是**回归守卫**，不是新功能（docstring 已写明） |
> | （计划没提自证） | **➕ 6/6 逐条变异自证** | 4 条新测试是**先红后绿**（真 TDD）；另 2 条是**钉现有行为**的守卫 ⇒ 逐条把接线改坏、确认**真会红**、再还原（脚本 `/tmp/prove-resume.py`：**6/6 RED**，还原后 **6 passed**） |
>
> **判据（可打印）**：
> ```bash
> venv/bin/python -m pytest app/tests/test_approval_resume.py -q
> # ⇒ 6 passed
> venv/bin/python -m pytest app/ -m "not integration and not needs_db" -q
> # ⇒ 243 passed, 3 skipped, 22 deselected, 0 failed   （本轮之前 237 ⇒ +6，⛔ 无回归）
> ```
> ⚠️ **本 Task 只到"接线与语义"** —— **"上下文真的连续"没有端到端跑过**（要真 LLM + 真 `MemorySaver`，**联网花钱**）。
> ⇒ **硬门 D 的验收演示仍差这一步**，⛔ 别把"6 passed"读成"硬门 D 已验证"。


**Files:**
- Modify: `app/routing/api_v1_agent.py`（`/agent/approve`）
- Test: `app/tests/test_approval_resume.py`（**无 marker**，用假图）

**Interfaces:**
- Produces: `POST /agent/approve` 增加可选参数 **`edited_answer: str | None`**

- [x] **Step 1: 写失败测试**（用假图，⛔ 不真跑 LLM）

```python
# app/tests/test_approval_resume.py
"""接管后续跑（B6）。

🔴 判据（`通用/四硬门 §3-D`）：**接管后会话【上下文连续】** —— 不是重开一轮。

⚠️ 用假图替掉 `agent_graph`：真图要调 LLM，**那会让这条测试变成"要联网、要花钱"**。
   本文件测的是【接线与语义】，不是模型质量。
"""
import api_v1_agent as m


class _FakeGraph:
    """记录被怎么调用，并能按剧本返回。"""
    def __init__(self):
        self.calls = []
        self._state = type("S", (), {"next": ("approval",)})()

    def get_state(self, config):
        return self._state

    def update_state(self, config, values=None):
        self.calls.append(("update_state", values))

    def invoke(self, arg, config):
        self.calls.append(("invoke", arg))
        # 续跑时传的是 None（= 从 checkpoint 继续），⛔ 不是新的 HumanMessage
        return {"messages": []}


def test_resume_invokes_with_none_not_a_new_message(monkeypatch):
    """🔴 **核心判据**：续跑必须 `invoke(None, config)`。

    ⛔ 若某天有人改成 `invoke({"messages": [HumanMessage(question)]}, …)`，
       那就是**重开一轮** —— 上下文断了，而**接口返回看着一切正常**。
    """
    fake = _FakeGraph()
    monkeypatch.setattr(m, "agent_graph", fake)
    m.approve_agent_action(thread_id="t1", approved=True, user_name="admin")
    assert ("invoke", None) in fake.calls, f"续跑没有用 None 续跑：{fake.calls}"


def test_edit_note_is_written_into_state(monkeypatch):
    """改写后提交：人工改的答案要进 state，**否则改了等于没改**。"""
    fake = _FakeGraph()
    monkeypatch.setattr(m, "agent_graph", fake)
    m.approve_agent_action(thread_id="t1", approved=True, user_name="admin",
                           edited_answer="人工改过的答案")
    values = [v for (k, v) in fake.calls if k == "update_state"]
    assert any("人工改过的答案" in str(v) for v in values), (
        f"edited_answer 没有被写进 state：{values}"
    )
```

- [x] **Step 2: 跑，确认失败**

```bash
python -m pytest app/tests/test_approval_resume.py -q
```
预期：`TypeError: approve_agent_action() got an unexpected keyword argument 'edited_answer'`
（⚠️ 若它**直接通过**，说明 `invoke(None, …)` 那半条测的是现状 —— 那就把该断言留着当**回归守卫**，
并在 docstring 里写明"这是钉住现有正确行为的守卫，不是新功能"）

- [x] **Step 3: 给 `/agent/approve` 加 `edited_answer`**

> 🔴🔴 **勘误—— 下面这个代码块是【当时的计划原文】，⛔ 别照抄。**
> 它有两处**后来被真机推翻**的写法：
> ① `AIMessage(content=edited_answer)` —— **必须是 `ToolMessage` 按 `tool_call_id` 配对 + 显式 `as_node="tools"`**；
> ② 结尾**无条件** `resolve(thread_id)` —— **只有图真的走完才许注销**。
> 两处的后果与实测 ⇒ 本节上方  那一行 + 「⚠️ 看代码会误判的地方」表那两条。
> ⚠️ **为什么留原文**：它是**当时的决策依据**，删了就看不出"为什么当初会这么想"。

```python
@router.post("/agent/approve")
async def approve_agent_action(
    thread_id: str,
    approved: bool,
    edited_answer: str = None,                 # 新增：人工改写后的答案（可选）
    user_name: str = Depends(get_current_user_hybrid),
):
    """
    人工审批：批准 / 拒绝 / **改写后提交**。

    🔴 续跑用的是 `agent_graph.invoke(None, config)` —— **`None` 表示"从 checkpoint 继续"**，
       ⛔ **不是**新开一轮。改成传新消息 = 上下文断裂，而接口返回**看着一样**。
       守卫见 `app/tests/test_approval_resume.py`。

    `edited_answer`：人工把答案改过之后再放行。**不给就按原样续跑。**
    """
    from langchain_core.messages import AIMessage
    from pending_approvals import resolve

    config = {"configurable": {"thread_id": thread_id}}
    current_state = agent_graph.get_state(config)

    if current_state.next != ("approval",):
        resolve(thread_id)
        return {"status": "error", "message": "当前没有等待审批的任务"}

    if approved:
        if edited_answer is not None:
            # ⚠️ 用 update_state 把人工的改写**推进 messages**，而不是"跳过模型直接返回"
            #    —— 后者会让接下来的节点看不到这个改写。
            agent_graph.update_state(
                config, {"messages": [AIMessage(content=edited_answer)]}
            )
        result = agent_graph.invoke(None, config)      # ⭐ None = 续跑
    else:
        ...   # 拒绝分支：保持原样，只需在 return 前 resolve(thread_id)

    resolve(thread_id)
    return {"status": "approved" if approved else "rejected", ...}
```

- [x] **Step 4: 跑 + 提交**

```bash
python -m pytest app/tests/test_approval_resume.py -q                       # → passed
python -m pytest app/ -m "not integration and not needs_db" -q          # → 全绿
git add app/routing/api_v1_agent.py app/tests/test_approval_resume.py
git commit -m "feat(硬门D): B6 —— 接管后续跑可带人工改写，并用测试钉住'从 checkpoint 续跑'"
```

---

# 🔵 实施计划 ③ · **流式与取消**（立 · **执行 · 收口**）

> **来源**：`后端补齐清单-待裁-20260929.md` 的 **B1 · B2 · B3**。
> **作者已裁**：~~**B1 范围 = 只做 `/agent/langgraph_chat`**~~；**B2 验收 = 先以本机证据为准**。
> 🔴 **该范围【扩大】**：作者当天裁「**剩余 4 条链本轮一起做**」（含 `plan_execute`，不延后）
> ⇒  的完成口径从「**1 条**」变成「**5 条对话链全齐**」。见下方。
> ⚠️ **排最后** —— B2 自标「**最容易假完成**」，且它的落点**依赖 B1 做完**。
>
> 📌 **进度**： · · 🆕

## · Agent 端 SSE（**范围：只 `/agent/langgraph_chat`**）· ✅ **完成**

**Files:** Modify `app/routing/api_v1_agent.py`（导 `StreamingResponse`、新增流式路由）· Test `app/tests/test_agent_sse.py`

**Interfaces:** `POST /agent/langgraph_chat/stream` —— `media_type="text/event-stream"`

> ### ⛔ **一条硬约束（本次核查发现⑥）**
> **不能照抄 `api_v1_rag.py` 当时的写法** —— 那里 `.stream()` 是**同步迭代**，
> 在 async 生成器里 `for` **会阻塞事件循环**（当时靠每次 `yield` 后的 `await asyncio.sleep(0.01)` 让出）。
> ⇒ Agent 端要用 **`astream`** 之类的异步迭代。
> 🔴 **现状（剩余 4 条链）**：RAG 那处**已换成 `get_llm_stream.astream(messages)`**
> （`api_v1_rag.py:736`），**原 `:655-662` 那个反面例子已经不存在了** ——
> ⚠️ 所以**这条注释现在指不到现场**，改指 `app/routing/sse.py` 与 `app/routing/specs/sse.md` 的约束③。
> （`chunk_delay=0.01` 的限速**保留着**，⛔ 别删。）

> ### 🔴🔴 **计划没覆盖的那一句（实际做的时候才发现它才是难点）**
> 计划把这件事当成"**服务端加条 SSE 路由**"。**真正决定成不成的在图的另一侧**：
> `astream(stream_mode="messages")` 要出**真 token**，**节点必须声明 `config: RunnableConfig`
> 并把它转发进模型的流式调用** —— 否则它只吐 **1 块**（整段），
> ⚠️ **而接口长得一模一样**（照样 `text/event-stream`、照样 `data:` 帧）⇒ **计划里那条"契约测试"根本判不出来**。
> ⇒ **两条改动**（本文件 + `app/agent/agent_graph.py`），**裁定落 **。

- [x] Step 1 写失败测试（**测"是不是真流式"，不是"有没有这个路由"**）：
  ⚠️ **计划的写法（TestClient 断言 `data:` ≥ 2）【不够】** —— 它**假流式也能过**。
  ✅ **实际做法**：把"真流式"拆成**可在离线测的机制**，钉在 `app/tests/test_agent_sse.py`（12 例 · 纯离线 · 进 CI）：
  ① **块数**（`test_graph_streams_one_chunk_per_token` —— 数**块数**，⛔ 不看 header）
  ② **不重复**（`test_chunks_are_not_duplicated`）
  ③ **`tool_calls` 不丢**（`test_agent_decide_preserves_tool_calls`，B4 审批靠它）
  ④ **同步 `invoke()` 不能被弄坏**（`test_non_streaming_invoke_still_works`）
  ⑤ AST 判 `config` **真转发**了（⛔ 不是 grep 名字）
  ⑥ **空流不写 `None`**（`test_empty_stream_neither_writes_none_nor_returns_an_empty_answer`）
  ⑦ 端到端帧数 / 参数透传 / 停机登记 / 最终状态来源

- [x] Step 2 跑 → 失败 → Step 3 实现（**`astream` + `StreamingResponse`**）
- [x] Step 4 ⭐ **真服务的判据**（⛔ 别只用 TestClient）：

```bash
# 逐字节到达 = 真流式。看时间戳是否递增、连接是否保持不关闭
curl -N -s -X POST "http://127.0.0.1:8000/api/v1/agent/langgraph_chat/stream?question=你好&thread_id=t1" \
  -H "Authorization: Bearer $TOKEN" | while IFS= read -r line; do echo "$(date +%T.%3N)  $line"; done
```
  ⇒ 对照 `通用/四硬门 §3-A` 的 4 条判据（①逐字出现 ②`text/event-stream` **保持不关闭** ③**时间戳递增** ④日志里 token 计数**随 chunk 增长**）
  · ✅ **实测结果**：**29 个 token 帧，时间戳严格递增 `0.640s → 0.821s`**，随后 `status: answered` + `[DONE]`。
  · 🔴🔴 **这一步【真的抓到一个 bug】** —— 第二次调用（搜索类问题）返回 `status: "pending_approval"`，
    而图**其实已经跑完**。根因 = **结尾的 `summary` 是从"流过 `agent` 节点的块"攒出来的**，
    模型因工具返回"未找到工具"**重试**时节点进**多次** ⇒ 攒出了**上一轮的** `tool_calls`。
    ✅ **修法**：改从 **图的最终状态**取（`await agent_graph.aget_state(config)` ⇒ `summarize_agent_result`），
    与 `/agent/langgraph_chat` **同一套语义** ⇒ 再验：**216 个内容帧 + `status: answered` + `pending_tool_calls: null`**。
    📌 这条已经**进"看代码会误判"表**（本文件顶部）—— ⛔ 它是**计划里完全没写**的一步。
  · ⚠️ **本步的副作用（好的那种）**：真服务跑把**两个既有 bug**照了出来（`SENSITIVE_TOOLS` 默认值匹配不到
    任何真实工具 ⇒ **审批永不触发**；`tool_execute` 分派 `"search"` 而真名是 `duckduckgo_search` ⇒ **搜索永远失败**）
    ⇒ 详见 `app/agent/specs/agent_graph.md` 的 🟡 节。**两个都不是  引入的。**
- [x] Step 5 建/更 `app/routing/specs/api_v1_agent.md`（本文件）的状态行 → 提交
  · 同时更了 `app/agent/specs/agent_graph.md`（另一半）· 落

## · 服务端 cancel **传播到上游**（🔴🔴 自标「最容易假完成」）· ✅ **已做**

**Files:** `app/routing/api_v1_rag.py`（`stream_search`）+ `app/routing/api_v1_agent.py`（流式端点）+ `app/core/metrics.py`
Test `app/tests/test_cancel_propagation.py`（10 例 · 纯离线 · 进 CI）

**计划要补三件**：① 检测客户端断开（`request.is_disconnected()`）② **主动关上游 HTTP 流** ③ **`finally` 兜底**

> ### ⚠️ 执行下来：**①不用补 · ②③照做 · 而计划里的验收判据【落空了】**
>
> | 计划 | 实际 |
> |---|---|
> | ① `is_disconnected()` | ⛔ **不用写** —— uvicorn 报 `spec_version 2.3` ⇒ **Starlette 已经替我们监听 `http.disconnect` 并取消生成器**。真正的缺口**只有②**（§①不用补） |
> | ② 关上游 | ✅ 上游改 `astream`（同步 `for` 会**阻塞事件循环**，取消得等下一块）+ `finally: await stream.aclose()` |
> | ③ `finally` 兜底 | ✅ 且**必须**放 `finally` —— Starlette 有**两条**关闭路径（2.3 抛 `CancelledError` / 2.4 抛 **`GeneratorExit`**），后者**不是** `CancelledError` 子类 ⇒ 只写 `except` 会**静默不记** |
> | 🔴 判据 ③「**token 计数停止增长**」 | ⛔ **该计数在流式路径上不存在**（`grep -ci token app/core/metrics.py` = 0；PG 记账**只在生成结束后整笔写**）⇒ **判据无法证伪 = 任何实现都能通过**。<br>✅ 换成：① 日志有 `[cancel]` ② `stream_cancelled_total{endpoint}` **+1** ③ `outcome == "cancelled"`（**循环没跑完** ⇒ 比"计数涨了"更接近"上游真停了"） |

> ### ✅ 验收口径（**作者 已裁：先以本机证据为准**）
> ⚠️ **已知代价仍然成立**：**证明不了"上游计费真的停"** —— 本机没有 DashScope 侧账单。
> ⇒ **接受它**；上云后（阶段⑦/⑧）若有机会再补真链路，但**不作为本轮验收前提**。
> ⛔ 因此本轮**不许**把"我们关了流"说成"账单停了"（§遗留·2）。

- [x] Step 1 写测试 —— ⛔ **⾏不通**：计划里的 `test_token_counter_stops_after_cancel` **写不出来**
      （被测对象不存在，见上表）。改写为 `app/tests/test_cancel_propagation.py`（10 例）——
      **钉在 ASGI 层**：把 `StreamingResponse` 当 ASGI app 跑（`spec_version="2.3"`，与 uvicorn 实测一致），
      `receive` 在第 N 帧后回 `http.disconnect` ⇒ 与真服务器**走同一段取消代码**
- [x] Step 2 跑 → **红**（⚠️ 第一版假上游写成 `async def` + `yield`，**假绿** —— `asyncio.run()` 收尾的
      `shutdown_asyncgens()` 会替我们把它关掉 ⇒ 改成**手写迭代器**，`closed` 为真**只能**是我们调的 `aclose()`）
- [x] Step 3 实现（⚠️ `finally` 是**主路**不是兜底 —— 三条出口都要关流）
- [x] Step 4 ⭐ **真服务（本机）**：`curl --max-time` 切断，两条端点各验一遍
      —— ✅ 计数 1.0 → **2.0**（rag）/ **→ 1.0**（agent）+ 各有 `[cancel]` 日志行
      ⚠️ **两个坑记在 **：中间件时长**不是**生成时长 · 本机**有语义缓存**（换问句才能测）
- [x] Step 5 提交，并在 `app/routing/specs/api_v1_rag.md` 里把「不关上游 HTTP 流」那条**划掉**（本文件同改）

## · ✅ **已做**（「先核」核出了别的东西）

**核的结果（判据两条都没过，①的根因比判据假设的大一圈）：**

| 判据 | 核出来什么 |
|---|---|
| **①** cancel 后已产生的 token **有记账** | ❌ **落空，且不是"取消时没记"** —— `grep -c record_usage app/routing/api_v1_rag.py` ⇒ **0**、`app/rag/rag_pipeline.py` ⇒ **0** ⇒ **RAG 侧四条调 LLM 的路径从来不记账**（成功也不记）。真库佐证：非 embedding 行**全库只有 6 行**，全是 的 agent graph 运行 |
| **②** 半截答案处理方式**明确** | ⚠️ 现状是**丢**，但**不是决定、是碰巧** —— `append_chat_history` 写在循环之后，取消在它之前 `raise`；⚠️ **用户那句提问跟着一起丢** |

⚠️ **还有一条技术上绕不过去的**：取消瞬间的 token 数**协议上拿不到** ——
~~`app/core/llm_factory.py` 没开 `stream_usage`，~~ usage 只在**最后一帧**回来，而我们提前 `aclose()`
那一帧**永远不会到**。⇒ 硬补只能估算 = **往账本写假数**，比空着更坏。

> 🔴 **更正两条**（复盘）：
> ① **划掉的那半句是错的** —— 实测本仓 provider **默认 `stream_usage=False` 也**拿得到
> `usage_metadata`（挂在最后一帧、`content=''` 上）⇒ **"取消仍补不了"的结论不变**，
> 但**理由是"那帧到不了"，⛔ 不是"没开某个开关"**。
> ② **上表 ① 那格已【全部消账】** —— 非流式两条链  · 流式 `/rag/stream_search`
> `/ws/agent` ⇒ ⛔ **别再把"RAG 侧从来不记账"当作现状**（那是 的快照）。

**决策**：**存**，不是丢 ——
① 作者原意就是存（`api_v1_rag.py:667` 注释写着"**使它支持历史补偿**"）；
② 存的形态 = 提问 + 半截 + `INTERRUPTED_SUFFIX` 标记（**成对写**，标记**必须**有，否则下一轮 prompt 会把断话当说完）；
③ 落点是 **`finally`**（⛔ 不是 `except CancelledError` —— 2.4 分支抛 `GeneratorExit`，同）；
④ 一块都没生成 ⇒ **什么都不写**。

**Agent 端这边【不用改】** —— 它的"半路状态"由 langgraph 的 checkpointer（`MemorySaver`，
`app/agent/agent_graph.py:250`）持有，取消时**已经落在里面**了；RAG 端什么都没有 ⇒ 两端本来就不对称。
⚠️ **`MemorySaver` 是【进程内存】** ⇒ 与我们自己的 `pending_approvals` 同一个限制：**重启即空**。

- [x] Step 1~3 测试先红后绿（`app/tests/test_cancel_propagation.py` 10 → **13 例**）
      —— ⚠️ 第三条（"一块都没生成就不写"）**红不出来**（改动前它本来就过）⇒ 它是**反面守卫**，防"修过头"
- [x] Step 4 ⛔ **没起 Docker 真服务**（用户本轮手动关了 `qingyun-rag-agent-api-1`）—— ⚠️ **这次是能省的**：
      本任务的判据落在**进程内**，而测试走的是**真 ASGI 断开**（与  同一段取消代码），
       那轮必须真服务是因为要证「Prometheus 计数在真 uvicorn 下也涨」，这一轮没有同类的"跨进程"观测对象。
      ⚠️ **仍未端到端验的是**：「**下一轮 prompt 真的读到了那半截**」——
      那要真 LLM + 真 Redis 续问一轮（本机 `MemorySaver`/Redis 都在，**是可做的，只是本轮没做**）

- [x] Step 5 写进  + `app/routing/specs/api_v1_rag.md`（本文件同改）

## · ** 剩余 4 条链** · ✅ **完成**（作者当天裁「本轮一起做」）

**Files:** `app/routing/api_v1_agent.py`（+4 路由 · 抽走自己的 SSE 生成器）· `app/routing/api_v1_rag.py`（同改）·
**新建** `app/routing/sse.py` + `app/routing/specs/sse.md` · **新建** `app/tests/test_agent_stream_chains.py`
图侧 4 个模块（见下表）· `app/agent/plan_execute.py`

**Interfaces:** `POST /agent/{advanced_chat, plan_execute, memory_chat, mcp_chat}/stream` —— 均 `text/event-stream`

> ### 🔴 **落地形状：抽共享层，⛔ 不是复制第 5、6 份内联生成器**（作者 裁）
> `app/routing/sse.py` 收的是 **5 条实测出来的顺序约束**（同步收尾排在 `await` 前 / shield 关上游 /
> ⛔ 不吞 `CancelledError` / `X-Accel-Buffering: no` / 汇总只从图状态取），
> **⛔ 不是"重复代码"** —— 判据见 `app/routing/specs/sse.md` 的 ①~⑨ 表。

**四条链**（⚠️ **每条形态都不同，⛔ 别互相照抄**）：

| 链 | 端点 | 图 / 实现 | 改了哪个模块 | 形态 |
|---|---|---|---|---|
| A | `advanced_chat/stream`（`:585`） | `advanced_agent` | `agent_graph_advanced_learning.py`（**4 个节点**） | 同步节点 · 🔴 **必须 `subgraphs=True`** |
| B | `memory_chat/stream`（`:982`） | `checkpointer_agent` | `agent_checkpointer.py`（1 个） | 同步节点 · 带 `interrupt_before` |
| C | `mcp_chat/stream`（`:1349`） | `mcp_agent` | `agent_graph_advanced.py`（2 个） | ⚠️ **节点是 `async`** ⇒ 用 `astream` |
| D | `plan_execute/stream`（`:809`） | **不是图** —— `plan_task()` 同步函数 | `plan_execute.py`（`_invoke_llm` 加 `on_token`） | 🔴 **线程 → 事件循环**，见下 |

- [x] Step 1 **Step 0 三个 spike 先跑**（仓规：「**写不出命令的，就是还没核过**」）
      ⇒ ①子图节点名 ②`usage_metadata` 还在不在（**真打 API**）③规划段流出的是什么
      ⚠️ ②的结论**与预想相反**（usage 挂在**最后一块**上）⇒ 换来一条实现约束：**聚合必须遍历所有块**。
      🔴 **附带撞出一个既有 bug**（`agent_checkpointer.py:83` 的 `hasattr(response, "usage")` **恒为 False**
      ⇒ 链 B 的记账**从来没执行过**）—— ⛔ **本轮不修**（修了=开始拦人=行为变更，要单独裁）。
      ✅ **当天晚些时候已修**（就是那次「单独裁」）：判据换成 `record_from_response`，
      现于 `app/agent/agent_checkpointer.py:110`；那行 `hasattr` 作为**墓碑注释**留在 `:95`。
      勘察 ⇒ `qingyun-rag-agent-TODO待办/硬门A-Agent端流式勘察-20261003.md` §8.5
- [x] Step 2 建 `app/routing/sse.py` + spec + `app/tests/test_sse_layer.py`（**15 例**）
- [x] Step 3 两条既有点端改用它 ⇒ ⭐ **判据 = 两份既有用例"全绿且 diff 为空"**
      （`app/tests/test_agent_sse.py` + `app/tests/test_cancel_propagation.py` —— ⚠️ 原写 **44 passed**，**命令与数字对不上**
      （44 = 当时那两份的 **29** + `app/tests/test_sse_layer.py` 的 **15**）⇒ 评审收口按**实测**更正为 **31 passed**）—— ⛔ **没有新加断言**
- [x] Step 4 改节点（A 4 个 · B 1 个 · C 2 个）—— **每个只加 `config` 形参 + 换流式 + `+` 聚合**
      ⚠️ **⛔ 没有把任何同步节点改成 `async def`**（：同步 `graph.invoke()` 会当场 `TypeError`）
- [x] Step 5 四条新路由（**两条前置闸照抄，⛔ 不挪进共享层** —— 两个 AST 守卫挖的是函数体内部）
      · 🔴 **链 D 的桥**：`_ThreadTokenBridge`（`:562`）—— `plan_task` 跑在 `asyncio.to_thread` 里，
      token 靠 `loop.call_soon_threadsafe(queue.put_nowait, …)` 回主循环；`aclose()` **协作式**
      （置标志 + 排空 + 取消任务），⛔ **不 `await thread.join()`**（线程杀不掉）。
      ⚠️ **它用 `extract=None`**（桥吐出来的**就是文本**）—— 同族的 `graph_message_text` 会 `ValueError`。
      ⚠️ **链 D 只流「规划段」** ⇒ 之后是**一长段静默**（`execute_plan` 不流），然后才是末帧 —— ⛔ **不是 bug**。
- [x] Step 6 新测试 `app/tests/test_agent_stream_chains.py`（**39 例** —— 收工时的数；⚠️ 评审收口后 **47 例**，夹具**复用**既有两份，⛔ 不新造一套）
      四条链各测：①**逐 token 出帧** ②**汇总来自 `aget_state`**（⛔ 不是攒块）③**断连 ⇒ 关上游 + 计数 + 日志**
      + A 链**额外**：`supervisor`/`calc_execute` 的字**一帧都不许漏**（那条 ⭐ 表的红线）
      + D 链**额外**：`_invoke_llm` 换流式后**仍然记账**（⛔ 不能拿"接口正常"代替）
- [x] Step 7 文档收口（本文件 · `sse.md` · `api_v1_rag.md` · 4 份图 spec · `待办总表`
      `接口契约` · Postman · `CHANGELOG`）

⚠️ **本批唯一一处"改了线上字面"**：Agent 链取消时那句日志从
**「已停止生成并关闭图的流」** 变成共享层的 **「已停止生成并关闭上游流」**
—— ⛔ **没有测试断言这句**，且**它只在日志里**（前端/接口看不到）。
📌 **它是"两条链各自措辞"被共享层统一后的必然结果**，有意为之 ⇒ 记在。

---

## 🆕 · 「外部检索与工具Agent编排」那一页接线的三笔

### 1 · 13 条端点补上 `response_model`

`langgraph_chat` · `mcp_chat` · `plan_execute` · `trace/{thread_id}` · `trace/{thread_id}/cost`
`pending` · `pending/context` · `approve` · `approvals/history` · `available_tools`
`budget/intercepts` · `memory/search` · `memory/add`。
模型与逐条口径 ⇒ `app/routing/specs/schemas.md` 末尾那一节。
⚠️ 同一条门规：被前端调用的端点**当场补声明**，⛔ 不进 `PENDING`。

### 2 · `langgraph_chat` 开始写轨迹轴

改前 `start_trace` 只有 `mcp_chat` / `mcp_chat/stream` / `rag/stream_search` 三条在调
⇒ **主链没有"每一步打了谁 · 花了多久"**，而那一页正是靠它。
现在：**起表排在两道门【之前】**（被拒的那次也留得下痕迹，照 `mcp_chat` 的位置）·
**收表只在【答完】那一支** —— 停在审批点**不收**，让批准后的续跑继续记进同一条轨迹。
⚠️ 代价：那一格 `duration_ms` 取 `end_time or now` ⇒ **"从发起到现在"**，页面上写明了口径。

### 3 · 三条流式端点进那道门的 `NOT_JSON`

`langgraph_chat/stream` · `mcp_chat/stream` · `plan_execute/stream`（SSE · 200 不是 JSON 体）。
⚠️ 页面侧的分帧纯逻辑在 `app/static/js/agent.js` 的 `parseFrames()`（**半帧必须缓冲** · 有 node 用例）。

---

## 🆕 · 两条工具端点补上 `response_model`

`GET /agent/tool_health` → `AgentToolHealthResponse` · `GET /agent/tool_versions` → `AgentToolVersionsResponse`
（📄  —— 「系统观测」页打这两条）。

⚠️ **同一次接线里，这一页还故意【没有】用 `POST /agent/tool_health/refresh`** ——
它会 `await run_health_check()` **逐条真调工具**（含 `web_search` 真联网、`execute_python` 真执行代码）
⇒ 访客点一下就会真的跑起来。权衡与理由 ⇒  §2.3。
