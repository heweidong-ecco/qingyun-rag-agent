"""
文档预处理管道
使用的可配置、可扩展的正则表达式清洗规则库
配置文件：app/rag/cleanup_rules.json
对解析后的原始文本进行清洗、去重、标准化。
"""

import json
import os
from typing import List, Dict
# =========== 清洗规则加载器 配置文件：app/rag/cleanup_rules.json ===========
class CleanupRuleLoader:
    """从配置文件加载清洗规则"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            # 默认配置文件路径（与当前文件同级目录）
            config_path = os.path.join(os.path.dirname(__file__), "cleanup_rules.json")
        self.config_path = config_path
        self._rules_cache = {}

    def load_rules(self, domain: str = "default") -> List[Dict]:
        """
        加载指定领域的清洗规则。

        参数:
            domain: 领域名称，如 "default", "legal", "medical"
        返回:
            规则列表，每条规则包含 pattern, replacement, description
        """
        # 如果已缓存，直接返回
        if domain in self._rules_cache:
            return self._rules_cache[domain]

        with open(self.config_path, "r", encoding="utf-8") as f:
            all_rules = json.load(f)

        # 获取指定领域的规则，如果不存在则使用默认规则
        rules = all_rules.get(domain, all_rules.get("default", []))
        self._rules_cache[domain] = rules
        return rules

    def apply_rules(self, text: str, domain: str = "default") -> str:
        """对文本应用指定领域的所有清洗规则"""
        rules = self.load_rules(domain)
        for rule in rules:
            text = re.sub(rule["pattern"], rule["replacement"], text)
        return text

# =========== 文档预处理管道，使用的可配置、可扩展的正则表达式清洗规则库 ===========
# =========== 对解析后的原始文本进行清洗、去重、标准化。===========
import re
from typing import List

class DocumentPreprocessor:
    """文档预处理管道，按顺序执行清洗、去重、标准化操作"""

    def __init__(self, domain: str = "default"):
        self.steps = [
            self.clean_whitespace,
            self.remove_noise_markers,
            self.normalize_text,
            self.deduplicate_lines,
        ]
        self.rule_loader = CleanupRuleLoader()
        self.domain = domain

    def process(self, text: str) -> str:
        """执行完整的预处理管道"""
        for step in self.steps:
            text = step(text)
        return text.strip()

    # ==================== 清洗步骤 ====================
    def clean_whitespace(self, text: str) -> str:
        """清理多余空白"""
        # 将多个连续换行替换为两个换行（保留段落分隔）
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 将多个连续空格替换为单个空格
        text = re.sub(r'[ \t]{3,}', ' ', text)
        # 去除每行首尾空白
        lines = [line.strip() for line in text.splitlines()]
        return '\n'.join(lines)

    # 2026-09-20 删掉了一份**空壳定义**（审计 §二）：
    #    这里原先有**两个** `remove_noise_markers` —— 上一份只有一句 docstring
    #    （`"""去除常见的噪声标记（新增 URL、邮箱过滤）"""`），下一份才是真实现。
    #    Python **后者胜出** ⇒ 当前行为是对的，**但改上面那份不会生效、也不会报错**。
    #    与 `CLAUDE.md` 修复记录 #7 同型（`db.py` 的 `get_db` 重复定义 ⇒ 写入不提交）。
    #    回归测试:app/tests/test_audit_fixes.py::test_no_duplicate_method_definitions_in_preprocessor
    def remove_noise_markers(self, text: str) -> str:
        """去除噪声标记（从配置文件加载规则）"""
        #
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        # 先应用配置文件中的正则规则
        text = self.rule_loader.apply_rules(text, self.domain)
        
        # 保留一些硬编码的基础规则（不需要配置化的）
        # 特殊情况处理:例如第二条去除控制字符（除了换行和制表符）
        # 控制字符规则 留在 Python 代码中更合适,
        # 理由：控制字符是不可、不可打印的，配置文件里写出来反而容易出错。
        # 这类规则是底层基础清洗，与领域无关，不应该频繁修改。放在代码中一目了然，维护成本更低。
        # 注意：raw string 中括号不需要双重转义（\\[ 是字面反斜杠+开括号，会损坏正则）
        #
        # 2026-09-21 注（登记 §三·3.7 / N14）：**这条规则不是死代码 —— 它在 `legal`/`medical` 域正在生效。**
        #    登记文件原写「这条规则**从未生效**」——**那是错的**，已按实测更正。
        #
        #    实测（三个域各跑一次，判据可复跑）：
        #      · `legal` / `medical` 域 —— **它正在干活**：这两个域的配置里【没有】图片规则，
        #        图片收敛**靠的就是这一条** ⇒ **删不得**（删了这两个域的图片处理会坏）
        #      · `default` 域 —— 配置里有一条**逐字等价**的规则（`cleanup_rules.json` 的 `default` 第 5 条），
        #        而且**先跑** ⇒ 这一条在 default 域是**多余的**
        #      · 但 `default` 域的 URL 规则（`https?://\S+`）**排在图片规则之前**，
        #        会把 `![alt](http://…)` 打成 `![alt](` —— **残废后两条图片规则都不匹配**
        #        ⇒ **`default` 域的 `http://` 图片才是真问题**（输出残留 `![图](`）
        #
        #    ⇒ 准确的一句话：**「在 legal/medical 生效；在 default 被 URL 规则打残」**。
        #    这条行为**有回归用例锁着**（`test_audit_fixes.py::test_markdown_image_rule_is_not_dead`）——
        #       有人删它，用例会红。
        #    要**修** `default` 域（把图片规则提到 URL 规则之前）会**改变文本预处理输出**
        #       影响已有语料 ⇒ 属**行为变更**，**待作者裁**（见 `docs/待办登记…` §三·3.7）。
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)  # Markdown图片语法
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)  # 去除控制字符
        return text

    def normalize_text(self, text: str) -> str:
        """标准化文本格式（含精细化半角规范化）"""
        # 1. 英文字母、数字、基本符号：全角转半角
        text = self._full_to_half(text)
    
        # 2. 中文标点符号：保持全角（保留语义和格式价值）
        #。！？；：“”‘’【】《》 这些不转换
    
        # 3. 统一省略号
        text = text.replace('…', '...').replace('⋯', '...')
    
        # 4. 将孤立的换行符合并（保留段落结构）
        lines = text.splitlines()
        result = []
        for line in lines:
            if line.strip():
                result.append(line.strip())
            elif result and result[-1] != '':
                result.append('')  # 保留一个空行作为段落分隔
        return '\n'.join(result)

    def _full_to_half(self, text: str) -> str:
        """
        精细化全角转半角：
        - 英文字母（Ａ-Ｚ → A-Z）
        - 数字（０-９ → 0-9）
        - 基本符号（！＂＃＄％＆＇＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～）
        但保留中文专用标点（。！？等）的全角形式。
        """
        result = []
        for char in text:
            code = ord(char)
            # 全角空格单独处理
            if code == 0x3000:
                result.append(' ')
            # 全角字母、数字、基本符号转半角
            elif 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            # 中文标点符号保持全角，不在此范围内，直接保留
            else:
                result.append(char)
        return ''.join(result)

    # ==================== 去重步骤 ====================
    def deduplicate_lines(self, text: str) -> str:
        """按行去重：保留第一次出现的行，去除后续完全相同的行"""
        seen = set()
        lines = text.splitlines()
        result = []
        for line in lines:
            stripped = line.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                result.append(line)
            elif not stripped:
                result.append(line)  # 保留空行
        return '\n'.join(result)

    def deduplicate_chunks(self, chunks: List[str], threshold: float = 0.85) -> List[str]:
        # 2026-10-08 改：`0.9` → **`0.85`** —— 对齐 `docs/说明/语料要求.md` §一#3
        #    定死的那个数（「语义去重阈值 0.85」）。
        #    **这一改动【当前零影响】** —— 本方法没有任何调用方（见 docstring 里那三条）。
        """
        对文档块进行语义去重。
        如果两个块的语义相似度超过阈值，只保留第一个。

        注意：这个方法需要 sentence-transformers。

        2026-09-20 核实（§三·B4）：**当前无任何调用方** ——
           唯一的调用点（`app/routing/api_v1_rag.py` 的批量入库路径）**已被注释掉**：
               # chunks = preprocessor.deduplicate_chunks(chunks)
           ⇒ 原 docstring 写「**只在批量入库时使用**」是**半真半假**：批量入库的代码还在，
             但那句调用**是注释状态** ⇒ **它现在根本没被使用**。已把该句改掉，避免误读。
           ⇒ **保留**（去重能力本身有价值），但**别以为它在生效**。
        """
        try:
            from sentence_transformers import SentenceTransformer, util
        except ImportError:
            print("sentence-transformers 未安装，跳过语义去重")
            return chunks

        if len(chunks) <= 1:
            return chunks

        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        embeddings = model.encode(chunks)
        keep = [True] * len(chunks)

        for i in range(len(chunks)):
            if not keep[i]:
                continue
            for j in range(i + 1, len(chunks)):
                if not keep[j]:
                    continue
                sim = util.cos_sim(embeddings[i], embeddings[j]).item()
                if sim >= threshold:
                    keep[j] = False

        return [chunk for idx, chunk in enumerate(chunks) if keep[idx]]