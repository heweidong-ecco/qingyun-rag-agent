# `app/agent/agent_graph_advanced.py`

> ⚠️ **正文里那些 `:NNN` 行号停在 2026-10-05 / 10-08 那几批** —— 之后本模块又长了几十行，
> 那些行号**整体偏小**（实测：`build_mcp_agent()` 在 `:512`，正文写的是 `:288`）。
> ⛔ **别拿它们去 `sed -n`** —— 用 `grep -n "def <名字>" app/agent/agent_graph_advanced.py` 现找。
> 📌 2026-10-13**只校正了**与记忆检索相关的那几处 ＋「规模」那一格，**其余没动**。

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用，且是生产链** —— 但 🔴 **有一处实锤缺陷**（见下；另一处 已修）<br>✅ **改完**： + 都已落在它的 `llm`（`:50`）上 —— 见「✅ 做了什么」末条<br>✅ **（``）**：该 `llm` **改走 `llm_factory.make_llm("chat", "agent")`** —— `model`/`api_key`/`base_url`/`max_tokens` 不再写在本地。<br>⚠️ **`timeout` / `max_retries` 没丢**：它们走 `make_llm` 的 `**extra` **逐点透传**（这是本仓第一处用到 `**extra` 的地方）。<br>⚠️ **`llm.bind_tools(...)` 照旧能用**（那句在 `get_llm_with_mcp_tools()` 里，现于 `:295`；`llm_with_tools` 是 `:354` 拿到它的）—— 这正是「工厂返回值必须是裸 `ChatOpenAI`」那条约束的来由之一<br>🔵 **：两个节点改【真流式】** —— `chat_node`（`:315`）与 `agent_decide`（`:369`）都声明 `config: RunnableConfig` + 换 `async for chunk in llm.astream(…, config=config)` 逐块 `+` 聚合。⚠️ **本图的两个节点本来就是 `async`** ⇒ **走 `astream`**，⛔ 别照抄 `agent_graph.py` / `agent_checkpointer.py` 那两处（那两处是**同步**节点，改成同步会**阻塞事件循环**）。新增模块级 `STREAMABLE_NODES = frozenset({"agent", "chat"})`（`:304`）—— ⛔ **`tools` 不在里面**（它不调 LLM，且它返回的 `ToolMessage` 会被当成"新消息"发出去）。<br>⚠️ **`+` 聚合在本图是【必须】的**：`tool_calls` 碎片化到达，只拼 `content` 会丢掉它们 ⇒ `should_continue` 判不出 `"tools"` ⇒ **工具永远不会被执行**，而接口一切正常。📌 守卫 `app/tests/test_agent_stream_chains.py::test_real_chain_c_nodes_stream_one_chunk_per_token`<br>🔴 **：预算拦截从【软】变【硬】** —— 被拦不再只是塞 `ToolMessage` 就继续走，而是**同时在 state 上写 `budget_intercept`**，两个端点据此回 **429 / error 帧**（改前是 HTTP 200，只有 LLM 知道被拦了）。⚠️ **同一批还堵掉一个更糟的现状**：被拦之后图**无条件回 `agent`** ⇒ 模型再调一次同一个工具、再被拦一次……改前实测直接 `GraphRecursionError: Recursion limit of 25 reached`（**在已判定"没钱了"之后又白烧十几轮 LLM**）⇒ 新增 `after_tools` 条件边，被拦即 `END`。📄 决策与**为什么不是在节点里 `raise`** ⇒ · 计划 ⇒ 下方「实施计划 · 批 2」<br>⚠️ **新 state 键 `budget_intercept`（`AgentState`）**：⛔ **没挂 `operator.add`** ⇒ last-write-wins + **落 checkpoint** ⇒ 由**入口节点 `agent_decide` 每轮开头清零**（它的**两个出口都带**）。🔒 守卫 `app/tests/test_budget_hard_intercept.py::test_上一轮的拦截标志不会串到下一轮`<br>🔴 **（批 7）：本图【另两处】软返回一并收口** —— `chat_node`（兜底）与 `agent_decide`（入口）的 `check_token_budget` 软返回原先**只塞一句话当正常答案**（HTTP 200）⇒ 现在**同时写 `budget_intercept`**，端点回 **429 / error 帧**。⚠️ `agent_decide` 那处**语义翻转**（改前写 `**cleared` 显式清零 ⇒ 现在写本轮原因）；⚠️ **顺带堵掉"钱花在闸之前"**（`chat_node` 的记忆检索下移到门之后）。📄 · 判据 `app/tests/test_budget_soft_return.py`（21 例，**变异自证 27/27**）<br>🔴 **：记忆检索改【fail-open】** —— `inject_memories_to_prompt` 里那行裸调用会让**整条 `/agent/mcp_chat` 在 embedding 取不到时 500**（CI 的占位 key 就是这个形状）。现已包 try/except：失败只**少一段上下文** ＋ `logger.warning` 留痕。⚠️ **本机看不见**（真 key ＋ Redis 缓存）。📌 见 ⚠️ · 守卫 `app/tests/test_memory_injection_failopen.py` |
| **对外提供** | `build_mcp_agent()`（返回编译好的图，`:288`）· `mcp_session()` · `get_mcp_tools()` · `call_mcp_tool()` · `_get_actor()` · `aclose_mcp_session()`（🔴 后两个 -B 新增） |
| **谁在用** | `api_v1_agent.py:1327` 的 `POST /agent/mcp_chat`（**三代 Agent**）· 🆕 `POST /agent/mcp_chat/stream`（`:1407`）<br>⚠️ **两个端点都会把 `budget_intercept` 转成调用方看得见的东西**：非流式 **429**（`:1384`）· 流式 **error 帧**（`:1470`） |
| **规模** | **699 行**（与 `wc -l` **一致** —— 本文件末行有换行符；⚠️ 与 `agent_graph.py` / `agent_checkpointer.py` **不同**）<br>⚠️ **本行曾停在 519 很久** —— ~ 10-13 那几批（等）**改了代码没同步这一格**，实测已是 **679**· 🔴 ⇒ 679 → **699**（两处 fail-open 守卫 + `loguru` import） |

## ✅ 做了什么

- **三代 Agent 图**：`agent_decide` →（有 tool_calls）→ `tool_execute` → 回 `agent`；(无) → `chat_node` → `END`
- **工具走 MCP Client**：🔴 **2026-10-08改成长驻会话** ——
  `_McpSessionActor` 用一个 **holder task 独占整个 `async with`**，调用方只投队列 + 等 future。
  实测**每次工具调用 ~2s → 4–7ms**（成本本来几乎全在起子进程 + 重新 import langchain）。
  ⚠️ `mcp_session()`（`:117`）**保留着**（单次自开自关那条老路），actor 内部就是用它。
  ⚠️ 会话池**已移除**（2026-09-20），理由写在 `:83-105`
- **工具调用缓存**：`call_mcp_tool_with_cache`（`:185`）· `CACHE_TTL_MAP`（`:169`）按工具分档；`execute_python` TTL=0（不缓存）
- **长期记忆注入**：`inject_memories_to_prompt`（`:67`）—— 从 mem0 检索后拼到 system prompt 前
  · 🔴 **2026-10-13：改成 fail-open** —— 检索失败只**少一段上下文**，⛔ 不打挂整条链
    （改前是裸调用 ⇒ `/agent/mcp_chat` 在 embedding 取不到时**整条 500**）。见 ⚠️⑨
- **轨迹**：`record_tool_start` / `record_tool_end` / `record_agent_decision`（`:223` 导入）
- **⭐ 多级预算**：`tool_execute` 里调 `check_multilevel_budget` —— **这是全仓【唯一】的调用点**<br>🔴 **2026-10-05起是【硬】拦截**：被拦 ⇒ 写 `budget_intercept` + `after_tools` 直接把本轮送去 `END`

## 🟡 做到哪 / 缺什么

- 🔴 **记账用错单价**（见 ⚠️②）—— 本 spec 新查出
- ~~🔴 **超预算是软拦截**（见 ⚠️③）~~ ✅ **2026-10-05 已修（🅗）** —— 见状态表末条与下方「实施计划 · 批 2」
- ~~⚠️ **`llm` 没有 `timeout` / `max_retries`**（`:39-44`）：`ChatOpenAI` 吃 SDK 默认
  ⇒ 最坏 `600s × (1+2)`。~~ ✅ **2026-10-01 已修（🅗）**：现为 `timeout=60` + `max_retries=1`。
  ⚠️ **本条到 2026-10-02 才划掉** —— 2026-10-01 那天只改了状态表与「✅ 做了什么」，**这条 bullet 忘了同步**。<br>
  🔴 **2026-10-02（`①b`）复核**：`timeout` / `max_retries` 现由 `make_llm(..., timeout=…, max_retries=…)` 的
  `**extra` 传入 ⇒ **值没变**（60 / 1），只是换了个写法。
- ⚠️ `check_token_budget(user_name, estimated_tokens=500)` 的 **500 硬编码**，且 **`agent_decide` 与 `chat_node` 各查一次**
- ⚠️ `MemorySaver()`（现 `:414`）**进程内存** ⇒ 重启即丢（与 `/agent/langgraph_chat` 同）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴🔴 **④ MCP 工具子进程「应该」继承父进程的环境变量** | ⛔ **不继承** —— MCP 的 `stdio_client` **只给子进程 6 个变量**<br>（Linux 白名单 = `HOME` / `LOGNAME` / `PATH` / `SHELL` / `TERM` / `USER`，见 `mcp/client/stdio.py::DEFAULT_INHERITED_ENV_VARS`）<br>⇒ **我们 `.env` 里的东西，一个都到不了子进程**。<br>🔴 **最致命的是 `DOCKER_ENV`**：它不在白名单 ⇒ 子进程里 `IS_DOCKER=False`<br>⇒ `core/config.py` 的「本地开发覆盖」把 **`REDIS_HOST` 与 `POSTGRES_HOST` 双双改成 `localhost`**<br>⇒ 而容器里那两个服务叫 **`redis` / `postgres`** ⇒ **子进程连不上**（所有 `*_API_KEY` 同理）。<br>🔴 **它在本地【永远看不出来】** —— 本机 `localhost:6379` 恰好就是对的<br>⇒ 只在容器里现形，而现形方式是**一行 fail-open 的警告**<br>（`工具缓存 Redis 不可用 ⇒ Error 111 connecting to localhost:6379`）—— ⛔ **不会让任何用例变红**。<br>✅ **2026-10-15 已修**：构造 `StdioServerParameters` 时**显式 `env={**os.environ}`**。<br>⚠️ **为什么可以直接给全量 env**：MCP 那份白名单是给**不受信任的第三方 MCP 服务**用的，<br>而**这个子进程是我们自己的脚本**（`app/tools/mcp_server.py`），它本来就要读同一份配置。<br>📌 守卫 ⇒ `app/tests/test_agent_repairs.py::test_mcp_server_gets_the_full_environment`<br>（**变异自证**：把 `env=` 那行删掉 ⇒ 当场红）。<br>⚠️ **同文件里那两个替身 `_Params` 也必须接 `env` 关键字** —— 不接会 `TypeError`，<br>而它被 `_McpSessionActor` 包成「**MCP 会话起不来**」⇒ **看起来像另一个问题**（改的时候真踩了一次）。 |
| 🔴 **① 这是"进阶示例"** | ⛔ **它是生产实现** —— 模块 docstring 第 2 行写着「LangGraph **进阶示例**」，<br>但它就是 **`POST /agent/mcp_chat`（三代 Agent）的图本体**。<br>📌 **`agent_graph.py` 的 docstring 反而老老实实**。名字与自述**都不可信**，**看谁 import 它**。 |
| ✅ **② 记账记的是实际模型** | **2026-10-01 已修**（`🅗 S4`+）。原状：`model=` **写死成 `"qwen-turbo"`**（`:316` `:364`），而 `.env` 里实际是 `deepseek-v4-flash`。<br>**当时的完整影响链**：`record_usage(model="qwen-turbo")` ⇒ `PRICING["qwen-turbo"]` = 0.003/0.006<br>⇒ 写进 `token_usage_logs.cost` 的是**按 qwen-turbo 单价算的钱** ⇒ `get_thread_cost()`（`:702`）<br>⇒ **`check_multilevel_budget` 第二级 `MAX_THREAD_COST`（单位【元】）拿它比** ⇒ 🔴 **连拦截都是拿错的数在判**。<br>✅ **现状**：改成 `getattr(…, "model_name", …)`（`plan_execute.py:154` 的写法，**从对象取**），且 `MODEL_PRICING` **已补 `deepseek-v4-flash`**。<br>📌 **一处细节**（`agent_graph_advanced.py:366`）：记的是 **`llm_with_tools`**（`:345` 实际调用的那个），不是模块级的 `llm` —— 实测 `llm.bind_tools(...)` 后 `.model_name` 仍是 `deepseek-v4-flash`。<br>⚠️ **仍是近似**：单价**不区分缓存命中** ⇒ 偏高估（偏保守）。 |
| ✅ **③ 超预算会被拒** | **2026-10-05 已修** —— 改前是**软拦截**：塞一条 `ToolMessage` 就 `continue`，<br>**HTTP 仍是 200**，那句"⚠️ 预算拦截"**只有 LLM 看得见** ⇒ **调用方在响应里看不出"被拒了"**。<br>✅ **现状**：`tool_execute` 被拦时**同时在 state 上写 `budget_intercept`**<br>⇒ `/agent/mcp_chat` 回 **429 `QUOTA_EXCEEDED`** · `/agent/mcp_chat/stream` 发 **`{"error": …}` 帧**（响应头已发出，改不了状态码）。<br>⚠️ **另一条同时堵掉的现状**：改前被拦后图**无条件回 `agent`** ⇒ 模型再调一次、再被拦 …… 实测直接<br>`GraphRecursionError: Recursion limit of 25 reached`（**已经判定没钱了，又白烧十几轮 LLM**）⇒ 新增 `after_tools` 条件边。<br>🔴 **同一个坑还在别处**：`check_budget` 依赖（`api_v1_agent.py:1283`）判的是【用户每日 token 预算】，与这条**并存**；<br>~~而 `chat_node:332` / `agent_decide:~392` 的 `check_token_budget` 软返回**仍是软的**~~ ⇒ ✅ **2026-10-05 批 7已收口**：本图**两处**软返回（`chat_node:377` · `agent_decide:451`）改成一并写 `budget_intercept` ⇒ 端点回 **429 / error 帧**。<br>⚠️ **`agent_decide` 那处的语义【翻转】了** —— 改前它写 `**cleared`（**显式清零**），本批改成写**本轮的原因**（"入口节点自己就是拦截点"）。 |
| ⚠️ **④ `should_continue` 跟 `agent_graph.py` 里那个是一回事** | ⛔ **不是** —— 本文件的是一个**内嵌在 `build_mcp_agent()` 里的局部函数**（`:377`），<br>返回值是 **`"tools"` / `"chat"`**；而 `agent_graph.py:95` 那个返回 **`"approval"` / `"tools"` / `END`**。<br>**同名、不同语义、不同作用域。** ⚠️ 搜 `should_continue` 会同时命中两个。 |
| ⚠️ **⑤ 「MCP 会话是池化的」** | ⛔ **池化已移除**（2026-09-20）—— 但文件里**同时留着"曾经的方案是全局单例"那段叙述**（`:76-78`）<br>和"会话池已移除"的说明（`:83-105`）。**读开头那几行容易读成当前实现。**<br>✅ **现状（2026-10-08 -B 起）**：**长驻 actor**（`_McpSessionActor`）—— 一个 holder task 独占整个 `async with`。<br>⚠️ **`mcp_session()` 还在**（`:117`），但**不再是每条调用路径直接用它** —— 它现在只被 actor 用。<br>🔴 **⛔ 别因此读成"又池化了"**：池化横跨 **3 个 task**（炸），actor 只跨 **1 个**。<br>📌 判据：`grep -n "async with mcp_session" app/agent/agent_graph_advanced.py` ⇒ 只应命中 `_hold` 里那一处。 |
| ⚠️ **⑥ 记账的 `model=` 是"用了那个模型"** | ⛔ **那只是记账时的标签，不影响真调用** —— 真模型是 `llm`（**2026-10-02 起由 `make_llm("chat", …)` 决定**，即 `LLM_MODEL_CHAT`）。<br>⇒ **换标签不会让服务换模型，只会改【单价口径】**。✅ 2026-10-01 起标签已从对象取（见 ⚠️②）。 |
| 🔴 **⑨ 「`inject_memories_to_prompt` 里那个 `try/except` 是多余的保护」** | ⛔ **它是  的修复本体**（2026-10-13）—— 改前那行是**裸调用** ⇒ 记忆检索一失败（key 不对 / 网络不通 / mem0 或向量库挂了）**整条 `/agent/mcp_chat` 当场 500**。<br>⚠️ **本机看不见**：本机有真 key ＋ Redis 里有 embedding 缓存（同族教训 ⇒  §六）。<br>🔴 **里面那句 `except AssertionError: raise` 也⛔ 不是冗余** —— `test_budget_soft_return._no_memory` 换上去的那个探针报的就是 `AssertionError`，而**它是 `Exception` 的子类**<br>⇒ 少了那两行，探针会被下面的 `except Exception` **静默降级**、那条「钱⛔ 不许花在闸之前」的守卫**当场失效，而没有任何门会红**（实测：拆掉它，`test_budget_soft_return` 仍 21 passed）。<br>📌 守卫 ⇒ `app/tests/test_memory_injection_failopen.py`（**4 条**，每条都带**正控**）；**变异自证**：拆守卫 ⇒ 2 红 · 只拆 `except AssertionError` ⇒ 1 红。 |

---

# 🔵 实施计划 · 批 2 ·  预算软拦截 → 硬拦截（2026-10-05 立）

> **要的是什么**：作者 2026-09-30 裁定（本文件末表第 4 条）——
> 超预算时**调用方在响应里看得出"被拒了"**。现状 `:255-262` 塞一条 `ToolMessage` 就 `continue`
> ⇒ **HTTP 200**，只有 LLM 自己看得见 ⇒ 「预算拦住了」这句话**只对 LLM 成立**。

## 🔴 先把一个陷阱钉死：**⛔ 不能在图节点里 `raise`**（实测，非推断）

裁定原文写的是「改成抛 `AppException(QUOTA_EXCEEDED)`」。**照字面做会引入一个新缺陷**，
实测（探针：`StateGraph` + `MemorySaver`，节点内 `raise` 后检查 checkpoint）：

```
抛异常后的 checkpoint：next = ('tools',)
  HumanMessage 'hi' tool_calls=None
  AIMessage    ''   tool_calls=['c1']      ← 🔴 没人回答的 tool_calls
第二轮同一 thread：agent 节点收到 3 条 ——
  HumanMessage 'hi' / AIMessage(tc=['c1']) / HumanMessage '再问一句'
```

两条后果，都是**新的**：
1. **消息序列非法** —— 「带 `tool_calls` 的 `AIMessage`」后面跟的不是 `ToolMessage`
   ⇒ 真 provider（OpenAI 口径）**400**。而 `agent` 是入口节点，**每一轮都会跑**
   ⇒ 这个 thread **从此废掉**（不是"下次再说"，是"下一次必定 500"）。
2. **失败的 task 会重跑** —— `next` 停在 `('tools',)`，下次 `invoke` 会把那个拦截**再抛一遍**。

> ⚠️ **"预算用完了我自己知道"不是理由**：第三级（用户日预算）**午夜会重置**；
> 第一、二级（`MAX_SINGLE_CALL_COST` / `MAX_THREAD_COST`，元）**管理员可以调**。
> 重置之后用户回到这个 thread —— 撞上的是**被污染的会话**，与预算再无关系。

## ✅ 采用的方案：**拦截是图的一等公民结果，走 state 出来；端点层再转成 429**

```
tool_execute  被拦 ⇒ ① 照旧答一条 ToolMessage（序列合法，本轮每个 tool_call 都有回应）
                      ② 置 state 键 budget_intercept = 拦截原因
                      ③ 条件边把 tools 直接送去 END（⛔ 不再回 agent —— 回去只会再调一次工具）
端点 /agent/mcp_chat        读完 ainvoke 的结果 ⇒ budget_intercept 非空 ⇒ 抛 AppException(QUOTA_EXCEEDED)  ⇒ 429
端点 /agent/mcp_chat/stream 收尾帧之前查 ⇒ 改发 {"error": …} 帧                                ⇒ 调用方看得见
```

**为什么 flag 不会串轮（这是本方案唯一的真风险）**：入口节点 `agent_decide` 在**每一轮开头**
把它清零 —— 入口是 `agent`（`:441`），每轮第一个跑的就是它 ⇒ "标志只反映**本轮**"。
🔒 守卫用例：`test_上一轮的拦截标志不会串到下一轮`（**删掉那句清零即红**）。

**为什么清零是必须的**（⛔ 别以为是多此一举）：`budget_intercept` 是**普通 state 键**
（不像 `messages` 挂了 `operator.add`）⇒ **last-write-wins + 落 checkpoint**
⇒ 不清零的话，"上一轮被拦"会让**下一轮不需要工具的正常提问也返回 429**。

## 备选与代价（⛔ 不选，理由留在）

| # | 方案 | 为什么不选 |
|---|---|---|
| 甲 | 节点内 `raise`（= 裁定字面） | 🔴 **上文的实测**：污染 checkpoint，会话报废 |
| 丙 | 端点**预检** `check_multilevel_budget` | 一、二级闸门**要 `tool_name`**，而工具是 LLM 在图里选的 ⇒ 端点根本不知道 |

## 📋 任务分解（TDD，每条都带**变异自证**）

**任务 1 · 节点：拦截改为「答 ToolMessage + 置标志 + 直接 END」**
- 落点：`agent_graph_advanced.py` 的 `AgentState`（加键）· `tool_execute:255-262` · 建图处 `:446`（`add_edge("tools","agent")` → 条件边）
- 测试（**节点是模块级的，可直接调**）：`test_拦截时节点不抛异常且答满所有 tool_call`
  · `test_拦截时置上_budget_intercept`
- 变异：把标志那行删掉 ⇒ 必须红

**任务 2 · 入口清零（防串轮）**
- 落点：`agent_decide` 开头（该节点**嵌在 `build_mcp_agent()` 里** ⇒ 运行期取不到对象，**跑真图**）
- 测试：`test_上一轮的拦截标志不会串到下一轮` —— 喂一个已带标志的 state 跑 `mcp_agent`，出来必须是 `None`
- 变异：删掉清零那行 ⇒ 必须红（**这条就是「不清零会怎样」的判据本身**）

**任务 3 · 两个端点**
- 落点：`api_v1_agent.py` 的 `mcp_agent_chat:1366`（`ainvoke` 之后）· `mcp_agent_chat_stream` 的 `_complete:1437`
- 测试：`test_被拦时非流式端点回429` · `test_被拦时流式端点发error帧且不发汇总帧`
- 变异：把端点那段判断去掉 ⇒ 必须红

**任务 4 · 收口**：本 spec 状态表 + ⚠️③ · `CHANGELOG`

### ✅ 执行记录（2026-10-05 当天做完）

| 判据 | 值 |
|---|---|
| 新增用例 | **11 条**（`app/tests/test_budget_hard_intercept.py`）—— 4 条节点 · 3 条图 · 2 条非流式端点 · 2 条流式端点 |
| 全量 | **619 passed**（基线 608 + 11）·  退出码 0 |
| **变异自证** | **7 处全中**（`M1`–`M7`）：改坏每一处修复，**该红的那一条**都红，且还原后逐字比对原文一致 |
| 🔥 变异 `M3` 的意外收获 | 把 `after_tools` 还原成 `add_edge("tools","agent")` ⇒ 用例红在 **`GraphRecursionError: limit of 25`** —— 这正是**改前**的真实行为（被拦之后又白烧十几轮 LLM） |

⚠️ **本节上面那些行号是「写计划时」的**（实现之后整段下移）—— 现状 anchors：
`AgentState` 的新键 `:46` · `tool_execute` `:245` · `agent_decide` 的清零 `:425`
`after_tools` `:489` · 条件边 `:511` · 端点 `api_v1_agent.py:1384` / `:1470`。
⛔ **别照抄上面的旧号去 grep** —— 本仓已有「行号过期」这一类坑。

## ⚠️ 本轮**不动**、但已看见的两处（⛔ 别以为顺手改了）

| 处 | 现状 | 处置 |
|---|---|---|
| `chat_node:332` 与 `agent_decide:~392` 的 `check_token_budget` 软返回 | 同样塞一句"今日Token预算已用完"就**当正常答案返回**（HTTP 200） | ✅ **2026-10-05 批 7已收口**—— 本图 2 处都改成写 `budget_intercept` |
| 被拦的工具只有 `record_tool_start`、**没有 `record_tool_end`** | 追踪里留一条"开了没结束" | ✅ **改前改后一致**（原来 `continue` 也不写 end）⇒ 不是本轮的回归 |

### 🔴 批 7（2026-10-05）在本图改了什么

| 处 | 改动 |
|---|---|
| `chat_node`（**兜底**，非入口） | 软返回加 `"budget_intercept": "今日Token预算已用完，请明天再试。"`（**字面量** —— 本图这两处不走 `BUDGET_EXCEEDED_MSG` 常量） |
| `agent_decide`（**入口**） | 软返回由 `**cleared` 改成写**本轮原因**（**语义翻转**）；正常出口的 `cleared`（`":435"`）原样保留 |
| 🔴 **`chat_node` 的记忆检索【下移到门之后】** | 改前 `inject_memories_to_prompt(...)` 排在 `check_token_budget` **之上** ⇒ **预算已被拒的那一轮照样打一次 DashScope embedding**，取回的记忆**当场被丢弃**（走不到 `messages`）。⚠️ 发现方式：本批用例**本机绿、CI 红**（本机 `.env` 有真 key ⇒ **在花真钱**） |
| 判据 | `app/tests/test_budget_soft_return.py` —— **直调节点函数**（`graph.nodes[name].bound.func/.afunc`），⛔ **不是"跑整张图看最终 state"** |

> 🔴 **为什么判据必须【逐出口】而不是"跑整张图"**（本批踩过、已记进）：
> 本图有**两个**预算检查点（入口 `agent_decide` 与兜底 `chat_node`），
> 而 `supervisor` 那类超预算**正是落到 `chat_node`** ⇒ **后者的写入掩蔽了前者**。
> 实测：把 `agent_decide` 那处的标志删掉，"跑整张图"的写法**照样绿**。

---

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/api_v1_agent.md` | **唯一入口** `POST /agent/mcp_chat` |
| `app/billing/specs/token_tracker.md` | `PRICING` / `check_multilevel_budget` / `get_thread_cost` 的本尊 · **计划 ①a 的  要扩 `PRICING`** |
| `app/agent/specs/plan_execute.md` | ⚠️ **同一个"漏传 / 写死"家族的对照**（那边超时都显式设了） |
| `app/agent/specs/pending_approvals.md`（待建） | 三代图**没有审批中断**（与 `/agent/langgraph_chat` 不同） |
| `后端补齐清单`  | ✅ **已接（2026-10-01）** —— 原 `:39` 的 `llm` 现于 `:50` 接 `MAX_TOKENS_AGENT`（1024）<br>✅ **2026-10-02**：这个 `max_tokens` 已**收进 `llm_factory`** ⇒ 本地改为 `make_llm("chat", "agent")` |
| `后端补齐清单`  | ⚠️ **R4「成本可见」就建立在本条 ⚠️② 之上** ⇒ **不修单价，看板上的钱就是错的** |

> ### ✅ 要不要做 —— **2026-09-30 作者全部裁定**
>
> | # | 事 | 裁定 | 落点 / 注意 |
> |---|---|---|---|
> | **1** | 🔴 **修 3 处 `model="qwen-turbo"` 硬编码** | ✅ **已修（2026-10-01）** | `agent_graph_advanced.py:316` · `:364` · `agent_checkpointer.py:58`<br>⇒ 改成 `getattr(…, "model_name", None) or getattr(…, "model", "unknown")`（**照抄 `plan_execute.py:154`**） |
> | **2** | 🔴 **`PRICING` 表补 `deepseek` 条目** | ✅ **已补（2026-10-01）** | 落在 `token_config.py` 的 `MODEL_PRICING` ⇒ **`deepseek-v4-flash`: 0.001 / 0.002 元/千 token**<br>（官方人民币口径「输入 1 元 / 输出 2 元 每百万」；**作者选定此口径**。⚠️ 不区分缓存命中 ⇒ 偏高估，理由写在 `token_config.py` 内） |
> | **3** | ⚠️ **`llm` 补 `timeout` / `max_retries`**（🅗） | ✅ **已补（2026-10-01）** | 照 `plan_execute.py:70-76` 那三个常量的**做法**（`timeout=` + `max_retries=1`）<br>✅ **秒数已定：`AGENT_LLM_TIMEOUT = 60`**（`agent_graph_advanced.py` 内，就这一处用）<br>**为什么是 60**：这是**多轮工具对话**（`:345` 每次 invoke 一轮，带 MCP 工具 schema），**不抄 `plan_execute` 的单步 30/20/15** ⇒ 取 planner 的两倍（① 输出上限 1024 比那份 JSON 长；② 工具 schema 更大 ⇒ 首 token 更慢）。<br>📌 **60 是实施者的判断，⛔ 不是业务裁定** —— 要改就改这一个数。 |
> | **4** | ⚠️ **软拦截 → 硬拦截** | ✅ **要变成硬拦截** | 现在 `:244-251` 是塞 `ToolMessage` 文本、**HTTP 200** ⇒ 改成**抛 `AppException(QUOTA_EXCEEDED)`**<br>⚠️ **注意层次**：本文件在**图节点里**（不是路由层）⇒ 抛出的异常要能被**端点层**接住并转成 `AppException`（参照 `api_v1_agent.py:209` 接 `BudgetExceededError` 的写法）<br>⚠️ **改硬拦截会改变 `/agent/mcp_chat` 的响应形状** ⇒ 测试要一起改<br>✅ **2026-10-05 已全部做完**（批 2 +  批 7）—— ⚠️ ⛔ **但没照字面"在图节点里抛 `AppException`"**： 实测那样会**污染 checkpoint**（留下没人回答的 `tool_calls` ⇒ 该 thread **每轮必 500**）⇒ 改走 **`budget_intercept` → 端点层转 429 / error 帧**。 |
