# `app/rag/document_parser.py`

## ✅ 做了什么

**多格式文档解析**：PDF / Word / Markdown / HTML 的文本提取。
依赖 **PyMuPDF（`fitz`）** + **`pdfplumber`**；`extract_text_with_layout` 按阅读顺序取 PDF 文本、
**自动处理双栏**。

## 🟡 做到哪 / 缺什么

- **2 处产品代码引用 · 0 个测试文件提到**。
- 🔴 **三条函数都"当前无任何调用方"**（2026-09-20 核实 · 审计 §三·B1 / B2 / B3）：
  `extract_text_with_layout` · `extract_text_from_word` · 另一条 HTML 提取。
  ⇒ 它们**不是死代码**（是停放的能力），但**⛔ 别以为上传链路在用它们**。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **"函数在" ≠ "有人调"** —— 本文件整体**不在上传链路上**（那条链走的是
   `app/rag/document_preprocessor.py`，且只吃**已解析的文本**）。
   📌 判据：`git grep -n "document_parser" -- app | grep -v specs` ⇒ 看有几处**真 import**。
2. ⚠️ **两个 PDF 库并存**（`fitz` 与 `pdfplumber`）—— ⛔ 别以为有一个是遗留：
   它们**分工不同**（版式/表格 vs 通用抽取）。删任一个前先核**它在哪条路径上**。
3. ⚠️ **返回值是 `List[Dict]`（每页一条）**，⛔ 不是一整段字符串 ——
   下游要自己拼（这个形状差异是本文件与其它"解析器"最容易混的地方）。
4. ⚠️ **本文件**没有**真正的测试**（`git grep -l document_parser -- app/tests` ⇒ 0 个文件）
   ⇒ 改它**没有守卫兜**，动之前先手工验证。

## 关联

- `app/rag/specs/document_preprocessor.md` —— 下游（解析完的文本交给它清洗）
- `docs/待办总表.md` —— 「B1/B2/B3 无调用方」那几条的登记处
- `docs/契约/接口契约.md` —— 上传接口的**对外形状**
