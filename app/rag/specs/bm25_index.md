# `app/rag/bm25_index.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用** —— 且 **它是「多用户隔离」的两个承重层之一**（决策 5：过滤写在共享层） |
| **对外提供** | `bm25_search()` · `get_all_documents()` · `get_bm25_index()` · `invalidate_bm25_cache()`（+ 私有的 `_meaningful_tokens()` / `_require_identity()` / `_NON_WORD`） |
| **谁在用** | ⚠️ **没有直接调用方** —— 都是经 `db.py` 的**惰性转发层**（`db.bm25_search` / `db.bm25_search_async` / `db.invalidate_bm25_cache`）⇒ 再往上是 `hybrid_search.py` 与 `rag_pipeline.py` |

## ✅ 做了什么

- **BM25 关键词检索** —— `jieba` 中文分词 + `rank_bm25.BM25Okapi`
- **进程内缓存**，避免每次搜索重建索引
- 是  功能现状表里 **✅「混合检索（向量 + BM25 + RRF 融合）」** 的**稀疏那一路**

## 🔴 · 公共语料库—— 语料从「按人切」变成「按人切 + 公共那份」

`get_all_documents(user_id)` 的 SQL 加了一个 `OR requested_by = PUBLIC_OWNER`
（常量定义在 `core/db.py:18`）。

🔴 **容易被漏掉的一点**：BM25 是**另一条取语料的路**（它不在 `db.py` 里）——
只改向量那条 ⇒ **混合检索里 BM25 那一半看不见公共语料，而且不会有任何报错**。

⚠️ **一条要留意的后果**：缓存**仍然按 `user_id` 分桶** ⇒ **公共语料会被复制进每个用户的桶**。
示例语料的规模（74 块）无所谓；⚠️ 公共语料要是长到很大，这里会按用户数线性吃内存。

⚠️ **灌公共语料之后必须 `invalidate_bm25_cache()`** —— 它清的是**所有桶**（`:93`），那是安全侧的做法。

## 🔴 · 多用户隔离（甲段）

**两件事都改了**（互相牵连，别拆开读）：

**① 语料按人切，⛔ 不是"检索出来再筛掉"** —— `get_all_documents(user_id)` 在 **SQL 层**就
`WHERE requested_by = %s`；缓存的 key 从「一份全局」改成 **`user_id` 分桶**。

> ⚠️ **为什么必须是"切语料"**：若是"全库建索引、返回时筛"，别人的文档**仍会参与 IDF 统计**
> （`BM25Okapi` 是在整个语料上算的）—— 结果里虽看不到原文，**但词频/权重已被别人的数据影响**。
> 判据是「**看不见**」，那就**别让它进索引**（决策 3）。

**② 入选判据换了**（** 决策 7** —— ⚠️ 本 Agent 拍的板，作者 裁「不拆单独 DEC」）：

```
原判据： if scores[idx] > 0        ← "分数为正才算命中"
新判据： if query_tokens & token_sets[idx]   ← "实词有重合"
```

**根因**：`rank_bm25` 的 idf = `log(N - n + 0.5) - log(n + 0.5)` ——
**词出现在超过一半文档里就是负的**。语料**按人切**之后，小用户（1 篇）必然触发
⇒ 分数为负 ⇒ 原判据**恒假** ⇒ **关键词检索对小用户整个失效**（`/rag/hybrid_search` 退化成纯向量）。

**实测**（探针语料）：1 篇时 `idf = -0.27` / `score = -0.82`；5 篇时 `idf = +1.10` / `score = +2.18`。

⚠️ **对外部行为的差异（大语料上）**：以前"只靠高频词命中"的文档会被**丢弃**，
现在会**入选但分数最低** ⇒ 排在最后（`hybrid_search` 的 RRF 按**名次**给分，它们的贡献最小）。

## 🟡 做到哪 / 缺什么

- 🔴 **「改完文档要清缓存」全靠调用方记得** —— 本模块**没有**任何自动失效机制。
  已知**有 3 处记得**（`/rag/insert` · `/rag/insert_batch` · `DELETE /rag/documents/{id}`），
  **1 处漏了**：`/rag/upload_document`。详解见 `db.md` 的同名条目。
- ⚠️ **缓存是进程内的** ⇒ 多 worker 部署时**每个 worker 各一份**，
  A worker 插入后清不到 B worker 的缓存。**单 worker 下不成问题，多 worker 下是真问题。**
- ⚠️ **零测试覆盖本模块直接行为** —— `app/tests/test_isolation.py` 有 2 条**间接**打到
  `bm25_search` 的隔离性（`test_bm25_search_only_returns_own_documents`
  `test_bm25_still_finds_own_document`），**但打分质量 / 排序正确性无测试**。
- ⚠️ **`_meaningful_tokens` 只用于入选判定**，⛔ **不参与打分** ——
  过滤会改变词频/idf，那属"调质量"，不在隔离这一段的范围内（有意留着）。

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「`scores` 是正的 ⇒ 命中」** | ⛔ **判据已经不是分数了**—— `scores` **只用来排序**，入选靠 `query_tokens & token_sets[idx]`。<br>⇒ 会看到"分数为负还挺在结果里"而以为坏了 —— **那是有意的**（决策 7）。<br>📌 判据：`grep -n "query_tokens & token_sets" app/rag/bm25_index.py` ⇒ `:135` |
| 🔴 **「`_bm25_cache` 是一份全局索引」** | ⛔ **按 `user_id` 分桶**（`:44`）。<br>⚠️ 且 **`invalidate_bm25_cache` 清【所有】桶**（`:96` `_bm25_cache.clear`）—— **不是只清某人**。<br>⇒ 插入/删除时**不知道影响谁**，全清是**安全侧**（宁可多重建，⛔ 不可留旧语料）。**代价是重建变频繁** |
| 🔴 **「BM25 在 `hybrid_search.py` 里」** | ⛔ **不在** —— 那是 **RRF 融合**层。BM25 本体**只在本文件**；`hybrid_search.py` 经 `db` 转发层调它 |
| 🔴 **「`jieba` 分出来的词都参与"是否重合"判定」** | ⛔ **不是** —— `jieba.cut` 会吐出 `' '` / `'·'` / `'-'` 这类**每篇都有**的词元，拿它们判定 ⇒ **任意两篇都算重合**，判定就废了。<br>⇒ `_NON_WORD`（`:23`）专门剔掉它们，**只用于入选判定** |
| ⚠️ **「IDF 是按全库算的」** | ⛔ **按【该用户的语料】算** —— 这是**故意的**（同上方 ①）。<br>⇒ **同一个词，在不同用户那里分数不同** ⇒ ⛔ **别拿 A 的分数去解释 B 的结果** |
| ⚠️ **「两路召回（向量/BM25）各返回 `top_k`」** | ⛔ **实际各取 `top_k * 2`** —— 融合前多召回一些，见 `hybrid_search.py:84` / `:87` |

## 关联

`app/core/specs/db.md`（转发层与 `_require_identity`）· `app/rag/specs/hybrid_search.md`（RRF 融合）·
`docs/原理/架构.md` §1.2


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/rag/bm25_index.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ 整段**原样搬来**，⛔ 未改写。

**依赖方向只允许单向**：本模块 `from db import get_db`；
`db.py` 一律用**函数内惰性导入**反向引用本模块。
**不要**在 `db.py` 模块层 `import bm25_index` —— 那会形成循环导入，
且只在"先 import db"时才碰巧能跑（"先 import bm25_index"就会炸），是最难查的一类问题。
