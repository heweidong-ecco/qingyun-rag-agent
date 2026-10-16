# `app/agent/` —— Agent 编排

> 📇 **本层 = 索引表 + 主要内容**：agent 进到这一层，先读这份；不够再往 `specs/` 转。

## 📇 本目录索引

**职责**：编排 · 计划 · 审批 · 检查点 · 长期记忆

| 模块 | 它的 spec |
|---|---|
| `agent_checkpointer.py` | `specs/agent_checkpointer.md` |
| `agent_graph.py` | `specs/agent_graph.md` |
| `agent_graph_advanced.py` | `specs/agent_graph_advanced.md` |
| `agent_graph_advanced_learning.py` | `specs/agent_graph_advanced_learning.md` |
| `approval_audit.py` | `specs/approval_audit.md` |
| `memory_store.py` | `specs/memory_store.md` |
| `pending_approvals.py` | `specs/pending_approvals.md` |
| `plan_constraints.py` | ⛔ **缺** |
| `plan_execute.py` | `specs/plan_execute.md` |
| **`specs/`** | 本组模块的规格 —— **与模块同名**（见 `specs/CLAUDE.md`） |

## 🔴 本层特有的规矩

- 🔴🔴 **本层 =【外部知识线】；⛔ 不接 RAG —— 这是设计，不是欠账**（2026-10-12 需求方口述补记）
  **五条图绑的都是同一套 7 个通用工具**（`calculator` / `date_*` / `json_extract` / `stats` /
  `web_search` / `execute_python`），**没有一个查我们自己的文档库**。
  **为什么不接**（需求原话）：「如果给 `/agent` 接上 rag，**意图识别的控制是非常难的，
  没办法 100% 保证不出错** —— 给了 `tools-rag` 这个工具，**就会有错的风险**：
  query 本来是查外部，**结果查了内部资料污染了**。所以做了**两个单独模块**。」
  ⇒ **要内部资料，去 `/rag` 那条线**（前端对应**另一个页面**）。
  📄 **完整理由 · 两条线职责表 · 可打印判据** ⇒ `docs/原理/架构.md` §3.3 那个方框。
  ⚠️ **别把它读成"漏了没做"**（`docs/待办总表.md` 里**没有**这条）；
  ⚠️ **图里唯一的"检索"是 Mem0 记忆检索** —— **⛔ 不是文档库**。

- 🔴 **改完一个模块 ⇒ 更新 `specs/<同名>.md`**（`pre-commit-gates.py` 对**新增模块**硬拦）
- 🔴 **导入写绝对形式、根是 `app/`**：`from core.config import X`（⛔ 不是 `from app.core...`）
- ⚠️ 本组的 `.py` **已不在 `app/` 根** ⇒ 凡 `dirname(__file__)` 算路径的**都已跟着搬**，
  ⛔ **别再把它和它的数据文件拆开**

## 📍 往上读

- `../CLAUDE.md`（`app/`）· 仓库根 `CLAUDE.md`（全局约定）
