# `app/routing/schemas.py`

## ✅ 做了什么

**Pydantic 模型的集中定义**：请求体 / 响应体 / 路径参数 / 查询参数。

## 🟡 做到哪 / 缺什么

- **8 处产品代码引用 · 5 个测试文件提到**。
- 模型里带 `Field(..., ge=1, le=20)` 之类的**入参校验**（如 `QuestionRequest.top_k`）。
- 🔴 **2026-10-15 加（§七·1）**：`TokenResponse`（`/auth/login`）· `AccessTokenResponse`（`/auth/refresh`）。
  ⚠️ **加之前真调了一次核对键**（login 回 **4 键** · refresh 回 **3 键**）——
  `response_model` **真会裁字段**，见下面那条（`RagSearchResponse` 那次 500）。

## ⚠️ 看代码会误判的地方 ⭐

🔴 **声明 `response_model` 之前，先问「这条端点有没有【别的出口】」**（2026-10-13）：
本文件里已有一处栽过 —— `RagSearchResponse` 第一版把 `answer`/`sources` 声明成**必填**，
而那条端点 `generate_answer=false` 时**根本不产出**这两个键 ⇒ **整条端点 500**。
⚠️ 同款第二次差点再栽：`/agent/cost/records` 有一个 **`{"error": …}` 的失败分支** ——
⇒ 那个模型**所有字段都写成可选**（`CostRecordsResponse`），否则那条分支也会被打成 500。
📌 **判据**：读一遍那条端点的**所有 `return`**，⛔ 不是只看成功那一条。

🔴 **`TokenBudgetResponse` 的 `*_daily_limit` 是 `float | str`，不是 `float`** ——
额度可以是**字符串「无限」**（本仓 `/debug/quota` 那一族确实会回中文串）⇒ 写死 `float` 会让那种角色一访问就 **500**。
⛔ **别把它"收紧"成 `float`** —— 那不是类型不严，是**把一个已知出口留在声明里**。


1. 🔴 **有一处注释与代码【相反】，是 2026-09-20 审计抓出来的**：
   原文写「仅供管理员使用，**暂不加权限控制**」—— 而代码**是加了**的。
   ⇒ 读本文件时**以装饰器/依赖为准，⛔ 别信那句老注释**。
   （本仓原话：**「这段注释说别处有」⛔ 不等于「别处真有」** —— 去重时得核。）
2. ⚠️ **Swagger 的 `example` 会进 `/docs`** ⇒ ⛔ **别在 `Field(example=...)` 里写真凭据**。
   本仓为此栽过（凭据门里的豁免项之一就是这类**良性示例**）。📄。
3. ⚠️ **改字段名 = 改【对外契约】** ⇒ 前端 `app/static/js/*.js` 与 `docs/契约/接口契约.md`
   **同批要跟**。📌 判据：`grep -rn "<新字段名>" app/static/js docs/契约` ⇒ 有命中。
4. ⚠️ **`QuestionRequest` 的 docstring 提到"对话历史"** —— 那是给**真停止按钮**的历史补偿用的，
   ⛔ 不是普通 RAG 检索的一部分。

## 关联

- `docs/契约/接口契约.md` —— 对外契约的**权威**
- `app/routing/specs/api_v1.md` · `app/routing/specs/api_v1_agent.md` —— 谁在用这些模型
-  —— Swagger 示例与凭据的边界

## 🔴 2026-10-12 · `QuestionRequest` 删了 `mode`+ 本仓**第一个响应模型**

**① `QuestionRequest.mode` 是【死字段】，已删。**
它收下的值**没有任何人读** —— `/rag/search` 用的 `mode` 是 **query 参数**（`SearchMode = Literal[...]`）。
⇒ 传 `{"mode":"full"}` 会被**静默忽略**，实跑的是 query 参数的默认值 ⇒ **页面上看不出来**。
⚠️ 改前核过两条：全仓**无人构造** `QuestionRequest(mode=…)`；该模型**没** forbid 额外字段
⇒ **删它是安全的**（老调用方多传一个键，照样被忽略）。契约里那行对不上的默认值**同批改掉**。

**② 🆕 `RagSearchResponse` —— 本仓第一个 `response_model`。**
🔴 **它真会裁字段**（反证实测：声明成只含 `a` ⇒ 实返 `{'a': 1}`，多出来的键**被静默吃掉**）
⇒ 所以模型里**只列真返回里有的**，一个不多。

🔴 **`answer` / `sources` 是【可选】的 —— 这条是拿 500 换来的**：
第一版声明成**必填**，然后 `generate_answer=False` 真调一次
`ResponseValidationError: missing 'answer' / missing 'sources'` ⇒ **整个端点 500**。
根因：这个返回是**条件性**的（不成文就**不产出**那两个键）。
⇒ 改成可选带默认值 ⇒ 形状反而**稳定**了。
⚠️ **代价（已知并接受）**：`generate_answer=False` 的返回**多出** `answer: null` 与 `sources: []`（原先没有）—— 纯新增。

⚠️ **顶层钉死、嵌套留 `dict`/`list[dict]`** 是**有意**的：嵌套若钉死，上游多给一个键就**被静默吃掉**。
⇒ 要钉嵌套，**先给它配一条"字段集合"的门**。

🔴 **一条门** ⇒ `app/tests/test_response_model_declared.py`（凡被前端调用的端点必须有 `response_model`）。

---

## 🆕 2026-10-13· **Agent 线那一批 13 个响应模型**

用途：下面这 13 条是**「外部检索与工具Agent编排」页**打的（📄）。
⚠️ 那道门的规矩是**当场补、⛔ 不能进 `PENDING`**（那个清单只装门立起来之前就存在的老端点）。

| 模型 | 端点 | ⚠️ 看代码会误判的地方 |
|---|---|---|
| `AgentChatResponse` | `POST /agent/langgraph_chat` | 🔴 **两态共用**：`answered`（answer 有值）与 `pending_approval`（工具还没执行，answer 是已写出的半句）⇒ 两态**都要声明**，缺一个就是 500 |
| `AgentMcpChatResponse` | `POST /agent/mcp_chat` | ⚠️ **它没有 `status`** —— 那条链**不会停在审批点**（实测一次问句连发 5 次联网检索、pending 0） |
| `AgentPlanExecuteResponse` | `POST /agent/plan_execute` | 🔴 **形状与另两条完全不同**：没有 `answer`、没有 `thread_id`，只有 `plan[]` + `execution_result`。⚠️ 参数名是 **`goal`** |
| `AgentTraceResponse` | `GET /agent/trace/{thread_id}` | ⚠️ 两支：找到 ⇒ `trace`+`requested_by`；找不到 ⇒ **只有 `error`** ⇒ `requested_by` 必须可选 |
| `AgentTraceCostResponse` | `GET /agent/trace/{thread_id}/cost` | 🔴 **页面上的"本次花费"只认它** —— `/trace/{id}` 的 `total_tokens`/`total_cost` **恒为 0** |
| `AgentPendingResponse` · `AgentPendingContextResponse` | `GET /agent/pending` · `…/context` | ⚠️ 内存队列（重启即空）· context 有 `ok`/`error` 两支 |
| `AgentApproveResponse` | `POST /agent/approve` | 🔴 **5 种形状**（批/拒/第三态/强制收口/错误）⇒ **除 `status` 外全部可选**（本仓返回形状最多变的一条） |
| `AgentApprovalHistoryResponse` | `GET /agent/approvals/history` | ⚠️ 本人默认 · admin 全量 |
| `AgentToolsResponse` | `GET /agent/available_tools` | ⚠️ 没体检过的工具也算 healthy |
| `AgentInterceptsResponse` | `GET /agent/budget/intercepts` | ⚠️ **进程内计数器**（重启归零） |
| `AgentMemorySearchResponse` · `AgentMemoryAddResponse` | `GET /agent/memory/search` · `POST /agent/memory/add` | ⚠️ search 是 **GET** 且参数名就叫 `query`；add **没有 `requested_by`** |

⚠️ **声明之后返回里多出一些 `null` 键**（可选字段的必然结果，与 `RagSearchResponse` 那条同款）——
**纯新增，⛔ 没删任何东西**。真调逐条核过（13 条全是 200）。

🔴 **`/agent/*/stream` 那三条不进这里** —— SSE 的 200 **不是 JSON 体** ⇒ 进那道门的 **`NOT_JSON`** 栏
（⛔ 不是"还没补"）。⚠️ 它们是**故意**在 `agent.js` 里写成**顶层大写常量**的，否则那道门的尺子②**扫不到**。

🔴 **一个已知的缝**（本轮撞上的）：那道门的尺子②把「顶层 `const XXX = '/api/v1/…'`」**一律当成端点**。
⇒ **路径前缀**（如 `/api/v1/agent/trace/`）写成常量会被它报成"一个不存在的端点"。
处理：前缀**不写成常量**，直接在图 4 那个 `buildTrackPath` 里现拼（保护由 `agent.test.js` 的前缀断言兜）。
⛔ **没有去改那道门** —— 它的规则在承重。

---

## 🆕 2026-10-13· 「系统观测」页那 6 个模型

用途：下面 6 条是**「系统观测」页**打的（📄）。⚠️ 同一条门规：当场补、⛔ 不进 `PENDING`。

| 模型 | 端点 | ⚠️ 看代码会误判的地方 |
|---|---|---|
| `DebugCacheStatsResponse` | `GET /debug/cache_stats` | ⚠️ **全站口径**（⛔ 不是"你的"）· 🔴 `cached_embeddings_count == 0` **不等于缓存坏了**（冷启动就是 0） |
| `DebugCountResponse` | `GET /debug/count` | ⚠️ **全站口径**（全站文档数） |
| `DebugQuotaResponse` | `GET /debug/quota` | 🔴 **`daily_limit` / `remaining` 是 `int \| float \| str`** —— 预算为 `inf` 时后端回**字符串「无限」**（`token_tracker.get_token_budget_info`）⇒ 声明成 `int` **admin 一调就 500**。<br>⚠️ **单位是 token ⛔ 不是"次"**（统一了口径，字段名没改）· ⚠️ 它是 **fail-open** 的：查库失败回 0 ⇒ `remaining` 看着像满额度 |
| `DebugRateLimitResponse` | `GET /debug/rate_limit` | ⚠️ `capacity` / `rate` 是**代码常量**（`billing/token_config.py`）· ⚠️ 桶在 **Redis**；`get_remaining` 在 Redis 不通时报**满桶**（有意 fail-open）⇒ 页面不能只看 `remaining_tokens` |
| `AgentToolHealthResponse` | `GET /agent/tool_health` | ⚠️ 启动时 `tools` 是**空 `{}`**（**空 ≠ 全好**）· 只有登记过的工具才体检 |
| `AgentToolVersionsResponse` | `GET /agent/tool_versions` | — |

🔴 **这 6 条的路径在 `app/static/js/ops.js` 里是【顶层大写常量】** —— ⛔ 别改成对象属性：
那道门的尺子②只认顶层常量，改成属性就**扫不到**，这几条会**长期在门外**（"漏扫 ≠ 通过"）。

## 🆕 2026-10-16· 删除文档的响应模型

| 模型 | 端点 | ⚠️ 看代码会误判的地方 |
|---|---|---|
| `DocDeleteResponse` | `DELETE /rag/documents/{doc_id}` | 🔴 **`id` 是前端【真在用】的字段** —— `kb.html` 的「撤销」那格拿它回显「✅ 删掉了（id=…）」。**`response_model` 真会裁字段**（本文件上面那条纪律）⇒ 漏了就是把那个回显**静默**变成 `undefined`，而**没有任何门会红**。 |

🔴 **本条是"门本身有洞"照出来的**（两半一起修，⛔ 只做前一半值不了本）：
① 端点原先**没有** `response_model`；
② 而那道门 `app/tests/test_response_model_declared.py` **看不见它** —— 两个洞叠在一起：
　· 删除 URL 是前端**运行时拼**的（`kb.js` 的 `FULL_PATH + id` · `kb.html` 的 `'/api/v1/rag/documents/' + id`），
　　字面量只到引号为止 ⇒ 被 `.rstrip("/")` **塌缩成列表端点**那条路径（而它**有** schema）；
　· `_declared()` 原先**按路径、不按方法**，且一命中就 `break` ⇒ 同路径的 **GET** 把 DELETE 的缺失**掩蔽**了。
⇒ 门现在**按 `(路径, 方法)` 判**，且**认拼接前缀**（字面量以 `/` 结尾时展开成所有以它为前缀的路由）。
📌 判据：`venv/bin/python -m pytest app/tests/test_response_model_declared.py -q` ⇒ 绿；
**反证**：把 `response_model=DocDeleteResponse` 摘掉 ⇒ 它必须**点名报红** `DELETE /api/v1/rag/documents/{doc_id}`。
