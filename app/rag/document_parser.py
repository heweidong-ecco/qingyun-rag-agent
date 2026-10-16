"""
多格式文档解析模块
支持 PDF、Word、Markdown、HTML 的文本提取。
"""
import fitz  # PyMuPDF
import pdfplumber
from typing import List, Dict


def extract_text_with_layout(pdf_path: str) -> List[Dict]:
    """
    按阅读顺序提取 PDF 文本，自动处理双栏排版。
    返回: [{"page": 1, "text": "按阅读顺序排列的文本", "tables": [...]}, ...]
    """
    doc = fitz.open(pdf_path)
    pages_content = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        # 获取页面中所有文本块（带坐标信息）
        blocks = page.get_text("dict")["blocks"]
        
        # 按阅读顺序排序文本块（先从上到下，同一水平线从左到右）
        text_blocks = []
        for block in blocks:
            if block["type"] == 0:  # 文本块
                # 提取块内所有行的文本
                block_text = ""
                for line in block["lines"]:
                    line_text = "".join([span["text"] for span in line["spans"]])
                    block_text += line_text + "\n"
                text_blocks.append({
                    "text": block_text.strip(),
                    "bbox": block["bbox"]  # 边界框: (x0, y0, x1, y1)
                })
        
        # 当前在用：简化版（lambda 排序）按 y 坐标排序（从上到下），同行的按 x 坐标排序（从左到右）
        text_blocks.sort(key=lambda b: (round(b["bbox"][1] / 10) * 10, b["bbox"][0]))

        ordered_text = "\n\n".join([b["text"] for b in text_blocks])
        
        pages_content.append({
            "page": page_num + 1,
            "text": ordered_text,
        })

    doc.close()
    return pages_content


def extract_tables_with_pdfplumber(pdf_path: str) -> List[Dict]:
    """
    使用 pdfplumber 提取 PDF 中的表格。
    返回: [{"page": 1, "tables": [["单元格1", "单元格2"], ...]}, ...]
    """
    tables_data = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            if tables:
                tables_data.append({
                    "page": page_num + 1,
                    "tables": tables
                })
    
    return tables_data


def parse_pdf_complete(pdf_path: str) -> List[Dict]:
    """
    综合解析 PDF：提取有序文本 + 表格。
    返回: [{"page": 1, "text": "...", "tables": [...]}, ...]
    """
    # 1. 提取文本（处理双栏）
    text_pages = extract_text_with_layout(pdf_path)
    
    # 2. 提取表格
    table_pages = extract_tables_with_pdfplumber(pdf_path)
    
    # 3. 合并结果
    table_dict = {t["page"]: t["tables"] for t in table_pages}
    
    for page_data in text_pages:
        page_num = page_data["page"]
        page_data["tables"] = table_dict.get(page_num, [])
    
    return text_pages


# ==================== 文本块排序辅助函数 ====================
def sort_blocks_by_reading_order(blocks: List[Dict]) -> List[Dict]:
    """
    将文本块按阅读顺序排序（从上到下，从左到右）。
    这是处理双栏排版的核心逻辑。

    核实（§三·B1）：**当前无任何调用方**（`grep -rn` 全仓 0 次）。
       ⇒ **保留**（它是双栏排版的核心能力，删了要用得重写），但**不要以为它在生效**。
         如果哪天要用，先确认调用它的那条解析路径也真的跑过。
    """
    # 按 y 坐标分组（同一水平线的文本块归为一组）
    y_tolerance = 5  # 5 像素以内的视为同一行
    sorted_blocks = sorted(blocks, key=lambda b: b["bbox"][1])
    
    rows = []
    current_row = [sorted_blocks[0]]
    current_y = sorted_blocks[0]["bbox"][1]
    
    for block in sorted_blocks[1:]:
        if abs(block["bbox"][1] - current_y) <= y_tolerance:
            current_row.append(block)
        else:
            # 当前行结束，按 x 坐标排序当前行
            current_row.sort(key=lambda b: b["bbox"][0])
            rows.extend(current_row)
            current_row = [block]
            current_y = block["bbox"][1]
    
    # 处理最后一行
    current_row.sort(key=lambda b: b["bbox"][0])
    rows.extend(current_row)
    
    return rows
# ==================== 扩展：将表格转为结构化文本存入知识库:辅助函数 ====================
def table_to_text(table_data: list) -> str:
    """将表格转换为可读的文本描述，用于存入知识库。

    核实（§三·B2）：**当前无任何调用方**（`grep -rn` 全仓 0 次）。
       ⇒ **保留**（表格→文本是入库链路上有价值的一环），但**不要以为它在生效**。
    """
    if not table_data:
        return ""
    
    lines = []
    header = table_data[0]
    for row in table_data[1:]:
        row_desc = []
        for col, value in enumerate(row):
            if value and col < len(header):
                row_desc.append(f"{header[col]}：{value}")
        if row_desc:
            lines.append("；".join(row_desc))
    
    return "。\n".join(lines)

# ==================== 上面的都是处理PDF文件代码 包括辅助函数 ====================

# ==================== Word (.docx) 解析 ====================
# ==================== Word (.docx) 解析 ====================
def parse_docx(file_path: str) -> str:
    """
    解析 Word 文档，提取所有段落的文本。
    返回完整的纯文本内容。
    """
    from docx import Document
    
    doc = Document(file_path)
    paragraphs = []
    for para in doc.paragraphs:
        if para.text.strip():
            paragraphs.append(para.text.strip())
    
    return "\n\n".join(paragraphs)


# ==================== Markdown (.md) 解析 ====================
def parse_markdown(file_path: str) -> str:
    """
    解析 Markdown 文件。
    策略一：直接返回原始文本（保留 Markdown 标记，适合代码/技术文档检索）。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def parse_markdown_to_plain(file_path: str) -> str:
    """
    解析 Markdown 文件。
    策略二：转换为纯文本（去除所有 Markdown 标记）。
    适合需要去除格式符号、只保留文字内容的场景。

    核实（§三·B3）：**当前无任何调用方** ——
       全仓唯一出现处是 `parse_markdown` 里一句**注释**：
           return parse_markdown(file_path)  # 或 parse_markdown_to_plain
       ⇒ **保留**（是 `parse_markdown` 的备用策略），但**别以为它在生效**。
    """
    import markdown
    from bs4 import BeautifulSoup
    
    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    
    # 将 Markdown 转为 HTML，再提取纯文本
    html = markdown.markdown(md_text)
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text()


# ==================== HTML 解析 ====================
def parse_html(file_path: str) -> str:
    """
    解析 HTML 文件，提取纯文本内容。
    自动去除脚本、样式、导航等噪声。
    """
    from bs4 import BeautifulSoup
    
    with open(file_path, "r", encoding="utf-8") as f:
        html_text = f.read()
    
    soup = BeautifulSoup(html_text, "lxml")
    
    # 移除脚本和样式标签
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    
    # 提取纯文本
    text = soup.get_text()
    
    # 清理多余空白
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(lines)


# ==================== 统一入口 ====================
def parse_document(file_path: str) -> str:
    """
    根据文件扩展名自动选择解析器，返回纯文本内容。
    支持的格式：.pdf, .docx, .md, .html
    """
    ext = file_path.lower().split(".")[-1]
    
    if ext == "pdf":
        pages = parse_pdf_complete(file_path)
        return "\n\n".join([p["text"] for p in pages])
    elif ext == "docx":
        return parse_docx(file_path)
    elif ext == "md":
        return parse_markdown(file_path)  # 或 parse_markdown_to_plain
    elif ext == "html":
        return parse_html(file_path)
    else:
        raise ValueError(f"不支持的文档格式: {ext}")
