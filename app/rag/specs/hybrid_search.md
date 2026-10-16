# `app/rag/hybrid_search.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用，但它在全仓是【第二份 RRF 实现】** |
| **对外提供** | `reciprocal_rank_fusion()` · 三个组合函数 |
| **谁在用** | ⚠️ **只有路由层**（`/rag/hybrid_search` · `/rag/rerank_search` · `/rag/rewrite_search`）—— **`rag_pipeline` 不用它** |

## ✅ 做了什么

- **RRF（Reciprocal Rank Fusion，k=60）** —— 融合稠密向量检索与稀疏 BM25 两路结果
- 是  功能现状表里标 **✅「混合检索（向量 + BM25 + RRF 融合）」** 的落点

## 🔴 2026-10-03 · 多用户隔离（甲段）

**三个对外函数都加了【必填】的关键字形参 `user_id`**：

```
hybrid_search(query, top_k=5, *, user_id, thread_id)
rerank_search(query, top_k=3, *, user_id, thread_id)
hybrid_search_with_rewrite(query, top_k=5, conversation_history=None, *, user_id, thread_id)
```

> 🔴 **2026-10-16三个函数又多了一个同样【必填】的关键字形参 `thread_id`** ——
> 理由与 `user_id` **逐字同款**（⛔ 不给默认值）。它往下传到 `get_embedding` 与改写器，
> 否则账会落进 `"system"` / `"unknown"`，而**会话闸查的是调用者那个桶** ⇒ **闸恒看到 0**。

- **为什么必填、⛔ 不给默认值**：有默认值 = "可以忘记传" ⇒ 还是 fail-open。
  漏传 = `TypeError`，⛔ 不是"静默查全库"（§六 ③）。
- **过滤逻辑⛔ 不在这文件里** —— 在**共享层** `db.search_similar` / `bm25_index.bm25_search`
  （本文件只负责**把身份传下去**）。
- ⚠️ **由此修好 3 条端点**（本文件服务的那三条）—— 见 `api_v1_rag.md`。
- ⚠️ **配套**：`bm25_index` 的**入选判据**同期由「分数为正」改成「实词有重合」
  （小语料上 idf 为负 ⇒ 原判据恒假）⇒  **决策 7**。

## 🔴 2026-10-05 · 把身份继续递到改写器

`hybrid_search_with_rewrite` 里那两处改写调用**跟随上面的 `user_id` 走**：

```
:129  rewrite_query(query, conversation_history, user_name=user_id, thread_id=thread_id)
:132  expand_query(optimized_query, user_name=user_id, thread_id=thread_id)
```

> 🔴 **2026-10-16**：上面那两行**各补了一个 `thread_id=`**（行号现取，别抄）——
> 同一件事的**会话**那一维。

⚠️ **只此一处改动**，且 ⛔ **不给默认值** —— 目的是让 `query_rewriter` 能把
真账记到**具体的人**头上；漏传 = `TypeError`（同  的取向）。

## 🟡 做到哪 / 缺什么

- 🔴 **同算法两份实现**（见下）
- 🔴 **零测试覆盖**（`docs/说明/测试.md` §六 **#1**）
- ⚠️ **它是  自己点名的"复审出过 bug 的地方"**：
  > 「**RRF 恰恰是 2026-08-17 复审出 bug 的地方**」
  > （那次是元组解包崩溃：按 3 元组解包，而 `db.search_similar` 返回 4 列 —— 修复记录 #4）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「`/rag/search` 用的就是 `hybrid_search.py`」 | 🔴 **不是** —— `/rag/search` 走 `rag_pipeline.py:201` 的 **`_rrf_fusion`**（**另一份实现**）<br>⇒ **本文件服务的是另外三条**（`/rag/hybrid_search` · `/rag/rerank_search` · `/rag/rewrite_search`），**`/rag/search` 不在内** |
| 「本文件只服务 `/rag/hybrid_search` 一条端点」 | 🔴 **2026-10-03 更正：三条** —— `rerank_search` 与 `hybrid_search_with_rewrite` 也在这里（`grep -n 'hybrid_search\|rerank_search' app/routing/api_v1_rag.py` 看调用点）。<br>⚠️ 原先写"一条"是**错的**（只盯了函数名带 `hybrid_search` 的那个） |
| 「RRF 被测试守着」 | 🔴 **没有** —— `test_rag_search.py` 测的是它**自己复制的一份 `_rrf_fusion`**（第三份副本）⇒ **真融合逻辑【没有直接覆盖】** |
| 「BM25 在这文件里」 | 🔴 **不在这** —— BM25 在 `app/rag/bm25_index.py`（jieba + rank_bm25，带进程内缓存） |
| 「`user_id` 在这层被用来过滤」 | 🔴 **不是** —— 本文件**只把它往下传**，真正的 `WHERE requested_by = %s` 在 `db.search_similar` 与 `bm25_index.bm25_search`。<br>⇒ **要改过滤，改那两个；要改"谁能调"，改这层的签名**（决策 5：共享层承重） |
| 「传了 `user_id` 就隔离了」 | ⚠️ **要看到它一路到 SQL 才算** —— 中间任何一层把 `user_id` 丢掉（或换成常量），签名看着齐全、隔离却是假的。<br>⇒ 判据是 `app/tests/test_isolation.py` 那 8 条（**已做过证伪**），⛔ 不是"签名里有这个参数" |
| 「本文件不花 LLM 钱」 | ⚠️ **只有前两个函数不花** —— `hybrid_search_with_rewrite` **无条件**调改写/扩展（`query_rewriter`，**真 LLM 调用**）⇒ `/rag/rewrite_search` 每次都花钱，2026-10-05 起**每次都记账**。 |

> ⭐ **这条是本 spec 最有价值的发现**：
> **同一个算法三份代码**（`rag_pipeline._rrf_fusion` · 本文件的 `reciprocal_rank_fusion` · 测试里的副本），
> **测试测的那份【不是产品在跑的那份】。**

## 关联

 功能现状表 · `docs/说明/测试.md` §六 · `docs/原理/架构.md` §1.2（"同一算法两份实现"）·
