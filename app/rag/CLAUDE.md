# `app/rag/` —— 检索与生成

> 📇 **本层 = 索引表 + 主要内容**：agent 进到这一层，先读这份；不够再往 `specs/` 转。

## 📇 本目录索引

**职责**：检索 · 解析 · 分块 · 向量 · 重排 · 引用

| 模块 | 它的 spec |
|---|---|
| `answer_with_citations.py` | `specs/answer_with_citations.md` |
| `bm25_index.py` | `specs/bm25_index.md` |
| `chunker.py` | `specs/chunker.md` |
| `document_parser.py` | `specs/document_parser.md` |
| `document_preprocessor.py` | `specs/document_preprocessor.md` |
| `embedding_client.py` | `specs/embedding_client.md` |
| `hybrid_search.py` | `specs/hybrid_search.md` |
| `query_rewriter.py` | `specs/query_rewriter.md` |
| `rag_pipeline.py` | `specs/rag_pipeline.md` |
| `reranker.py` | `specs/reranker.md` |
| **`specs/`** | 本组模块的规格 —— **与模块同名**（见 `specs/CLAUDE.md`） |

## 🔴 本层特有的规矩

- 🔴 **改完一个模块 ⇒ 更新 `specs/<同名>.md`**（`pre-commit-gates.py` 对**新增模块**硬拦）
- 🔴 **导入写绝对形式、根是 `app/`**：`from core.config import X`（⛔ 不是 `from app.core...`）
- ⚠️ 本组的 `.py` **已不在 `app/` 根** ⇒ 凡 `dirname(__file__)` 算路径的**都已跟着搬**，
  ⛔ **别再把它和它的数据文件拆开**

## 📍 往上读

- `../CLAUDE.md`（`app/`）· 仓库根 `CLAUDE.md`（全局约定）
