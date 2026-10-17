# `app/rag/query_rewriter.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **可用；它真记账了**（此前 `record_usage` **import 在、调用 0 次**） |
| **对外提供** | `expand_query(original_query, num_variants=3, *, user_name, thread_id)` · `rewrite_query(original_query, conversation_history=None, *, user_name, thread_id)`<br>🔴 **两个函数各多一个同样【必填】的 `thread_id`** —— 它落到两处 `record_usage` 的 `thread_id=` 上 |
| **谁在用** | `rag_pipeline.py:99,105`（`/rag/search`）· `hybrid_search.py:128,131`（`/rag/rewrite_search`） |
| **依赖** | **裸 `openai.OpenAI`**（不是一个 `ChatOpenAI`）· Redis（改写缓存，`CACHE_TTL = 3600`） |

## ✅ 做了什么

- `rewrite_query` —— 口语转书面语、**按对话历史消解指代**（"它"/"这个" ⇒ 明确对象）、省略句补全
- `expand_query` —— 生成 `num_variants` 个语义相同、表述不同的变体，扩大召回
- 两条都**带 Redis 缓存**（命中即早退，不再调 LLM）
- 🔴 **两条各记一笔 `record_usage`**
  （`expand_query` 在 `:75` · `rewrite_query` 在 `:175`），
  且签名加**必填** keyword `user_name`（`:43` / `:123`）

## 🟡 做到哪 / 缺什么

- 🔴 **零测试覆盖**（`docs/说明/测试.md` §六）—— 本 spec 建立时**仍然**没有单模块测试；
  现有的覆盖来自 `app/tests/test_rag_billing_wiring.py`（**只测记账那部分**）与
  `app/tests/test_rag_search.py`（把这两个函数**整个 patch 掉**，⛔ 根本没跑到真逻辑）
- ⚠️ **缓存键里没有用户**（`_get_cache_key` 只拼 `prefix:text:extra`）——
  跨用户命中的前提是**问题文本 + 历史字符串完全相同**，实际很难撞上；
  ⚠️ 但它**不是**按用户隔离的，别把它读成"天然隔离"
- ⚠️ **增量没做**：每次改写都是一次**完整的** LLM 调用，没有 token 预算预估、没有截断

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「本模块和其它模块一样用 `ChatOpenAI`」 | 🔴 **不是** —— 它是**全仓唯一**用**裸 `openai.OpenAI`** 的地方（另 15 处走 `llm_factory.make_llm` ⇒ `ChatOpenAI`）。<br>⇒ **直接后果**：它的响应只有 **`.usage`**，**没有 `usage_metadata`**。 |
| 「记账调 `record_from_response` 就行」 | 🔴 **不行，会静默不记** —— `record_from_response` 的判据是 `usage_metadata`，对裸客户端的响应**恒返回 `False`**（不抛异常）。<br>⇒ 本模块**只能**走 `record_usage`（吃 `prompt_tokens` / `completion_tokens`）。<br>📌 守卫：`app/tests/test_rag_billing_wiring.py::test_query_rewriter_uses_record_usage_not_record_from_response` |
| 「`:98` 那个 `import record_usage` 是没用的死 import」 | 🔴 **它曾经就是** —— 自建立起 import 在、**一次都没被调用过**（的病根之一）。**它被用了两次**。<br>⚠️ 这也是「**grep 到 `record_usage` ≠ 它被调用**」的现成例子：本仓那几条 AST 守卫就是为这件事写的。 |
| 「改写成空 ⇒ 这次没花钱」 | 🔴 **花了** —— 「空结果回退原问题」（`:190`）是**在调用之后**发生的。⇒ 记账点必须排在**那条早退分支之前**，⛔ 不是 `return` 之前"碰巧"就行。⛔ **别把记账挪到 `return` 上方** —— 那条口子会吞掉它。 |
| 「命中缓存也会记一笔」 | ⚠️ **反了** —— 命中缓存 ⇒ `expand_query` 在 `:57` 早退、`rewrite_query` 在 `:142` 早退 ⇒ **不调 LLM ⇒ 不记账**。那**是故意的**（没花钱就不该记），⛔ 别当漏记去"修"。<br>📌 守卫：`test_cache_hit_does_not_record` |
| 「`user_name` 有默认值，可以不传」 | 🔴 **没有默认值** —— 它是**必填 keyword**（`:43` / `:123`）。漏传 = `TypeError`，⛔ 不是"静默记成 `unknown`"。<br>⇒ 这是**有意的**（明文把 `.get(...,"unknown")` 当反例）。 |
| 「缓存能挡住大部分调用」 | ⚠️ **只挡完全相同的输入** —— 键含**对话历史**（`rewrite_query`）⇒ 多轮对话里历史一变就是新键，**命中率远低于直觉**。 |

## 关联

`app/rag/specs/hybrid_search.md` · `app/billing/specs/token_tracker.md` · `app/routing/specs/api_v1_rag.md`

> ⚠️ **`app/rag/rag_pipeline.py` 至今没有 spec** ——
> 它是 `/rag/search` 的真管线，**本轮改了它三处**（`:99` `:105` `:207`）。
> 建 spec 是**另一件事**，⛔ 本轮没做，已登记。
