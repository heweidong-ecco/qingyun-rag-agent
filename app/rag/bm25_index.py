"""
BM25 关键词检索（jieba 中文分词 + rank_bm25）。

从 `db.py` 切出（重构计划 ⑥ 切开点 1）。目的：让 `import db` 不再被迫拉起
`numpy` / `jieba` / `rank_bm25` 三个重包。

**依赖方向只允许单向**：本模块 `from db import get_db`；
`db.py` 一律用**函数内惰性导入**反向引用本模块。
**不要**在 `db.py` 模块层 `import bm25_index` —— 那会形成循环导入，
且只在"先 import db"时才碰巧能跑（"先 import bm25_index"就会炸），是最难查的一类问题。
"""
import re

import numpy as np
import jieba
from rank_bm25 import BM25Okapi

from core.db import get_db, PUBLIC_OWNER

# 「实词」= 剔掉空白 / 标点 / 下划线之后的词元。
# 为什么需要它：`jieba.cut` 会吐出 `' '` / `'·'` / `'-'` 这类**每篇都有**的词元，
#    拿它们做"是否重合"的判定 ⇒ **任意两篇都算重合**，判定就废了。
_NON_WORD = re.compile(r"^[\W_]+$", re.UNICODE)


def _meaningful_tokens(tokens) -> set:
    """从 jieba 词元里取**实词集合**（`{"BETA","DOC"}` 这种）。

    只用于**入选判定**；BM25 自己的打分仍用**未过滤**的词元
       （过滤会改变 idf/词频 ⇒ 属"调质量"，不是本段（隔离）的范围）。
    """
    return {t for t in tokens if t and not _NON_WORD.match(t)}

# 按身份分桶的缓存：避免每次搜索都重建索引。
#
# 2026-10-03 改（决策 5：共享层承重）—— 原先这里是**一分全局单份**
#    `{"bm25": None, "docs": []}`，语料 = `get_all_documents()` = **全库无过滤**
#    ⇒ 检索结果里必然混进**别人的**文档。
#
#    现在**按 `user_id` 分桶**，且语料在 **SQL 层**就 `WHERE requested_by = %s` 过滤。
#    **为什么是"过滤语料"而不是"检索出来再筛掉"**：后者别人的文档**仍会参与 IDF 统计**
#       （`BM25Okapi` 是在整个语料上算的），是**另一条渗漏** —— 结果里虽看不到原文，
#       但词频/权重已经被别人的数据影响了。判据要的是"看不见"，那就**别让它进索引**。
_bm25_cache = {}  # user_id -> {"bm25": BM25Okapi | None, "docs": [...]}


def _require_identity(user_id, where: str) -> None:
    """fail-closed：没有身份 ⇒ **抛错**，不是"当成匿名、查全库"（§六 ③）。"""
    if not user_id:
        raise ValueError(
            f"{where} 需要 user_id（非空）—— 传空就等于「查全库」，"
            f"那是本仓已裁定的 fail-open 反模式（§二 根因 / §六 ③）"
        )


def get_all_documents(user_id: str):
    """获取**该用户 + 公共语料**的内容、来源和ID，用于构建BM25索引。

    2026-10-03：加 `WHERE requested_by = %s`。原先无 WHERE ⇒ 全库。
    2026-10-12：加 `OR requested_by = PUBLIC_OWNER` ——
       公共语料**人人可见**，而**用户之间仍然互相看不见**（两件事，别读成"隔离被取消"）。
    """
    _require_identity(user_id, "get_all_documents")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, source FROM documents "
                "WHERE requested_by = %s OR requested_by = %s",
                (user_id, PUBLIC_OWNER),
            )
            return cur.fetchall()


def get_bm25_index(user_id: str):
    """获取**该用户**缓存的BM25索引，如果不存在则重建。

    返回 `(bm25, docs, token_sets)` —— `token_sets` 是每篇的**实词集合**，供入选判定用
    （见 `bm25_search` 里那段说明）。
    """
    _require_identity(user_id, "get_bm25_index")
    slot = _bm25_cache.setdefault(user_id, {"bm25": None, "docs": [], "token_sets": []})
    if slot["bm25"] is None:
        docs = get_all_documents(user_id)
        if docs:
            # 使用 jieba 进行中文分词
            tokenized_docs = [list(jieba.cut(doc[1])) for doc in docs]
            slot["bm25"] = BM25Okapi(tokenized_docs)
            slot["docs"] = docs
            slot["token_sets"] = [_meaningful_tokens(t) for t in tokenized_docs]
    return slot["bm25"], slot["docs"], slot["token_sets"]


def invalidate_bm25_cache():
    """在文档插入/删除后调用，清空缓存以触发重建。

    现在是**清空所有桶**（原来是重置单份）—— 插入/删除不知道影响哪个用户时，
       全清是**安全侧**的做法（宁可多重建，不可留旧语料）。
    """
    _bm25_cache.clear()


def bm25_search(query: str, top_k: int = 10, *, user_id: str):
    """
    用 BM25 关键词检索**该用户自己的**文档。
    返回 [(id, content, source, bm25_score), ...]
    """
    _require_identity(user_id, "bm25_search")
    bm25, docs, token_sets = get_bm25_index(user_id)
    if bm25 is None or not docs:
        return []

    tokenized_query = list(jieba.cut(query))
    scores = bm25.get_scores(tokenized_query)

    # 2026-10-03 修（决策 5 的连带）—— **入选判据换掉了**。
    #
    # 原判据是 `if scores[idx] > 0`（"分数为正才算命中"）。那在**全库语料**下碰巧成立，
    # 但语料**按人切**之后就不成立了 —— `rank_bm25` 的 idf 是
    #     `log(N - n + 0.5) - log(n + 0.5)`
    # **词出现在超过一半文档里就是负的**。小用户（1 篇）必然触发 ⇒ 分数为负
    # ⇒ 全部被丢掉 ⇒ **关键词检索对小用户整个失效**（`/rag/hybrid_search` 退化成纯向量）。
    # 实测（探针语料）：1 篇时 `idf = -0.27` · `score = -0.82`；5 篇时 `idf = +1.10` · `score = +2.18`。
    #
    # ⇒ 改成按**实词重合**判定（"这篇确实含查询词"），拿掉"分数正负"这个**代理判据** ——
    #   分数只用来**排序**，不再用来**决定入不入选**。
    #   对**大语料**的行为差异：以前"只靠高频词命中"的文档会被丢弃，现在会入选、
    #      但分数最低 ⇒ 排在最后（`hybrid_search` 的 RRF 按名次给分，它们的贡献最小）。
    #
    # **没走"用全库语料算 idf、返回时按 owner 过滤"那条路** —— 那样打分质量更好，
    #    但**别人的数据仍会影响权重**，与"判据 = 看不见"（决策 3）不符。已记进。
    query_tokens = _meaningful_tokens(tokenized_query)
    if not query_tokens:
        return []

    # 先全扫再按 top_k 截断（原来是先截断再过滤 ⇒ 头部几条不合格就白少给几条）
    results = []
    for idx in np.argsort(scores)[::-1]:
        if not (query_tokens & token_sets[idx]):
            continue
        doc = docs[idx]
        results.append((doc[0], doc[1], doc[2], float(scores[idx])))
        if len(results) >= top_k:
            break

    return results
