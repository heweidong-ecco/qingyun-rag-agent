# `app/billing/token_tracker.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用** —— ⚠️ **2026-10-03 起它不再是"三套口径"之一**（次数那套已删）<br>🟢 **①a 已落地（2026-10-01）**：额度常量已收口到 `app/billing/token_config.py`（本文件**只剩同名别名**）· 本文件下方 **实施计划 ①a** 已执行完<br>🔵 **①b（2026-10-02）**： ✅ /  ✅（B7 接线）/ ** ✅（B8 会话级 · 已接 7 条链）** / ** ✅（B10 全局日级 · 判定函数）** / ** ✅（B11 熔断 · 已接 8 处， 由此生效）** / ** 🟡 部分**（改写后只做构造收口，⛔ 自动兜底【推迟】—— 见该 Task 的修订块） / ** ✅（`决策一` 落地 ·  —— 撤次数配额、原位换 token 口径 = `R1.3`）** / ** ✅（实跑核成本可见 ·  —— `①b` 收尾）**<br>⚠️ ** 曾一度"有函数没接线"（2026-10-01 当天）—— 那句话已作废**，2026-10-02  接上了<br>🔴 **2026-10-04：新增 `record_from_response()` —— 【取用量的唯一实现】** —— 962 → **1027 行**。改前"从响应取 usage"这件小事**在每条链里各写一遍**，于是三条链里有的**写错了属性名**（`.usage` 恒假）⇒ **静默不记账**。现在**三张图 9 个调用点**统一调它，⛔ 不许再各写各的。<br>⚠️ **它只做「取+记」，⛔ 不做「拦」** —— 拦是 `check_token_budget` 的事，由**调用方在 `.stream()` 之前**自己调 |
| **对外提供** | `record_usage()`（`:71`）· 🆕 **`record_from_response()`（`:159`）** · `record_cost()`（`:216`）· `check_multilevel_budget()`（`:789`）· `check_token_budget_detail()`（`:440`）· `get_token_budget_info()`（`:477`）· **10 个汇总函数**（2026-10-03 起 +`get_user_overview`）· 🆕 常量 `BUDGET_EXCEEDED_MSG`（`:156`）<br>🔵 **2026-10-03 起**：`get_token_budget_info()` 还被 **`main.QuotaMiddleware`** 消费（全路径按用户日级 = `R1.3`）—— **它不再只是"记账/看板"用的** |
| **谁在用** | `permission`（取角色）· `agent_graph_advanced.py:239`（唯一调多级预算的地方）· `cost_dashboard.py` · 各 `api_v1_*.py`<br>🔴 **2026-10-04 起新增 3 个图模块**：`agent_graph.py` · `agent_checkpointer.py` · `agent_graph_advanced_learning.py`（都是 `check_token_budget` + `record_from_response` 成对）<br>✅ **2026-10-05 又 +3**：`rag_pipeline.py` · `answer_with_citations.py` · `query_rewriter.py`（**`query_rewriter` 用 `record_usage`** —— 它是裸 `openai.OpenAI`，响应只有 `.usage`）<br>✅ **2026-10-06 再 +1**：**`api_v1_rag.py`**（`/rag/stream_search` 的流式答案 —— `_StreamUsageTap` 在收尾出口调 `record_from_response`）<br>⚠️ **判据**：`grep -rn "record_from_response" app/*.py \| grep -v "^app/test_"` ⇒ **9 个文件**（⚠️ **含注释里的提及** —— `query_rewriter.py` 是**反面**那个：它**必须**用 `record_usage`，注释里写着"⛔ 不能用 `record_from_response`"，⛔ 别把它读成记账点） |
| **规模** | **1027 行**（2026-10-04  重取；此前 962） |

## ✅ 做了什么

- **按 model 记账**：`record_usage(model=…)`（`:71`）· `PRICING` 表 + 兜底单价（**同名别名**，`:56-60`，真身在 `token_config`）· `by_model` 汇总（`:606`）
- 🆕 **`record_from_response(llm_obj, response, purpose, *, user_name, thread_id, …)`（`:159`）** ——
  **从一次 LLM 响应里取用量并记账的唯一实现**。取 `getattr(response, "usage_metadata", None)`；
  无 usage ⇒ **返回 `False`、静默跳过**（⛔ 不写 0 行）；有 ⇒ 调 `record_usage(...)` 并返回 `True`。
  ⚠️ **参数 `llm_obj` 只是为了取 `model_name`**（`getattr(llm_obj, "model_name", None) or getattr(llm_obj, "model", "unknown")`）。
- 🔵 **2026-10-06（契约 B 前置）：抽三个纯函数，算式不再有第二份**
  · **`resolve_model_name(llm_obj)`（`:166`）** —— 模型名的**唯一实现**
    （原先是 `record_from_response` 与 `usage` 帧各写一遍 `getattr`；本仓因"模型名两个来源"按错单价记过账）
  · **`compute_cost(model, prompt_tokens, completion_tokens)`（`:179`）** —— 成本算式**唯一实现**
    （⚠️ 改前**同一个算式在本文件里就有两份**：`record_usage` 与 `record_cost`）
  · **`usage_summary(llm_obj, response)`（`:190`）** —— 把响应整理成 `usage` 帧的载荷，
    键固定 `{model, prompt_tokens, completion_tokens, cost_usd}`；**没带用量 ⇒ `None`**。
  📌 判据：`app/tests/test_token_tracker_cost_helpers.py`（**5 例**）
  ⚠️ **`usage_summary` 返回 `None` 时调用方必须【不出帧】** —— 「记账了才出帧」是一条判据，
  它保证**帧里报的钱与账本里的钱同一时刻、同一来源**（守 ⇒ `app/tests/test_frontend_contract.py`）。
- **多级预算**（`:789` `check_multilevel_budget`）—— **自标「3 级」**：
  ① 单次上限（**元**，`MAX_SINGLE_CALL_COST=0.5`，`:787` 从 `token_config` import）
  ② 单线程上限（**元**，`MAX_THREAD_COST=5.0`，`:787`）
  ③ 每日预算（**token**，本文件**已不再持别名** —— 🔴 2026-10-11 起走 `quota_pools.total_daily_limit()`，它由 `token_config` 的**两张池表**相加得来）
- **10 个汇总函数**（⚠️ 行号 2026-10-04 重取）：`get_daily_token_usage`(:257) · `get_user_summary`(:316) · `get_purpose_summary`(:323) · `get_thread_summary`(:328) · ⭐ **`get_user_overview`(:336) ←  新增** · `get_recent_usage`(:399) · `get_token_budget_info`(:477) · `generate_monthly_report`(:493) · `get_daily_usage_cost`(:722) · `get_intercept_count`(:772)
- **拦截记录**：`record_intercept`(:746)
- 🆕 **2026-10-06：`thread_cost_breakdown(user_name, thread_id, *, include_all=False)`** ——
  **逐笔**花费明细（成本轴的读侧出口，给 `/agent/trace/{thread_id}/cost` 用）。
  回 `{items, total, truncated}`；`items` 按 `created_at DESC`、上限 `_BREAKDOWN_LIMIT = 2000`。
  🔴 **`total` 由 SQL 对【整条线程】聚合，⛔ 不是对 `items` 求和** ——
  明细有 `LIMIT`，让页面自己加会在笔数多时**静默偏小**且不报错。
  🔴 **它带 `user_name`**（`WHERE user_name = %s AND thread_id = %s`）；
  `include_all=True`（admin 例外）把条件换成 `1 = 1`，**⛔ 走同一条 SQL 不写第二份**。
  · 同批加**私有**辅助 **`_iso_utc(dt)`** —— 把库里的**无时区**时间戳标成 `+00:00`（见下方「看代码会误判」）。
  📌 判据：`app/tests/test_trace_cost.py`（**15 例** · 假 pg · **进 CI**）·
  `app/tests/test_trace_cost_db.py`（**9 例** · `needs_db` · **不进 CI**）
- 🔴 **工具级的两张预估表** —— `TOOL_ESTIMATED_COST`（**元** · 供**对外展示**）与
  `TOOL_ESTIMATED_TOKENS`（**token** · 供**预算判定**）· 取用函数
  `estimate_tool_cost()` / `estimate_tool_tokens()`。
  ⚠️ **两套必须并存、⛔ 不可互相替代**（历史事故：判定逻辑误用「元」表去和 Token 预算相减
  ⇒ 三处闸门恒放行 —— 见文件下方「两套表」那段）。
  🔴 **2026-10-08：各 6 → 9 条**（+`date_calc` / `json_extract` / `stats`，**全是 0**）。
  🔴 **它是「加一个工具要登记在五处」的其中一处**，且**改前零守卫** ⇒ 已由
  `app/tests/test_tool_registration_completeness.py` 补成**会红的断言**（见下方 ⚠️ 表）。

## 🟡 做到哪 / 缺什么

- 🟡 **额度常量散在 3 个文件 5 处**（本文件的 ~~`ROLE_TOKEN_BUDGET`~~（🔴 2026-10-11 已删）/ `MAX_THREAD_COST` / `MAX_SINGLE_CALL_COST` / `PRICING`、`plan_execute.PLAN_TOTAL_BUDGET_SECONDS`）
  —— 原为「4 个文件 6 处」，**2026-10-03 少了 `permission.ROLE_QUOTA`**（删掉）
- ✅ ~~🔴 **`"admin": float("inf")`**~~ ⇒ **2026-10-01 已去**：现在是有限值 = `premium` = 100000/天。<br>⚠️ **但"全局日级"仍是另一个东西**（B10，在 `①b`）—— per-user 检查**永远看不到「大家加起来超了」**
- ✅ ~~🔴 **没有「会话级」上限**~~ ⇒ **2026-10-01 有了**（`①b`）：
  `get_session_token_usage` / `check_session_token_budget`，**接在 7 条真调 LLM 的对话链上**。
  ⚠️ **数据源是 `token_usage_logs` 表，⛔ 不是 `_thread_summary`** —— 内存**重启即清零**，拿它当上限等于"重启就能绕开"
- ✅ ~~🟡 **「全局日级」**~~ ⇒ **建了函数（2026-10-01）+ 接了线（2026-10-02）**：
  `get_global_daily_token_usage` / `check_global_daily_budget`，**已接进 `breaker.py`**（8 处调用点）。
  🔴 **2026-10-03又给它补了【出口】** —— 在那之前它**只有入口没有出口**：
  超了所有人吃 429，**界面上却看不到逼近**。
  现在 `/agent/token/budget` 与看板第 5 格都报了 `global_used_today` / `global_remaining`
- 🟡 **测试覆盖【2026-10-04 起不再为零】**（原先 `docs/说明/测试.md` §六 **#8** 记"零覆盖"）：
  新增 `app/tests/test_token_budget_hookup.py`（**5 例**，钉新函数 `record_from_response`：正常路径 · **旧错属性墓碑** · 无 usage 静默跳过 · 模型名兜底 · 多模态只取 `input/output_tokens`）。
  ⚠️ **覆盖的是新函数那一小块** —— 本文件的**其余部分（10 个汇总函数 / 多级预算 / 熔断计数）仍无测试**。
- ⬜ **R2.2 恢复条件未核** —— `EXPIRE 86400` 是首次 INCR 时设的（滚动），**没人实测过 TTL**

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「全站那道闸已经有了，embedding 也管着」** | ⛔ **不是** —— 2026-10-14 之前只有 `check_global_daily_budget`，它数的是**全部 purpose** 的**全站 LLM 口径**。而 **embedding 的约束是【另一把尺子】**：DashScope 那把 key 的免费额度 = **100 万 token【总量】/ 90 天**。<br>🔴 **缺口算得出来**：发号闸 300 人/天 × 访客 10 000/天 = **一天上限 300 万** ⇒ **8 小时烧光 90 天**。<br>⇒ 新增 `get_global_embedding_usage(hours)` + `check_global_embedding_budget()`（§二·8 需求方裁「甲」），接在 `main.QuotaMiddleware` 里（**按池** ⇒ 只对 `POOL_EMBEDDING` 的路径生效）。<br>⚠️ **⛔ 别把这两个函数跟全站日级那个合并成"加个开关"** —— 理由见 `get_global_daily_token_usage` 的 docstring：漏传参数会**静默**退化成另一个口径。 |
| 🔴 **「这道闸可以放进 `embedding_client.get_embedding()`」** | ⛔ **不行** —— 那个函数在**启动时的缓存预热**里也会被调（容器日志里那 10 条 `[Token] embedding … 用户: system`）⇒ 额度用完后**应用会起不来**。<br>⇒ 落在**中间件**（handler 之前 ⇒ 拒绝时一个 token 都还没花；且**预热不走 HTTP**）。<br>📌 判据 ⇒ `app/tests/test_global_embedding_gate.py`（6 条，含**正控**：⛔ 不许拦到 LLM 池） |
| 🔴 **「`_iso_utc` 收到的一定是 `datetime`」** | ⛔ **不是** —— 2026-10-14（的甲）实测：**SQLite 那条路上 `created_at` 回来是 `str`**（那列类型是 `TIMESTAMP`，而 SQLite 动态类型 ⇒ 存的就是文本）⇒ 对着它调 `dt.replace(tzinfo=…)` 当场 `TypeError: str.replace() takes no keyword arguments`。<br>⇒ 该函数**先解串再补区**。⚠️ **⛔ 别图省事"原样返回那个串"** —— 它**不带时区**，前端会按**浏览器本地时间**解析 ⇒ 东八区用户看到的时刻**静默早 8 小时**（这个函数存在就是为了这件事）。<br>📌 判据：`app/tests/test_trace_cost_db.py`（两个后端都要过） |
| 🔴 **「多级预算是硬拦截」** | ⛔ **不是** —— `agent_graph_advanced.py:239` 超预算时是往图里**塞一条 `ToolMessage` 文本提示**，**HTTP 仍是 200**。**前端看不出"被拒了"** |
| 🔴 **「这个文件管所有配额」** | ⚠️ **2026-10-03 起：是的**（原先"不是"）。<br>**原先**另有 `permission.ROLE_QUOTA` + `quota_limiter.py` 那套「每日**请求次数**」，**与 token 互不知情**（实测**差 35 倍**）。<br>⇒  把那套**整张删掉**，配额**只剩 token 一套**（金额那套是同一物不同单位，经 `PRICING` 换算）。<br>⚠️ **但它仍不是"所有路径"** —— 挂多级预算的只有 `/agent/mcp_chat` 一条；<br>**全路径那层在 `main.QuotaMiddleware`**（消费本文件的 `get_token_budget_info`）。 |
| 🔴🔴 **「那三个 `get_*_summary` 是通用查询，哪儿都能用」** | ⛔ **不能当对外展示的数据源** —— `get_user_summary` / `get_purpose_summary` / `get_thread_summary`（`:252/:259/:264`）读的是**进程内存**（`_user_summary` 等三个 `defaultdict`，只在 `record_usage` 里累加、**从不回读 DB**）⇒ **重启归零**。<br>⚠️ 它们**不是坏的** —— 语义本来就是"**本进程**这段时间花了多少"，`:148` 的即时花费告警**正需要**这个。<br>🔴 **坏的是拿它们当展示口径**：`/agent/cost/overview` 原来就这么干，实测 admin 在库里有 **4216 tokens**、它答 **`0`** —— **不报错、界面照常出数**（`①b`  核出来）。<br>✅ 展示走 **`get_user_overview`**（读库）；⚠️ **另一个坑**：`get_purpose_summary()` 还**不收 `user_name`** ⇒ 它一直是**全站**口径。<br>📌 判据（可打印）：`app/tests/test_cost_visibility.py` |
| ⚠️ **「两个单位混着 ⇒ 是 bug」** | 🟢 **不是** —— 第一二级（元）与第三级（token）**量纲本来就不同**，代码注释 `:665` 明说「**别统一掉**」 |
| 🔴 **「工具估算表漏登记一个也没人会发现」** | ⚠️ **改前确实如此** —— `estimate_tool_tokens` 的兜底是 **`DEFAULT_ESTIMATED_TOKENS = 500`**，所以**漏登记不报错**，只会让一个**本地免费**工具被当**花 500 token** 算（可能误触预算门）。<br>🔴 **2026-10-08起有守卫**：`app/tests/test_tool_registration_completeness.py` 逐个已注册工具查**两张**表（本仓立场：**「从不命中」与「没人违规」在机器痕迹上完全一样**）。 |
| ⚠️ **「`ROLE_TOKEN_BUDGET` 就是最终日限额」** | ⚠️ **只对 `_invoke_llm` 那条链**。**挂多级预算的只有 `/agent/mcp_chat` 一条**（`check_multilevel_budget` 全仓唯一调用点在 `agent_graph_advanced.py:239`）⇒ **其他链全无预算** |
| ⚠️ **「本文件定义着 `PRICING` / `ROLE_TOKEN_BUDGET` / `MAX_*_COST`」** | 🔴 **2026-10-01 起【只是别名】** —— 真值在 `app/billing/token_config.py`（`PRICING is token_config.MODEL_PRICING` → `True`）。⇒ **改价改额度请去 `token_config.py`**。<br>🔴 **2026-10-11 更新**：`ROLE_TOKEN_BUDGET` 这个别名**已从本文件删掉**（两池分开后没有"每个角色一个数"），额度一律走 `quota_pools`。⚠️ 老文档提到它时**按本条读** |
| ✅ ~~🔴 **「`check_global_daily_budget` 存在 ⇒ 全站额度在管着」**~~ | ✅ **2026-10-02 起【是的】—— 这句话已经翻面，⛔ 别照旧理解。**<br>**2026-10-01 当天**确实如原文所说「**没有任何调用点**」；**把它接进了 `breaker.py:75`** ⇒ 现在**真的在管着**。<br>**判据（可打印）**：`grep -rn "check_global_daily_budget" app/ --include="*.py"` ⇒ 应命中 `breaker.py` 的 `:74/:75`（接线）**与** `token_tracker.py:907`（定义）。<br>📌 **保留这一行的理由**：它是「**常量/函数建好没接上**」（本仓第三次： 前、 前）的标本 —— 但**标本的意思是"当时没接"，不是"现在没接"**。⚠️ **这类行的有效期很短，读到请先跑判据。** |
| 🔴 **「`get_global_daily_token_usage` 与 `get_daily_token_usage` 差不多」** | 差的正是**全部**：前者 SQL **⛔ 不许有 `user_name`**（全站），后者**必须有**（单用户）。<br>⚠️ 抄后者改前者时**漏删** `WHERE user_name` ⇒ 函数名还叫「全局」、**返回值正常、只是偏小**、**没有任何报错** ⇒ 本仓**永远不会有全局额度**。<br>⇒ 已用 **AST 静态守卫**钉死（`app/tests/test_global_daily_budget_offline.py` 的**配对**测试：一边必须有、一边必须没有） |
| ⚠️ **「`GLOBAL_DAILY_TOKEN_LIMIT` = 1,000,000 是个随手写的默认值」** | 🟡 **2026-10-01 起它变成了【裁定值】**—— 需求方在源文档 B10 那个空上填的。⚠️ 但**它仍不在环境变量契约里**（`.env.example` / `docs/契约/环境变量.md` 都无此项），想不改代码调它**得先补契约** |
| 🔴 **「从响应取用量，`getattr(response, "usage", None)` 就行」** | ⛔ **属性名是 `usage_metadata`** —— `AIMessage` / `AIMessageChunk` 上**没有 `.usage`**，`hasattr` **恒为 False**。写错的后果是**静默不记账**（接口一切正常，只是没账）。本仓**真的栽过**：`agent_checkpointer.py` 那条链**从建立起就一笔都没记**。<br>✅ **统一走 `record_from_response`**（`:159`）—— ⛔ 别在各链里自己 `getattr`。 |
| 🔴 **「`record_from_response` 是全能的：取用量 + 拦预算」** | ⛔ **它只做「取+记」，⛔ 不做「拦」** —— 拦是 `check_token_budget` 的事，**必须由调用方在 `.stream()`/`.invoke()` 之【前】**自己调。<br>⚠️ 顺序反了（先调用后检查）⇒ 钱**已经花了**，只能丢结果、拦不住。 |
| 🔴 **「成本/模型名随便在哪算都行，反正同一个算式」** | ⛔ **2026-10-06 起不行了** —— 算式**只有一份**（`compute_cost` `:179` · `resolve_model_name` `:166`），**账本与 `usage` 帧共用**。<br>⚠️ 再在别处 `getattr(llm, "model_name", ...)` 或手写 `prompt/1000*pricing` **就是造第二份实现** —— 两处一旦漂移，**帧里报的钱与账本里的钱会对不上，而两边都不报错**（本仓因"模型名有两个来源"按错单价记过两次账）。<br>📌 判据：`app/tests/test_token_tracker_cost_helpers.py` |
| ⚠️ **「`usage_summary` 返回 `None` ⇒ 那就出个空帧/零值帧」** | ⛔ **必须【不出帧】** —— `None` 的含义是「这次响应里没有用量」（被拦下 / provider 没回），不是"花费为 0"。<br>⚠️ 出零值帧会让前端显示 **`$0.000000`**，而那是在说"这轮不要钱"。⇒ 「**记账了才出帧**」是判据，不是建议。 |
| 🔴 **「`record_from_response` 返回 `False` = 出错」** | ⛔ **`False` = 「这次响应里没有 usage」**（如被拦下、或 provider 没回 usage）⇒ **有意跳过、不写 0 行** —— 写 0 会污染 `token_usage_logs` 的计数（它是额度权威源）。<br>⚠️ 想看"到底记没记"，**别只看返回值** ⇒ 查库（端到端实测就是这么核的）。 |
| 🔴 **「`get_thread_cost(thread_id)` —— 签名上没有 `user_name` ⇒ 越权洞」** | ⚠️ **看起来像，实测不是**（§三·A 核过）。**全仓唯一调用点是本文件 `:888` 的线程预算检查**，传的正是**调用者自己的** thread_id（调用方在更上层按身份取）。⇒ **它只是一个"只给本人线程用"的内部函数**。<br>⛔ **别顺手给它加 `user_name`** —— 改它要动预算逻辑、有回归风险，且**加了也不会更安全**（上层本来就没法传别人的 id）。<br>⚠️ **但也⛔ 别把它当通用取数函数复用** —— 它没有归属条件，**换一个能把别人的 thread_id 传进来的调用点，它当场就变成真洞**。要对外展示线程花费，走 🆕 **`thread_cost_breakdown(user_name, thread_id)`**（带归属条件，见「✅ 做了什么」）。<br>📌 判据：`grep -rn "get_thread_cost" app/ --include="*.py"` ⇒ 只应命中定义 + `:888` 那一处 |
| 🔴 **「`created_at` 跟 `approval_audit` 一样是带时区的，直接 `.isoformat()` 就行」** | ⛔ **两条轴的时间列类型不同**：`token_usage_logs.created_at` 是 **`TIMESTAMP`（无时区）**，而 PG 容器 `SHOW timezone` = **`Etc/UTC`** ⇒ 里面存的是**裸 UTC 数字**。`approval_audit` 那侧是 **`TIMESTAMPTZ`**、天生带区。<br>⚠️ 原样 `.isoformat()` 会回 `2026-10-06T13:14:21`（**不带区**），JS `new Date()` 把它当**浏览器本地时间**解析 ⇒ 东八区用户看到的时刻**静默早 8 小时**，**不报错**。<br>✅ 出口一律过 **`_iso_utc()`**（补 `+00:00`）；前端 `trace.js::parseWhen()` **主动拒绝**不带区的时间戳（回 `--`）—— 两层都拦。<br>📌 判据：`app/tests/test_trace_cost.py::test_created_at_carries_utc_offset` · `app/tests/test_trace_cost_db.py` 里那条"是绝对时刻"的用例<br>⚠️ 同族前科：`logs/api_*.log` 混了两套时区 |

## 关联

`app/billing/specs/token_config.md`（**常量的真身**）·
`app/specs/归档/quota_limiter.md`（⚰️ **原先的「次数」那套 —— 2026-10-03 已删**）·
`app/specs/main.md`（**全路径按用户日级** = `R1.3`，消费本文件）·
`app/access/specs/permission.md`（⏳ 待建）·
（两套口径，**已由  收口**）· ****（次数那套的删除 + 原位换 token）·
****（`①b` ：**内存 vs 库 = 两个语义** · 全站额度的出口 = `get_user_overview` + 看板第 5 格）·
****（**三条链不记账** —— 本文件新增 `record_from_response` = 取用量+记账的**唯一实现**；`BUDGET_EXCEEDED_MSG` 也在此集中）·
🆕 ****（Trace 页 —— 本文件加 **`thread_cost_breakdown` / `_iso_utc`** 作**成本轴的读侧出口**；
同批核出 `get_thread_cost` "像洞而不是洞" 与 `created_at` 的时区陷阱，均见上表）·
`后端补齐清单` **B7/B8/B10/B11/B13**

---

# ✅ 实施计划 ①a · **额度收口**（2026-09-30 立 · **2026-10-01 已执行完**）

> **来源**：`后端补齐清单-待裁-20260929.md` 的 **B12 · B7 · 决策一**（**需求方已逐条裁定**）。
> **范围**：只做**两件【不改行为】的事** + 一条决策记录。
> ⚠️ **B8 / B10 / B11 / B13 不在本计划**；**`决策一` 的【实现】也不在**（原因见  那节的「顺序陷阱」）。
> ⛔ **本计划做完，服务的外部行为应当【完全不变】** —— 除  改的那几句错误文案。
>
> **为什么先做这批**：需求方定的开工序是「**B12 第一**」（全清单最便宜），
> 且 **`决策一`（统一 token）是结构改动** —— 它一落地，B8/B10 才知道挂在哪。

**目标**：把散在 4 个文件的额度常量收到 `app/billing/token_config.py` 一处；
让错误响应带**恢复时间**；把「请求次数」那套配额**降级**（`决策一` = 统一到 token）。

**架构**：新建 `app/billing/token_config.py` 作为**唯一常量来源**（**只读 env，⛔ 不做热加载** ——
`ChatOpenAI(max_tokens=…)` 是 **import 时求值**，17 个构造点都是，改了本来就要重启）。
`token_tracker` 与 `permission` 改为从它取值。

**技术栈**：Python 3.10 · FastAPI · pytest（marker：`needs_db` / `integration`）

**Spec**：本文件（`app/billing/specs/token_tracker.md`）·
本仓的施工单（⛔ 不随产品仓提供）（B12 · B7 · 决策一）

## 全局约束（**每个任务都适用**）

- 🔴 **跑测试必须用 CI 的同一条命令**：`python -m pytest app/ -m "not integration and not needs_db" -q`
  （⛔ 别拿"全量"顶替 —— 全量含 `needs_db` 用例，会掩盖"偷偷依赖库"，**2026-09-30 栽过**，见 本仓的复盘记录（⛔ 不随产品仓提供））
- 🔴 **新增/修改的测试不许碰 DB 或 Redis**，除非标了 marker。做法参照 `app/tests/test_rate_limit_identity.py` 的 `_no_db` fixture
- 🔴 **提交信息里要答两问**：① 有无决策（有 ⇒ 建 DEC）② CHANGELOG 写没写（见 `docs/规范/开发规范.md` §2.2）
- 🔴 **命令里写中文用「」『』，不用 ASCII 引号**（会被 shell 吃掉 —— 2026-09-30 栽过）
- ⛔ **`admin` 的日上限 = `premium` = 100000**（需求方 2026-09-30 裁）
- ⛔ **单次上限不分角色**（否则对 admin 等于不存在）
- ⏸ **角色档位体系本轮不重塑** —— 只改值，**不改"分几档"**（需求方：留第二轮）

## 文件结构

| 动作 | 文件 | 职责 |
|---|---|---|
| **新建** | `app/billing/token_config.py` | **额度常量的唯一来源**（只读 env） |
| **新建** | `app/billing/specs/token_config.md` | 它的 spec（⛔ 新建模块**必须**同时建 spec —— `pre-commit-gates.py` 会硬拦） |
| **新建** | `app/tests/test_token_config.py` | 常量来源与取值的回归测试 |
| **新建** | 本仓的决策记录（⛔ 不随产品仓提供） | `决策一` 的决策记录 |
| **修改** | `app/core/exceptions.py` | `AppException` 加 `retry_after` |
| **修改** | `app/main.py` | 异常处理器带上 `retry_after`；**修 4 处错误文案** |
| **修改** | `app/billing/token_tracker.py` | 常量改为从 `token_config` 取；`admin` 去 `inf` |
| **修改** | `app/access/permission.py` | `ROLE_QUOTA` 降级为「接口权重」（`决策一`） |
| **修改** | `app/tests/test_plan_execute_tools.py` | **那条钉住"两套口径差 35 倍"的用例要改**） |
| **修改** | `CHANGELOG.md` | 本次有行为改动 |

---

##  · 建 （`决策一` 的决策记录）

> 🔴 **2026-10-03 更正**：本节表格里写的「② `ROLE_QUOTA` **降级为接口权重**（不删）」
> 与「`get_role_request_weight()`」**都【从未存在过】** ——  裁定 **直接删**。
> ⚠️ 也就是说  当时在这两点上**自相矛盾**（§② 说"不删"、§遗留 1 说"未定"），
> 落地时才判清。⇒ **别照本节的 ② 读**。📄 本仓的决策记录（⛔ 不随产品仓提供）

**Files:**
- Create: 本仓的决策记录（⛔ 不随产品仓提供）

**为什么它是 **：后面三个任务**都按它写**。没有它，实施的人不知道"次数那套是废掉还是降级"。

- [ ] **Step 1: 写 DEC**（模板见 本仓的决策记录（⛔ 不随产品仓提供） / 本仓的决策记录（⛔ 不随产品仓提供））

必须含这五节（**别省「备选」和「反悔成本」**）：

| 节 | 内容 |
|---|---|
| **决策事项** | 本仓**已有两套口径**（请求次数 `permission.ROLE_QUOTA` vs token `token_tracker.ROLE_TOKEN_BUDGET`）， 实测**差 35 倍**；再加 R1 四层会变成三套 |
| **备选** | **甲** 统一到 token 一套（次数配额废掉或降为"接口权重"）· **乙** 保留两套但打通 · **丙** 先不动 |
| **裁定** | **甲**（需求方 2026-09-30 原话：「**甲 · 统一到 token 一套**」） |
| **落地含义** | ① 新建 `token_config.py` 收口 ② `ROLE_QUOTA` **降级为接口权重**（不删，因为 `QuotaMiddleware` 还挂在上面）③ `admin` 去 `inf` |
| **反悔成本** | **低** —— 全靠 `token_config.py` 一处取值，回退 = 改一个 `get_role_request_weight()` 的实现 |
| **关联遗留** |  的乙/丙**未做** ⇒ **本决策相当于用「甲」把它收口**，需在  里补一条「由  收口」 |

- [ ] **Step 2: 在  里补一条指针**

在 本仓的决策记录（⛔ 不随产品仓提供） 末尾加：

```markdown
> ✅ **2026-10-01 收口**：本决策的「乙/丙 未做」状态**已由 （统一到 token 一套）终结** ——
> 见 本仓的决策记录（⛔ 不随产品仓提供）。
```

- [ ] **Step 3: 提交**

```bash
git add
git commit -m "docs(): 决策一裁定落地 —— 额度统一到 token 一套（收口）"
```

---

##  · **B12** · 错误响应带恢复时间 + 修 4 处文案

**Files:**
- Modify: `app/core/exceptions.py`（`AppException.__init__`）
- Modify: `app/main.py`（异常处理器 `:345` 附近；4 处文案 `:158` `:460` `:481` `:496`）
- Test: `app/tests/test_error_contract.py`（新建）
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `AppException(error_code, message=None, retry_after=None)` —— `retry_after: int | None`（**秒**）
- Produces: 响应体多一个可选字段 `retry_after`；响应头 `Retry-After`

📌 **`retry_after` 的含义**（写进 docstring，别只写名字）：**客户端还要等多少秒**才能重试。

- [ ] **Step 1: 写失败测试**

```python
# app/tests/test_error_contract.py
"""错误响应的契约（对应 B12）。

🔴 为什么要有这个文件：本仓**有一条文案是说反的** ——
   `app/main.py` 全局限流触发时返回 429，`error` 字段却写着 "Internal server error"。
   那正是 `通用方法 §7.1` 的 R3.3 要防的：「❌ 熔断时静默/白屏/500 → 对方以为你的系统坏了」。

本文件**不需要 DB / Redis** —— 只测异常对象与处理器产出的契约。
"""
from exceptions import AppException, ErrorCode


def test_app_exception_carries_retry_after():
    """`retry_after` 要能挂到异常上 —— 否则处理器没法把它写进响应。"""
    exc = AppException(ErrorCode.RATE_LIMITED, "请求过于频繁", retry_after=30)
    assert exc.retry_after == 30


def test_app_exception_retry_after_defaults_to_none():
    """不给就是 None —— 不是 0（0 会被读成'立刻可重试'）。"""
    exc = AppException(ErrorCode.INTERNAL_ERROR, "boom")
    assert exc.retry_after is None
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
python -m pytest app/tests/test_error_contract.py -q
```
预期：`TypeError: AppException.__init__() got an unexpected keyword argument 'retry_after'`

- [ ] **Step 3: 改 `app/core/exceptions.py`**

```python
class AppException(Exception):
    """自定义业务异常，附带错误码，由全局异常处理器统一捕获"""
    def __init__(self, error_code: ErrorCode, message: str = None,
                 retry_after: int = None):
        self.error_code = error_code
        self.message = message or error_code.value  # 未提供消息则使用错误码名称
        self.status_code = ERROR_CODE_TO_HTTP_STATUS.get(error_code, 500)
        # 客户端还要等多少秒才能重试。只在【限流/配额】这类可恢复的错误上给；
        # 其他错误留 None ⇒ 处理器不会写这个字段（避免"等 0 秒"被误读成"立刻可试"）。
        self.retry_after = retry_after
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
python -m pytest app/tests/test_error_contract.py -q        # → 2 passed
```

- [ ] **Step 5: 写失败测试（响应契约）**

加到同一个文件：

```python
from fastapi.testclient import TestClient


def _client():
    # 惰性导入：`main` 导入期会建 Gradio Blocks（见 app/conftest.py 的注释）
    from main import app
    return TestClient(app)


def test_rate_limited_response_is_not_internal_server_error():
    """🔴 本条钉的是那条【说反了的文案】（`app/main.py` 全局限流分支）。

    改前：`{"error": "Internal server error", "code": "RATE_LIMITED", "status_code": 429}`
    改后：文案必须说清"是限流"，⛔ 不许出现 "Internal server error"。
    """
    from main import _rate_limited_payload      # 见 Step 7 抽出的纯函数
    body = _rate_limited_payload()
    assert body["code"] == "RATE_LIMITED"
    assert "Internal server error" not in body["error"], (
        f"限流却写着内部错误 —— 正是 R3.3 要防的：{body['error']!r}"
    )
    assert "频繁" in body["error"] or "限流" in body["error"], (
        f"文案要说清是限流，不是故障：{body['error']!r}"
    )
```

- [ ] **Step 6: 跑测试，确认失败**

```bash
python -m pytest app/tests/test_error_contract.py -q
```
预期：`ImportError: cannot import name '_rate_limited_payload'`

- [ ] **Step 7: 抽出纯函数并改文案（`app/main.py`）**

在 `RateLimitMiddleware` **之前**加：

```python
def _rate_limited_payload(retry_after: int = 60) -> dict:
    """全局限流触发时的响应体（抽成纯函数 ⇒ 可单测，不必真打 Redis）。

    ⚠️ 2026-10-01 修：此处原写 `"error": "Internal server error"` ——
       状态码是 429、文案却说"内部错误" ⇒ 调用方会以为**系统坏了**，
       而实际是**自己发太快**。这正是 `通用方法 §7.1` R3.3 要防的。
    """
    return {
        "error": f"请求过于频繁，请在 {retry_after} 秒后重试",
        "code": ErrorCode.RATE_LIMITED.value,
        "status_code": 429,
        "retry_after": retry_after,
    }
```

然后 `RateLimitMiddleware.dispatch` 里那段 `JSONResponse(status_code=429, content={...})`
改成：

```python
            response = JSONResponse(status_code=429, content=_rate_limited_payload())
            response.headers["Retry-After"] = "60"
            return response
```

**同类修正 3 处**（`503`，文案 `:460` `:481` `:496`）—— 把 `"error": "Internal server error"`
改成**说清是"还没就绪"**：

```python
            "error": "服务尚未就绪，请稍后重试",     # 原：Internal server error
```

⛔ **`:364` 那处（全局 500）不要动** —— 它**真的是**内部错误。

- [ ] **Step 8: 加一条守卫测试（防那 3 处被改回去）**

```python
def test_service_unavailable_copy_is_not_internal_error():
    """503 的三处也必须说清"未就绪" —— 它们与 500 不同。"""
    import inspect, main
    src = inspect.getsource(main)
    # 允许 500 处理器保留那句英文，但 503 的 payload 里不许再有
    unavailable_blocks = [b for b in src.split("status_code=503") if "Internal server error" in b[:400]]
    assert not unavailable_blocks, (
        "有 503 响应体还在用 'Internal server error' —— 那是'未就绪'，不是'内部错误'"
    )
```

- [ ] **Step 9: 跑全量 + CI 同款命令**

```bash
python -m pytest app/tests/test_error_contract.py -q                                        # → 4 passed
python -m pytest app/ -m "not integration and not needs_db" -q                         # → 全绿
```

- [ ] **Step 10: 补 CHANGELOG 并提交**

```bash
git add app/core/exceptions.py app/main.py app/tests/test_error_contract.py CHANGELOG.md
git commit -m "fix(错误契约): B12 —— 429/503 文案不再说'内部错误'，并加 retry_after"
```

---

##  · **B7** · 新建 `app/billing/token_config.py`，把额度常量收口

> ### ⚠️ **2026-09-30 追加 / 10-01 已做：本 Task 顺带并入 3 条**（`/specs` 核账挖出的 · 见 `待办总表` 🅗 的 –）
>
> ⛔ **不另开轮次** —— 它们都落在**同一个新文件**上，分批做等于**改两遍 `token_config.py`**。
>
> | 并入 | 事 | 为什么必须在这里 |
> |---|---|---|
> | **** | 3 处 `record_usage(model="qwen-turbo")` 硬编码 ⇒ 改成**从 `llm` 对象取** | 它和  **不可拆**（见下） |
> | **** | **`PRICING` 补 `deepseek` 条目** | ⚠️ **只修  不补价 ⇒ 落到 `_DEFAULT_PRICING` 兜底价，比"明确配一个"更糟**（看不出来是兜底） |
> | **** | **限流参数**（`global/user_limiter` 的 `rate`/`capacity`）也收进来 | 与  **同型**（参数写死）⇒ 一起收，⛔ 别再开一次 |
>
> ⇒ **Step 3 的 `token_config.py` 要多两块**（`MODEL_PRICING` 补 deepseek + 限流参数），
> 并**新增一个 Step** 改那 3 处 `model=`。

**Files:**
- Create: `app/billing/token_config.py`
- Create: `app/billing/specs/token_config.md` ← ⛔ **必须同时建**，否则 `pre-commit-gates.py` 硬拦
- Create: `app/tests/test_token_config.py`
- Modify: `app/billing/token_tracker.py`（常量改为从 `token_config` 取）

**Interfaces:**
- Produces: `MAX_TOKENS_ANSWER` · `MAX_TOKENS_AGENT` · `SESSION_TOKEN_LIMIT` · `GLOBAL_DAILY_TOKEN_LIMIT` · `ROLE_DAILY_TOKEN` · `MODEL_PRICING` · `DEFAULT_MODEL_PRICING`

- [ ] **Step 1: 写失败测试**

```python
# app/tests/test_token_config.py
"""额度常量的【唯一来源】的回归测试（对应 B7）。"""
import token_config


def test_single_call_caps_are_the_agreed_values():
    """需求方 2026-09-30 裁的起始值。⚠️ 终值等前端做完、浏览器实测后再调。"""
    assert token_config.MAX_TOKENS_ANSWER == 2000
    assert token_config.MAX_TOKENS_AGENT == 1024


def test_admin_is_no_longer_infinite():
    """🔴 需求方 2026-09-30 裁：「admin 也要同样上限」⇒ 不许是 inf。"""
    assert token_config.ROLE_DAILY_TOKEN["admin"] != float("inf")
    assert token_config.ROLE_DAILY_TOKEN["admin"] == token_config.ROLE_DAILY_TOKEN["premium"]


def test_single_call_cap_does_not_depend_on_role():
    """单次上限【不分角色】—— 否则对 admin 等于不存在。"""
    assert not hasattr(token_config, "ROLE_MAX_TOKENS")
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
python -m pytest app/tests/test_token_config.py -q     # → ModuleNotFoundError: token_config
```

- [ ] **Step 3: 建 `app/billing/token_config.py`**

> ⚠️ **下面的清单是【2026-10-01 建文件当时】的实况** —— 现在 `permission.ROLE_QUOTA` **已删**
> （2026-10-03）⇒ 现值是 **3 个文件 5 处**。实际文件里已补了这条更正说明。

```python
"""额度配置的【唯一落点】（B7 · 2026-10-01 落盘）。

为什么建这个文件：在此之前，额度类常量**散在 4 个文件 6 处**，且**单位混着**：
  · `permission.ROLE_QUOTA`（**次数**）
  · `token_tracker.ROLE_TOKEN_BUDGET`（token）· `MAX_THREAD_COST`（**元**）· `MAX_SINGLE_CALL_COST`（**元**）
  · `token_tracker.PRICING`（元/1000token）
  · `plan_execute.PLAN_TOTAL_BUDGET_SECONDS`（**秒**）
⇒ 这正是 「两套口径差 35 倍」与 `决策一`「三套怎么合」的**物理原因**。

⚠️ **本模块只读 env，不做热加载** —— 故意的。
   `ChatOpenAI(model=…, max_tokens=…)` 是 **import 时求值**（17 个构造点全是），
   改了值本来就要重启。**只求"集中"，不求"热加载"。**
"""
import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


# ==================== 单次上限（token）· ⛔ 不分角色 ====================
# 行业区间 500–2000（见 后端补齐清单 B7 附的检索来源）。
# ⚠️ 取上沿而不是"中和"：压低到 1800 以下**会开始截断答案**。
MAX_TOKENS_ANSWER = _int("TOKEN_MAX_ANSWER", 2000)   # RAG 答案生成 / WS agent
MAX_TOKENS_AGENT = _int("TOKEN_MAX_AGENT", 1024)     # Agent 对话 / planner / 质检
# 查询改写**已有自己的值**（query_rewriter.py:65/143），此处只登记、不改行为
MAX_TOKENS_REWRITE_VARIANTS = _int("TOKEN_MAX_REWRITE_VARIANTS", 200)
MAX_TOKENS_REWRITE_INTENT = _int("TOKEN_MAX_REWRITE_INTENT", 800)

# ==================== 分层上限（token）· 由后续计划接线 ====================
SESSION_TOKEN_LIMIT = _int("SESSION_TOKEN_LIMIT", 50000)          # B8
GLOBAL_DAILY_TOKEN_LIMIT = _int("GLOBAL_DAILY_TOKEN_LIMIT", 1_000_000)  # B10

# ==================== 角色日预算（token） ====================
# ⚠️ 档位体系【本轮不重塑】—— 需求方 2026-09-30：「还没有想好「分几档」，留第二轮」。
#    本轮只把 admin 的 inf 换掉。
# 🔴 **2026-10-11：这里原先抄的是 `ROLE_DAILY_TOKEN = {...}` 那张【单表】** ——
#    它已**按池拆成两张**（`LLM_DAILY_TOKEN` / `EMBEDDING_DAILY_TOKEN`），旧名**已删**。
#    ⚠️ 本 spec 是 `token_tracker` 的，⛔ **别再在这儿抄 token_config 的代码块** ——
#       抄一遍就多一个会烂的副本（这次就是）。真值看 §「关联」指过去的那份。
LLM_DAILY_TOKEN = {
    "free": _int("DAILY_TOKEN_LLM_FREE", 50_000),        # 🔴 2026-10-11：10_000 → 50_000
    "premium": _int("DAILY_TOKEN_LLM_PREMIUM", 500_000),
    "admin": _int("DAILY_TOKEN_LLM_ADMIN", 500_000),     # ✅ admin = premium（沿 2026-09-30 口径）
}
EMBEDDING_DAILY_TOKEN = {
    "free": _int("DAILY_TOKEN_EMBEDDING_FREE", 10_000),
    "premium": _int("DAILY_TOKEN_EMBEDDING_PREMIUM", 100_000),
    "admin": _int("DAILY_TOKEN_EMBEDDING_ADMIN", 100_000),
}

# ==================== 模型单价（元 / 1000 tokens） ====================
# 迁自 token_tracker.PRICING（:50）。⚠️ 加新模型时**只改这里**。
MODEL_PRICING = {
    "qwen-turbo": {"prompt": 0.003, "completion": 0.006},
    "qwen-plus": {"prompt": 0.008, "completion": 0.016},
    # ⚠️ 2026-10-14 B 段：`text-embedding-v2` 那一行【已删】（需求方裁「不留，直接删」）；
    #    改前是 "text-embedding-v2": {"prompt": 0.0005, "completion": 0},
}
# 未登记模型的**兜底单价** —— 取偏保守的一组（不低报花费）
DEFAULT_MODEL_PRICING = {"prompt": 0.003, "completion": 0.006}

# ==================== 花费上限（元）· 多级预算的第一、二级 ====================
MAX_SINGLE_CALL_COST = _float("MAX_SINGLE_CALL_COST", 0.5)
MAX_THREAD_COST = _float("MAX_THREAD_COST", 5.0)
```

> 🔴 **2026-10-01 更正（实施后回写）**：上面那段是**计划草稿**，其中
> `# 未登记模型的兜底单价 —— 取偏保守的一组（不低报花费）` **这句是错的** ——
> 0.003/0.006 只对**比 qwen-turbo 便宜**的模型保守，**对 `qwen-plus`（0.008/0.016）是低报**。
> 实际落地的 `app/billing/token_config.py` **已把这句改掉**（含"兜底价不是上界"的说明）。
> 📄 为什么值得单记一笔：我**先信了这句旧注释**，又**把它写成测试** ⇒ **CI 直接红**。
> 见 `app/billing/specs/token_config.md` 的 ⚠️ 表 + `app/tests/test_token_config.py`。

- [ ] **Step 4: 跑测试，确认通过**

```bash
python -m pytest app/tests/test_token_config.py -q     # 计划时估计 3 条；实际落地 10 条（含后加的守卫）
```

- [ ] **Step 5: 写 `app/billing/specs/token_config.md`**

⛔ **不写会被 `pre-commit-gates.py` 硬拦**（"新增模块必须同时建 spec"）。
按 `app/specs/归档/quota_limiter.md` 的格式写四节，**重点写「⚠️ 看代码会误判的地方」**：

必须写进去的一条：**「本模块只集中常量，⛔ 不做运行时路由/热加载」** ——
看代码的人容易以为"集中了就动态了"，**那两件事不是一回事**。

- [ ] **Step 6: 让 `token_tracker.py` 从 `token_config` 取值**

把这几处的**字面量**换成引用（⛔ **行为不变**，纯搬家）：

```python
from token_config import (
    MODEL_PRICING as PRICING,
    DEFAULT_MODEL_PRICING as _DEFAULT_PRICING,
    ROLE_DAILY_TOKEN as ROLE_TOKEN_BUDGET,
    DEFAULT_DAILY_TOKEN_BUDGET,
    MAX_SINGLE_CALL_COST,
    MAX_THREAD_COST,
)
```

⚠️ **`DEFAULT_DAILY_TOKEN_BUDGET` 现为 `int(os.getenv("DEFAULT_DAILY_TOKEN_BUDGET", "10000"))`**
（`token_config.py:66` —— ⚠️ 原写 `token_tracker.py:283`，**B7 已搬到 `token_config.py`**）。
🔴 **2026-10-04（丁）从 `100000` 降到 `10000`**：改前它 = `premium` = 最高档
⇒ `get_user_token_budget()` 的 `.get(role, DEFAULT_DAILY_TOKEN_BUDGET)`（`:374`）
一旦被走到，用户**静默拿到最高额度**。现在锚在 `free` = 现存最低档。📄

- [ ] **Step 7: 跑全量，确认行为没变**

```bash
python -m pytest app/ -m "not integration and not needs_db" -q     # → 应仍是 124 passed
```

- [ ] **Step 8: 更新模块表并提交**

```bash
--write
git add app/billing/token_config.py app/tests/test_token_config.py app/billing/specs/token_config.md \
        app/specs/README.md app/billing/token_tracker.py
git commit -m "feat(额度): B7 —— 建 token_config.py 把散在 4 文件的额度常量收口（行为不变）"
```

---

##  · **⚠️ 已移出本计划** —— 见文末「后续」的 ①b

> ## 🔴 写这份计划时发现的一个**顺序陷阱**（**这条比  本身重要**）
>
> **初稿把 （撤掉「请求次数」配额）排在这里，是错的。**
>
> **为什么错**：`决策一` 要**撤掉** `QuotaMiddleware` 的每日次数计数，
> **而 token 那套的【会话级】与【全局日级】（B8 / B10）在 ①b 里**。
> ⇒ **先撤次数、后接 token** ⇒ **中间出现一段"谁都不拦"的窗口** ——
> 一个**已经能上线跑**的服务**开了个日限额的洞**，而且**没人会立刻发现**。
>
> **正确顺序**：**先让 token 那套接管（①②b 的 B8/B10/B11 落地），再撤次数。**
>
> ⇒ **`决策一` 的【实现】跟着挪到 ①b 的最后一步**；本计划里的  只**建 DEC**（把口径定死），
> **⛔ 不动任何行为**。
>
> 📌 **教训（值得单独记）**：**「降级旧机制」和「上线新机制」必须【新先旧后】**
> —— 反过来就有一段**看起来一切正常**的空窗期。
> 本仓已有同型前科：本仓的复盘记录（⛔ 不随产品仓提供） 讲的也是"两个都在，但谁都不真拦"。

---

## （原  · 已移出 —— 保留在此只作查阅，**执行请去 ①b**）

> ### 🔴 **2026-10-03：本节【已作废】，⛔ 别照它执行**
>
> 它通篇假设「`ROLE_QUOTA` **降级成字符串哨兵**（`"deprecated-见-）」，** 推翻了这个做法** ——
> 实际是**直接删**（连 `get_user_quota` 一起），判定改为**原位置换成 token 口径**。
> ⚠️ **下面那个 Step 1 的测试代码是【错的示范】**：`from permission import get_user_quota, ROLE_QUOTA`
> 现在是 **ImportError**（两个名字都已不存在）。
> ✅ **真实落地的步骤、代码与判据 ⇒ 本文末 `①b` 的 「 · 📌 修订块」**
> 📄 **裁定全文 ⇒ 本仓的决策记录（⛔ 不随产品仓提供）**
>
> **保留本节的理由**：里面的「**顺序陷阱**」那段（上面  那节）**仍然完全有效**，
> 而且  又**发现了它的一个变体**（原以为 `B8/B10/B11` 已补上"按用户每天"那一层 —— **没有**）。

### 原  · **`决策一` 落地** · 次数配额降级 + admin 去 `inf`

**Files:**
- Modify: `app/access/permission.py`（`ROLE_QUOTA` → 权限权重）
- Modify: `app/tests/test_plan_execute_tools.py`（**那条钉住"两套口径"的用例要改**）
- Modify: `CHANGELOG.md`

**⚠️ 本任务是【唯一会改行为】的一个** —— 前两个是加字段和搬家。

- [ ] **Step 0: 先读那条会红的测试**

`app/tests/test_plan_execute_tools.py:498` 的 `test_free_users_real_daily_limit_on_plan_execute_is_known`。
它断言 `by_tokens < by_requests`，**并直接 `from permission import ROLE_QUOTA`**。
⇒ **`决策一` 一落地它必红** —— **这不是意外，是它本来就想提醒的事**（它的 docstring 写着
「📌 谁改了 `ROLE_TOKEN_BUDGET`…这条会红 —— ⭐ 这不是"防改动"，是"防不知情"」）。

- [ ] **Step 1: 写失败测试**

```python
# 加到 app/tests/test_token_config.py
def test_request_quota_is_no_longer_a_second_accounting_system():
    """决策一：**统一到 token 一套** ⇒ 「请求次数」不再是一套独立配额。

    它保留下来只作**接口权重**（哪些端点更"贵"），⛔ 不再是"每天 100 次"那种口径。
    """
    from permission import get_user_quota, ROLE_QUOTA
    assert ROLE_QUOTA == "deprecated-见- (
        "次数配额应已降级 —— 若这里还是数字，说明决策一没落地"
    )
```

- [ ] **Step 2: 跑，确认失败**

```bash
python -m pytest app/tests/test_token_config.py::test_request_quota_is_no_longer_a_second_accounting_system -q
```

- [ ] **Step 3: 改 `app/access/permission.py`**

```python
from enum import Enum

class UserRole(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    ADMIN = "admin"


# 🔴 2026-09-30：本表**不再是配额**（决策一）。
#
# 原先它是一套【独立的每日请求次数配额】（FREE 100 / PREMIUM 10000 / ADMIN 无限），
# 与 `token_tracker.ROLE_TOKEN_BUDGET`（token）**互不知情** ——
#  实测两者**口径差 35 倍**（`plan_execute` 一次 ~3346 token ⇒ 按次数能跑 100 次、
# 按 token 只能跑 ~3 次）。
#
# ⇒ 决策一（需求方 2026-09-30）裁：**统一到 token 一套**。
#    本常量**降级为「接口权重」** —— 用来表达"哪些端点更贵"，⛔ **不再当日限额用**。
#    ⚠️ 具体怎么用（或者干脆删掉）**还没定** —— 见  的遗留一节。
ROLE_QUOTA = "deprecated-见-


def get_user_role(user_name: str) -> UserRole:
    """根据用户名获取角色。⚠️ 现在仍是硬编码；接 DB 的落点是 B9/R1.3（已挂起）。"""
    if user_name == "admin":
        return UserRole.ADMIN
    elif user_name == "test_user":
        return UserRole.PREMIUM
    else:
        return UserRole.FREE
```

⛔ **`get_user_quota` 暂时保留但会抛异常** —— 逼着调用点显式改掉（见 Step 4），
免得"静默变成了别的语义"：

```python
def get_user_quota(user_name: str):
    raise NotImplementedError(
        "次数配额已按  降级，不再是配额 —— "
        "调用点请改走 token 那套（token_config.ROLE_DAILY_TOKEN / token_tracker）。"
    )
```

- [ ] **Step 4: 改三个调用点**

| 位置 | 怎么改 |
|---|---|
| `app/main.py:245`（`QuotaMiddleware`） | **整段撤掉计数**，只保留"身份解析 + 响应头"。⚠️ **注意**：`_rate_limited_payload` 里那句"调用次数已用完"要改成 token 口径 |
| `app/routing/api_v1.py:221` | 改走 `token_tracker.get_token_budget_info(user_name)`（它已能答"已用/剩余/预算"） |
| `app/routing/api_v1_rag.py:25` | 只删 import（**实测未使用**） |

- [ ] **Step 5: 改那条会红的测试**

`app/tests/test_plan_execute_tools.py::test_free_users_real_daily_limit_on_plan_execute_is_known`
→ **重写成"只剩一套口径"的版本**：删掉 `ROLE_QUOTA` 的比较，
**保留**「FREE 用户对 `plan_execute` 的真实每日次数 ≈ 3」这个**实测数**（它本身是宝贵事实），
并把它**改挂到 token 口径**上。**别整条删掉** —— 那会丢掉  的唯一量化证据。

- [ ] **Step 6: 跑全量**

```bash
python -m pytest app/ -m "not integration and not needs_db" -q
```
预期：全绿。**如果有红，先看是不是"还有地方在读次数配额"** —— 那正是这个任务要清干净的东西。

- [ ] **Step 7: 更新文档 + 提交**

要同步的三处：`app/specs/归档/quota_limiter.md`（它写着"额度来自 `permission.ROLE_QUOTA`"）·
`app/billing/specs/token_tracker.md`（本文件的 ⚠️ 节）· `CHANGELOG.md`

```bash
git add -A
git commit -m "refactor(配额): 决策一落地 —— 次数配额降级，统一到 token 一套"
```

---

## ⏭ 后续（**不在本计划**，等 ①a 落地再立）

| 计划 | 条 | 为什么必须等 |
|---|---|---|
| **①b · 限额与熔断** | **B8**（会话级）· **B10**（全局日级）· **B11**（熔断 +  顺带）· **B13**（实跑核）<br>🔴 **+ `决策一` 落地（原）** | ① 它们要挂的**常量和取值口径**由 ①a 定；② **`决策一` 必须排在 B8/B10 【之后】** —— 新机制先上线，旧机制才降级（见  那节的「顺序陷阱」） |
| **② · 人工接管** | B4 · B5 · B6 | 与 ① 无依赖，可并行 |
| **③ · 流式与取消** | B1 · B2 · B3（核） | 同上；B2 自标「最易假完成」⇒ 排最后 |

> ### 📌 ①b 的**开工顺序**（写在这里，免得到时又想反）
>
> ```
> B7 的接线（把 token_config 接到 15 个构造点）   ← ✅ 2026-10-01 做完
>   → B8  会话级上限                              ← ⭐ 下一步
>   → B10 全局日级总额        ← 到这一步，token 那套【才真正能拦住东西】
>   → B11 熔断（按 key 断路器 + 要素④ 恢复实测 Redis TTL）+ L2 顺带
>   → 决策一落地（撤次数配额）  ← ⭐【最后】
>   → B13 实跑核一遍可见性
> ```

---

# ✅ 实施计划 ①b · **限额与熔断**（2026-09-30 立 · **2026-10-03 收尾** ——  ✅ /  ✅ /  ✅ /  ✅ /  ✅ / ** 🟡 部分** / ** ✅** / ** ✅**）

> **来源**：`后端补齐清单-待裁-20260929.md` 的 **B8 · B10 · B11 · B13 · 决策一（实现）**；
> 以及 `LLM模型路由与额度策略-待裁-20260930.md` 的 **L2**（十几个 model 顺带）。
> **前置**：**①a 必须先落地**（B7 的 `token_config.py` 要先建出来，否则没有常量可取）。
> **本计划【会改行为】** —— 这是它与 ①a 最大的区别。

**目标**：让 token 那套配额**真的能拦住东西** —— 会话级、全局日级、触顶熔断；
然后把旧的「请求次数」配额**最后**处置掉
（⚠️ **原文写「降级」，实际是「删掉 + 原位换 token」** —— 见  修订块）。

**架构**：**新增一层「查询 + 判定」**，全部**查库**（`token_usage_logs`），⛔ **不复用 `_thread_summary`**（理由）。
熔断做成**按 key 的通用断路器**（Redis 标记 + **TTL 分两种**：额度耗尽=永久 / 临时故障=数小时，）—— 这一套同时覆盖
**全局额度触顶**（动作=拒绝）与 **L2 的「某个模型免费额度耗尽」**（动作=换模型）。

---

##  · **前置决策： /  / ** —— ✅ **2026-10-01 全部已裁**

> 这三条在 `LLM模型路由与额度策略-待裁-20260930.md` §七 标着「**等  开工时再答**」。
> **需求方 2026-10-01 裁完** —— 权威记录在 **`docs/待办总表.md` §一·附 的 L 表**（本表只留摘要）。

| # | 问题（原备选） | ✅ **裁定** | 影响哪个 Task |
|---|---|---|---|
| **L3** | 熔断 key 的 **TTL 设多久** | 🔴 **分两种 key、两种 TTL**（**不是**原推荐的"到次日 0 点"）：<br>① `AllocationQuota.FreeTierOnly`（免费额度耗尽）⇒ **永久**，人工解封；<br>② 其余（限流 / 临时故障）⇒ **数小时**。<br>⚠️ **我原推荐的"到次日 0 点"已被证伪**（见下方更正块） |  |
| **L4** | **降级要不要对用户可见** | ✅ **响应里带标记**（与我推荐一致） | （L2 换模型时） |
| **L5** | **降级链按什么排序** | ✅ **人工指定顺序**（与我推荐一致） |  |

> 🔴 **2026-10-01 更正（本表自己那句推荐是错的）**：原写「到次日 0 点（**我推荐**，与"预算按天"天然对齐）」。
> **前提就不成立**：源文档 §三 部件 5 明写**免费额度是一次性的（90 天有效期），不是每天重置**
> ⇒ 「预算按天」这个类比**用错了对象** ⇒ 按它设 TTL，**到期自动放出来会立刻再撞一次 429**
> （额度根本没恢复）。**"天然对齐"是对齐了一个不存在的天然。**
> 📌 与 `①a` 那条 CI 红**同型**：**我拿一个自己觉得顺的类比，当成了事实**。

> ⚠️ **注意 L4 / L5 的对象不是"额度触顶"** —— 触顶已裁「**直接拒绝、不降级**」（要素②）。
> 它们的对象是 **：某个模型的免费额度耗尽 ⇒ 换下一个模型**。

- [x] **Step 1**：把这三条的裁定**写进 `docs/待办总表.md` §一·附**的 L 表 —— ✅ **2026-10-01 做完**。

---

##  · **B7 接线** · ✅ **2026-10-01 做完** —— 让【15】个构造点用上 `token_config.MAX_TOKENS_*`

> 🔴 **2026-10-01 落盘时更正两处【我自己写错的计划】**（原写「17 个」）：
> 1. ⚠️ **「17」是我没数就写下的数。** 实测（AST 扫 `app/*.py` 的 `ChatOpenAI(`）= **15 处**。
>    计划里那张 Files 清单逐条数出来是 **14** —— 连清单本身也对不上 17。
>    ⇒ **本 Task 的权威清单不是这张表，是 `app/tests/test_max_tokens_wiring.py` 的
>    `EXPECTED_MAX_TOKENS`**（它**同时**是守卫：漏一个就红）。
>    🔴 **2026-10-02该常量改名为 `EXPECTED_ROLES`** —— 门禁改写成「钉 `make_llm` 的角色」，见下方  的修订块。
> 2. 🔴 **`evaluate_with_ragas.py` 的 `eval_llm` 计划里【一次都没提】**（落盘时在 `:50`） —— 它也是个 `ChatOpenAI` 构造点，
>    而且**恰恰是"漏掉会看不出来"的那类**（离线评测脚本，不跑就没人发现它没有上限）。
>    ⇒ 已补：接 `MAX_TOKENS_ANSWER`（它在 `:137` 生成被评答案、`:282` 又当 RAGAS judge，
>    **两处都是长输出**；给 1024 可能**截断 judge 输出 ⇒ 评分静默失真**）。
> 📌 **教训与 `①a` 那条 CI 红同族**：**计划里的数字同样是"作者当时的理解"，不是事实。**
>    ⇒ 计划交给守卫测试去核，⛔ 别交给"我记得写的是 17"。

> ⚠️ **2026-09-30 追加：本 Task 顺带并入 1 条**（`待办总表` 🅗 的 ****）
>
> ****：`agent_graph_advanced.py:39` 的 `llm` **没有 `timeout` / `max_retries`** ——
> 同型问题 `plan_execute.py:70-76` **早就修过**（显式设 30/20/15 + `max_retries=1`），**这里漏了**。
> ⇒ **本 Task 本来就要动那 17 个构造点**，顺手补上，⛔ **别另开一轮**。
> ⬜ **具体秒数待定** —— 这是**多轮对话**，比 `plan_execute` 的单步长，**别直接抄 30/20/15**。

**Files:**
- Modify: `app/routing/api_v1_rag.py`（`:563` `:726`）· `app/rag/rag_pipeline.py`（`:48`）· `app/agent/agent_graph.py`（`:20`）· `app/agent/agent_checkpointer.py`（`:20`）· `app/agent/agent_graph_advanced.py`（`:39`）· `app/agent/plan_execute.py`（`:92` `:249` `:458`）· `app/agent/agent_graph_advanced_learning.py`（`:20` `:85-87` `:221`）
- Test: `app/tests/test_max_tokens_wiring.py`（新建）

**Interfaces:**
- Consumes: `token_config.MAX_TOKENS_ANSWER` / `MAX_TOKENS_AGENT`

⚠️ **一致性陷阱**：17 处要**按角色分类**接，⛔ **不是全接同一个常量**：
**答案生成类**（RAG answer / WS agent / `rag_pipeline.answer_llm`）接 `MAX_TOKENS_ANSWER`；
**中间步骤类**（planner / executor / quality_checker / agent 各节点）接 `MAX_TOKENS_AGENT`。

- [ ] **Step 1: 写失败测试**

```python
# app/tests/test_max_tokens_wiring.py
"""单次上限的接线回归（B7）。

🔴 为什么不能只测常量值：常量对了、**没接上**，等于没有上限。
   本文件钉的是"构造点真的带上了 max_tokens"。
⚠️ 不碰 DB / Redis —— 只看构造出来的对象属性。
"""
import inspect


def test_no_chat_openai_without_max_tokens():
    """全仓**不许**再有"没给 max_tokens"的 `ChatOpenAI(...)` 调用。

    ⚠️ 用 AST 而不是 grep：`ChatOpenAI(` 会出现在注释与 docstring 里
       （本仓栽过 —— 见 `docs/规范/开发规范.md` §3.1「批量替换后按位置核」）。
    """
    import ast, pathlib
    offenders = []
    for p in pathlib.Path("api").glob("*.py"):
        if p.name.startswith("test_"):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name != "ChatOpenAI":
                continue
            if not any(k.arg == "max_tokens" for k in node.keywords):
                offenders.append(f"{p}:{node.lineno}")
    assert not offenders, (
        "这些 ChatOpenAI(...) 没设 max_tokens ⇒ 单次上限对它们不存在：\n  "
        + "\n  ".join(offenders)
    )
```

- [x] **Step 2: 跑，确认失败** —— ✅ 2026-10-01，**列出 15 处**（原计划写"17 处"，实测 15）

```bash
python -m pytest app/tests/test_max_tokens_wiring.py -q
```
预期：FAIL，并**列出**那 15 处（**这条失败信息本身就是待办清单**）。
📌 **实测**：`2 failed, 1 passed` —— 两条红各列一遍那 15 行（含计划漏掉的 `evaluate_with_ragas.py` 那处）。

> ⚠️ **本 Task 的 Step 1 最终落地**是 **3 条测试**（计划只写了 1 条）—— 多出来的两条见
> `app/tests/test_max_tokens_wiring.py` 顶部：
> * `test_each_site_uses_the_agreed_budget` —— 钉**分类**（只钉"有没有"的话，全接成 1024 也能过，
>   而那会把**答案截断**；这正对应本 Task 顶部那条 ⚠️「一致性陷阱」）
> * `test_the_two_budgets_are_distinct_and_answer_is_larger`

- [x] **Step 3: 逐处接上** —— ✅ 2026-10-01，**15 处全接完**

模式（以 `app/agent/agent_checkpointer.py:20` 为例）：

```python
from token_config import MAX_TOKENS_AGENT          # 新增
llm = ChatOpenAI(
    model=LLM_MODEL_FAST,
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    max_tokens=MAX_TOKENS_AGENT,                   # 新增
)
```

分类表（**照这个接，别自己判断**）：

| 构造点 | 接哪个 |
|---|---|
| `api_v1_rag.py:566`（流式答案）· `:730`（WS agent）· `rag_pipeline.py:49`（`answer_llm`）· **`evaluate_with_ragas.py:50`**（计划里**漏了**，2026-10-01 补） | **`MAX_TOKENS_ANSWER`** |
| `agent_checkpointer.py:21` · `agent_graph.py:21` · `agent_graph_advanced.py:50` · `agent_graph_advanced_learning.py:21/87/88/89/223` · `plan_execute.py:93/251/461` | **`MAX_TOKENS_AGENT`** |

> ⚠️ **行号是 2026-10-01 接线【之后】的**（接线本身让每处 +1~2 行）。
> ⛔ **别拿这张表当清单用** —— 权威清单是 `app/tests/test_max_tokens_wiring.py` 的 `EXPECTED_MAX_TOKENS`。
> 🔴 **2026-10-02该常量改名为 `EXPECTED_ROLES`**。

- [x] **Step 4: 跑测试，确认通过** —— ✅ **2026-10-01**

```bash
python -m pytest app/tests/test_max_tokens_wiring.py -q         # → 3 passed
python -m pytest app/ -m "not integration and not needs_db" -q   # → 15 failed / 127 passed（红的仍是 Redis/MCP）
```

> 📌 **实测记录**（⚠️ 本机无 Redis ⇒ 那 15 条红是**本机固有问题**，CI 上绿）：
> * **接线前**（在 `HEAD=a257de3` 的独立 worktree 上实测）：`15 failed / 124 passed / 3 skipped`
> * **接线后**：`15 failed / 127 passed / 3 skipped` ⇒ **+3 正好是新测试 3 条**
> * **两份 `FAILED` 清单 `diff` 逐条相同** ⇒ 无回归

- [x] **Step 5: 提交** —— ⏸ **待需求方发话**（需求方 2026-10-01：「不要频繁提交 commit」）
  ⚠️ 且**门这一层就过不去**：本仓的工程配置（⛔ 不随产品仓提供）（**需求方正在用的 worktree，⛔ 别动**）
  让**文档链接门变红** ⇒ `commit` 被 本仓的工程配置（⛔ 不随产品仓提供） 拦下。
  📌 **不是本 Task 的改动有问题** —— 红线全落在那个 worktree 的副本里。详见 `ROADMAP.md` ①b 段的「一处副作用」。

```bash
git add app/*.py app/tests/test_max_tokens_wiring.py
git commit -m "feat(额度): B7 接线 —— 15 个 ChatOpenAI 构造点带上单次上限（2000/1024）+ S12"
```

---

##  · **B8** · 会话级 token 上限 · ✅ **2026-10-01 做完**

> 🔴 **落地时更正计划里三处**（**口径**，全部由需求方 2026-10-01 裁定：
> **窗口** / **key** / **范围**）—— 见 本仓的决策记录（⛔ 不随产品仓提供）：
>
> 1. **窗口 = 会话 × 今日**（带 `CURRENT_DATE`）。计划原样是对的，**但那是"没写理由的选择"**；
>    定它的真正理由是：与仓里**其余所有 token 预算**（用户日预算）同口径，
>    ⛔ 而不是"顺手复制 `get_daily_token_usage`"。
> 2. 🔴 **key 从「只 `thread_id`」改成「`user_name` + `thread_id`」** ——
>    计划**只想到 `thread_id`**。核实端点时发现 4 个 Agent 端点的 `thread_id` **默认值是 `"default"`**
>    ⇒ 只按 `thread_id` 分桶，**所有没显式传它的调用者共用一个桶**，谁先烧完 50000
>    **其他默认用户一起撞 429**（软共享状态 → 硬拒绝）。
>    ⇒ 连带 **接口改名**：`get_thread_token_usage` → **`get_session_token_usage`**
>    （key 变两段了，沿用旧名**表述不实**），且 `user_name` **必填、不给默认值**
>    （给了默认值 ⇒ 漏传的调用点静默落进同一个桶，**决策二白定**）。
> 3. 🔴 **范围：计划写「两处主链」，实做【7 条】** —— 来源文档 B8 要求「**覆盖全部对话链**」。
>    ⚠️ **但比来源文档又少 4 条** —— 逐条核实后发现 **`/rag/ask` · `/rag/jwt_ask`
>    `/rag/async_ask` · `/rag/parallel_ask` 根本不调 LLM**（前两条只 `SELECT documents`，
>    后两条是 `asyncio.sleep(2)` 的 mock）⇒ **接上去是错的**（让不花钱的接口占额度甚至被拦）。
>    🔴 **2026-10-04 补：这 4 条现在【全部已删除】** —— `/rag/ask`·
>    `/rag/jwt_ask`· `/rag/async_ask` + `/rag/parallel_ask`。
>    ⇒ **上面那句「接上去是错的」已经不用再防了**（没有对象可接）；
>    ⛔ **但结论本身仍然有效**：**列清单前必须逐条对代码核** —— 这正是本条的教训句。
>    📌 连带：两份**接线守卫的反向清单**（`test_session_budget_wiring.py` / `test_breaker_wiring.py`）
>    **删到空之后，守卫本身也删了**（空清单 = 静默假通过）⇒  变更记录末条。
>
> 📌 **教训与  那条同族**：**计划里的清单同样是"作者当时的理解"，不是事实。**
>     是**少数**了（漏 `evaluate_with_ragas.py`），本条是**多数**了（多列 4 条）
>    —— 两个方向都会错，⇒ **清单必须逐条对着代码核**。

**Files:**
- Modify: `app/billing/token_tracker.py`（新增 2 个函数）· `app/routing/api_v1_agent.py`（5 处接线 + 1 个新参数）·
  `app/routing/api_v1_rag.py`（2 处接线 + 1 个新参数 + 1 个 WS 会话 id）
- Create: `app/tests/test_session_budget_offline.py`（**无 marker ⇒ 进 CI**）·
  `app/tests/test_session_budget.py`（`needs_db`）· `app/tests/test_session_budget_wiring.py`（**接线守卫**）

**Interfaces（⚠️ 与计划原名不同 —— 见上方更正 ②）：**
- Produces: `get_session_token_usage(user_name: str, thread_id: str) -> float`
- Produces: `check_session_token_budget(user_name: str, thread_id: str, estimated_tokens: int = 0) -> tuple[bool, str]`

> ### 🔴 那个**必须避开**的陷阱（设计理由）—— 计划写对了，保留
>
> `record_usage` 里已经在内存里按 thread 累加了（`_thread_summary`）。
> **⛔ 但绝不能拿它当上限的数据源** —— 它是**进程内存**，
> **重启即清零** ⇒ 想绕开上限，**重启一下就行**。
>
> ⇒ **权威数据源只能是 `token_usage_logs` 表**（`get_daily_token_usage` 就是这么做的）。
> ⚠️ 这也意味着**每次检查要查一次库** —— 这正是  的熔断器要解决的（触顶后别再查）。
>
> ⚠️ **来源文档 B8 的「落点」栏与此相反**（它写「`_thread_summary` **可直接扩展**」）——
> **那一句已被  显式推翻**，以 DEC 为准。

### 判据（可打印）

```bash
venv/bin/python -m pytest app/tests/test_session_budget_offline.py -q     # 11 passed
venv/bin/python -m pytest app/tests/test_session_budget_wiring.py -q      # 8 passed
venv/bin/python -m pytest app/ -m "not integration and not needs_db" -q
#   ⇒ 15 failed / 138 passed / 3 skipped
#      接线前是 15 failed / 127 passed / 3 skipped ⇒ +11 = 本次新增的离线判据
#      `15 failed` 与接线前 FAILED 清单同为「本地无 Redis」，**逐条同类 ⇒ 无回归**
```

**做过红→绿实证的守卫**（⛔ 不是"写完就绿"）：

| 守卫 | 怎么验出红 |
|---|---|
| `test_session_query_is_scoped_by_user_name` | 把 SQL 的 `WHERE user_name = %s` 改掉 ⇒ **红** |
| `test_user_name_is_required` | 给 `user_name` 加默认值 `"unknown"` ⇒ **红** |
| `test_endpoint_calls_session_budget[memory_chat]` | 摘掉 `memory_chat` 里的判定调用 ⇒ **红** |

**对象级核**（⛔ 不是只看源码）：`import main` 后读 `app.openapi()` ——
6 个 HTTP 端点的 query 参数里都有 `thread_id`（含**新补的** `/agent/plan_execute`
`/rag/stream_search`）。

### 并入 `🅗 S9`

接线的同时把「**同一维度、两种窗口**」写进  备选方案·一 与本节：
`MAX_THREAD_COST`（元/线程，**无日期**）vs 会话 token 上限（**按日**）——
⚠️ **它是【已知且有意】的**，归并留给  的 `①b`。⛔ 别当成 bug 顺手"统一"。

---

##  · **B10** · 全局日级 token 总额 · ✅ **2026-10-01 做完**

**Files:**
- Modify: `app/billing/token_tracker.py`
- Test: `app/tests/test_global_daily_budget_offline.py`（无 marker，进 CI）

**Interfaces:**
- Produces: `get_global_daily_token_usage() -> float`
- Produces: `check_global_daily_budget(estimated_tokens: int = 0) -> tuple[bool, str]`

### ✅ 做完了什么

| 项 | 落点 |
|---|---|
| `get_global_daily_token_usage()` | `app/billing/token_tracker.py` —— `SUM(total_tokens) WHERE created_at >= CURRENT_DATE`，**⛔ 无 `user_name`** |
| `check_global_daily_budget(estimated_tokens=0)` | 同上；阈值取 `token_config.GLOBAL_DAILY_TOKEN_LIMIT` |
| 阈值 | **`1_000_000` /天** —— 值没变，但**从"没人读的默认值"变成了裁定值** |
| 测试 | `app/tests/test_global_daily_budget_offline.py` —— **12 passed**（无 marker ⇒ 进 CI） |
| 决策 | 本仓的决策记录（⛔ 不随产品仓提供） |

### 🔴 三处与计划原样不同的地方（都已落地）

1. **阈值是【需求方裁定】过的，不是默认值**。
   源文档 B10 的「✍️ 裁」栏原文是「**⚠️ 附一问（仍待答）：具体阈值 ______（业务判断，我不替你定）**」。
   ⇒ 需求方 2026-10-01 裁定 = **`1,000,000` /天**（≈ ¥1–2/天 · = 10 个 premium 满额）。
   ⚠️ 选它的**另一个理由**：5,000,000 太松 ⇒ 熔断**永远触发不了** ⇒ **验收时无法证明它成立**。
2. **`estimated_tokens` 从「收下不用」改成「真的参与判定」**。
   计划样例的签名有它，**函数体里却没用** —— 「签名看着对、行为是死的」。
   ⇒ 已补上，并加 `test_estimated_tokens_can_trip_it` 钉住（变异验证：删掉那三行 ⇒ 红）。
3. **守卫改用 AST，不用计划里那段 `inspect.getsource(...).split('"""')[-1]`**。
   理由与  同：**注释/docstring 里也有 `user_name` 这些词**，
   按"源码里有没有这个词"判会把**注释**当成 **SQL**（`docs/规范/开发规范.md` §3.1）。
   ⇒ 统一用 `ast.walk` 取 `cur.execute(...)` 的**字面量**。

### ⚠️ 本任务【不接线】——「全绿」≠「全局限额生效」

`check_global_daily_budget` **当时没有任何调用点**。接线在 **``**。

> ✅ **2026-10-02 更新：已经接上了** —— 落在 **`app/billing/breaker.py`**（`circuit()` / `global_key()`），
> 接在 **8 处**（`api_v1_agent.py` ×5 · `api_v1_rag.py` ×2 · `api_v1.py` ×1）。
> ⚠️ **落点不是 `app/main.py`**（原计划写的）—— 理由是 `QuotaMiddleware` 整段包在
> `if user_name:` 里 ⇒ **匿名请求完全绕过**，而 `benchmark-embedding` **恰恰是匿名能打且真花钱的**。
> 🔴 **2026-10-04更正**：`benchmark-embedding` **已不再是匿名的**
> （补上 `Depends(require_admin)` ⇒ 见 `api_v1.py` 该函数的注释）。
> ⚠️ **但这不改变「落点为什么选它」** —— 那条理由写的是**当时**的实况；
> 而且**中间件绕过这件事本身仍在**（`QuotaMiddleware` 依然整段在 `if user_name:` 里）。
> ⇒ ⛔ **别读成"既然加了鉴权，落点可以搬回 `main.py` 了"**。
> **判据（可打印）**：`grep -rn "circuit(global_key())" app/ --include="*.py" | grep -v test_` ⇒ **13 处**
> （`api_v1_agent.py` ×10 · `api_v1_rag.py` ×2 · `api_v1.py` ×1）
> ⚠️ **2026-10-04 实测更正：原写「8 处」（`agent` ×5 · `rag` ×2 · `api_v1` ×1）** ——
> 之后 `api_v1_agent.py` 又接了 5 处（与  **无关**，是更早的漂移）。
> 📌 模块详情 ⇒ `app/billing/specs/breaker.md`

> 🔴 **这是同一个陷阱的第三次**（⚠️ 前两次都真栽了，第三次靠接线测试兜住）：
> ·  之前 —— 常量建好了，**没接上** ⇒ 单次上限不存在；
> ·  之前 —— 有 `SESSION_TOKEN_LIMIT`，**没有判定函数**；
> ·  —— 有判定函数，**曾一度没有调用点**（2026-10-01 当天）。
> ⇒ 📌 **判据（已从"命中不了 api_v1_*.py"改成下面这条）**：
> `grep -rn "circuit(global_key())" app/ --include="*.py" | grep -v test_` ⇒ **8 处**；
> ⛔ **只 grep `check_global_daily_budget` 已经不够了** —— 现在它本来就该只出现在
> **定义处 + `breaker.py` + 测试** 里（调用方走的是 `circuit()` 那一层）。
> 📌 接线本身由 **`app/tests/test_breaker_wiring.py`** 静态钉住（AST 查 8 个函数体）。

### 证据（可打印）

```bash
pytest app/tests/test_global_daily_budget_offline.py -q    # 12 passed
pytest app/ -m "not integration and not needs_db" -q # 15 failed / 158 passed / 3 skipped
```

全量里 15 条失败**全是** `redis.ConnectionError`（本机没起 Redis），与改动前同集合。
📌 **条数对账**（别只看"通过数涨了"）：无 / 三个文件时收集 **145** 条
⇒ `+11 +8`（B8 两份）⇒ 164 ⇒ `+12`（本任务）⇒ **176** = 实跑 `158 + 15 + 3`。
（⚠️ 另有一份 `app/tests/test_global_daily_budget.py` 带 `needs_db` ⇒ **被 CI 命令 deselect**，不计入 176。）

### 🔴 那条 SQL **真的被执行过** —— 但这一步有个坑，记下来

**离线测试从头到尾【没有执行】那条 SQL**（只 `AST` 取字面量）。
⇒ 表名/列名/语法错，**CI 永远绿**。这是 `` 计划里没堵上的一个口子。

**当时本机 PG 没起**（`nc -z localhost 5432` 不通；`docker ps` 空）⇒ `needs_db` 那类跑不了。
（✅ **2026-10-01 当天已补跑** —— 结果见本节末。）
⇒ 改用 **stdlib `sqlite3`**：把**从函数里 AST 取出的那条真 SQL 字符串**（⛔ 不是我重打的）
配上 `app/schema.sql` 里那三列的真实 DDL 执行一遍（这条 SQL 无 PG 专有构造）：

```bash
# 一次性验证（未提交成测试 —— sqlite ≠ postgres，提交它会造成"验过了"的错觉）
sqlite> CURRENT_DATE = 2026-10-01
【全站】今日         = 12000   ← 1000(alice) + 2000(bob) + 4000(alice) + 5000(admin)
【对照·单用户】alice = 5000
【对照·单用户】bob   = 2000
```

✅ **证明了**：SQL 真的可解析可执行 · **跨用户求和**（含 `admin`）· **日期窗口真的在生效**
（昨天那条 9999 **没被算进今日**）。
⛔ **没证明**：PostgreSQL 下的行为（方言/类型/时区）· 真库上的列名是否 100% 一致
（列名是对着 `app/schema.sql` 与 `app/core/db.py:109` 的建表语句核的，**不是**对着真库核的）。
⇒ **补洞口的是 `app/tests/test_global_daily_budget.py`**（`needs_db`）—— 本机起 PG 后跑它。

#### ✅ 2026-10-01 已补跑（那 7 条 `needs_db` 真过了一遍）

前置：当天 Docker 的 Linux VM 卡死（`dockerAPI: stopped`，`/ping` 一直超时）⇒ 杀掉**重启前遗留的
僵尸 `com.docker.backend`** 后重拉才恢复；`postgres-rag` 起来并 `healthy`。

```bash
POSTGRES_DB=rag_test POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
  ./venv/bin/python -m pytest app/tests/test_session_budget.py app/tests/test_global_daily_budget.py -m needs_db
# ⇒ 7 passed
```

> ⚠️ `POSTGRES_HOST` / `POSTGRES_DB` **必须显式覆盖**：`.env` 里 `POSTGRES_HOST=postgres`
> 是 **compose 网络内的服务名**，在宿主上解析不了。`app/core/config.py` 的 `load_dotenv()` 没传
> `override=True` ⇒ 默认 `override=False` ⇒ **导出的环境变量会赢**（这是本命令能生效的前提）。

🔴 **库名隔离对账**（本仓有前科：R2 冒烟忘带库名，`record_usage` 往**真库**写了 4 行 ——
本仓的复盘记录（⛔ 不随产品仓提供））：

| 库 | 跑前 | 跑后 |
|---|---:|---:|
| `rag_db`（真库） | 513 | **513** ← 一行没动 |
| `rag_test`（隔离） | 148 | **155**（+7） |

⇒  的那条 SQL **已在真 PostgreSQL 下执行过**（不再只靠 sqlite 近似）。
⚠️ 本次补跑**只覆盖「能跑通 + 跨用户求和为真」**，不构成别的结论 ——
PG 方言/时区下的**边界**行为、生产数据量下的表现，**都没验**。

⛔ 且 `app/tests/test_global_daily_budget.py` **不进 CI**（CI 无 postgres service）——
所以  的核心判据（SQL 里没有 `user_name`）**依然只在离线那份里**，
别因为「7 条绿了」就以为离线那份可以省。

### 变异验证（**4 条守卫逐条证明会红**，跑完即还原、sha256 一致）

| 变异 | 变红的测试 |
|---|---|
| 全局 SQL 加回 `WHERE user_name = %s` | `test_global_query_has_no_user_filter` · `test_the_two_daily_queries_are_actually_different` |
| 全局 SQL 去掉 `CURRENT_DATE` | `test_global_query_is_today_only` |
| 阈值写死在函数里（不取 `token_config`） | `test_limit_comes_from_token_config` |
| 删掉 `estimated_tokens` 的判定 | `test_estimated_tokens_can_trip_it` |

---

##  · **B11** · 熔断（按 key 的通用断路器）

> ### 🔴 **本节是【原始计划】—— 它写的形状早被  推翻，⛔ 别照着它读**
>
> **2026-10-02 已做完**（状态以 `docs/待办总表.md` 为准：**已完成**），
> 但**落地的那套和这一节描述的【不是同一套】**：
>
> | | 本节（原计划） | ✅ 实际落地 |
> |---|---|---|
> | 接口 | `is_open()` / `trip()` / `seconds_until_reset()` | **`circuit(key, estimated_tokens)` / `global_key()`** |
> | 存储 | **Redis**（`SETEX`） | **PG**（`token_usage_logs` 的日级 SQL）—— ⛔ 不碰 Redis |
> | 恢复 | **TTL 到期** | **跨天自然重置**（`WHERE created_at >= CURRENT_DATE` 自己翻页）—— 🔴 **没有 TTL 可核** |
> | 接线 | `main.py` 中间件里放行前先问 | **8 个端点函数**各自问一次 |
>
> ⇒ **实际形状** ⇒ `app/billing/specs/breaker.md`（含 **7 条「看代码会误判的地方」**）·
> **设计裁定（四选一，含备选与反悔成本）** ⇒ 本仓的决策记录（⛔ 不随产品仓提供）。
>
> 🔴 **为什么加这段横幅**：本节下面那 5 个 `- [ ]` **不是「没做」**，是**按旧形状写的**。
> 接手人照原文会得出两个错结论 —— **「 没做」+「设计是 Redis 那套」**。
> 📌 同型前科：本仓 本仓的复盘记录（⛔ 不随产品仓提供）。

**Files:**（⬇️ **原计划**的落点 —— ⛔ 与实际的差异见右栏）

- Create: `app/billing/breaker.py` + `app/billing/specs/breaker.md`（⛔ 新建模块**必须**同时建 spec，否则 `pre-commit-gates.py` 硬拦）⇒ ✅ **都建了**
- Create: `app/tests/test_breaker.py`（无 marker）⇒ ✅ 建了（**外加** `app/tests/test_breaker_wiring.py` —— **双向接线守卫**）
- Modify: `app/billing/token_tracker.py`（触顶时开断路器）· `app/main.py`（放行前先问断路器）
  ⇒ 🔴 **两个都没改**：判定函数本来就在 `token_tracker.py` 里；接线在 **8 个端点**、**不在中间件**。

**Interfaces:**（⬇️ **原计划**的接口 —— ⛔ **这三个都没实现**）

- Produces: `is_open(key: str) -> bool` · `trip(key: str, ttl_seconds: int) -> None` · `seconds_until_reset(key: str) -> int`
- key 形如 `global:2026-09-30`（B11）/ `model:qwen-turbo:2026-09-30`⇒ ✅ **只有这一条对**，且 `` 直接靠它

> ### 为什么是「按 key」而不是写死"全局额度"
> 这就是 「十几个 model 顺带」的**具体形态**：**同一个函数换一个 key 前缀**。
> ✅ **这一句仍然成立**，**是 `` 的直接依据**（见下）。
> ⛔ 不为  先建任何东西。

- ⛔ **Step 1（旧形状）：写失败测试（mock Redis，⛔ 不连真 Redis）** —— **没按这个做**。
  ✅ 实际是 `app/tests/test_breaker.py` **9 条**，**里面没有一行 mock Redis**（因为**不用 Redis**）；
  另有 **`app/tests/test_breaker_wiring.py` 9 条**专钉接线（旧计划里没有这一层）。

  <details><summary>旧计划里那段（⛔ 已作废，留档）</summary>

  ```python
  def test_trip_then_is_open_then_expires(monkeypatch):
      """开 → 判 → 到期恢复。⚠️ 用假 Redis，不连真服务。"""
      import breaker
      fake = {}
      monkeypatch.setattr(breaker, "_setex", lambda k, ttl, v: fake.__setitem__(k, (ttl, v)))
      monkeypatch.setattr(breaker, "_exists", lambda k: k in fake)
      monkeypatch.setattr(breaker, "_ttl", lambda k: fake.get(k, (0, None))[0])

      assert breaker.is_open("global:2026-09-30") is False
      breaker.trip("global:2026-09-30", ttl_seconds=3600)
      assert breaker.is_open("global:2026-09-30") is True
      assert breaker.seconds_until_reset("global:2026-09-30") == 3600
  ```

  </details>

- ⛔ **Step 2–3（旧形状）：先跑失败 → 再实现 `app/billing/breaker.py`（用 `rate_limiter.py` 同一个 `redis.Redis` 连接方式；`trip` 用 `SETEX`）**
  —— ✅ **文件建了**，但**形状不是这个**：实际 `app/billing/breaker.py` 是**PG 分派器**，**不建 Redis 连接**、**没有 `SETEX`**。
  📌 TDD 的「先看着它失败」这一步**仍然做了** —— 只是对象换了（见 `CHANGELOG.md` 那条「判据纪律的一处自我更正」）。

- ⛔ **Step 4（旧形状）：接线（`token_tracker` 触顶 ⇒ `trip`；`main.py` 中间件先 `is_open` ⇒ 抛 `AppException`）**
  —— ✅ **接上了，但位置不同**：接在 **8 个端点函数**里（**不是中间件**），
  `token_tracker.py` / `main.py` **两个都没改**。
  **判据（可打印）**：`grep -rn "circuit(global_key())" app/ --include="*.py" | grep -v test_` ⇒ **8 行**。

- ⛔ **Step 5（旧形状）：⭐ 要素④ 的【实测】—— 开一个 5 秒断路器，证明 TTL 到点会恢复**
  —— 🔴 **没有 TTL 可核**：日级用量在 **PG**（`token_usage_logs`），恢复靠 SQL 自己翻页，
  **整个机制里没有「到期释放」这回事**。⇒ 详见 `app/billing/specs/breaker.md` §⚠️ 第 1 条。
  ✅ 替代的实测：`test_yesterdays_usage_does_not_count`（塞一条**昨天**的记录，断言它不进今日合计）。
  ⚠️ **它顺带要求核掉的 `quota_limiter` 那条** —— **2026-10-02 已补核**，见 `app/specs/归档/quota_limiter.md`
  （那条是**另一套机制**：`quota_limiter.py` 确实用 Redis + `EXPIRE 86400`）。
  🔴 **2026-10-03**：那个模块**已随  删除** ⇒ 上句是**历史结论**，⛔ 别再当现状读。

- ✅ **Step 6：跑了 + 建了 spec + 已提交**（`app/billing/specs/breaker.md`
  落主干的那条 commit 是 **`0f9a67a`**，PR **#65**）
  ⚠️ **⛔ 别引用 PR 之前的分支哈希** —— 本仓走 squash 合并，**分支上那些 sha 在主干上根本不存在**。

---

##  · ** 顺带** · 某个模型的免费额度耗尽 ⇒ 换下一个 · 🟡 **改写后【部分】落地（2026-10-02）**

> ### 🔴 **本节是【原始计划】—— 形状已被改写，⛔ 先读下面的修订块**
>
> 原计划要的是「**额度耗尽 ⇒ 自动换下一个模型**」。**2026-10-02 落地的不是这个**：
> 落地的是 **`甲` = 只做构造收口，⛔ 不做自动兜底**。**两张的差别见下。**

### 📌 修订块（2026-10-02 · 落地版）

> 📄 **设计裁定（含备选与反悔成本）⇒ 本仓的决策记录（⛔ 不随产品仓提供）**

**实际做的（形态 `甲`）**：把 15 个 LLM 构造点收进**一个** `app/core/llm_factory.py` 的 `make_llm()`。
⇒ 效果：`model` / `max_tokens` / `api_key` / `base_url` **各自只剩一个落点**；
**行为零变化**（角色按改动前的取值原样固化，见 `app/tests/test_max_tokens_wiring.py::EXPECTED_ROLES`）。

**⛔ 没做的：自动兜底。** 这**不是忘了**，是**评估后故意推迟** —— 理由表见 `app/core/specs/llm_factory.md`。

> ### 🔴 一句话说清「为什么推迟」（2026-10-02 实测，⚠️ 中间我曾判断错两次，已更正）
>
> 我一开始说 `主.with_fallbacks([备])` 会**在 import 期 AttributeError ⇒ 整个服务起不来**（理由是类上没有 `bind_tools`）。
> **⛔ 那是错的** —— 我查的是**类**，而实例有 `__getattr__` 委托。用**真的 `ChatOpenAI` 备用**实测：
>
> ```
> w.bind_tools(tools)  →  ✅ 能用，且返回的对象仍然带兜底
> w.model_name         →  🔴 永远返回【主】模型名
> ```
>
> ⇒ **包上去不会炸**，**真正的缺陷是静默的**：**备用模型烧掉的 token 会被那 4 处记账记到主模型头上**。
> ⇒ 收益只在**额度耗尽那一刻**兑现，代价是**账目静默失真** ⇒ 这一轮**不值得**。
> 📌 反证测试钉在 `app/tests/test_llm_factory.py::test_wrapping_would_silently_break_cost_attribution`。

**⇒ 本轮的兜底方式仍是【手动】的**：额度耗尽 ⇒ 请求报错，人工改 `.env` 的
`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_*` 并**重建容器**
（⚠️ `docker compose restart` **不重读 `env_file`**，要用 `docker compose up -d api`）。

**下面的原始计划【保留】**，因为  /  /  的裁定仍然有效，将来做真兜底时直接用：

**Files:** Modify `app/billing/breaker.py` 接线处（**复用  的断路器，不新建文件**）
（⚠️ **本轮实际改的**是 `app/core/llm_factory.py`（新建） + 15 个调用点 + `app/tests/test_max_tokens_wiring.py`（改写））

- [x] ⛔ **开工前必须已有  /  /  的裁定**。—— ✅ 2026-10-01 已裁
- [ ] 判据（`LLM模型路由与额度策略` 坑②）：**只对 `403` + `AllocationQuota.FreeTierOnly` 开断路器**，
  ⛔ **`RateLimitExceeded`（限流）不许开** —— 那是"等一下"，不是"用完了"。
  📌 ⬜ **本轮没做**；⚠️ 不过这条判据**技术上可分**：`403 → PermissionDeniedError`
  `429 → RateLimitError`（openai SDK 的类型区分），**⛔ 不用去解析响应体**。
- [ ] key 用 `model:<名>:<日期>`；动作按 / 裁的结果（换哪个、要不要对用户可）。
  📌 ⬜ **本轮没做**（`breaker.py` 的 `model:` 那一类仍是空的，见 `app/billing/specs/breaker.md`）。

**落点已经收敛**：将来要做，**只需改 `app/core/llm_factory.py` + 处理那 5 个 `bind_tools` 点**，
**15 个调用点一行都不用再动**。

- [x] **本轮落地的东西**：`app/core/llm_factory.py`（新建）· 15 个调用点改走 `make_llm()`
  `app/tests/test_llm_factory.py`（新建 · 12 条）· `app/tests/test_max_tokens_wiring.py`（改写：**工厂以外零 `ChatOpenAI(`**）·
  `app/core/specs/llm_factory.md`（新建）
- [x] **顺带修掉**：`evaluate_with_ragas.py` 的 `os.getenv("LLM_MODEL_CHAT", "deepseek-chat")`
  兜底值与 `config.py` 的 `qwen-plus` **不一致** —— 现在两边同源。

---

##  · **`决策一` 落地** · 撤掉「请求次数」配额 · ✅ **已落地（2026-10-03）**

> ### 🔴 **本节是【原始计划】—— 其中一句是错的，⛔ 先读下面的修订块**
>
> 原计划（下一段）写「**到这一步，B8/B10/B11 已经在拦了，撤掉次数才是安全的**」。
> **2026-10-03 核过：这句不完整。**
>  是**会话级**（按 `(user, thread_id)`）·  是**全站合计**（`SUM` 无 `user_name`），
> **没有一层是「按用户每天」** ⇒ 直接撤次数会开一个「**单用户跨会话无限花**」的洞。
> 📌 这正是 `①a` **自己那个「顺序陷阱」的同类病**，只是换了个方向。

### 📌 修订块（2026-10-03 · 落地版）

> 📄 **决策全文（含三处裁定 / 备选 / 反悔成本）⇒ 本仓的决策记录（⛔ 不随产品仓提供）**

**实际做的是「原位置换」，⛔ 不是「把这一层删掉」**：
在 `QuotaMiddleware` **同一个位置**，把「按用户按天的**次数**上限」换成「按用户按天的 **token** 上限」。
⇒ **覆盖范围不变**（仍是「所有非公开路径 + 按用户 + 按天」），**顺带把 `R1.3` 做掉**。

| # | 做了什么 | 落点 |
|---|---|---|
| ① | **删** `ROLE_QUOTA` + `get_user_quota()`（`UserRole` / `get_user_role` **保留**） | `app/access/permission.py` |
| ② | `QuotaMiddleware` 改判 **token 日预算**（数据源 `token_tracker.get_token_budget_info`） | `app/main.py:287` |
| ③ | 抽两个**纯函数** `quota_reject_payload(info)` / `quota_headers(info)` + `_next_day_reset_ts()` | 同上 `:238` / `:245` / `:273` |
| ④ | `/debug/quota/{user_name}` 改走同一套（字段名不变、**单位变**） | `app/routing/api_v1.py:220` |
| ⑤ | 删未使用的 `get_user_quota` / `UserRole` import | `app/routing/api_v1_rag.py:39` · `app/routing/api_v1.py` |
| ⑥ | **删模块** `app/quota_limiter.py`（撤点后零调用者）+ **归档 spec** | `app/` · `app/specs/归档/quota_limiter.md` |
| ⑦ | 重写那条会红的测试（**保留** `_MEASURED_PLAN_EXECUTE_TOKENS = 3346` 这个实测数） | `app/tests/test_plan_execute_tools.py` |
| ⑧ | **新增** `app/tests/test_quota_middleware.py`（9 条 · **不连 DB/Redis**） | `app/` |

⛔ **原计划里的两条描述【作废】**：
* 「`ROLE_QUOTA` **降级为接口权重**」——  裁**直接删**（全仓没有任何代码读"权重"）；
* 「**整段撤掉计数**，只保留'身份解析 + 响应头'」—— 改成**原位换 token 判定**（理由见上）。

⚠️ **三条一起变的行为**（否则下一个人会当成 bug）：
1. **`X-Quota-*` 头名没变、语义从「次数」变成「token」**（`X-Quota-Reset` 仍是次日 0 点的时间戳）；
2. **每请求新增一次 DB 查询**（原先次数那套是 Redis `INCR`）—— 口径不同，无法共用计数器；
3. **仍是 fail-open**（查库失败 ⇒ 放行）—— 与 / 同取向，⛔ 别"顺手统一"成鉴权的 fail-closed。

**判据（可打印）**：

```bash
venv/bin/python -m pytest app/tests/test_quota_middleware.py -q      # ⇒ 9 passed
grep -rn "ROLE_QUOTA" app/*.py | grep -v "^app/test_"          # ⇒ 只应命中【注释/历史说明】
venv/bin/python -m pytest app/ -m "not integration and not needs_db" -q
# ⇒ 15 failed / 198 passed；与改动前基线（189 passed）**逐条 diff 红的清单 = 完全一致** ⇒ 无回归
```

⚠️ **那 15 条红与本次改动无关** —— 全是本机没开 Redis（14 条 `redis.ConnectionError`）+ 1 条 MCP。

> 📌 **两处由测试自己抓出来的错**（本仓「判据写歪了不报错」的又一例）：
> ① 守卫用例第一版用**文本匹配** `"ROLE_QUOTA" in 源码` ⇒ 把**正当的历史注释**也判成违规
> ⇒ 改用 **AST**（禁的是**引用**，不是**提及**）；
> ② 自证用例抓出扫描器**漏了 `ast.alias`** —— `import` 走的**不是 `ast.Name`**，
> 漏掉这一支 ⇒ **最典型的引用形态完全测不出来**。

**Files（实际）:** `app/access/permission.py` · `app/main.py` · `app/routing/api_v1.py` · `app/routing/api_v1_rag.py`
`app/quota_limiter.py`（删）· `app/tests/test_plan_execute_tools.py` · `app/tests/test_quota_middleware.py`（新建）

---

##  · **B13** · 实跑核一遍成本可见 · ✅ **已落地（2026-10-03 ·  —— `①b` 收尾）**

> ### 🔴 **四个面都打得开 —— 但核出两处【不报错】的错**
>
> 📌 **本 Task 的价值全在"实跑"两个字上**： 原话就是「**不写代码先核**」。
> 若是照着代码读一遍，这两处**一处也发现不了** —— 它们**不抛异常、测试全绿、界面照常出数**。

### 📌 修订块（2026-10-03 · 落地版）

> 📄 **决策全文（四处裁定 / 备选 / 反悔成本 / 遗留）⇒ 本仓的决策记录（⛔ 不随产品仓提供）**

**实跑结果（2026-10-03 · 起服务后逐面看）**：

| 面 | 结果 | 判据 |
|---|---|---|
| `/dashboard` | ✅ `HTTP 307` → 跟随后 `200`（47468 B） | 页面在，第 5 格「全站预算」已出现 |
| `/agent/token/budget` | ✅ 用户级 + **全站级**都有数 | 见下 |
| `/agent/cost/overview` | 🔴 **改前答 0** ⇒ ✅ 改后答 4216 | 见下 |
| `/agent/trace/{thread_id}` | ✅ `200`（不存在的线程答"未找到"，**不报错**） | ⚠️ 进程内存，设计如此 —— 见  §遗留 2 |

**核出来的两处错 + 修法**：

| # | 事实 | 修法 | 落点 |
|---|---|---|---|
| ① | `/agent/cost/overview` 的三个总数读 **`get_user_summary` = 进程内存** ⇒ **重启归零**。<br>🔴 实测：库里有 **4216 tokens / 6 行**，它答 **`0`**，**不报错**。<br>⚠️ 同一份 JSON 里 `total_*` 是**本人**、`by_purpose` 却是**全站** | 新加 **`get_user_overview()`（读库 · 全时 · 本人）**；端点改读它，`by_purpose` 随之变**本人** | `token_tracker.py:272` · `api_v1_agent.py` |
| ② | **/ 的全站日级额度没有任何出口** —— 超了**所有人**吃 429，而**界面上看不到逼近**（`get_global_daily_token_usage()` 原只被 `breaker` 调过） | `/agent/token/budget` 加 `global_daily_limit` / `global_used_today` / `global_remaining`；看板加第 5 格 | `api_v1_agent.py` · `cost_dashboard.py` |

✅ **判据（`R4`）现在成立了**：以前「全站还剩多少」**答不出**，现在答得出（`global_remaining: 999961`）。

⚠️ **三条一起变的行为**（否则下一个人会当成 bug）：
1. **`by_purpose` 从【全站】变【本人】—— 这是对外可见的行为变化**。外部消费方若有，
   会**静默拿到更小的数**。本仓 `grep` 结果：**除测试外无消费方**。
2. **内存那三个 `get_*_summary` 仍然存在、仍有调用点**（`:148` 告警 · `/agent/token/overview`
   `/agent/thread/{id}/overview` · 看板第 2 格）。⛔ **别当残留删掉** —— 语义是"本进程"，只是不能对外展示。
3. **看板 5 格不是一套口径**（前 4 格 = 本人，第 5 格 = 全站；第 2 格仍是内存）——
   已在 `get_dashboard_summary` 的 docstring 里列表说明。

**判据（可打印）**：

```bash
venv/bin/python -m pytest app/tests/test_cost_visibility.py -q          # ⇒ 4 passed（进 CI）
POSTGRES_DB=rag_test venv/bin/python -m pytest app/tests/test_cost_visibility_db.py -q
# ⇒ 3 passed（@needs_db · 本机需先停掉占用 qdrant 锁的 uvicorn）
venv/bin/python -m pytest app/ -m "not integration and not needs_db" -q
# ⇒ 217 passed, 3 skipped, 22 deselected —— 【0 failed】
```

⚠️ **`test_cost_visibility_db.py` 是本 Task 补的【真缺口】**：离线那份只做 AST 静态核对，
**表名/列名打错、SQL 语法错，CI 永远绿**。DB 那份让这条 SQL **至少有一条路径真的执行它**。
📌 与本仓前科呼应：本仓的复盘记录（⛔ 不随产品仓提供） —— **跑它必须带 `POSTGRES_DB=rag_test`**。

**Files（实际）:** `app/billing/token_tracker.py`（+`get_user_overview`）· `app/routing/api_v1_agent.py`
`app/billing/cost_dashboard.py` · `app/tests/test_cost_visibility.py`（新建）·
`app/tests/test_cost_visibility_db.py`（新建）· 本仓的决策记录（⛔ 不随产品仓提供）（新建）

---

# ✅ 实施计划 ①b · **收尾**

`①b` 的 8 个 Task（0–7）**全部落地**。⬜ **未进 `①b`、但仍挂着的遗留**见
 §遗留（看板第 2 格仍是内存 · `tool_visualizer.py` / `cost_dashboard.py` 无 spec
`get_intercept_count()` 口径未核）。

## 🆕 今日按用途拆 **LLM / Embedding / 其它**（2026-10-11 · 前端整体调整 批 4）

- `get_daily_tokens_by_purpose(user)` —— 今日**按 `purpose` 分组**的 token（读库）。
  ⚠️ 与 `get_daily_token_usage` **同一张表、同一个窗口**（`created_at >= CURRENT_DATE`）
  ⇒ 两次查询的和**应当对得上**；⛔ 别另立窗口。
  ⚠️ 查库失败 ⇒ 回 `{}`（**空**），⛔ 不是编一排 0。
- `split_daily_tokens(by_purpose)` —— **纯函数**，拆成三份。
  🔴 **两张表都是【白名单】**（`LLM_PURPOSES` / `EMBEDDING_PURPOSES`），
  ⛔ **不是"除 embedding 之外都算 LLM"** —— 「其它」**单独列出来**，并进任何一边都是编数。
- 🔴 **新增一个 `purpose` 忘了归类 ⇒ 有门会红**：
  `app/tests/test_daily_token_split.py` 扫活代码里出现过的每一个 `purpose="…"`。
  ⚠️ 没有它的话，漏归类的那个**静默进「其它」**，页头上那两个数**悄悄变小**。
