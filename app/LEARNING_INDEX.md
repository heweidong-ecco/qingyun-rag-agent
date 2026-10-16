标准化注释块
"""
---
day: 28
topic: Reranker批量化推理
version: v2-batch
status: superseded  # active | superseded | deprecated | experimental
superseded_by: reranker_v3_singleton.py  # 仅当 status=superseded 时填写
key_learning: Cross-Encoder支持batch输入，吞吐量提升3x，但显存占用增加
tags: [reranker, performance, batch-processing]
---
说明: Day28实现的批量推理版本。相比v1逐条处理性能显著提升，
      但未解决模型重复加载问题，已被Day35的v3单例版取代。
      保留用于对比batch vs sequential的性能差异。
"""

## Reranker 模块演进

| 版本 | Day | 状态 | 关键学习点 | 文件 |
|------|-----|------|-----------|------|
| v1-naive | 12 | superseded | 理解Cross-Encoder基本原理 | reranker_v1_naive.py |
| v2-batch | 28 | superseded | 批量推理吞吐提升 | reranker_v2_batch.py |
| v3-singleton | 35 | **active** | 单例+批量，当前最优 | reranker_v3_singleton.py |

> 🔴 **上表第三列的三个文件名【都已不存在】—— 这是【知情的、不打算删】。**
>
> **本文件是【学习轨迹】，不是文件清单。** 上表的"文件"一栏记的是**当时那个版本叫什么**，
> 用来串起 Day12 → Day28 → Day35 的演进；**三个文件后来被合并成一份实现，历史名不再保留成文件**。
> （上表首行 front-matter 的 `superseded_by: reranker_v3_singleton.py` 同理 —— ⛔ 别拿它去找文件。）
>
> ✅ **现在真正在跑的是**：**`app/rag/reranker.py`**（CrossEncoder `BAAI/bge-reranker-v2-m3`
> 懒加载单例 + `rerank` / `rerank_async`），由 `hybrid_search.py` 与 `rag_pipeline.py` 引用。
>
> **判据（可打印，2026-10-07 核）**：
> ```bash
> ls app/ | grep -i rerank          # ⇒ 只有 reranker.py（v1/v2/v3 那三个都不在）
> grep -rn "def rerank" app/rag/reranker.py   # ⇒ rerank / rerank_async 都在这一个文件里
> ```
> 📌 **本条对应 `docs/待办总表.md` 的 **（原文：「**只需明确一句：这是知情的、不打算删**」）。

"""
---
day: 50
topic: 结构化日志与Timing输出修复
version: v2-structured
status: active
key_learning: Timing字典需通过extra参数传递，Formatter需支持嵌套序列化
tags: [observability, logging, timing, production-readiness]
---
"""

