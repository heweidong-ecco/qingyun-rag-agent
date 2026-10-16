# `app/rag/specs/` —— 本组模块的规格

> 📇 **一份 spec 对应一个模块，且【与模块同名】**。
> 判据：`app/rag/<模块>.py` ⇔ `app/rag/specs/<模块>.md`

## 📇 本目录索引

| spec | 对应 |
|---|---|
| `answer_with_citations.md` | `app/rag/answer_with_citations.py` 的 spec |
| `bm25_index.md` | `app/rag/bm25_index.py` 的 spec |
| `chunker.md` | `app/rag/chunker.py` 的 spec |
| `document_parser.md` | `app/rag/document_parser.py` 的 spec |
| `document_preprocessor.md` | `app/rag/document_preprocessor.py` 的 spec |
| `embedding_client.md` | `app/rag/embedding_client.py` 的 spec |
| `hybrid_search.md` | `app/rag/hybrid_search.py` 的 spec |
| `query_rewriter.md` | `app/rag/query_rewriter.py` 的 spec |
| `rag_pipeline.md` | `app/rag/rag_pipeline.py` 的 spec |
| `reranker.md` | `app/rag/reranker.py` 的 spec |

## 🔴 写法（⛔ 别自由发挥）

四段式 —— 模板与说明见 `app/specs/README.md`：
`✅ 做了什么` / `🟡 做到哪缺什么` / **`⚠️ 看代码会误判的地方 ⭐`** / `关联`

⭐ **重头是第三段** —— 前两段读代码也能推出来，**只有第三段推不出来**。
内容要来自**代码里的 ⚠️/🔴 注释**与 `、`DEC-*`，⛔ **不是读一遍代码的转录**
（本仓立场：**转录即负债**）。

## 📍 往上读
- `../CLAUDE.md`（本组）· `app/specs/README.md`（模板）· 仓库根 `CLAUDE.md`
