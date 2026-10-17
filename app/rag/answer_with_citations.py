"""
带引用溯源的答案生成模块
"""
from langchain_core.prompts import ChatPromptTemplate

#    它把 `AIMessage` 剥成 `str`，`usage_metadata`（`record_from_response` 的唯一判据，
#）随之丢光 ⇒ 记账静默收不到数。已删。
from billing.token_tracker import record_from_response

CITATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个严谨的问答助手。请严格根据以下上下文回答用户的问题。

**引用规则（必须遵守）：**
1. 当你使用上下文中的某条信息时，必须在句末标注来源编号，格式为 `[来源:X]`，其中 X 是文档编号。
2. 如果一句话使用了多个来源，标注为 `[来源:X, Y]`。
3. 不要编造任何上下文以外的信息。如果上下文不足以回答问题，请直接说“根据现有资料，无法回答”。

**上下文文档：**
{context}

**用户问题：**
{question}

**回答（请严格遵循引用规则）：**
"""),
    ("user", "{question}")
])

def generate_answer_with_citations(
    contexts: list[dict], question: str, llm, *,
    user_name: str, thread_id: str,
) -> tuple[str, list[dict]]:
    """
    生成带引用标注的答案，并返回来源信息列表。

    参数:
        contexts: 检索到的文档块列表，每个包含 id, content, source 等字段
        question: 用户问题
        llm: 语言模型实例
        user_name: **必填** —— 记账归属。不给默认值：
            给了就等于允许「静默记成 `"unknown"`」= 假记账（明文的反例）。
        thread_id: **必填** —— 会话标识（B8 会话级上限用）。**不给了，理由与 `user_name` 逐字同款**：
            原先是 `thread_id: str = "default"`，而 `/rag/search` 的调用点**根本没传**
            ⇒ 每一个调用者的账都落进 **`"default"` 这一个桶**，
            而会话闸查的是**调用者传的那个** ⇒ **闸恒看到 0**。

    返回:
        (带引用标注的答案文本, 来源信息列表)
    """
    # 构建带编号的上下文
    context_text_parts = []
    sources = []
    for i, doc in enumerate(contexts, start=1):
        context_text_parts.append(f"[文档{i}来源：{doc.get('source', '未知')}]\n{doc['content']}")
        sources.append({
            #  契约 A：与 `api_v1_rag.py` 的流式出口**同一条口径**
            #    （同一个 `i` / 全文 + 预览并存）—— 非流式链的消费者拿到的形状必须一样。
            #    改一边忘另一边 ⇒ 两个出口的形状悄悄分叉，而两边各自的用例都是绿的。
            "index": i,
            "id": doc.get("id"),
            "source": doc.get("source", "未知"),
            "content": doc["content"],
            "content_preview": doc["content"][:100],
            #：形状与流式出口**逐字同构**，这条键也必须跟着加。
            # 本链的 `contexts` 来自 `rag_pipeline` 的 RRF 融合结果，那里**没有**
            #    `similarity`（候选字典只有 id/content/source/from/rrf_score）
            "similarity": doc.get("similarity"),
        })
    
    context_text = "\n\n".join(context_text_parts)
    
    # 调用 LLM 生成
    #    会让 `usage_metadata` 丢光，`record_from_response` 随即**静默跳过**（一笔不记）。
    #    改为直调 `invoke`（与 `rag_pipeline.search_async` 的普通生成分支同款）。
    messages = CITATION_PROMPT.format_messages(context=context_text, question=question)
    response = llm.invoke(messages)
    # 紧贴调用之后记账：钱已经花了，不放在 return 之前靠"顺序碰巧"。
    record_from_response(
        llm, response, "answer_generation",
        user_name=user_name, thread_id=thread_id,
    )

    return response.content, sources