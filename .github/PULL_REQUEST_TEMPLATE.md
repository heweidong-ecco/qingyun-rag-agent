<!--
写法：不适用的条目直接删掉，**不要留空模板**；每节都要有实际内容或明确的「不适用」。
-->

## 这个 PR 做了什么

<!-- 一句话说清变更。关联到某个 issue 的把编号写上。 -->

-

## 一、测试

- [ ] **CI 通过**（`.github/workflows/ci.yml`：`python -m pytest . -m "not integration and not needs_db"`）
- [ ] **本机复跑过**：<命令 + 实际输出；没跑就不勾>

> ⚠️ **带库的用例不进 CI**（CI 里没有 Postgres）。凡触及检索 / 入库 / 记账的改动，
> 请在**本机**跑一遍带库的那部分，并把命令与输出贴上来。

## 二、是否改 Prompt / 工具 / 记忆

| 类别 | 位置 |
|---|---|
| **Prompt** | `app/rag/answer_with_citations.py` · `app/rag/rag_pipeline.py` · `app/rag/query_rewriter.py` |
| **工具** | `app/tools/` 下的 `mcp_server.py` · `mcp_tool_factory.py` · `simple_tools.py` · `search_tools.py` |
| **记忆** | `app/agent/memory_store.py` |

> **任一改了就写清改了什么、为什么改。** 这三类直接改变系统对外行为 —— 改而不说，
> 等于让下一个人对着行为变化猜原因。
>
> ⚠️ 这三类改动**都要连带跑一轮检索/问答**再贴结果（只跑单测盖不住它们）。

## 三、接口 / 契约

- [ ] **不改对外接口**（纯内部重构 / 文档 / 测试）
- [ ] **改了** —— 已同步更新 `docs/契约/`（接口 · 环境变量 · 表结构）
- [ ] 改了 `app/schema.sql` 或建表逻辑 —— <怎么迁；老库怎么办>

## 四、回滚

<怎么退回去；有没有不可逆的部分（数据 / 索引 / 已发出的凭据）>
