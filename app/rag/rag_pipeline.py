"""
综合检索管线
（带耗时统计）
将查询改写、混合检索、RRF 融合、重排序整合为统一入口。
每个环节可独立开关，通过配置参数灵活组合。
"""
import time
from rag.query_rewriter import expand_query, rewrite_query
from rag.reranker import rerank_async
from rag.embedding_client import get_embedding
from core.db import search_similar_async, bm25_search_async
from collections import defaultdict
from core.llm_factory import make_llm   # ①b：model / api_key / base_url / max_tokens 的唯一落点
#    标准库 + `token_config`（`db` / `langchain` 都是**函数内**惰性导入）
#    —— 同一条理由见 `api_v1_rag.py:21-23` 那段注释。
from billing.token_tracker import record_from_response


class RAGPipeline:
    """可配置的 RAG 检索管线 （带耗时统计） """

    def __init__(
        self,
        enable_rewrite: bool = False,
        enable_expand: bool = False,
        enable_bm25: bool = True,
        enable_rerank: bool = True,
        rrf_k: int = 60,
        candidate_multiplier: int = 3,
        answer_llm=None
    ):
        """
        参数:
            enable_rewrite: 是否启用查询优化（口语转书面语、指代消解）
            enable_expand: 是否启用查询扩展（生成多个查询变体）
            enable_bm25: 是否启用 BM25 关键词检索
            enable_rerank: 是否启用 Cross-Encoder 重排序
            rrf_k: RRF 平滑常数
            candidate_multiplier: 候选文档倍数（多召回留足重排序空间）
        """
        self.enable_rewrite = enable_rewrite
        self.enable_expand = enable_expand
        self.enable_bm25 = enable_bm25
        self.enable_rerank = enable_rerank
        self.rrf_k = rrf_k
        self.candidate_multiplier = candidate_multiplier
        # 增加一个用于生成答案的 LLM 实例
        # 角色 = 「模型轴 fast」+「长度轴 answer(2000)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
        self.answer_llm = answer_llm or make_llm("fast", "answer")

    #    **全仓零引用**（`grep -rn '\.preprocessor' app/` = 0）。它只是**在类定义时构造一次**，
    #    从没有任何方法读它。（真正做预处理的实例是在各方法内部**就地构造**的。）

    async def search_async(
        self,
        query: str,
        top_k: int = 5,
        conversation_history: list[dict] = None,   # [{"role","content"}]；旧形态 list[str] 也兼容（见 query_rewriter._history_lines）
        generate_answer: bool = False,   # 新增
        strict_mode: bool = False,        # 新增
        citations: bool = False,          # 新增
        *,
        user_id: str,                     # 加（决策 4/5）
        thread_id: str,                   # 🔴：会话标识，往下贯穿到**每一处记账**
    ) -> dict:
        """执行完整检索流程，返回结果和管线元信息。

        修：这段 docstring 原先**躺在查询规范化那几行之后**，是**空操作** ——
           本方法**没有 docstring**。已上移到签名正下方（代码一行未动）。

        ：加**必填** `user_id`—— 检索**只在该用户自己的文档内**做。
           身份用**显式形参**贯穿到 `db.search_similar_async` / `bm25_search_async`，
           不用 contextvar（决策 4）。**必填、不给默认值** ⇒ 漏传 = `TypeError`，
           不是"静默查全库"。
        """
        timing = {}  # 存储各阶段耗时（毫秒）
        t_total_start = time.time()
        # 查询规范化（与文档入库使用同一套规则）
        from rag.document_preprocessor import DocumentPreprocessor
        preprocessor = DocumentPreprocessor()
        query = preprocessor.clean_whitespace(query)
        query = preprocessor.normalize_text(query)
        pipeline_info = {
            "original_query": query,
            "rewrite_enabled": self.enable_rewrite,
            "expand_enabled": self.enable_expand,
            "bm25_enabled": self.enable_bm25,
            "rerank_enabled": self.enable_rerank,
        }

        # ===== 第一阶段：查询改写 =====
        t_rewrite_start = time.time()
        search_queries = [query]

        if self.enable_rewrite:
            rewritten = rewrite_query(query, conversation_history,
                                      user_name=user_id, thread_id=thread_id)
            search_queries = [rewritten]
            pipeline_info["rewritten_query"] = rewritten

        if self.enable_expand:
            base = search_queries[0]
            expanded = expand_query(base, num_variants=3, user_name=user_id, thread_id=thread_id)
            search_queries = expanded
            pipeline_info["expanded_queries"] = expanded

        timing["rewrite_ms"] = round((time.time() - t_rewrite_start) * 1000, 2)

        # ===== 第二阶段：多路检索 + RRF 融合 =====
        t_search_start = time.time()
        all_docs = []
        seen_contents = set()

        for sq in search_queries:
            # 向量检索
            query_embedding = get_embedding(sq, user_name=user_id, thread_id=thread_id)
            vector_results = await search_similar_async(
                query_embedding, top_k=top_k * self.candidate_multiplier, user_id=user_id
            )

            # BM25 检索
            bm25_results = []
            if self.enable_bm25:
                bm25_results = await bm25_search_async(
                    sq, top_k=top_k * self.candidate_multiplier, user_id=user_id
                )

            # RRF 融合当前子查询的两路结果
            fused = self._rrf_fusion(vector_results, bm25_results, top_k * self.candidate_multiplier)
            for doc in fused:
                if doc["content"] not in seen_contents:
                    seen_contents.add(doc["content"])
                    all_docs.append(doc)

        # 对所有子查询的结果再次做 RRF 排序
        all_docs.sort(key=lambda x: x.get("rrf_score", 0), reverse=True)
        candidates = all_docs[:top_k * self.candidate_multiplier]
        timing["search_ms"] = round((time.time() - t_search_start) * 1000, 2)

        # ===== 第三阶段：重排序 =====
        t_rerank_start = time.time()
        if self.enable_rerank and candidates:
            candidates = await rerank_async(query, candidates, top_k=top_k)
        timing["rerank_ms"] = round((time.time() - t_rerank_start) * 1000, 2)


        # 过滤明显不相关的文档。
        # 注意：RRF 分数本身很小（约 1/(k+rank)，k=60），不能用 0.7 这种余弦阈值判断；
        # 重排序分数为 Cross-Encoder logits，正数视为可接受。
        if self.enable_rerank and candidates:
            candidates = [
                doc for doc in candidates
                if doc.get("rerank_score", 0) >= 0
            ]
        # 如果所有文档都被过滤掉，返回空列表
        if not candidates:
            return{
                "query":query,
                "pipeline":pipeline_info,
                "timing":timing,
                "docs":[],
            }

        # ===== 总计 =====
        timing["total_ms"] = round((time.time() - t_total_start) * 1000, 2)

        # ===== 返回 =====
        result = {
            "query": query,
            "pipeline": pipeline_info,
            "timing": timing,
            "docs": candidates[:top_k],
        }
        
        # ========== 生成答案（可选） ==========
        if generate_answer and candidates:
            if citations:
            # 带引用
                from rag.answer_with_citations import generate_answer_with_citations
                answer, sources = generate_answer_with_citations(
                    candidates[:top_k], query, self.answer_llm,
                    user_name=user_id,   #：身份必须到得了记账点
                    thread_id=thread_id,  # 🔴：**会话也要** —— 否则账落 `"default"`、闸查调用者那个桶
                )
                result["answer"] = answer
                result["sources"] = sources
            else:
                # 普通生成
                context_text = "\n\n".join([doc["content"] for doc in candidates[:top_k]])
                # 根据 strict_mode 选择 Prompt
                if strict_mode:
                    system_prompt = "根据上下文回答。找不到则说'无法回答'。"
                else:
                    system_prompt = "根据上下文回答，可适当补充常识。"
                from langchain_core.prompts import ChatPromptTemplate
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt + "\n\n上下文：\n{context}"),
                    ("user", "{question}")
                ])
                #    它把 `AIMessage` **剥成 `str`**，`usage_metadata` 随之丢光
                #    记账**拿不到数**（这正是本轮要修的"零记账"，而测试照常全绿）。
                #    改为直调 `invoke`，取消息本体。
                messages = prompt.format_messages(context=context_text, question=query)
                response = self.answer_llm.invoke(messages)
                record_from_response(
                    self.answer_llm, response, "answer_generation",
                    user_name=user_id,
                    # 🔴：**这里原先写死 `thread_id="default"`** ——
                    #    于是每一个调用者的账都挤进那**一个**桶，而会话闸查的是**调用者传的那个**
                    #    ⇒ 闸恒看到 0 ⇒ **那条路上的会话上限永远不会触发**（实测复现过）。
                    thread_id=thread_id,
                )
                result["answer"] = response.content

        # 如果过滤后 candidates 为空，generate_answer 即使为 True 也不生成答案（因为没有上下文）
        elif generate_answer and not candidates:
            result["answer"] = "根据现有资料，无法回答。"

        return result

    def _rrf_fusion(self, vector_docs, bm25_docs, top_k):
        """内部 RRF 融合函数。

        ⚠️ **`similarity` 是 加的**：融合**只该改排序，不该丢字段** ——
        它此前把向量那一路的 `similarity` 丢在这里，于是引用帧里那一格**恒为 `null`**
        （`answer_with_citations` 早就诚实地写着"这里会取到 `None`"）。
        ⚠️ **只向量那一路有相似度** ⇒ BM25 独中的文档**诚实地取 `None`**，⛔ 不许编一个分出来。
        """
        rrf_scores = defaultdict(float)
        doc_info = {}

        for rank, (doc_id,content, source, similarity) in enumerate(vector_docs, start=1):
            rrf_scores[content] += 1.0 / (self.rrf_k + rank)
            doc_info[content] = {"id": doc_id, "content": content, "source": source,
                                 "from": "vector", "similarity": similarity}

        for rank, (doc_id, content, source, bm25_score) in enumerate(bm25_docs, start=1):
            rrf_scores[content] += 1.0 / (self.rrf_k + rank)
            if content in doc_info:
                # ⛔ 别在这里重写 `id` / `similarity` —— 两路都中的文档以**向量那一路**为准
                #    （它有相似度；且 `id` 是同一个文档的同一个 id）
                doc_info[content]["from"] = "both"
            else:
                doc_info[content] = {"id": doc_id, "content": content, "source": source,
                                     "from": "bm25", "similarity": None}

        sorted_contents = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for content, rrf_score in sorted_contents[:top_k]:
            info = doc_info[content]
            info["rrf_score"] = round(rrf_score, 4)
            results.append(info)

        return results


# 预定义几套常用配置
def create_fast_pipeline() -> RAGPipeline:
    """快速检索：不用查询改写和重排序，速度最快"""
    return RAGPipeline(enable_rewrite=False, enable_expand=False, enable_bm25=True, enable_rerank=False)

def create_accurate_pipeline() -> RAGPipeline:
    """精确检索：启用查询改写和重排序，效果最好"""
    return RAGPipeline(enable_rewrite=True, enable_expand=False, enable_bm25=True, enable_rerank=True)

def create_accurate_norerank_pipeline() -> RAGPipeline:
    """精确检索：启用查询改写，但【不做】Cross-Encoder 重排序（因此不依赖 torch）。

    修：原 docstring 与上面 `create_accurate_pipeline` **逐字相同**
       （"启用查询改写和重排序"）—— 而本函数的 `enable_rerank=False`。
       函数名 + 实参都写着"no rerank"，只有 docstring 说反了。
    """
    return RAGPipeline(enable_rewrite=True, enable_expand=False, enable_bm25=True, enable_rerank=False)

def create_full_pipeline() -> RAGPipeline:
    """完整管线：启用所有优化，效果最佳但延迟最高"""
    return RAGPipeline(enable_rewrite=True, enable_expand=True, enable_bm25=True, enable_rerank=True)