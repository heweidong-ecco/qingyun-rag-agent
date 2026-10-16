"""
文本分块模块
统一管理文档分块策略，支持按文档类型选择不同的分块参数。
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import List


# ==================== 默认分块配置 ====================
#    全仓零引用（判据：`grep -rn "DEFAULT_CHUNK_SIZE\|DEFAULT_CHUNK_OVERLAP" . --exclude-dir=venv`），
#    且与下面 `CHUNK_CONFIGS["default"]` 里的 500/50 **重复** —— 同一个数两个来源必然漂移。
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "，", " ", ""]

# 针对不同文档类型的推荐配置
CHUNK_CONFIGS = {
    "default": {"chunk_size": 500, "chunk_overlap": 50},
    "technical": {"chunk_size": 600, "chunk_overlap": 60},       # 技术文档 · 手册（: 500/50 → 600/60）
    "legal": {"chunk_size": 800, "chunk_overlap": 100},          # 法律合同
    "report": {"chunk_size": 800, "chunk_overlap": 100},         # 财报、报告
    "article": {"chunk_size": 1000, "chunk_overlap": 200},       # 长篇文章
    "faq": {"chunk_size": 300, "chunk_overlap": 50},             # FAQ 问答对（新增）
}


def get_text_splitter(doc_type: str = "default") -> RecursiveCharacterTextSplitter:
    """
    根据文档类型获取对应的文本分割器。
    
    参数:
        doc_type: 文档类型，可选: default, technical, legal, report, article, faq
                  **未知档位会静默回落 `default`**（下一行的 `.get(..., default)`）——
                  ⇒ 端点那一层会**先拒掉**未知档位，别指望这里报错。
    """
    config = CHUNK_CONFIGS.get(doc_type, CHUNK_CONFIGS["default"])
    return RecursiveCharacterTextSplitter(
        chunk_size=config["chunk_size"],
        chunk_overlap=config["chunk_overlap"],
        separators=DEFAULT_SEPARATORS,
    )


def split_text(text: str, doc_type: str = "default") -> List[str]:
    """
    对文本进行分块。
    
    参数:
        text: 输入的文本
        doc_type: 文档类型，用于选择分块策略
    返回:
        分块后的文本列表
    """
    splitter = get_text_splitter(doc_type)
    return splitter.split_text(text)


def split_text_with_filter(text: str, doc_type: str = "default", min_length: int = 20) -> List[str]:
    """
    分块并过滤掉过短的无效块。
    
    参数:
        text: 输入的文本
        doc_type: 文档类型
        min_length: 最小有效块长度（字符数）
    返回:
        过滤后的有效文本块列表
    """
    chunks = split_text(text, doc_type)
    return [c for c in chunks if len(c.strip()) >= min_length]