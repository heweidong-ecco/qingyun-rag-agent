# CLAUDE.md

> 给 **AI 编码工具**看的入口（Claude Code 认 `CLAUDE.md`，Codex 等认 `AGENTS.md`）。
> ⚠️ `AGENTS.md` 是本文件的**符号链接** ⇒ **只有一份内容**，⛔ 不是副本。

## 这是什么

一个 **RAG（检索增强生成）+ Agent API 服务** —— 混合检索、重排序、查询改写、引用溯源，
外加 LangGraph Agent / MCP 工具 / 长期记忆与成本控制。

## 怎么跑

见 `README.md`。一句话：**`docker compose up -d`**，库走 PostgreSQL（**带 pgvector**）。

## 代码怎么读

| 想找什么 | 去哪儿 |
|---|---|
| 应用本体（7 个模块组 + 测试 + 前端） | `app/` |
| 接口 / 环境变量 / 表结构（**对外承诺**） | `docs/契约/` |
| 架构（模块 · 请求流 · 数据流 · 依赖） | `docs/原理/` |
| 部署 · 运维 · 测试怎么跑 | `docs/说明/` |
| 开发规范（含判据纪律） | `docs/规范/` |

⚠️ **每个模块的规格与它【同目录】**：`app/<组>/<模块>.py` ⇔ `app/<组>/specs/<模块>.md`。
⭐ **最该读的是每份 spec 的「看代码会误判的地方」那一节** —— 前两节读代码也能推出来，只有那节推不出来。

## 两条别踩的

- 🔴 **测试**：`python -m pytest app/ -m "not integration and not needs_db"`（`app/` 是 `sys.path` 的根 ⇒ 导入写 `from core.config import …`）。
- 🔴 **向量检索**：库里那个索引是 **`hnsw`**；带 `WHERE` 过滤的检索**必须**先开 `iterative_scan`，
  否则近似索引**会静默少回结果**（`app/core/db.py::enable_ann_iterative_scan`）。
