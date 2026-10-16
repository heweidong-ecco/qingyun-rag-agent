# `app/agent/memory_store.py`

## ✅ 做了什么

**长期记忆**（Mem0 · **本地模式**，数据落 `.mem0/`）：`search_user_memory` / 写入路径。

- 客户端构造用 **`Memory.from_config(dict)`**。
  ⚠️ **⛔ 不是 `Memory(dict)`** —— `mem0ai>=2.0` 的 `__init__` 只接受 `MemoryConfig` 对象。
- 🔴 **2026-10-11：LLM 与 embedder 换成 `langchain` provider** ——
  塞进去的是 `make_llm(..., callbacks=[_UsageRecorder()])` 与 `RecordingEmbeddings()`
  ⇒ **它们的 token 从此记在真实用户头上**（改前一个都不记，见「看代码会误判」第 5 条）。

## 🟡 做到哪 / 缺什么

- **5 处产品代码引用 · 1 个测试文件提到**（`app/tests/test_agent_repairs.py`）。
- 用它的链路：`app/agent/agent_graph_advanced.py` · `agent_graph_advanced_learning.py`
  · `app/routing/api_v1_agent.py` —— 即**二代/三代 Agent 在入口注入长期记忆**。
- 🔴 **2026-10-11：embedder 的模型名收口**（§二·2）—— 它与 `rag/embedding_client.py`
  **同一个变量** `EMBEDDING_MODEL_MAIN`（⛔ 别再在这里写第二份字面量）；`api_key` 也补上了
  `config` 回落（改前是**裸 `os.getenv`**）。

## ⚠️ 看代码会误判的地方 ⭐

0. 🔴🔴 **「Qdrant 存在哪」曾经**跟着 CWD 跑**，本机**已经裂成两份**（2026-10-14 · `①b`）

   改前那句是 **`"path": "./.mem0/qdrant"`（相对路径）**，而它在**模块导入时**就被
   `Memory.from_config(...)` 解析掉了 ⇒ **落点取决于当时从哪启动**。

   🔴 **实测（⛔ 不是推的）—— 两份独立的记忆，都还在**：
   · `app/.mem0/qdrant/collection/mem0`（2026-08-17）
   · `./.mem0/qdrant/collection/mem0`（2026-09-17）
   ⇒ 从 `app/` 启动写一份、从仓库根启动写另一份，而**没有任何门会报**。

   ⇒ 现在锚在**这个包自己的位置**上（`_MEM0_ROOT` = `app/.mem0`），**绝对路径、与 CWD 无关**；
     要换落点用 **`MEM0_DATA_DIR`**。
   ⚠️ **改动【不搬动已有数据】** —— `app/.mem0` 正是本仓一直以来的那个落点。
   🔴 **另一份（`<repo>/.mem0`）【没删】**（⛔ 本次一份都没删）——
      它现在**读不到了**，要留要删由作者定。
   📌 判据 ⇒ `app/tests/test_memory_store_path.py`（3 条，含"env 每次现读"）

1. 🔴 **mem0 的签名漂移【修过两次】，且第一次没修完**（2026-09-20）：
   - 第一处：`search(query, user_id=…, limit=…)` **不成立** —— mem0 2.x 是
     `search(query, *, top_k=20, filters=None, …)`：**不收 `user_id`**、`limit` **已改名 `top_k`**。
   - 第二处：**返回形状也变了**。
   🔴 **第一处为什么没一次修完**：我当时只验了「**不抛异常 + `len()==1`**」
   ⇒ 这个判据**量不到"返回形状"** ⇒ 第二处才暴露。
   ⇒ 📌 本仓立场：**判据要量到你真正在意的那个量**（同族）。
2. ⚠️ **本地模式的 `user_id` 是【过滤条件】，不是"谁在问"** ——
   ⛔ 别把它当成身份认证的落点（身份在 `app/access/` 那条线上）。
3. ⚠️ **`.mem0/` 在 `.gitignore` 里** ⇒ 记忆**不随仓库走**。换机/重建 = 记忆为空，**不是丢数据**。
4. 🔴 **它自成一个 embedding 站点** —— 收口 embedding 配置时**最容易漏的就是这一处**：
   `api_key` 是**裸 `os.getenv`**（**不走** `config.validate_config()`，`docs/契约/环境变量.md` 明写），
   模型名也曾是**写死的字面量**。⇒ ⛔ **"还有没有别处写死模型名"这个问题，别只 grep `embedding_client`**：
   2026-10-11 实测全仓有**三处**（这里 + `rag/embedding_client.py` + `eval/evaluate_with_ragas.py`）。
   🔴 **2026-10-11 已收口**：改成 `langchain` provider + `RecordingEmbeddings`
   ⇒ 它**不再自己造客户端**，走的是 `rag/embedding_client.get_embedding`（**同一份**实现/缓存/记账）。
5. 🔴🔴 **「这条路的 token 记在谁头上」以前【根本没人管】**（2026-10-11）——
   mem0 用它**自己的** LLM 与 embedder，两样**都不记账**
   ⇒ `/agent/memory/add` 回 200，而库里 **0 行**。
   ⚠️ **一次 add 实测 = 8112 个 LLM token**（`memory_extract`：prompt 7850 + completion 262）
   —— 而 FREE 访客的 LLM 池只有 10 000 ⇒ **一次加记忆几乎打满**。
   ✅ 现在 LLM / embedder 都换成了我们自己的对象，靠 **`ContextVar`**（`_CURRENT_USER`）把"算谁的"传进去。
   ⚠️ **那两个入口函数必须 `set` + `finally: reset`** —— 不复原就会**把下一个请求记到这个人头上**（有守卫）。
   ⚠️ **别再改回 mem0 自带的 provider** —— 它会**静默**变回不记账。
   判据 ⇒ `app/tests/test_memory_store_accounting.py`（**13 条**，含"mem0 真的接上了我们这两个对象"那条结构判据）。

## 🆕 2026-10-13· 新增 `memory_block(user_id, query)` —— 「记忆渲染成一段文本」的唯一出处

**它在哪被用**：`/agent/langgraph_chat`（「外部检索与工具Agent编排」页的主链）在**本任务第一步**
调它一次并注入（`agent_graph.agent_decide`）。
⚠️ 它存在的理由：那是**不依赖图状态**的那一份取法 —— 本模块是 `search_user_memory` 的家。
📄 为什么那个调用点要这么写。

| 口径 | 说明 |
|---|---|
| 入参 | `user_id` = `f"{user_name}:{memory_space}"`（两个调用点都这么拼）· `query` = 拿什么去检索 |
| 出参 | 一段**可直接拼进 prompt** 的文本；**一条都没检索到 ⇒ `None`**（⛔ 不是空串 —— 空串会被拼成"有一句空白的记忆"） |
| 花钱 | ⚠️ 它走一次 embedding（起记在**真实用户**头上）⇒ **必须排在预算门之后**调 |
| 判据 | `app/tests/test_memory_injection_wiring.py`（**4 条**：有记忆 ⇒ 拼在最前 · 没记忆 ⇒ 原样透传 · 第二步以后不再检索 · 被预算拦时一次都不检索） |
| 🔴 **取不到就 fail-open** | 检索失败（key 不对 / 网络不通 / mem0 或向量库挂了）⇒ **只降级（不注入）** ＋ `logger.warning` 留痕，⛔ **不许把调用它的那条链打挂**。<br>⚠️ 这一条是拿一次真实的 CI 全红换来的：CI 的 key 是占位值 ⇒ 401 ⇒ 异常从图节点穿出来 ⇒ 14 条用例全红（本机看不见 —— 本机有真 key ＋ Redis 缓存命中）。<br>📄  §六 · 反证：去掉那个 `try/except` ⇒ 对应用例当场红 |

🔴 **⛔ 没有合并那两份历史副本**：`agent_graph_advanced.py:67` 与 `agent_graph_advanced_learning.py:116`
各有一份 `inject_memories_to_prompt`。它们**不是死代码** —— 是 **5 个测试文件**在用的 monkeypatch 缝
（`monkeypatch.setattr(aga, "inject_memories_to_prompt", …)`，默认 `raising=True`）。
**合并 = 挪缝 ⇒ 那些补丁静默失效**（本仓  就是这么烂的）⇒ 重复**已单独立账**（§六），⛔ 本轮不动。

## 🆕 2026-10-13· **那两份副本各自也改成 fail-open**

上表那条「取不到就 fail-open」原先**只成立在 `memory_block`**（新加的那份）；
两份历史副本里还是**裸调用** ⇒ `/agent/mcp_chat` / `/agent/advanced_chat` 在 embedding 取不到时
**整条 500**。现已在**三个调用点**各包一层 try/except（失败 ⇒ `logger.warning` + 空记忆）：

| # | 落在哪 | 说明 |
|---|---|---|
| 1 | `agent_graph_advanced.py:92` | `inject_memories_to_prompt` 内 —— 即 `/agent/mcp_chat` 走的那份 |
| 2 | `agent_graph_advanced_learning.py:134` | `inject_memories_to_prompt` 内 |
| 3 | 🔴 `agent_graph_advanced_learning.py:497` | **入口节点 `supervisor`** —— 它**直调** `search_user_memory`，⛔ 不走上面那个函数 |

🔴 **第 3 处是修的时候才照出来的**（的账原先只写了前两处）⇒ 只改函数**盖不到它**。
⚠️ 三处都写了 `except AssertionError: raise` —— ⛔ **不是冗余**：`test_budget_soft_return._no_memory`
的探针报的是 `AssertionError`，而它是 `Exception` 的子类 ⇒ 少了那两行，那条守卫**静默失效**。
📌 守卫 ⇒ `app/tests/test_memory_injection_failopen.py`（**4 条**，每条带正控 · **变异自证**过）

## 关联

- `app/tests/test_agent_repairs.py` —— 两处 mem0 漂移的守卫（本文件的两条教训都钉在那）
- `docs/说明/mcp长驻会话-调研-20261008.md`（⛔ 文件名以实际为准）
- 🔴 **本仓另一个项目 `memory-system` 是【独立系统】** —— ⛔ 别把它与这个模块混为一谈
