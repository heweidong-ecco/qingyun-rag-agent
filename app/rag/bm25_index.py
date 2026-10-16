"""BM25 关键词检索（jieba 中文分词 + rank_bm25）。

从 `db.py` 切出（重构计划 ⑥ 切开点 1）。目的：让 `import db` 不再被迫拉起
`numpy` / `jieba` / `rank_bm25` 三个重包。
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
#    `{"bm25": None, "docs": []}`，语料 = `get_all_documents()` = **全库无过滤**
#    ⇒ 检索结果里必然混进**别人的**文档。
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

    ：加 `WHERE requested_by = %s`。原先无 WHERE ⇒ 全库。
    ：加 `OR requested_by = PUBLIC_OWNER` ——
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
