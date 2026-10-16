# `app/rag/reranker.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **仅开发机可用** |
| **对外提供** | `get_reranker()` · `rerank()` · `rerank_async()` |
| **谁在用** | `rag_pipeline.py`（`accurate` / `full` 模式） |

## ✅ 做了什么

- `BAAI/bge-reranker-v2-m3` Cross-Encoder 重排序
- ⭐ **真懒加载** —— `from sentence_transformers import CrossEncoder` 写在 `get_reranker()` **函数体内**
  （`:14`），不在模块顶层 ⇒ **缺这个依赖时应用整体仍能启动**（2026-08-17 修复记录 #20）
- 模型名**硬编码**在 `:17` —— ⚠️ **⛔ 没有 `.env` 开关**：原先 `.env.example` 里那个 `RERANKER_MODEL_NAME` 是**死键**（全仓零引用），**2026-10-15 已删**。要换模型只能改代码。

## 🟡 做到哪 / 缺什么

- ⬜ **容器里跑不了**（见下）
- ⬜ **零测试覆盖**（`docs/说明/测试.md` §六 **#2**）
- ⬜ 模型首次调用要下 **2.3 GB**（需网络）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「重排序做完了，代码完整」 | 🔴 **只在开发机能跑** —— `app/Dockerfile:73` 构建期用 `grep -vE` **裁掉了 torch 系** ⇒ **容器里 `ImportError`** |
| 「想换重排模型 ⇒ 去 `.env` 里找那个键」 | 🔴 **`.env` 里没有这个开关** —— 模型名**硬编码**在 `:17`（原先那个 `RERANKER_MODEL_NAME` 是死键，2026-10-15 已删）|
| 「镜像里没装是因为 `requirements.txt` 裁了」 | 🔴 **不是** —— `requirements.txt` **一个字没动**（`sentence-transformers` 等 5 个包**都在**）；**是 Dockerfile 构建期过滤的** |

⇒ **要在容器里用重排序，得改 `app/Dockerfile` 那行** —— ⚠️ **但镜像会从 1.28 GB 涨回 6+ GB**。

## 关联

（构建期裁依赖）· （不去撞 torch）· `ROADMAP` 功能现状表「Cross-Encoder 重排序 🟡」
