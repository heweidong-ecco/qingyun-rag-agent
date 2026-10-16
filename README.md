# 青云精工 · RAG + Agent API 服务

一个**生产级**的检索增强生成（RAG）与 Agent 编排后端 ——
混合检索、重排序、查询改写、引用溯源，外加 LangGraph Agent、MCP 工具与**按用户计量**的成本控制。

> ### 🔗 在线演示
> **<https://qingyun.paleoix.com>** —— 不是截图，是**真在跑的服务**：注册/登录后可以真上传文档、真检索、真提问。
>
> ⚠️ 免费额度有限（embedding 走的是免费 key），跑满会返回 429 —— 那是**成本闸在工作**，不是坏了。

---

## 它能干什么

| 能力 | 说明 |
|---|---|
| **混合检索** | 向量（pgvector） + BM25 关键词 + **RRF 融合**；支持查询改写与查询扩展 |
| **重排序** | Cross-Encoder 精排（可选链路，默认不装重排依赖以保持镜像小） |
| **引用溯源** | 答案带 `[来源 n]` 标注，与返回的 `sources[]` 一一对应 |
| **Agent 编排** | **LangGraph** 多链（工具编排 / 计划-执行 / 记忆 / MCP）；带**人工审批点**与检查点续跑 |
| **工具生态** | **MCP** 协议；本地工具 + 联网检索 + **独立容器里的代码执行器** |
| **长期记忆** | mem0 驱动的跨会话记忆，写入与检索都纳入计量 |
| **多用户隔离** | 检索、写入、删除**全部按 `requested_by` 过滤**；另有全站共享的公共语料层 |
| **成本控制** | 三层闸：**角色日预算** · **会话预算** · **全站熔断**；每次调用逐条记账 |

## 快速开始

```bash
git clone <本仓地址> && cd qingyun-rag-agent
cp .env.example .env          # 填上 DASHSCOPE_API_KEY 等（见 docs/契约/环境变量.md）
docker compose up -d
```

起来之后：

| 地址 | 是什么 |
|---|---|
| <http://localhost:8000/> | 总览首页（企业仪表盘） |
| <http://localhost:8000/kb> | 知识库：上传 / 列出 / 删除 |
| <http://localhost:8000/rag> | 检索与问答 |
| <http://localhost:8000/agent> | Agent 对话（多链） |
| <http://localhost:8000/ops> | 运行观测（限流 / 配额 / 工具健康） |
| <http://localhost:8000/docs> | OpenAPI |

## 技术栈

**后端** FastAPI · PostgreSQL + **pgvector**（`hnsw` 索引） · Redis · LangChain / LangGraph · MCP · mem0
**前端** 原生 HTML/CSS/JS（无构建步骤）· 9 个页面
**质量** pytest（离线 + 真库两档）· CI · 提交前结构门

## 值得一看的三处

1. 🔴 **向量检索的静默丢结果** —— 近似索引**先扫索引、后套 `WHERE`** ⇒ 带过滤的检索会**少于 `LIMIT`**。
   本仓的做法：每次 ANN 查询前 `SET LOCAL … iterative_scan`，并**在启动时拒绝 pgvector < 0.8**（否则那个开关会静默失效）。
   ⇒ `app/core/db.py`
2. 🔴 **成本闸的桶必须与账本的桶一致** —— 记账落一个 `thread_id`、闸查另一个的话，
   闸会**恒看到 0**（= 那道闸形同虚设）。⇒ `app/billing/token_tracker.py` · `app/rag/rag_pipeline.py`
3. 🔴 **工具执行不在应用进程里** —— `execute_python` 走**独立容器**，应用**不碰 `docker.sock`**。
   ⇒ `app/tools/code_executor.py` · `docker-compose.yml` 的 `executor`

## 目录

```
app/          应用本体（core · routing · access · billing · agent · rag · tools）
  <组>/specs/   每个模块的规格 —— 与模块【同目录】
docs/
  契约/        接口 · 环境变量 · 表结构（对外承诺）
  原理/        架构 · 请求流 · 依赖方向
  说明/        部署 · 运维 · 测试
  规范/        开发规范（含判据纪律）
frontend/     前端的设计文档与形态稿（⛔ 真前端代码在 app/static/）
testdata/     示例语料
```

## 许可

见 `LICENSE`。
