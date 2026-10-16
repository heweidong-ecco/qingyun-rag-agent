"""
混合检索模块
融合向量检索（稠密）+ BM25关键词检索（稀疏），
使用 RRF 算法进行科学的结果融合。
"""
from core.db import search_similar, bm25_search
from rag.embedding_client import get_embedding
from collections import defaultdict
from rag.reranker import rerank
from rag.query_rewriter import expand_query, rewrite_query

def reciprocal_rank_fusion(
    vector_docs,  # [(id, content, source, similarity), ...] ← 修：原写 3 元组，与 L35 的解包不符
    bm25_docs,    # [(id, content, source, bm25_score), ...]
    k=60,
    top_k=5
):
    """
    使用 RRF 算法融合两路检索结果。
    
    参数:
        vector_docs: 向量检索结果列表
        bm25_docs: BM25 检索结果列表
        k: 平滑常数，默认 60
        top_k: 返回的最大文档数量
    
    返回:
        [{"content": ..., "source": ..., "rrf_score": ..., "from": ...}, ...]
    """
    # 用内容作为文档的唯一标识
    rrf_scores = defaultdict(float)
    doc_info = {}
    
    # 1. 处理向量检索结果（db.search_similar 返回 (id, content, source, similarity)）
    for rank, (doc_id, content, source, similarity) in enumerate(vector_docs, start=1):
        rrf_scores[content] += 1.0 / (k + rank)
        doc_info[content] = {
            "id": doc_id,
            "content": content,
            "source": source,
            "from": "vector"
        }
    
    # 2. 处理 BM25 检索结果
    for rank, (doc_id, content, source, bm25_score) in enumerate(bm25_docs, start=1):
        rrf_scores[content] += 1.0 / (k + rank)
        # 如果同一个文档被两路都检索到，标记为 both
        if content in doc_info:
            doc_info[content]["from"] = "both"
        else:
            doc_info[content] = {
                "content": content,
                "source": source,
                "from": "bm25"
            }
    
    # 3. 按 RRF 分数降序排序
    sorted_contents = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    
    # 4. 构建返回结果
    results = []
    for content, rrf_score in sorted_contents[:top_k]:
        info = doc_info[content]
        results.append({
            "content": info["content"],
            "source": info["source"],
            "rrf_score": round(rrf_score, 4),
            "from": info["from"]
        })
    
    return results


def hybrid_search(query: str, top_k: int = 5, *, user_id: str, thread_id: str):
    """混合检索：向量检索 + BM25 关键词检索，使用 RRF 融合。

    ：加**必填** `user_id`（决策 4/5）—— 身份用**显式形参**贯穿到
       `db.search_similar` / `bm25_index.bm25_search`，那两处负责真正过滤。
       **必填、不给默认值** —— 有默认值就等于"可以忘记传"，
          那就还是  §二 那个 fail-open 根因。
    """
    # 1. 向量检索
    query_embedding = get_embedding(query, user_name=user_id, thread_id=thread_id)   # 🔴：会话也要往下传
    vector_results = search_similar(query_embedding, top_k=top_k * 2, user_id=user_id)  # 多召回一些

    # 2. BM25 关键词检索
    bm25_results = bm25_search(query, top_k=top_k * 2, user_id=user_id)  # 多召回一些

    # 3. RRF 融合
    return reciprocal_rank_fusion(
        vector_docs=vector_results,
        bm25_docs=bm25_results,
        k=60,
        top_k=top_k
    )
    

# 在 RRF 融合后，增加重排序步骤：
def rerank_search(query: str, top_k: int = 3, *, user_id: str, thread_id: str) -> list[dict]:
    """
    完整的混合检索 + RRF 融合 + 重排序流程。
    这是最终版检索入口。

    ：加**必填** `user_id`（决策 4/5），向下透传。
    """
    # 1. RRF 混合检索（多召回一些，给重排序留足候选池）
    candidates = hybrid_search(query, top_k=top_k * 3, user_id=user_id, thread_id=thread_id)

    # 2. Cross-Encoder 重排序（精细打分）
    return rerank(query, candidates, top_k=top_k)

# 新增带 用户查询问题改写 的检索函数：
def hybrid_search_with_rewrite(
    query: str,
    top_k: int = 5,
    conversation_history: list[dict] = None,  # [{"role","content"}]；旧形态 list[str] 也兼容，见 query_rewriter._history_lines
    *,
    user_id: str,
    thread_id: str,
) -> list[dict]:
    """
    带查询改写的完整检索流程：
    1. 优化原始查询（补全上下文、转书面语）。
    2. 生成多个查询变体（扩大召回）。
    3. 对每个变体执行混合检索 + RRF 融合。
    4. 对所有变体的结果再次做 RRF 融合。
    """
    # 第一步：查询优化
    optimized_query = rewrite_query(query, conversation_history, user_name=user_id, thread_id=thread_id)

    # 第二步：查询扩展
    query_variants = expand_query(optimized_query, user_name=user_id, thread_id=thread_id)

    # 第三步：对每个变体分别检索，收集所有结果
    all_docs = []  # [(doc_content, rrf_score), ...]
    seen_contents = set()

    for variant in query_variants:
        docs = hybrid_search(variant, top_k=top_k * 2, user_id=user_id, thread_id=thread_id)  # 多召回一些
        for doc in docs:
            if doc["content"] not in seen_contents:
                seen_contents.add(doc["content"])
                all_docs.append(doc)

    # 第四步：对合并后的结果按 RRF 分数排序，取 top_k
    all_docs.sort(key=lambda x: x.get("rrf_score", 0), reverse=True)
    return all_docs[:top_k]



