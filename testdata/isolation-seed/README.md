# 三家隔离用户的常驻语料（`isolation-seed`）

> **这是什么**：三个隔离用户各自的文档语料 + 一个把它们**经真接口灌进真库**的脚本。
> **它和别处的"测试数据"不一样** —— ⛔ **这份是【常驻】的，跑完不删**。

---

## 一 · 为什么有它（作者 2026-10-04 原话）

> 「documents 你给它们造好并写文档……**调用接口模拟真实用户的操作，写入数据库和向量数据库，
> 没有真实写入以后怎么用作判断和测试**。我要：**真库里有三家各自的文档、肉眼可查**，
> 且是真实模拟用户传入，这样有记录，能验证**用户隔离，安全，role thread_id tag**等等相关的关键信息。」

⇒ 要的是**留下来能查的那种**数据，不是跑完就清掉的夹具。

---

## 二 · 三家是谁

| 用户 | 角色（`permission.get_user_role`） | 日预算 | 语料主题 |
|---|---|---|---|
| `isolation_a` | `FREE` | 10000 token | 咖啡烘焙 |
| `isolation_b` | `FREE` | 10000 token | 宠物寄养 |
| `isolation_c` | `PREMIUM` | 100000 token | 工业阀门 |

⚠️ **角色不是平均分配的** —— `app/access/permission.py` 目前**硬编码**：`isolation_c` → PREMIUM，其余 FREE。
⛔ **这不是脚本造的，是既有实现**；要打印判据见 §五·④。

## 三 · 语料清单（9 篇 · 每篇约 1000 字 · 切成 2–3 个 chunk）

```
testdata/isolation-seed/
├── A-01-产品手册.md          A-02-烘焙工艺说明.md       A-03-门店价目表.md
├── B-01-寄养服务说明.md       B-02-入住与接送流程.md     B-03-收费与常见问题.md
└── C-01-闸阀产品规格书.md     C-02-质量检测标准.md       C-03-售后与备件清单.md
```

⭐ **文件名就是「标签」** —— 上传时 `filename` 原样进 `documents.source`，所以库里能看到：

```
isolation-seed/A-01-产品手册.md
```

⚠️ **`documents` 表没有 `tag` 列**（只有 `id / content / source / embedding / requested_by`）
⇒ 「标签」在本仓的落点就是 **`source`**。

---

## 四 · 怎么重跑

**两条轴，两个脚本**（⛔ **docs 脚本不会自动跑 threads 脚本** —— 见 §六·2 末）：

```bash
# ⚠️ 本仓**不带灌库脚本** —— 那两步请手工做（用的是公开接口，见下）：

# ① documents 轴：把这三家（A/B/C）各当一篇文档，用各自那三把 API Key 上传
curl -sS -X POST https://<你的地址>/api/v1/rag/upload_document \
     -H "X-API-Key: <A 家的 key>" -F "file=@A-01-产品手册.md"

# ② thread_id 轴：让这三家各真跑一次 agent（会真花钱 —— 三家合计约 10,374 token）
curl -sS -X POST "https://<你的地址>/api/v1/agent/mcp_chat?thread_id=<各家自己的 id>" \
     -H "X-API-Key: <A 家的 key>" -H 'Content-Type: application/json' \
     -d '{"message":"你好"}'
```

**docs 脚本做的事**：`POST /api/v1/rag/upload_document`（带各自 `X-API-Key`）
⇒ 真 embedding（DashScope `text-embedding-v2`）⇒ 真写 `documents` 表 + pgvector。

**threads 脚本做的事**：`POST /api/v1/agent/mcp_chat?thread_id=…`（带各自 `X-API-Key`）
⇒ 真跑一次 agent 图 ⇒ 真写 `cost_records` + `token_usage_logs` 两张表。

**幂等**：两个脚本都只清**自己造的那一份** —— docs 清 `source LIKE 'isolation-seed/%'`，
threads 清那三个 `thread_id` 当天的行。⛔ 都不 `TRUNCATE`、⛔ 都不碰 `admin` 的 112 条。

---

## 五 · 怎么验（判据全在一处 —— `--check` 原样打印）

| # | 判据 | 期望 |
|---|---|---|
| ① | `SELECT requested_by, count(*), count(DISTINCT source), count(embedding) … GROUP BY 1` | 三家各 5–6 chunk / 3 篇 / 向量齐 |
| ② | 种子行里 `requested_by` 不属于三家的条数 | **0**（归属没被写成别人） |
| ③ | 种子行里 `embedding IS NULL` 的条数 | **0** |
| ④ | 每家 `source` 清单 | 只有自己那三篇 |
| ⑤ | 拿 A 的 key 搜 B/C 的主题词（`mode=fast`） | 返回里**只有 A 的来源** |

`threads` 脚本另有一组：

| # | 判据 | 期望 |
|---|---|---|
| ① | 三家各自的 `thread_id` 记账 | `A-thread-001`→`isolation_a` 等，各 4 条 |
| ② | `thread_id` 与 `user_name` 对不上的条数 | **0**（对不上 = 一个会话的钱记到别人头上） |
| ③ | 两表是否都写 | `cost_records` 12 · `token_usage_logs` 12 |
| ④ | `purpose` 分布 | 看得出真跑了图（`agent_decision` / `answer_generation`） |
| ⑤ | 三家当日额度消耗 | 对照 §二 的日预算 |

手查：

```bash
docker exec qingyun-rag-agent-postgres-1 psql -U postgres -d rag_db -c \
  "SELECT id, requested_by, source, length(content) FROM documents
   WHERE source LIKE 'isolation-seed/%' ORDER BY requested_by, source, id;"
```

---

## 六 · ⚠️ 三件必须说清楚的事

### 1 · 它与  的口径**相反，但两者并存**

`app/tests/test_isolation.py::probe_docs` 的探针文档（`source='isolation-probe-test'`）**每次跑完就删**，
且**只在 `POSTGRES_DB=rag_test` 时才会跑**（防误写真库）。那是**测试卫生**。
**本份要的恰好是留下来的那一类。**
⇒ ⛔ **不是把探针那条改掉**，是两件事各管各的。

### 2 · ✅ 「自定义 `thread_id` 落进真库」—— **用 `/agent/mcp_chat`，做到了**

> 🔴 **2026-10-04 更正**：本节原先写的是「**做不到**」。**那是错的** ——
> 我当时核的是 `/agent/advanced_chat`（它走**不记账**的 `agent_graph_advanced_learning`），
> 而**会记账的 `agent_graph_advanced.py` 是给 `/agent/mcp_chat` 用的**。
> 用户一句「**thread_id 不是哈希，怎么是常量？**」把这条断言问翻了。

**五条链路，逐条实测**：

| 链路 | 记账情况 | 实测 |
|---|---|---|
| `/rag/upload_document` | ⛔ 写死 `user_name="system"`, `thread_id="system"` | `app/rag/embedding_client.py:36-37` |
| `/agent/memory_chat` | ⛔ 记账判据 `hasattr(response,"usage")` **恒为假** | `app/agent/agent_checkpointer.py:78-83`（源码自己写着「本端点的记账从来没执行过」） |
| `/agent/advanced_chat` | ⛔ 实测 HTTP 200，真库**新增 0 条** | 它走的是 `agent_graph_advanced_learning`，不是会记账的 `agent_graph_advanced` |
| ✅ **`/agent/mcp_chat`** | ✅ **记**：`user_name` 真实 + **`thread_id` = 调用方传的原值** | 端点 `api_v1_agent.py:1306` 注入 state → `agent_graph_advanced.py:360` 记账。实测 `?thread_id=A-thread-001` 落库原值 |
| `/agent/plan_execute` | 🟡 记 `user_name`（真实）+ `thread_id="plan_execute"`（**常量**） | `app/agent/plan_execute.py:172-179` |

🔴 **别用 `plan_execute`**：一次大约吃掉 **9,800 token** —— **接近 `FREE` 档一整天的额度（10,000）**。
2026-10-04 那一次探针就把 `isolation_a` 的当日额度用光了（第二次调用直接 429）。
⇒ **它又贵、`thread_id` 又是常量**，两头都不占。

⇒ **正确做法**：（三家各 2 轮真对话，`thread_id` 由我们指定）。
⚠️ **它会花真钱**（实测每家 2 轮 ≈ 3,200–3,800 token · 三家合计 **10,374**）：这正是它**不被 `seed_isolation_docs.sh` 自动触发**的原因。

**另两条替代**（都**不落真库 / 不 durable**，⛔ 别拿它们当长期证据）：
- `/agent/memory/add` → Mem0 记忆空间（`user_id = 用户名:space`）—— ⚠️ 存在**容器内** `.mem0/qdrant`，
  ⛔ 没挂到宿主机 ⇒ **重建容器就丢**
- 流式对话端点 → Redis `chat_history:{用户名}` —— ⚠️ **24 小时 TTL**

### 3 · 两边能 durable 的，都在 Postgres 里

| 存的东西 | 在哪 | 重建容器后 |
|---|---|---|
| `documents` | Postgres（named volume `postgres_data`） | ✅ 还在 |
| `cost_records` / `token_usage_logs` | **同一个 Postgres** | ✅ 还在 |
| Redis `chat_history:*` | Redis | ⚠️ 有 TTL |
| `.mem0/qdrant` | **容器内**，没挂出来 | ⛔ **丢** |

⇒ **要「长期可查、肉眼能看」，就落 Postgres 这两处** —— 正好对应本份的两条轴。

---

## 七 · 怎么清掉

**① documents 轴**（9 篇 → 17 chunk）

```bash
docker exec qingyun-rag-agent-postgres-1 psql -U postgres -d rag_db -c \
  "DELETE FROM documents WHERE source LIKE 'isolation-seed/%';"
```

⛔ 别用 `TRUNCATE documents` —— 那会连 `admin` 的 112 条一起删。

**② thread_id 轴**（那三个 `thread_id` 的记账）

```bash
docker exec qingyun-rag-agent-postgres-1 psql -U postgres -d rag_db -c \
  "DELETE FROM cost_records      WHERE thread_id IN ('A-thread-001','B-thread-001','C-thread-001');
   DELETE FROM token_usage_logs  WHERE thread_id IN ('A-thread-001','B-thread-001','C-thread-001');"
```

⚠️ **`token_usage_logs` 是额度的权威数据源** —— 只删 `cost_records` 的话，
**三家的当日额度仍然是花掉的**（账面上的钱没退）。两张要一起删。

---

## 关联

| 文档 | 说明 |
|---|---|
|  | ① documents 轴 |
|  | ② thread_id 轴（⚠️ 花真钱） |
| `app/tests/test_isolation.py` | 另一条轴：跑完就删的探针 |
| `docs/契约/环境变量.md` | 三个 `ISOLATION_*_API_KEY` 在哪 |
