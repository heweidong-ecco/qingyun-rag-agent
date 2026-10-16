# `app/routing/sse.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **新建并已接上全部 6 条流式端点（· 剩余 4 条链）** —— 本仓**所有流式端点的骨架**<br>⚠️ **它收的不是"重复代码"，是 5 条实测出来的顺序约束**<br>✅ 批 2 建层 + 单测 · 批 3 两条旧端点改用它（**逐帧等价**）· 批 5 的 4 条新链**一开始就建在它上面** |
| **对外提供** | `SSE_HEADERS` · `DONE_FRAME` · `sse_frame(payload, *, ensure_ascii)` · `sse_response(source)`<br>· `graph_message_text(item, *, nodes)`（LangGraph `messages` 模式）· `llm_chunk_text(chunk)`（裸 LLM）<br>· `sse_stream(open_upstream, *, endpoint, extract, on_complete, on_incomplete, on_error, ensure_ascii, chunk_delay)` |
| **谁在用** | **6 条，全部**（`grep -rn "sse_stream(" app/routing/api_v1_*.py`）：<br>`api_v1_agent.py` —— `langgraph_chat_stream` · `advanced_agent_chat_stream` · `memory_chat_stream` · `mcp_agent_chat_stream` · `agent_plan_execute_stream`（**5 条**）<br>`api_v1_rag.py` —— `stream_search`（**1 条**） |
| **用例** | `app/tests/test_sse_layer.py`（**17 条**，⛔ 不碰端点 —— 端点的等价性由既有两份用例"全绿且不改"来证）<br>+ `app/tests/test_agent_stream_chains.py`（**60 条**）用**同一个骨架**驱动 4 条新链 |

## ✅ 做了什么

- **一副骨架顶掉 2（很快 6）份内联生成器** —— 删掉的将是 `api_v1_agent.py:227-310` 与
  `api_v1_rag.py:715-792` 里**逐字重复**的 `try / except / except / finally`。
- **5 条约束的落点**（模块 docstring 有表）：

  | # | 约束 |
  |---|---|
  | ① | 同步收尾（计数 → 日志 → `on_incomplete`）**排在【任何 `await` 之前**」<br>⚠️ **异常那条同理**：`on_incomplete(collected, "error")` 排在**第一个 `yield` 之前**（`yield` 也是 await 点） |
  | ② | 关上游包 `anyio.CancelScope(shield=True)` |
  | ③ | `except asyncio.CancelledError: raise`（⛔ 不吞 · ⛔ 不 yield） |
  | ④ | `X-Accel-Buffering: no`（在 `SSE_HEADERS` 里，⛔ 不可省） |
  | ⑤ | 汇总**只从图的最终状态取** —— 骨架**不实现**它，而是**不提供**任何"攒块"的接口（`on_complete` 拿到的是已发文本，要状态就自己去 `aget_state`） |

- **两种 extractor**：图（按节点白名单 + 跳空 `content`）· 裸 LLM 块。`extract=None` ⇒ 原样当文本。

## 🟡 做到哪 / 缺什么

| 批 | 事 | 状态 |
|---|---|---|
| 2 | 建 `app/routing/sse.py` + 本 spec + `app/tests/test_sse_layer.py` | ✅ |
| 3 | 两条既有端点改用它（**行为逐帧等价**） | ✅ **改的那一刻两份文件一行未动** —— `app/tests/test_agent_sse.py` / `app/tests/test_cancel_propagation.py` **都不在 PR #78 的改动清单里**（`git show --stat 93eb2fb`）<br>⚠️ 本行原写 **44 passed** —— 那是把 `app/tests/test_sse_layer.py` 的 **15** 条也算进去了（**当时**那两份文件 **29** 条：29 + 15 = 44）⇒ **命令与数字对不上**。评审收口按**实测**更正：同一命令 **31 passed**（29 + 新增的 2 条） |
| 4 | 改节点（A/B/C 三张图） | ✅ 见 `agent_graph.md` · `agent_checkpointer.md` · `agent_graph_advanced.md` · `agent_graph_advanced_learning.md` |
| 5 | 4 条新 SSE 路由 | ✅ `advanced_chat` · `memory_chat` · `mcp_chat` · `plan_execute` |
| 6 | `app/tests/test_agent_stream_chains.py` | ✅ **39 条**（收工时的数）<br>⚠️ 评审收口后 **47 条**（补：白名单 × 图 4 · 链 C 碎片守卫 1 · 汇总帧 `requested_by` 3）<br>⚠️ **同一天 落地后 60 条**（留痕 13 条：完成 / 取消 / 异常 × 3 链 + 审批点不写 + 链 D 三条） |
| 7 | `on_cancel` ⇒ `on_incomplete` | ✅ **异常出口也收尾了** —— 原来只有**取消**一条出口调同步钩子，`except` 那条**一个字都不留**。<br>泛化后两条出口**各调一次**（`status` = `"cancelled"` / `"error"`），`on_complete` 管正常出口。<br>⚠️ **`outcome == "error"` ⇒ `finally` 里那块不会再调一次**（一次出口只调一次） |

- ✅ **链 D（`plan_execute`）的「线程 → 事件循环」桥接器按计划落在调用方** ——
  `api_v1_agent.py::_ThreadTokenBridge`（`plan_task` 是同步函数、跑在 `asyncio.to_thread` 里）。
  ⛔ **共享层不引入 `threading` 依赖**；桥接器是一个「有 `__aiter__` / `__anext__` / `aclose`」的对象，
  仍走 `open_upstream`。⚠️ 它 `aclose()` **非阻塞 + 协作式**（置取消标志 + 排空队列 + 取消后台任务），
  ⛔ **不 `await thread.join()`** —— 线程杀不掉，**只能让它把剩下的跑完**（只是不再发）。
- ⚠️ **`extract=None` 是给链 D 的**：桥吐出来的**就是文本**（不是图的消息块）⇒ 原样发。
  ⛔ 别给它套 `graph_message_text`（那要 `(chunk, meta)`，会 `ValueError`）。

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| ⚠️ **① `ensure_ascii` 是个可以随便调的旋钮** | ⛔ **它是为"不改线上字节"而存在的**：`/agent/*` 一直用 `False`（中文原样），<br>`/rag/stream_search` 一直用默认 `True`（中文 `\uXXXX`）。**两处今天就不逐字相同。**<br>⇒ 抽公共层**不是顺手统一编码的理由** ⇒ RAG 显式传 `True`，把旧字节留住。<br>⚠️ 两种编码 **JSON 解码后的值相同** ⇒ 功能上无感，但**统一会让"逐帧等价"从"字面为真"变成"差不多"**。<br>📌 ⬜ **未裁**（要不要统一）。 |
| 🔴 **② 骨架会自动补 `[DONE]`** | ⛔ **不会** —— `[DONE]` 与其它收尾帧的**先后是每条链自己的契约**。<br>RAG 是 `内容 → [DONE] → sources`（`[DONE]` 在 sources **之前**，很反直觉但是现状，前端已按此适配）。<br>⇒ 整条尾巴交给 `on_complete`，**骨架一个字都不补**。 |
| ⚠️ **③ 收尾期被取消要算取消** | ⛔ **不算**：`outcome = "done"` 是在**收尾之前**置的（与两条旧实现一致）。<br>⇒ `aget_state` / 落历史 / 发 sources 期间客户端断开 ⇒ **不计入 `stream_cancelled_total`**。<br>🔴 **别"顺手修正"** —— 那会改动一条**没有任何用例覆盖**的边界。 |
| ⚠️ **④ 错误路径统一成 `error + [DONE]`** | ⛔ **不是** —— 默认是那样（= `/agent/*` 现状），但 **RAG 显式传 `on_error` 只发 error 帧**。<br>理由不是保守：**`[DONE]` 会被读成"正常收尾"**，RAG 今天靠"**没有** `[DONE]`"分辨出错。<br>⚠️ 统一会把"出错"变得像"正常结束"。 |
| ⚠️ **⑤ 取消时先关流、再收尾** | ⛔ **反了**（约束①）：**三件同步的收尾在前，`aclose()` 在最后**。<br>`await` 会被**二次投递的取消**打断 ⇒ 关流放前面，后面那三件**一件都不跑**，而**单测全绿**。 |
| ⚠️ **⑥ `extract` 收到的是 `(chunk, meta)`** | **看链**：无子图（B/C）= `(chunk, meta)`；**有子图（advanced_chat）必须 `subgraphs=True`**，形状变成 `(namespace, (chunk, meta))` —— **不开它，一个字都流不出来**。<br>⚠️ 归一化在 `graph_message_text` 里，⛔ 端点别再解一次。<br>🔴 且**先看 `len(item) == 3`**：`ns` 自己就是 tuple，`isinstance(item[0], tuple)` **分不出** `(ns, chunk, meta)` 与 `(ns, (chunk, meta))` —— 顺序写反 ⇒ ValueError。 |
| ⚠️ **⑦ 这块是不是 token 有办法判** | ⛔ **没有**。而且**不止 LLM 会产块**：实测 `advanced_chat` 的 `supervisor`（它 `return state`）会把它收到的<br>**用户提问原文**当"新消息"发出来 ⇒ 不加白名单，**用户会先看到自己的问题被回显一遍**。<br>⇒ **就按节点名收**（`nodes=` 白名单）。 |
| 🔴 **⑧ 这里是记账的地方** | ⛔ **不是** —— 本模块只**发帧**，**一行 token 记账都没有**。<br>⚠️ 但要记住一件**极易踩的反向坑**：节点内部把流**聚合成一条消息**去记账时，<br>**必须对【所有】块做 `+`，⛔ 不许跳过 `content` 为空的块** ——<br>实测本仓 provider 把 `usage_metadata` 挂在**最后一块**（`content=''`）上，跳过它**账就没了**，<br>而接口一切正常。判据 ⇒ `qingyun-rag-agent-TODO待办/探针-流式与记账.py`。 |
| ⚠️ **⑨ `SSE_HEADERS` 可以直接传** | ⛔ **不能** —— `StreamingResponse` 会**持有并改写**自己的 `headers` ⇒ 共享同一个 dict 会让所有响应**串台**。<br>⇒ `sse_response()` 每次都 `dict(SSE_HEADERS)` 复制一份（用例：`test_response_headers_are_copied_not_shared`）。 |
| 🔴 **⑩ 一个出口 `on_complete` / `on_incomplete` 会都调** | ⛔ **不会 —— 三条出口【恰好各调一个】**：正常跑完 ⇒ 只 `on_complete`；取消 ⇒ 只 `on_incomplete(…, "cancelled")`；异常 ⇒ 只 `on_incomplete(…, "error")`。<br>⚠️ **两个都是【可选】参数，且【默认都不调】** ⇒ 「传了 `on_complete` 就以为留痕有了」在**取消 / 异常**两条路上是**空的**。<br>⚠️ **反过来也成立**：正常收尾时 `on_incomplete` **一次都不调** —— 别在那里 `return` 完就以为它兜底了。<br>⚠️ 两个钩子**都是同步**的（约束① 的理由对它们一样成立）⇒ 想在里面做 `await` 的事（落库 / 调 API）得自己想办法递交给别处。<br>🔴 **且 `on_complete` ≠「这轮答成了」** —— 上游**正常跑完**不等于调用方**拿到答案**：4 条 `/agent/*` 流式端点在 `on_complete` 里读到图的 `budget_intercept` 时会**改发 `{"error": …}` + `[DONE]`**、⛔ **不发汇总帧**（批 7）。<br>⇒ 判「答没答成」看**发了哪一帧**，⛔ **别看出现在哪个钩子里**。⚠️ 本层**一个字都不知道**这件事 —— 它由端点自己决定。 |

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/api_v1_agent.md` · `app/routing/specs/api_v1_rag.md` | 批 3 要改用本层的两条端点（含它们**不能动**的契约：帧序、限速） |
| `app/core/specs/metrics.md` | `track_stream_cancel` / `stream_cancelled_total` 的本尊 |
| `qingyun-rag-agent-TODO待办/探针-真流式与子图.py` | ⚠️ ⑥ 那张六剧本表（`subgraphs` 开关与产出形状） |
| `qingyun-rag-agent-TODO待办/探针-流式与记账.py` | ⚠️ ⑧ `usage_metadata` 挂在哪一块上 |


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/routing/sse.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ 整段**原样搬来**，⛔ 未改写。

---

## 这个模块**存在的理由**：那 5 条**不能交给调用方**的约束

骨架里凡是"顺序"和"关流方式"，**都是实测撞出来的**，不是设计偏好。
交给调用方自己写 ⇒ **一定会有人写反**（本仓**已经写反过一次**，）。

| # | 约束 | 出处（都是实测，不是推断） |
|---|---|---|
| ① | **同步收尾（计数 → 日志 → `on_incomplete`）必须排在【任何 `await` 之前】** |：`await stream.aclose` 会被**二次投递的取消**打断 ⇒ 排在它后面的收尾**一件都不跑**，而**单测全绿**（假流的 `aclose` 不抛）。只在**晚切**（用户已看到字再点停止）时露出来 |
| ② | **关上游必须包 `anyio.CancelScope(shield=True)`** | 同上。取消作用域在**每个 await 点反复投递** ⇒ 不护住，"关"这个动作每次都半途而废 |
| ③ | `except asyncio.CancelledError: raise` —— **不吞 · 不在里面 `yield`** | /：旧实现在取消时还发了一帧 `[DONE]`，**实测真发得出去**（接收方已走，发出去只会让"已取消"与"正常收尾"在**帧层面长得一样**） |
| ④ | `X-Accel-Buffering: no` **不可省** | 有反代（Nginx / Cloudflare）时它会把 SSE **攒着发** ⇒ 本地直连一切正常、**上线后变成假流式** |
| ⑤ | 汇总/状态**只从图的最终状态取**（`aget_state`），**不是"把流过的块攒起来"** |  **真服务**撞出来的：攒块会让上一轮的 `tool_calls` 混进来 ⇒ 判成 `pending_approval`，而图**早就跑完了** ⇒ 前端**永远等一个不会来的批准** |

**③ 有两副面孔**：Starlette 2.3 抛 `CancelledError`，2.4 走 `GeneratorExit`（**不是** `Exception` 的子类）。
⇒ `outcome` **默认按「被取消」算**，只有跑到收尾才改 `"done"` —— 这样**两条分支都记得上**。

---

## 它**不**负责什么（别往里塞）

| 不进共享层 | 为什么 |
|---|---|
| `check_session_token_budget` / `circuit` | 必须在**建生成器之前**拒（触顶要回 4xx，不是"HTTP 200 + 流一半断"）；且两条 AST 守卫钉着它们在**端点函数体**里（`app/tests/test_session_budget_wiring.py` · `app/tests/test_breaker_wiring.py`） |
| `register` / `resolve`（待接管队列） | 四条链各不同；且靠**模块全局**查找，挪进来 `monkeypatch` 就够不着了 |
| `summarize_agent_result` / status 口径 | 只有 Agent 链有 status；`plan_execute` 根本没有 |
| `session_key` 拼身份 · 原 `thread_id` 回显 | 端点特有（**两条轴**：预算用原值、图用拼过的，别合并） |
| 检索 / prompt 构建 / LLM 单例 | 各链专有 |
| **`[DONE]` 与其它收尾帧的先后** | 那是**每条链自己的契约**（RAG 是 `内容 → [DONE] → sources`，很反直觉但是现状）⇒ 整条尾巴交给 `on_complete`，**骨架不自动补 `[DONE]`** |
| **节点名** | 见 `graph_message_text(..., nodes=...)` —— 四条链的节点名不同，别在这里写死 |

---

## 调用方必须知道的两件事

1. **`extract` 的入参形状取决于图的 `subgraphs` 开关**（实测）：
   `subgraphs=False` ⇒ `(chunk, meta)`；`subgraphs=True` ⇒ `(namespace, (chunk, meta))`。
   `graph_message_text` 已经把这个归一化掉了，别在端点里再解一次。
2. **聚合与发帧是两件事**：本模块只管**发帧**。节点内部要把流**聚合成一条完整消息**去记账时，
   **必须对【所有】块做 `+`，不许跳过 `content` 为空的块** ——
   实测本仓 provider 把 `usage_metadata` 挂在**最后一块**（`content=''`）上，跳过它**账就没了**
   （判据见 `qingyun-rag-agent-TODO待办/探针-流式与记账.py`）。
