# `app/routing/api_v1_rag.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **`/rag/stream_search` 的检索【改走与 `/rag/search` 同一条链】** —— 经由新抽的 `_stream_retrieval` 调 `create_accurate_norerank_pipeline`（改写 + 向量 + BM25 + RRF，**不重排**）。<br>⚠️ **代价已知情**（作者 裁）：**首字变慢**（改写真调一次 LLM）+ **多花 token**。<br>⚠️ **帧序 / `ensure_ascii` / `chunk_delay` 一行没动**（那是线上契约）—— 本刀只换**检索**那一半。<br>📌 判据 ⇒ `app/tests/test_stream_same_chain.py`（**2 条**；**三条反证都跑过** —— 含"两边一起退化时第 1 条会绿"那条）· 📄 <br>🔵 **（第一条）：`/rag/stream_search` 的帧集【再多一帧 `no_answer`】** —— 答案**以那句拒答语开头**时，在 `[DONE]` 之后、`sources` 之前多发一帧 `{"no_answer": true}`（`:840` 判 · `:841` 发）。<br>🔴 **判据在开头，⛔ 不是"含"**（`startswith`）；**prompt 与判据共用同一个模块级常量 `REFUSAL_SENTENCE`**（`:697`）—— 两处必须一致，而它们分家的表现是**静默的**（prompt 换了措辞 ⇒ 帧永远不发 ⇒ 页面上只是一句普通回答）。<br>⚠️ **⛔ 不提高拒答率**：prompt / 模型 / 检索**一行没动**，补的是**可观测性**。⛔ 也**不是**"加相似度阈值" —— 那条路已由三轮真栈 spike **结构性判死**（同文档邻居问题上量不出来，见 §二/§三）。<br>⚠️ **对老前端天然兼容**：`sse.js` 认不出的帧落到 `'unknown'`，而页面**什么都不做**。<br>⚠️ **非流式那条链（`answer_with_citations.py`）一行没动** —— 它用的是**逐字同一句**，但**没有这个帧**，也**没有界面**；⇒ 🔴 **那句拒答语现在全仓有两份**，将来那边要加同样信号时**必须先收成一份**（§八·2）。<br>🔴 **`/rag/search` 与 `/rag/rewrite_search` 补上会话级 + 全站两道闸**（`unified_search` `:534`/`:538` · `rewrite_search_api` `:480`/`:484`，各加 `thread_id: str = "default"`）—— **改前这两条链零闸**，而它们默认就真调 LLM。⚠️ **记账点不本文件**，在其下游（`rag_pipeline` / `answer_with_citations` / `query_rewriter`）。<br>✅ **`/rag/stream_search` 的流式答案【补上记账】** —— 端点里新增 `_StreamUsageTap`，用骨架本来就逐块调用的 `extract` 钩子累积所有块，收尾出口一次 `record_from_response(purpose="answer_generation")`。⚠️ **改前那句"流式 usage 拿不到"是错的**（见下方「看代码会误判」）—— 真根因是**没人读带 usage 的那一帧**（它 `content=''`，被 `llm_chunk_text` 判空丢掉）。⛔ **取消 / 异常仍不记**（那帧根本不到 · §遗留·2）。<br>✅ **`tags=["模拟类测试"]` 整组【归零】** —— 另 **2 条**（`/rag/async_ask` · `/rag/parallel_ask`）**已删**（纯 mock · 零消费者）。本文件因此 **`@router.` 14 → 12**、**HTTP 12 → 10**。<br>⚠️ ⛔ **本文件现在没有任何"假端点"了** —— 剩下的 10 条 HTTP 条条都动真东西（库里/embedding/共享层）。<br>🔴 **`POST /rag/jwt_ask` 已【删除】** —— 三条理由与做法。本文件因此 **`@router.` 15 → 14**、**HTTP 13 → 12**。<br>🟡 ~~部分可用 —— 有 2 条是"模拟类测试"（原 3 条；`/rag/ask` 已删）~~ ⇒ **已归零**。<br>🔵 **（剩余 4 条链）：`/rag/stream_search` 的 SSE 生成器【改成走共享层】** —— 内联的 `try/except/finally` 整段换成 `sse_response(sse_stream(...))`（`:731`）。<br>· 🔴 **行为必须【逐帧等价】，⛔ 不是"顺手统一"** ⇒ 三处**显式覆盖**骨架默认值：`ensure_ascii=True`（中文仍 `\uXXXX`）· `on_error`（**只有 error 帧、⛔ 不加 `[DONE]`**）· `chunk_delay=0.01`（限速照旧）。<br>· ⭐ **判据 = 既有两份用例"全绿且文件 diff 为空"**（`app/tests/test_agent_sse.py` + `app/tests/test_cancel_propagation.py`）—— ⛔ 没有新加断言 = 重构真的等价。<br>· ⚠️ **上游从同步 `.stream` 改 `astream(messages)`（`:735`）不是的改动**（那是）；只是把它搬进 `lambda: …` 工厂。<br>📄 骨架见 `app/routing/specs/sse.md`<br>✅：`/rag/stream_search` 的**取消传播做完了** —— 上游改 `astream`、`finally` 里 `aclose` 关流、取消时记 `stream_cancelled_total`。⚠️ **"上游真停"仍只有代码内证据**（本机无出账）<br>✅：中断后**那半截答案存进历史**（提问 + 半截 + `INTERRUPTED_SUFFIX` 标记，落 `finally` ——）<br>✅：**三条出口都留痕，且各带一个 `status`** —— `done`（= 完整答案、⛔ 无标记）/ `cancelled` / `error`。🔴 改前 **`except Exception` 那条一个字都不留**（连提问一起丢）⇒ 现在也写了。留痕例程已收进 **`cache.persist_turn`**，本文件只剩**两个调用点**（`_complete` `:715` · `on_incomplete` `:745`）<br>🔴 ~~**未修**：**本文件的 LLM 调用一处都不记账**（`grep -c record_usage app/routing/api_v1_rag.py` ⇒ **0**）⇒ 见下方「做到哪」与 §遗留·2~~ ⇒ **部分修复**：`/rag/search` 与 `/rag/rewrite_search` 两条链**记账点在下游模块**、闸在本文件；⛔ **`/rag/stream_search` 与两条 Agent 路径仍未修** ⇒ 见下方「做到哪」<br>✅：两处 `ChatOpenAI`（**当时** `:578` 流式答案 · `:751` WS agent）接上 `MAX_TOKENS_ANSWER`—— ⚠️ **两处都已不存在**：改走 `llm_factory.make_llm("chat","answer")`（见下一条），`ChatOpenAI` 早已不是本文件的调用形状<br>✅ ：那两处**改走 `llm_factory.make_llm("chat", "answer")`** ⇒ **本文件已不再 import `ChatOpenAI` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_CHAT`**。<br>⚠️ **`get_llm_stream` 的惰性没变**（`make_llm` 自己把 langchain 的 import 关在函数内）· ⚠️ `temperature=0.3` + `streaming=True` 是**本处特有的逐点调参**，仍写在调用点上 |
| **对外提供** | **10 条 HTTP**（文档管理 4 · 检索 **5** · 流式 1 · ~~**模拟 2**~~ ⇒ **0**）· **1 条 WebSocket**<br>⚠️ **计数沿革**：14 →13 —— `/rag/ask` 已删→**12** —— `/rag/jwt_ask` 已删→**10** —— `/rag/async_ask` · `/rag/parallel_ask` 已删→**11** —— **WS `/ws/test` 已删**（§十）。<br>🔴 **顺带更正一个【改前就存在】的错**：本行原写「**15 条 HTTP**」，**而括号里的分项一直加不到 15**（4+6+1+3 = **14**）—— **分项是对的，那个 15 是错的**。判据（可打印）：`grep -c '@router\.' app/routing/api_v1_rag.py` ⇒ **11**（= **10 HTTP + 1 WebSocket**，WS 那条在 `:883`）。📌 **这种"总数与分项对不上"的错，本仓已记过一次**（数出来的数要能对上）。<br>⚠️ **用 `grep -c '@router\.'` 数路由时：⚠️ 墓碑注释里⛔【别】写 `@router.` 字面串** —— 会把它一起数进去（实测：带 `@` ⇒ 14，去掉 ⇒ **12**）。📌 这就是判据纪律第 2 条「**注释里也有同样的串**」。<br>🔴 **又踩了一次**（§十）：删除 `/ws/test` 时墓碑注释写成了 `@router.websocket("/ws/test")` ⇒ 数出 **12**（应 11）。⚠️ **同一个坑、同一份文件、隔一天** —— ⇒ 墓碑一律写成 **`WS /ws/test`** 这种**不含 `@router.` 的形态**（本文件 那条墓碑本来就是这么写的）。 |
| **谁在用** | 全部对外检索入口 |

## ✅ 做了什么

| 组 | 端点 |
|---|---|
| 文档管理 | `/rag/insert` · `/rag/insert_batch` · `/rag/upload_document` · **`DELETE /rag/documents/{doc_id}`** |
| 检索 | `/rag/pg_search` · `/rag/hybrid_search` · `/rag/rerank_search` · `/rag/rewrite_search` · `/rag/search` |
| **流式** | **`/rag/stream_search`**（`stream_search`，`:603`）—— ⚠️ **"全仓唯一 SSE 端点"这句 失效**：Agent 端已有第二条（`POST /agent/langgraph_chat/stream`） |
| WebSocket | `/ws/agent`（⚠️ **要首帧认证**）<br>⚰️ `/ws/test` **已删**（§十）—— 纯回声测试桩 · 消费者 = 0 |

## 🔴 · 多用户隔离（**甲段 + 乙段**）—— ✅ **8 条检索路径全部收口**（⚠️ 现存 **6 条**）

> ⚠️ **后续删了 2 条**：`/rag/ask` 删· `/rag/jwt_ask` 删
> ⇒ **现存的检索路径是 6 条**，**这 6 条全部收口**。
> 下表的「8 条」是**收口当时**的口径，保留以便对照（它确实一度是 8 条）。
> ⚠️ **本节结论 ⛔ 不因删端点而变** —— 删的是**已经收口好的**两条；**没有任何一条"没做"的被删掉冒充"做完了"**。

**「谁能看见谁的文档」已收口。** 做法是**共享层承重**（决策 5）——过滤写在
`db.search_similar` / `bm25_index.bm25_search`，⛔ 不是每个端点各写一遍。
⚠️ **但乙段那两条【不走共享层】**（自己写 SQL）⇒ 改共享层**碰不到它们**，只能各修各的。
⇒ **下面这张表按「是否走共享层」分成两组**，这就是本节的读法。

| 端点（函数名锚点 ⭐ 行号会漂） | 现状 | 怎么过滤的 |
|---|---|---|
| `/rag/pg_search`（`pg_search`） | ✅ **本来就对** | **自己写 SQL**，一直有 `WHERE requested_by = %s` |
| ~~`/rag/ask`~~（`ask_question`） | ⛔ **已【删除】** | 见 —— 它**同样是自己写 SQL**、**同样一直有 `WHERE`**，所以**删它与隔离无关**（隔离账里它从来不欠账）。🔴 本行原先把端点名写成 `WS /ws/agent` —— **错**（`WS /ws/agent` 整条**不碰 `documents`**，且（当时）**无鉴权**、身份写死 `"unknown"` —— ✅ 这两条都已修） |
| `/rag/hybrid_search`（`hybrid_search_api`） | ✅ **甲段已收口** | `hybrid_search(…, user_id=user_name)` → 共享层 |
| `/rag/rerank_search`（`rerank_search_api`） | ✅ **甲段已收口** | `rerank_search(…, user_id=user_name)` → 共享层 |
| `/rag/rewrite_search`（`rewrite_search_api`） | ✅ **甲段已收口** | `hybrid_search_with_rewrite(…, user_id=user_name)` → 共享层 |
| `/rag/search`（`unified_search`） | ✅ **甲段已收口** | `pipeline.search_async(…, user_id=user_name)` → `db.search_similar` |
| ~~`/rag/jwt_ask`~~（`jwt_ask_question`） | ⛔ **已【删除】** | 见 —— 它**同样是自己写 SQL**（乙段给它补过 `WHERE requested_by = %s`），所以**删它与隔离无关**（隔离账里它从来不欠账）。⚠️ **⛔ 别读成"乙段那一下白做了"**：乙段那次收口**是对的、也有效的**；删它是**另一件事**（`LIMIT` 无 `ORDER BY` ⇒ 结果不可复现 + 能力已被 `/rag/pg_search` 覆盖） |
| `/rag/stream_search`（`stream_search`） | 🔴 **改走【与 `/rag/search` 同一条链】**<br>⚠️ 改前是**裸向量**（`search_similar`， 乙段那次只是把**隔离**对齐了，**召回源没对齐**） | 入口 = `_stream_retrieval` ⇒ `create_accurate_norerank_pipeline.search_async(...)`（改写 + 向量 + BM25 + RRF，**不重排**）。<br>判据 ⇒ `app/tests/test_stream_same_chain.py`（**2 条**） |

⚠️ **乙段【没有】动的两件事**（⛔ 别读成"乙段全修好了"）—— **两条后来都作为独立事项结清了**：
* ~~**`/rag/jwt_ask` 拿到 `question` 却不拿它做检索**（无 embedding、无 `ORDER BY`）~~ ⇒ ⛔ **连同端点一起删了** ——
  「**承诺检索**」与「**实际不检索**」的矛盾**不是被"修"好的，是那个矛盾的载体被删掉了**
  （这条端点全仓**无消费者**，删掉它没有任何人少一项能力）。
  ⚠️ **它当时不在隔离收口内** —— 乙段那次**只加 `WHERE`，⛔ 没动检索语义**，这话当时就写明了。
* ~~**`/rag/ask` 的定位**~~ ⇒ ✅ **已删** ——
  它**读真库**却自称「模拟类测试」，`LIMIT` 无 `ORDER BY` ⇒ 结果不可复现；能力被 `/rag/pg_search` 覆盖、全仓无消费者。
  ⚠️ **删它⛔ 不是隔离的事** —— 它一直是按人过滤的（`WHERE requested_by`），**隔离账上它从来不欠**。

⚠️ **甲段 / 乙段都只动「检索」** —— 写入侧（`/rag/insert` · `/rag/insert_batch` · `/rag/upload_document`）
本来就把 `user_name` 写进 `requested_by`，**不属于 fail-open 那一类**。
（`DELETE /rag/documents/{doc_id}` 的**归属校验**是另一件事，记在  §遗留。）

🔴 **检索侧【有意不给】admin 例外**（§七 裁决 2 的文字 已更正）——
已过滤的端点**对 admin 一视同仁**。⚠️ **今天看不出差别**（admin 拥有 100% 语料）。

📌 **判据（可打印）**：
* **用例** —— `POSTGRES_DB=rag_test venv/bin/python -m pytest app/tests/test_isolation.py -q -m needs_db` ⇒ **9 passed**
  （全文件 `18 tests collected` = 离线 **9** + `needs_db` **9**）
* **静态** —— `grep -n 'WHERE requested_by' app/routing/api_v1_rag.py | grep -v '#'` ⇒ **1 行**（`:415`）
  （只剩 `pg_search` —— **自己写 SQL 的读端点就剩这 1 条**；其余 **5** 条走共享层）
  ⚠️ **由 3 变 2**（`/rag/ask` 删）· **由 2 变 1**（`/rag/jwt_ask` 删）。
  ⚠️ **必须带 `| grep -v '#'`** —— 现在有**两行注释**里也含这个串（乙段加的 ·  墓碑加的；
  本仓判据纪律第 2 条：「批量替换后按位置核，⛔ 别只数替换了几处 —— **注释里也有同样的串**」）

## 🔴 · 公共语料库—— 本文件被改了**一处**

`pg_search`（**自己写 SQL** 的那唯一一条读端点）的 SQL 加了 `OR requested_by = PUBLIC_OWNER`。

🔴 **为什么单列一节**：这次改公共语料要动**三处** SQL（`core/db.py` · `rag/bm25_index.py` · **本文件**），
而本文件这一处**最容易漏** —— 前两处都在"检索层"，只有它在"路由层"里自己写了一遍。
⇒ **以后再动"谁能看见什么"，`grep -n 'WHERE requested_by' app/routing/api_v1_rag.py | grep -v '#'` 这一条必须重跑。**

⚠️ **写与删没动**：删除端点的「非 admin 且 `owner != 自己` ⇒ 403」原样保留。

## 🟡 做到哪 / 缺什么

- ✅ ~~**硬门 C（服务端 cancel）没做** —— 只有 `except asyncio.CancelledError`（旧 `:676`），**不关上游 HTTP 流**~~
  ⇒ **已做**：上游 `astream`（现 `:735`）· 关流与计数**搬进
  共享层 `app/routing/sse.py`**（约束①②：同步收尾排在 `await` 前 + `shield` 关上游）—— 本文件这边
  只剩一个 `on_incomplete` 回调（`:745`）。
  ⚠️ **仍未证的是"上游计费真停"** —— 本机没有 DashScope 出账，⛔ 别把"我们关了流"说成"账单停了"
- ⬜ **无停止按钮**（前端不存在）
- ✅ ~~**中断后那半截答案【直接丢】**（§遗留·3 点的名）~~ ⇒ **已改**：
  取消时**存**「提问 + 半截 + 中断标记」，落点由骨架排在 `await` 之前。
  · ⚠️ ** 搬了家**：`_persist_interrupted_turn`（原 `:608`）**已删**，逻辑收进
    **`cache.persist_turn`** —— 它与 5 条 Agent 链**共用同一段**（决策 2）。
  · ✅ ~~**`except Exception` 那条路仍然丢提问**（**有意**， §遗留·1）~~
    ⇒ **已改**：**异常出口也写了**（`status="error"`，同样带中断标记）。
    ⚠️ 这是  §遗留·1 明确留给  的那一件，不是"顺手扩范围"
- ✅ ~~**本文件的 LLM 调用【一处都不记账】**~~ ⇒ **全部关闭**。
  ⚠️ **修复范围要说准**（⛔ 别读成"本文件所有出口都记"）：
  · ✅ **`/rag/search` 与 `/rag/rewrite_search` 有会话级 + 全站两道闸**
    （`unified_search` `:534`/`:538` · `rewrite_search_api` `:480`/`:484`，各带 `thread_id: str = "default"` 形参）；
    **这两条链**的 LLM 调用已记账 —— 但**记账点不在本文件**，在
    `rag_pipeline` / `answer_with_citations` / `query_rewriter`（本文件只把 `user_name` 递进去）；
  · ✅ **`/rag/stream_search` 的答案生成现在也记账** ——
    走本文件的 `_StreamUsageTap`，`purpose="answer_generation"`；
    ⚠️ **只有成功出口**：取消 / 异常**不记**（带 usage 的那一帧根本不到 ·  §遗留·2）；
  · ⛔ **`get_agent_executor` / `agent_websocket` 仍不在本文件范围**（Agent 侧， 管）。
  ⚠️ **本文件里真正的 LLM 调用点**（`grep -n 'astream(\|ainvoke(\|make_llm(' app/routing/api_v1_rag.py`）：
  `stream_search`（`:980`，那行 `get_llm_stream.astream(messages)`）· `get_agent_executor`（`:1230`，被 Agent 端点共用）· `agent_websocket`（`:1322`）。<br>🔴 **再重取一次**（在本文件加了 `_stream_retrieval` 与接线）：**953→980 · 1194→1230 · 1286→1322**；⚠️ 同一轮实测：**改前那三个数（`873`/`932`/`1056`）早就不准了**（该重取成 `953`/`1194`/`1286`）—— 也就是说这张表**在 之前已经漂了约 80 行**。<br>🔴 **又重取一次**（给 `sources` 补 `similarity`、 加 `no_answer`，两刀都整体下移）：**815→873 · 874→932 · 998→1056**。<br>⚠️ **后两个行号 重取过** —— 本文件当天因收口 `calculator` 而位移（`get_agent_executor` 776→**777** · `agent_websocket` 831→**840**）。<br>🔴 **又重取一次**：三个数**全都漂了**（735→**765** · 777→**822** · 840→**946**）—— 漂因是 // 几轮在本文件里加/删/改注释。⛔ **不是删 `/ws/test` 引起的**（那条在 `:968` 之后，改不到 946）。<br>🔴 **再重取**（在本文件加了 `_StreamUsageTap` 与接线，整体下移）：**765→815 · 822→874 · 946→998**。⇒ 判据仍是本行开头那条 `grep -n`，**别凭记忆补** —— 这份 spec 里**每一次行号都是重取的**，正因为凭记忆补过一次就错过了。
  🔴 **更正（乙段顺带核出）**：本行原先列的四条里有**两条是错的** ——
  · ~~**`/rag/jwt_ask` 一处 LLM 都不调**（`jwt_ask_question` 整条是 `SELECT content … LIMIT` 然后返回）~~
    —— ⛔ **该端点 已整体删除**；这里保留的是**当时核出的事实**（它现在不存在了）；
  · **`/rag/search?generate_answer=true` 的 LLM 调用不在本文件** —— 它在 `rag_pipeline` 里
    （本文件只 `pipeline.search_async(…)`）⇒ **那条账要记到 pipeline 头上，不是这里**。
  真库佐证（改动**前**）：`token_usage_logs` 里非 embedding 行**全库只有 6 行**，全是 的 agent graph 运行。
  ⚠️ **取消场景补不了** —— 但**理由不是"没开 `stream_usage`"**（⚠️ 更正：那是错的，
    见下方「看代码会误判」）；真理由 = **usage 只在最后一帧回来，提前 `aclose()` ⇒ 那帧根本不到**
    ⇒ 硬补只能估算 = 往账本写假数。**范围就是成功路径**（§遗留·2 ·  §五）
- 🔵 **（契约 A / B）：`/rag/stream_search` 的「帧集」变了**（为对话页而改）：
  · **`sources` 帧每条补 `index` 与 `content`（全文）** —— 契约 A（`:744`）。
    `index` 与 prompt 里的 `[文档{i}]` **必须来自同一个 `i`**；`content` 是**追加**
    （老前端用的 `content_preview` 仍在，⛔ 不是替换）。非流式那份（`answer_with_citations.py`）
    **同步改成了同构** —— 但那是**手工对齐**，⛔ 没有任何东西钉住两份一致。
  · **末尾多一帧 `usage`** —— 契约 B（`:819`）：`{model, prompt_tokens, completion_tokens, cost_usd}`，
    由 `_StreamUsageTap` 收尾调 `token_tracker.usage_summary` 产出。
    🔴 **取消 / 出错时【没有】这一帧** —— 见「看代码会误判」那两行。
  · 🔵 **（契约 F）：再多一帧 `no_answer`** —— 帧序变成
    **`内容… → [DONE] → no_answer（仅拒答时）→ sources → usage`**。⚠️ **条件帧**：正常回答**不发**
    （所以**数帧数不能当判据** —— 见「看代码会误判」那行）。判据：`-k no_answer` **4 例**（含"句中出现不算"与帧序）。
  · 📌 判据：`app/tests/test_frontend_contract.py`（**契约 A/B 7 例** +  的 similarity +  契约 E 的 scope 3 例
    +  **契约 F 4 例** ⇒ 全文件 **17 passed**，含**帧序**与"没记账就不出帧"）
- 🟡 ~~零测试覆盖本文件（`docs/说明/测试.md` §六）~~ ⇒ **有了第一条**：
  `app/tests/test_cancel_propagation.py`（**22 例**；其中 **RAG 15 · Agent 7**）覆盖 **`/rag/stream_search`
  的取消路径与异常路径**（关流  + 半截答案  + 三条出口各写什么 `status`）。
  ⚠️ **覆盖仍偏在"非正常出口"上**：**检索 / 引用（`sources` 帧）** 只有零散几条；正常路径的**历史落库**
  现在有了（`test_rag_full_answer_is_saved_without_interrupt_marker`），但**检索语义本身仍然零覆盖**
- ✅ ~~**隔离：4 条收口 / 2 条没做**~~ ⇒ **（乙段）后：8 条检索路径全部收口**（甲段 + 乙段）。
  ⚠️ **更正**：那 8 条里**已删 2 条**（`/rag/ask`；`/rag/jwt_ask`）
  ⇒ **现存 6 条，全部收口**（结论不变：删掉的这两条本来就是收口好的，⛔ 不是"没做的被删掉"）。
  `app/tests/test_isolation.py` **18 条用例守着**（离线 **9** · `needs_db` **9**）。
  ⚠️ **时是 19 条**（离线 9 · `needs_db` 10）—— 随 `/rag/jwt_ask` 删除少了那 1 条
  （`test_jwt_ask_endpoint_does_not_leak_across_users`：端点不存在了，用例没有可守的对象）。
  已做过证伪：**甲段**拿掉共享层 `WHERE` ⇒ **4 条变红**；**乙段**分别退回那两条的修改 ⇒ **各恰好 1 条红**。
  ⚠️ **乙段那两条不走共享层**（自己写 SQL）⇒ 它们的过滤**不在** `db.py` / `bm25_index.py` 里 ——
  **改共享层碰不到它们**，这是本节最该记住的一条。
  ⚠️ ~~「收口后召回会降」~~ —— **更正：这句过头了**。非 admin 用户在那 6 条已收口的路径上
  **本来就只有 0 篇自己的文档** ⇒ 乙段是**消除不一致**，⛔ 不是新加一道限制。
  ✅ ~~**仍未修（且都不是隔离问题）**：`jwt_ask` 的「拿到 `question` 却不拿它做检索」~~ ⇒ **端点整体删除** ⇒ 该项**已消账**。
  ⚠️ **说清楚是哪种消账**：那是**把矛盾体删掉了**，⛔ **不是"把它修成会检索了"** —— 见上方专节。
  ✅ **`/rag/ask` 的定位**已在 由「已裁待删」变为**已删** ⇒ 该项已消账。
- ✅ **（§六·1）：`/rag/stream_search` 也【建轨迹】了**。
  改前全仓只有 **2 处** `start_trace`（**都在 Agent 链**）⇒ 演示路径（`/chat` 走的就是本端点）
  **从不建轨迹** ⇒ `/trace` 上半页**必然是空的**（⚠️ 不是坏，是**从没建过**）。
  · **两处**：端点开头 `start_trace(user_name, thread_id, req.question)`（**排在两道闸之前**，
    与 `api_v1_agent.py:1646` 那句「超限被拒时，追踪里仍留得下这次尝试的痕迹」**同序**）·
    `_complete` 里 `finish_trace(...)`（**任何 `yield` 之前**，理由同  的记账）。
  · ⚠️ **取消 / 出错两条出口【不收尾】** —— 与 Agent 链同款（追踪里留一条"开了没结束"）。
  · ⚠️ `total_tokens` / `total_cost` **不传** —— 与另两个调用点一致（`trace.js` 明写那两格**恒为 0**）。
  📌 判据：`app/tests/test_rag_trace_wiring.py`（**3 条**：建了 / 收尾了 / 键写对了 + 被拒也留痕）·
  `grep -rn '^[[:space:]]*start_trace(' app/ --include='*.py' | grep -v '^app/test_'` ⇒ **现跑**

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「10 条端点都是正经功能」 | ✅ **【成立】** —— `tags=["模拟类测试"]` **整组归零**，剩下的**条条动真东西**。<br>🔴 **在此之前【不成立】，而且那是本仓栽过的一个坑**：该组一度有 **4 条**，其中 **2 条**（`/rag/async_ask` · `/rag/parallel_ask`）**返回假文档**（`asyncio.sleep(2)` 后 3 条硬编码串），另 **2 条**（`/rag/ask` · `/rag/jwt_ask`）**自述"模拟类"却读真库**、`LIMIT` 无 `ORDER BY` ⇒ 结果不可复现。<br>⚠️ **删除次序**：`/rag/ask` · `/rag/jwt_ask` · 余下 2 条。<br>📌 **教训（⛔ 不随删改而失效）**：`tags` 是**自述**，⛔ 别拿它当"这条是桩"的判据 —— `/rag/ask` 就是自述"模拟类"却查真库的反例 |
| 🔴🔴 **「响应体里有 `"requested_by": user_name` ⇒ 这个端点按人筛过了」** | ⛔ **那只是个【回显】** —— 它告诉调用方"你是谁"，**与 SQL 里有没有 `WHERE` 毫无关系**。<br>📌 **两条可打印的计数**（删 `/rag/ask`与 `/rag/jwt_ask`后实测）：`grep -c '"requested_by"' app/routing/api_v1_rag.py` ⇒ **10**；而 `grep -c 'WHERE requested_by' app/routing/api_v1_rag.py` ⇒ **3** —— 🔴 **但 3 里有 2 处是【注释】**（乙段加的 · 墓碑加的）⇒ **真正在 SQL 里的只有 1 处**(`:415`)。<br>⚠️ 这两组数**都会随端点增删而变**（删 `/rag/ask` 前是 **12 / 5 / 3 处**； 后是 **11 / 4 / 2**）⇒ **判据要用上面那条 `\| grep -v '#'` 的写法现算**，⛔ 别照抄本文里的数字。<br>⇒ ⚠️ **所以这条判据必须写成 `grep -n 'WHERE requested_by' app/routing/api_v1_rag.py \| grep -v '#'`** —— 本仓判据纪律第 2 条：「批量替换后按位置核，⛔ 别只数替换了几处 —— **注释里也有同样的串**」。<br>⇒ 带引号的 **10** 处**没有一处在 SQL 里过滤**（实测 0），其中 **4 处在【写入】函数里**（`:169` `:269` `:333` `:363`）—— 与读侧过滤**根本不搭界**。<br>⚠️ **乙段后，8 条读端点【恰好】都是真过滤了**（⚠️ 该口径当时**含 `/rag/ask` 与 `/rag/jwt_ask`**；两条都已删 ⇒ 现在是 **6 条**，见本节开头那段后续说明）—— 但「回显」与「过滤」**对得上纯属巧合**，⛔ **不是因果关系**；那个巧合正是这条误读危险的地方（下一条新端点照抄回显就会再犯）。<br>⇒ **本仓最容易踩的误读。** 判据只有一条：**去那条端点的 SQL 里找 `WHERE requested_by`** —— ⛔ 别看响应字段（§1.2） |
| 🔴 **「修过隔离了 ⇒ 检索都隔离了」** | ✅ **（乙段）后【成立】** —— 8 条检索路径全部收口（⚠️ **现存 6 条，见本节开头**）。<br>⚠️ **但这句话在 当天曾经是错的** —— 「甲段」只收口 **4 条**，`/rag/jwt_ask` 与 `/rag/stream_search` 当时**照样能读到别人的文档**（当天实测：isolation_b 的 JWT 拿回 20 篇 admin 文档；stream_search 的 prompt 里**逐字**出现别人的文档）。<br>⇒ **教训**：「修过」≠「都修好」 —— ⛔ **说这句话必须带【哪个阶段】**；判据是 `app/tests/test_isolation.py` **覆盖到哪几条**，⛔ 不是"今天有人动过这个模块" |
| 🔴 **「`user_id` 是本文件在过滤」** | ⛔ **不是** —— **甲段那 4 条**只是**把 `user_name` 往共享层传**（`hybrid_search_api` · `rerank_search_api` · `rewrite_search_api` · `unified_search`），过滤**发生在** `db.search_similar` / `bm25_index.bm25_search`（决策 5：**共享层承重**）。<br>⇒ **要改过滤改那两个；要改"谁能调"改本文件的调用点。**<br>🔴 **更正**：本行原先写「**本文件一行 SQL 都没改**」—— **乙段起不成立**：`jwt_ask` 补了 `WHERE`（**当时** `:557`；⛔ **该端点 已整体删除**）· `stream_search` 的裸 SQL **被删掉**改调共享层（`:637`）。<br>⇒ 正确说法（**按 现状**）：**走共享层的那 5 条不碰 SQL；自己写 SQL 的读端点只剩 `pg_search` 一条**（时是 3 条：`/rag/ask` 已删；`/rag/jwt_ask` 已删）—— **它改的就是本文件**。 |
| 「`/rag/stream_search` 带真中断」 | ✅ **【成立】** —— 客户端断开 ⇒ 取消传给生成器 ⇒ 关上游流。⚠️ 之前写这句是**错的**（`CLAUDE.md`/`README` 都写过）。<br>⚠️ **但"真中断"≠"账单停了"** —— 本机看不到上游出账（§遗留·2）。<br>🔴 **更正**：这句**只在"早切"（还没吐字就断）时成立** —— 见下一行 |
| 🔴🔴 **「`finally` 里 `await stream.aclose` 就等于"把上游关了"」** | ⛔ **不够** —— **二次投递的取消**会在下一个真实挂起点重投：`aclose` 一挂起 ⇒ 抛 `CancelledError` ⇒ **`finally` 剩余部分整体作废**。<br>⚠️ **真服务实测**：「晚切」时计数 `2.0→2.0` ❌、**无 `[cancel]` 日志** ❌、半截 **0 条** ❌ —— 而**单测当时 13 条全绿**（假流的 `aclose` 不抛）。<br>⚠️ **早切测不出来**：生成器**还没被推进过** ⇒ `aclose` 不必真收尾 ⇒ **不挂起 ⇒ 打不断**。**只有「用户已经看到字再点停止」才露出来**（而那才是主场景）。<br>✅ **两条一起**（缺一不可）：① **同步**收尾（计数/日志/落盘）提到**任何 `await` 之前** · ② 关流包 `anyio.CancelScope(shield=True)`。<br>🔵 **这两条【不在本文件里】了** —— 它们搬进了共享层 `app/routing/sse.py` 的 `sse_stream`。<br>📌 判据（可打印）：`app/tests/test_cancel_propagation.py`；两条修法**各有一条用例独立钉住**（`…survives_interrupted_aclose` 钉 shield · `…lands_even_when_aclose_itself_fails` 钉顺序 —— 实测把关流挪回前面 ⇒ **只有后者变红**）<br>📄 · 复盘 |
| 🔴 **「`stream_search` 里有 `try/except/finally`」** | ⛔ **没有了** —— 整段换成 `sse_response(sse_stream(...))`（`:731`），<br>骨架在 **`app/routing/sse.py`**（`:731` 就是 `sse_response(...)` 那一行）。⚠️ **但本端点【不是】用骨架默认值** —— 它显式覆盖了三处（`ensure_ascii=True` · `on_error` **只发 error 帧** · `chunk_delay=0.01`），<br>⇒ ⛔ **别按"骨架的默认行为"去读这个端点**，也别把 `/agent/*` 四条链的形状套过来（它们三处都是默认值）。<br>🔴 **最反直觉的一条**：RAG 的帧序是 `内容 → [DONE] → no_answer（仅拒答时）→ sources → usage`（`[DONE]` 在 `sources` / `usage` **之前**；`usage` 那一帧是 加的，`no_answer` 也是），而骨架**一个字都不补尾巴**（`on_complete` 整段自管）。<br>⚠️ **`no_answer` 是【条件帧】** —— ⛔ **别拿帧数当判据**（正常回答那一轮比拒答那轮少一帧）。 |
| 🔴 **「自己去 `request.is_disconnected()` 轮询才知道客户端断了」** | ⛔ **不用，那是框架给的** —— uvicorn 报 `spec_version 2.3` ⇒ Starlette 已监听 `http.disconnect` 并**取消生成器**。<br>⇒ 真正的缺口只有「**停下并关掉上游**」这一件。**自己加轮询 = 多余，且会掩盖真缺口** |
| 🔴 **「中间件日志里那个秒数 = 生成耗时」** | ⛔ **不是** —— 它记到**响应开始返回**为止。实测：`(0.019s)` 的那条客户端收了 **27KB**、`(0.004s)` 的那条 **3 秒后**才 cancel。<br>⇒ ⛔ **别拿它当"生成提前停了"的证据**（第一版就这么误读过 ·  §真服务实测） |
| 🔴 **「换两个字问同一个问题就能测取消」** | ⛔ **会被语义缓存吃掉** —— 问句只差"基线/切断"⇒ 当成同一个问题、`0.006s` 返回全量 ⇒ **根本没在生成，取消测不出来**。<br>⇒ 测取消**必须换语义上不同的问句** |
| 🔴 **「历史里那条助手消息是完整回答」** | ⛔ **可能是半截**（起）—— 被中断的那轮存进去的答案**尾部带 `INTERRUPTED_SUFFIX`**（"…（本次回答被中断，以上为已生成部分）"）。<br>⇒ **读历史的人（人 / 模型 / 另一个脚本）必须看这个尾巴**，⛔ 别把半截当结论。⚠️ 这个尾巴是**故意**进 prompt 的：不标 ⇒ 模型会把断掉的话当成自己说完了。<br>🔵 **还有机器可读的那一份**：每条都是 `{role, content, status}`，`status ∈ done / cancelled / error`。<br>⚠️ **`cancelled` 与 `error` 的中断标记串【是同一个】**（`INTERRUPTED_SUFFIX`，有意不另造）⇒ **要区分只有 `status`**；<br>🔴 **老条目（之前写的）没有 `status`** ⇒ `get_chat_history` 读的时候**补成 `done`**（按"完整答案"处理，因为那时根本不存半截） |
| 🔴 **「取消后用户那问句也没了，是设计如此」** | ⛔ **不是设计，是碰巧** —— 改动前 `append_chat_history(user, …)` 与答案写在同一段收尾代码里，取消先 `raise` ⇒ **两个一起丢**。<br>⇒ **已让取消路径成对写**。<br>✅：**`except Exception` 那条也成对写了**（`status="error"`）—— 改前它**连提问一起丢**。⚠️ **三条出口现在都留痕，且都成对**；`persist_turn` 里那条「**空答案 ⇒ 一条都不写**」是唯一的"不写"分支（防空的助手消息污染下一轮 prompt） |
| 🔴 **「`/rag/upload_document` 里没看到 `invalidate_bm25_cache` ⇒ 它不清 BM25 缓存」** | ⛔ **这是误读** —— 不变量**下沉到了写操作自己那层**：它的插入走 `db.insert_document`（`:321`），而**那个 helper 自己清缓存**。<br>⚠️ **改前它确实不清**（待办）：上传的新文档在本进程的 BM25 召回里"不存在"，直到别的写路径顺手清了或重启 —— 而**同一个坑 修过一次、只修给了 `/rag/insert`**。<br>⇒ **判据不是"这个函数里有没有那句话"，是"它写库的那一层有没有"** —— 守卫 `app/tests/test_bm25_cache_invalidation_wiring.py` 正是**从 AST 推导写路径**来判这件事的。 |
| 🔴 **`upload_document` 现在有一个【可选】`doc_type` 形参** | ⛔ **别以为"上传就自动分档"** —— **不给就还是按扩展名猜**（`.pdf`→`legal`，其余→`technical`）。<br>它是**给我们的灌库脚本用的**（乙的四类语料各自显式指定）；**访客走的那条路一个字没变**（作者裁的就是这个默认值）。<br>⚠️ **给了未知档位 ⇒ 400**（⛔ 不是静默回落 `default` —— 那正是 `chunker` 模块内部的行为，见 `app/rag/specs/chunker.md` §⚠️ 第 2 条）。<br>📌 判据：`grep -n 'doc_type' app/routing/api_v1_rag.py` · 用例 `app/tests/test_rag_upload_doc_type.py`（10 条）。 |
| 🔴🔴 **「流式拿不到 `usage_metadata`，除非先开 `stream_usage`」** | ⛔ **错的，而且这条错话在本仓活了两天、进了三份文档**（§二 · 复盘 `-未核的推断被当成前提写进文档.md`）。<br>**实测**：本仓 provider 把 `usage_metadata` 挂在**最后一帧**（`content=''`）上，**默认 `stream_usage=False` 也拿得到**；且开与不开，按 `llm_chunk_text` 口径过滤后**帧序列逐帧相同**。<br>📌 **判据（可打印，别信这句话，去跑）**：<br>`venv/bin/python "qingyun-rag-agent-TODO待办/探针-流式与记账.py"` ⇒ 默认参数下 `聚合 usage_metadata = {input_tokens …}` 非空。<br>⇒ **真根因是"没人读那一帧"**：`llm_chunk_text` 返回 `chunk.content or None`，`''` 被判空丢掉。 |
| 🔴 **「`stream_search` 传 `extract=llm_chunk_text`，所以它只负责发帧」** | ⛔ **不是** —— 现在传的是 **`tap.extract`**（`_StreamUsageTap` 的方法）：它**返回的仍是 `llm_chunk_text(chunk)`**（发帧口径一字未动），但**顺手把每一块 `+` 进聚合**（`self._agg`），收尾时交给 `record_from_response`。<br>⚠️ **为什么必须塞在 `extract` 里**：骨架唯一能看到**每一块**的钩子就是它（`sse.py`：`text = extract(item) …` **排在 `if not text: continue` 之前**）；骨架自己攒的 `collected` 只收**过滤后**的文本 ⇒ 那份里永远没有 usage。<br>📌 判据：`grep -n 'extract=' app/routing/api_v1_rag.py` ⇒ **`tap.extract`**，⛔ 不是 `llm_chunk_text`。 |
| 🔴 **「`stream_search` 不建轨迹 ⇒ `/trace` 上半页永远是空的」** | ⛔ **不成立**。改前全仓只有 2 处 `start_trace`、**都在 Agent 链** ⇒ 演示路径（`/chat` 走的就是本端点）**从没建过轨迹**（当时 §62 的原话：「演示路径上，上半页**必然**是空的」）。<br>现在本端点在**两道闸之前** `start_trace(...)`、在 `_complete`（**任何 `yield` 之前**）`finish_trace(...)`。<br>🔴 **顺序是有意的**：排在闸之前 ⇒ **被拦下的请求也留痕**（排查时最想看到的就是那一类）。⛔ 别"顺手"挪到闸后面 —— `app/tests/test_rag_trace_wiring.py::test_rejected_request_still_leaves_a_trace` 钉的就是这个顺序。<br>⚠️ **取消 / 出错两条出口【不收尾】**；`total_tokens` / `total_cost` 也**不传**（与另两个调用点一致）。<br>📌 判据（可打印）：`grep -rn '^[[:space:]]*start_trace(' app/ --include='*.py' \| grep -v '^app/test_'` ⇒ **现跑**（⛔ 别抄数） |
| ⚠️ **「把 `api_v1_rag.py` 加进 `test_rag_billing_wiring._RAG_LLM_FILES` 就能守住了」** | ⛔ **加不进去**：那条 AST 守卫的 `_called_names(fn)` **刻意不下钻嵌套函数**，而本端点的记账必然在 `_complete`（嵌套 async gen）里 ⇒ 加进去**恒红**。⇒ 该端点的守卫是**行为判据**（`test_rag_billing_wiring.py` 末尾 5 条，驱动真端点 + 真骨架）—— 对这条链它**严格更强**。 |
| 🔴🔴 **「看到帧序 `内容 → [DONE]` 就到头了」** | ⛔ **`[DONE]` 后面还有帧** —— 完整帧序是 **`内容… → [DONE] → no_answer（仅拒答时）→ sources → usage`**（`:831` 先发 `DONE_FRAME`，`:841` 才发 `no_answer`，`:851` 才发 `sources`）。<br>⇒ 见 `[DONE]` 就 `return` / 断流 = **拒答标志、引用、费用整段丢**，而**页面上不报任何错**（表现就是"这次没有引用、也没有费用行"）。<br>⚠️ **对【所有】消费者都成立**，⛔ 不只是对话页 —— 任何"读到 `[DONE]` 就收工"的客户端都会静默丢这几段。 |
| 🔴 **「没有 `usage` 帧 ⇒ 这轮不要钱」** | ⛔ **另一种可能：通道提前断了。** `usage` 只在**最后一帧**回来 ⇒ **取消 / 出错时那帧根本到不了**。<br>⚠️ 所以"没有费用行"是**两个原因共用的一个表现**，⛔ 别合并读：**被中断**（真没计费 ·  §五）vs **半路断了而钱已经花了**（账在库里、只是没报给前端）。<br>📌 区分靠**出口**（`aborted` / `error` / `done`），⛔ 不靠"有没有 usage"。 |
| 🔴🔴 **「检索相似度低 ⇒ 这轮该拒答 ⇒ 加条阈值就完事了」** | ⛔ **这条路已被三轮真栈 spike 结构性判死**（§二/§三）—— **不是"阈值没定好"，是量错了东西**。<br>实测：`公司2025Q4净利润`（库里没有 ⇒ 该拒）**0.766** vs `紫色长颈鹿…`（库里逐字有 ⇒ 该答）**0.794** —— **差 0.028**；而 `净利润` 与 `营收` **打进的是同一篇** `financial_report`、**相似度几乎一样**，**一个该答一个该拒**。<br>⇒ **相似度量的是「问题 ↔ 文档」的距离，量不了「事实 ∈ 文档」。** ⚠️ 而"同文档邻居问题"正是**最该被拦住**的场合（用户问的是一篇他刚上传的文档的细节，模型手上全是"很像"的材料）。<br>📌 **方法论（⛔ 别只跑第 1 轮就下结论）**：只问不相干的题材，相似度天然塌到 0.09、断层"干净得可疑"；**把题目换成"同文档邻居"才戳破假象**。 |
| 🔴 **「既然后端已经判了，`answer_with_citations.py` 那份顺手改一下就好」** | ⛔ **那份用的是【逐字同一句】拒答语，但它是【另一份】**（`answer_with_citations.py:17` 的 prompt 里写死的）—— 与 `api_v1_rag.py` 的 `REFUSAL_SENTENCE` **没有结构关系**。<br>⚠️ **今天无害**（非流式那条链没有界面、也没有 `no_answer` 帧，没人消费它）；🔴 **但将来那边要加同样信号时，必须先把它收成一份**，⛔ 别照抄常量过去。 |
| 🔴 **「`sources[i].id` 就是那个引用编号」** | ⛔ **不是** —— **`id` 是数据库主键**（`documents.id`），引用编号是 **`index`**（1 起，与 prompt 里的 `[文档{i}]` **同源**， 契约 A）。<br>⚠️ **按数组下标取也一样错**（数组顺序是第三个东西）。⇒ 取错**不报任何错**，只是**点开的是另一篇文档**。判据 ⇒ `app/static/js/sse.test.js` 的乱序用例。 |
| 「`/rag/search` 是纯检索」 | 🟡 **它能生成答案** —— 传 `generate_answer: true` 即可（**默认 `False`**，`app/routing/schemas.py:14`） |
| 「检索都走 `rag_pipeline`」 | ✅ **成立** —— `/rag/stream_search` 现在走 `create_accurate_norerank_pipeline`（经 `_stream_retrieval`），与 `/rag/search` 的默认模式**同一条链**。<br>🔴 **改前不成立**，而且这是本端点最容易被读错的**历史**：乙段把它的**隔离**对齐了（改调共享层 `search_similar`），**召回源却一直没对齐** —— 它只有**纯向量**那一档，不经过 `hybrid_search` / **BM25** / RRF ⇒ 同一句话，两边**不是同一批文档**。<br>⚠️ **别把这两件事混起来**：**隔离对齐** 与 **召回对齐** 是两根轴，这条端点上它们**先后差了 14 天**。<br>📌 判据 ⇒ `app/tests/test_stream_same_chain.py`（第 2 条专门钉"这条路上真跑过 BM25"—— 只看第 1 条的话，**两边一起退化**时它照样绿） |
| 🔴 **「两种熔断都是 429 + `QUOTA_EXCEEDED`，靠 `code` 就能分开」** | ⛔ **分不开** —— `/rag/stream_search` 上**并列两道闸**（会话级 `:711` · 全站级 `:718`），两者 `ErrorCode` **完全一样**。而**恢复条件不同**（全站级只能等跨天；会话级**开新会话立刻能继续**）⇒ **给这两处补了 `scope`**（`"session"` / `"global"`）。<br>⚠️ **只有这两处有** —— 本文件别处（`/rag/search` · `/rag/rewrite_search` · `/ws/agent` 那几条）的 429 **仍然没有** `scope`，前端据此画 `—`。⛔ 那**不是** bug（bounded），但**别读成"scope 全仓都有"**。 |
| 🔴 **「本文件的端点都接了会话上限」** | ⛔ **不是** —— ** 4 条**：`/rag/stream_search` · `/ws/agent`+ **`/rag/search` · `/rag/rewrite_search`**（会话级**且**全站）。<br>⚠️ **新增的两条是"改前完全没有闸"** —— 它们跑的却是**每次都花 LLM 钱**的改写/生成链（§一）。<br>**原先**这里要解释「`/rag/async_ask` · `/rag/parallel_ask` **故意不接**（它们不调 LLM）」—— 🔴 **这两条端点已删，那句话不需要了**。<br>⚠️ **计数沿革**：4 条 →3 条 [`/rag/ask` 删 · ] →2 条 [`/rag/jwt_ask` 删 · ] →**0 条** [余下 2 条删 · ] →**4 条** [ 新增 2 条]。<br>🔴 **反向守卫随之删除**：`app/tests/test_session_budget_wiring.py` 与 `app/tests/test_breaker_wiring.py` 各有一张"**不许接**"的豁免清单，**清单删空后 `for` 体一次都不跑 = 恒绿假通过** ⇒ 两段守卫**已删除**，保护**收敛到** `app/tests/test_removed_endpoints.py`（判据 = **404**）。⚠️ **代价**：将来再加"不花钱"的端点，**没有测试会自动拉红**（详见两文件的模块注释与）。<br>⇒ ⚠️ **新增的守卫刻意用"点名"而不是"扫描"**：`app/tests/test_rag_billing_wiring.py::test_rag_endpoints_gate_both_session_and_global` **参数化点名** `unified_search` / `rewrite_search_api` 两个函数，⛔ **不是"扫一遍找没接闸的"**。<br>⛔ **两者的区别是致命的**：扫描式守卫要先"认出哪些端点该接闸"——**认不出的它一律放过**，于是漏掉的恰恰不被报（的形状盲区）。<br>⚠️ **代价**：点名式**不会**自动发现"将来新增的端点有没有接闸" ⇒ 那要靠**改本文件时人回头看这一行**（已在此写明）。 |
| ⚠️ **「`/rag/stream_search` 一直有 `thread_id`」** | 🔴 **才补的**（query 参数）。`QuestionRequest` **没有**这个字段 ⇒ 它**不在 body 里** |
| 🔴 **「`/ws/agent` 的额度是按人算的」** | ⛔ **按连接算** —— 会话 id 仍是**每连接生成的 uuid**（`thread_id = f"ws-{uuid4}"`）⇒ **断开重连 = 换一个新桶**。<br>✅ **不再记成 `"unknown"` 了** —— 整条链现在要过**首帧认证**（`deps.require_ws_user`），`user_name` 是**认证出来的真人**，账也记在他头上。<br>⚠️ **但"按连接算"没变**：WS 没有客户端传上来的会话 id ⇒ 重连换桶。**这是有意留下的口子**（遗留·2）——全局日级熔断仍罩着，不会因此失控。<br>🔴 **判据怎么来的**（别信这句话，去跑）： ⇒ 无鉴权路由**只剩 `/api/v1/` 一条**。<br>⚠️ **`MIDDLEWARE_EXEMPT_PATHS` 与这条无关**：那两个中间件是 `BaseHTTPMiddleware`，**只看 `scope["type"]=="http"`** ⇒ **永远看不到 WebSocket**；WS 的鉴权只能挂在**路由自己的依赖**上。 |
| 🔴🔴 **「乙段之后，单测 patch `api_v1_rag.get_db` 就够短路了」** | ⛔ **不够，而且本机看不出来** —— 乙段让 `stream_search` **改调共享层** `db.search_similar` ⇒ **`get_db` 的解析位置从 `api_v1_rag` 的模块全局搬到了 `db.py` 的模块全局**。<br>⇒ 只 patch `rag_mod.get_db` 的测试**够不着真实连接点**，会**真去连库**。<br>🔴 **本机为什么看不见**：本机 Postgres 真开着 ⇒ 连上、`fetchall` 回 `[]` —— **与假连接的返回值恰好一样** ⇒ **全绿**。**CI 没有 Postgres ⇒ `Connection refused`**（实测 12 条红，``）。<br>⇒ **判据**：`grep -rn 'setattr(.*get_db' app/test_*.py` —— 逐条看它 patch 的是**哪个模块的** `get_db`；<br>⚠️ **改了任何函数的【依赖来源】（换模块调 / 走共享层 / 抽公共层）⇒ 必须回头过一遍这个 grep**。<br>📄 （**教训是"入口没指向 "**） |
| 🔴🔴 **「本文件的 tools 跟别处一样，`calculator` 早就收口过了」** | ⛔ **改前不成立** —— `get_agent_executor` 里**本地又手抄了一套 tools**（**第 6 份拷贝**），其中 `calculator` **一直写的是 `eval`**（`2c1a922`），<br>而 `/ws/agent` **整条没有鉴权** ⇒ **匿名可达的任意代码执行**。⚠️ **两道 AST 守卫都看不见它** —— 旧判据只认「`ast.Call` 的 `func` 是裸名 `eval`」，而本行是 `asyncio.to_thread(eval, expression)`（`eval` 是**实参**）。<br>✅ **已收口**（换 `safe_math.calculate`）+ **守卫判据改成只认名字**（与调用形状无关）。<br>⚠️ **同批改了 `search`**：`DuckDuckGoSearchRun` → `search_tools.web_search`（`duckduckgo.com` 本机不通）。<br>✅ **鉴权补上了** —— 整条链要过首帧认证（`require_ws_user`），**匿名已连不进来**。<br>🔴 **⛔ 别读成"这处 `calculator` 以后安全了"**：`expression` 仍是 **LLM 生成**的，而 LLM 的上下文含**用户提问 / 搜索结果** ⇒ **间接提示注入面依旧存在**。<br>⚠️ **攻击者换人了而已**：改前是路人，现在是**持合法凭据的用户**。⇒ 三道闸（`safe_math`）**一条都不能撤**。<br>🔴 **那套手抄的 tools【整个删了】** —— `get_agent_executor` 里不再本地定义三个工具，改为 **`_async_shell` 包共享工具**（`name`/`description`/`args_schema` 全部取自 `mcp_server.TOOLS`）。<br>⚠️ **【工具名变更】`search` → `web_search`** —— 改前那个 `search` 在 `SENSITIVE_TOOLS` 白名单里**根本对不上**。<br>🔴 **⛔ 外壳必须保持 `async`**：共享工具是**同步**的，直接替换会**在事件循环里同步跑 20 秒的网络调用**（`to_thread` 是有意的）。判据：`_async_shell`。 |

> 🔴 **补（第二批 + B 段 · 本文件当天有三处改动）**：
> · **`upload_document` 加了两道上限**（§二·4）：
>   ① **单文件字节上限**（`config.MAX_UPLOAD_BYTES` = 48 KB）—— 🔴 **必须卡在 `await file.read()` 【之前】**，
>      改前那句是 `tmp.write(await file.read())`（**整份进内存**），而 本服务容器 **8 GiB / `Swap=0`** ⇒ **OOM 直杀**。
>      现在是**边读边计、一超就停**。
>   ② **单次块数上限**（`config.MAX_INGEST_CHUNKS` = 200）—— 与字节是**两个量纲**：字节管**花多少钱**，
>      块数管**耗时**（决定调多少次 embedding）。⚠️ **必须在入库循环【之前】判** ——
>      "跑到一半才超时"比"当场拒掉"糟得多。
>   两者都用 `PARAM_INVALID`(**422**) —— 与同函数那条「格式不支持」**同级**（⛔ 不新增错误码）。
> · **临时文件挪进 `try/finally`** —— 改前那句 `os.unlink` 在**函数末尾** ⇒ 中途抛异常（解析失败 / embedding 失败 /
>   上面那两条拒绝）都**执行不到它** ⇒ `/tmp` 攒垃圾。
> · **`pg_search` 加了 SQLite 支**（B 段：那个端点**自己写了一条 SQL**，而 SQLite **没有 `<=>`**）
>   ⇒ 走 `core.db.search_similar_sqlite()`。⚠️ **PG 那一支的 SQL 一个字符没动** --
>   守卫 `app/tests/test_public_corpus.py::test_pg_search_endpoint_mentions_public_owner` 钉着
>   「**本函数源码里带着 `PUBLIC_OWNER`**」。
> 📌 判据 ⇒ `app/tests/test_upload_limits.py`（4 条）· `app/tests/test_db_sqlite_vector_search.py`

## 关联

**`app/tools/specs/safe_math.md`**（收口清单 **5 → 6 处**）·
**`app/routing/specs/sse.md`**（本文件流式端点的**骨架** —— 它才在本文件里）·

`/rag/stream_search` 是它的 RAG 那一半；另 5 条 Agent 链见 `app/routing/specs/api_v1_agent.md`）·
`docs/契约/接口契约.md` §四 · `docs/原理/架构.md` §3.2
改前这两条链**零闸零记账**）·
改前这条链**闸在、账不写**；⚠️ 它同时更正了"流式拿不到 usage"那条**错前提**）·
 第一条 / 硬门 B 第二半；⚠️ 含**三轮真栈 spike** 与"相似度阈值"那条路的**结构性证伪**）·
`app/static/specs/static_frontend.md`（**消费这个帧的那一页**）·
`app/rag/specs/query_rewriter.md`


## 🆕 `GET /rag/chat_history` —— **读回对话历史**（前端整体调整）

- **它补的是一个真缺陷**：在这之前**全仓没有任何"读回对话历史"的出口**
  （`core.cache.get_chat_history` 只被服务端用来喂模型），而对话页**从来没做过回显**
  ⇒ 访客换个页面再回来，**页面上是空的、而模型还记得**。
  ⚠️ **那不是创空间的限制**（那只是单容器）。作者 实测报的。
- **只回当前用户 + 当前会话**（`session_key(user, thread)` 那个键）——
  `user_name` 来自**鉴权依赖**，⛔ **不是形参**；有一条守卫钉着这件事
  （`app/tests/test_chat_history_endpoint.py`，反证：把它改成 `Query(...)` ⇒ 当场红）。
- ⚠️ `thread_id` **必填无默认值**（有默认值 ⇒ 「忘了传」静默读回另一个桶）。
- ⚠️ **窗口是「最近 5 轮」**（实现就是 `lrange(key, -10, -1)`）—— 响应里带 `window` 说清它。
- ⚠️ **半截回答**带着 `INTERRUPTED_SUFFIX` 存着⇒ 页面要**看得出来**，
  ⛔ 别当完整答案渲染。

## 🔴 · `/rag/search` 声明了 `response_model` + `mode` 死字段已删

- `/rag/search` 加了 `response_model=RagSearchResponse`（本仓第一条，见 `schemas.md` 同日那节）。
- 它的 **`mode` 是 query 参数**，body 里那个（`QuestionRequest.mode`）是**死字段** ⇒ 已删。
- 🔴 **接线时实测到一条**：传 `?thread_id=X`，而 `cost_records` 里记的是**别的值**
  （`answer_generation → default` · `embedding → system` · `query_rewrite → unknown`）
  ⇒ **按会话的额度**（用调用者传的那个）与**按会话的账**（记的不是它）**算的是不同的桶**。
  ⇒ ✅ **已修** —— 见下面那一节。

## ✅：会话闸的桶与账的桶**对齐了**

🔴 **先把性质说清：那不是"账不好看"，是【那道会话闸失效了】。**
`unified_search` 一直用**调用者传的** `thread_id` 查 `check_session_token_budget`，
而 `thread_id` **从没往下传过**（`search_async` 连形参都没有）
⇒ 链上三处记账各落 `"default"` / `"system"` / `"unknown"`
⇒ **闸查的桶恒空** ⇒ **这条路上的会话 50k 上限永远不会触发**（静默放行）。
📌 **可打印判据**：`get_session_token_usage(user, <调用者的 thread>)` —— 改前 **0**、改后 **36**。

**改法**：`thread_id` 变成**与 `user_id` 同级的必填关键字形参**，从端点一路贯穿到**每一处记账**
（`search_async` → `rewrite_query` / `expand_query` / `get_embedding` / `generate_answer_with_citations`）。
🔴 **六处都传**：`/rag/search` · `/rag/rewrite_search` · `/rag/hybrid_search`（**原先连形参都没有**）·
`/rag/rerank_search`（同上）· `/rag/pg_search`（同上）· `/rag/stream_search`
（⚠️ 它原先只差**一半**：答案生成那笔已经带对了，只有 embedding 落 `"system"`）。

⚠️ **三个入库端点**（`/rag/insert` · `/rag/insert_batch` · `/rag/upload_document`）与
`/rag/benchmark_embedding`：**它们不是对话链**（没有"会话"这个概念）
⇒ 传的是**显式标记** `"ingest"` / `"benchmark"`，⛔ **不是**让它静默落 `"system"`
（那个值的语义是「**连用户都没有**」，而这四处用户是真的）。
🔒 守卫 ⇒ `app/tests/test_embedding_attribution.py::test_every_call_site_passes_a_thread`。
