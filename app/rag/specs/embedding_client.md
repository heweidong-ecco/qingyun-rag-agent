# `app/rag/embedding_client.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **客户端已惰性构造**（2026-10-05 · 批 6 ·）· 🔴 **模型名已从 env 读**（2026-10-11） |
| **对外提供** | `get_embedding(text, model=None, *, user_name="system", thread_id="system")` —— `None` 时解析成 `EMBEDDING_MODEL_MAIN`（先 env 后 `core.config`）· `assert_model_dimension(expected)`<br>🔴 **2026-10-11 新增 `user_name`（关键字限定）**—— 见「看代码会误判」第 1 条；⛔ **别把默认值删掉**（三条路本来就没有用户） |
| **谁在用** | 几乎所有检索/入库路径（经 `rag_pipeline` / `api_v1_rag` / `cache.warmup_cache` / `hybrid_search`） |

## ✅ 做了什么

- **服务路径上唯一的 embedding 调用点**，端点固定走 **阿里云百炼 DashScope**；
  模型名由 `EMBEDDING_MODEL_MAIN` 决定（**2026-10-14 B 段起默认 `qwen3.7-text-embedding-flash` · 1024 维**；改前是 `text-embedding-v2` · 1536 维）
- 内嵌 Redis 缓存查询（`cache.get_cached_embedding(text, model)`）· 调完记 `token_tracker.record_usage`
- ✅ **客户端惰性构造**（`_client = None` + `_get_client()`）—— **`import` 期不再碰凭据**；
  缺 `DASHSCOPE_API_KEY` 时抛**点名那个变量**的 `EnvironmentError`（与 `config.validate_config` 同族）
- 🔴 **2026-10-11：模型名收口到 `EMBEDDING_MODEL_MAIN`**（§二·2）——
  ⛔ 别再写回形参默认值里的字面量；`model=` 形参**必须保留**（`app/tests/test_isolation.py` 4 处靠它打桩）。
  ⚠️ **同时缓存键变成 `emb:<模型名>:<md5(text)>`**（见 `app/core/specs/cache.md` §12）——
  **这条不做的话，换模型会命中旧模型的向量且不报错**。
- ⚠️ **`assert_model_dimension(expected)` 目前【没有接线】** —— 函数与用例都在，但
  `main.py` 的 startup **还没调它**。原因：接线要一个 `expected` 的**来源**，全仓现在没有
  （`app/core/db.py` 里是行内字面量 `vector(N)`）。
  ✅ **2026-10-14 B 段已接线** —— `expected` 的来源是 **`core.db.embedding_dim_in_db()`**（查表自己的 `atttypmod`），
  ⛔ 不抽常量、也不在 `main.py` 里写死一个数（那正是当初不敢在 A 段硬接的原因）。
  ⚠️ **SQLite 那条路回 `None`** ⇒ 调用方**跳过**自检（⛔ 不是"当成通过"）。
- ✅ **有测试了**（原先零覆盖）：`app/tests/test_embedding_client_lazy.py`（3 条）·
  `app/tests/test_embedding_model_from_env.py`（5 条 · 模型名来源 / 缓存键 / 维度自检）—— **全离线 · 进 CI**

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「配置齐全就能跑」 | ✅ **现在成立了**。改前 🔴 **模块级 `OpenAI(api_key=DASHSCOPE_API_KEY, ...)`** —— `config.py:39` 的 `os.getenv` **无默认值** ⇒ 缺这一行时是 `None` ⇒ **构造期就抛 `OpenAIError`** ⇒ **整条 import 链崩**（任何 import 它的模块都起不来）。**2026-10-05 批 6 已修** |
| 「key 为空 = 会炸」 | 🟡 **要分清**：**变量缺失**（`None`）改前**构造期就炸**；**空字符串**（`DASHSCOPE_API_KEY=`）改前**构造得出来**、要到**请求时**才 401。改后两种都**在取客户端时**被同一句点名的错误拦住 |
| 「启动时 `validate_config()` 会先拦住缺 key」 | 🟡 **正常启动路径上确实会**（`DASHSCOPE_API_KEY` 是**必填**，`config.py:61`）；<br>⚠️ **但**：**测试 / 独立脚本 / 任何没走 `validate_config` 的入口** —— 改前报的是一句**难懂的 `OpenAIError`**（它提的 `OPENAI_API_KEY` 本仓根本不用），改后**点名 `DASHSCOPE_API_KEY`** |
| 「缓存命中时不需要客户端」 | 🔴 **不是了** ——  把取客户端放在**查缓存之前**（否则「同一句有时报错、有时不报」取决于缓存状态）。**这是一处有意为之的行为变更** |
| 🔴 **「这个账记在谁头上」** | **2026-10-11 之前记在一个假用户 `system` 上**（那行写死、原注释还自认"可后续改为动态获取当前用户"）⇒ **任何真实用户的 Embedding 用量恒为 0** ⇒ 🔴 **把「两个额度池」里的 Embedding 池整个废掉**（它判「额度 − 已用」，而"已用"恒 0 ⇒ 永远不会 429）。<br>✅ **已修**：`get_embedding(..., user_name=…)`，**8 处有用户的调用点都得传**。<br>⚠️ **默认值仍是 `"system"`，那是对的** —— 三条路（启动预热 / 维度自检 / `__main__`）**本来就没有用户**；⛔ 给它们硬塞一个用户 = **把我们的成本记到别人账上**。<br>🔴 **判据在调用点上**：`test_embedding_attribution.py` 扫活代码，**每个 `get_embedding(` 都得带 `user_name=`**（白名单只有 2 条） |
| 🔴 **「`thread_id` 也对了吧？」** | ⚠️ **`user_name` 对 ⛔ 不等于 `thread_id` 对** —— 这是 （2026-10-16）照出来的：<br>`/rag/search?thread_id=X` 那次调用，账落 `(user, "system")` —— **用户是对的**，而**会话闸查的是 `(user, X)`** ⇒ **闸恒看到 0** ⇒ 那条路上的**会话 50k 上限永远不会触发**。<br>✅ **现在两个都要传**，`thread_id` 由调用链从端点一路带下来（`rag_pipeline` / `hybrid_search` / `api_v1_rag`）⇒ `search_async(..., *, user_id, thread_id)`。<br>⚠️ **默认值仍留 `"system"`**，理由与 `user_name` **逐字同款**（启动预热 / 维度自检 / `__main__` 本来就没有会话）。<br>🔴 **判据**：同一个文件里的 `test_every_call_site_passes_a_thread`（白名单 3 条，各写理由）。 |
| 「LLM 能换，Embedding 也能换」 | 🟡 **2026-10-11 起要分开说**：**端点**仍然写死 DashScope（`base_url` 在模块里，⛔ 不是配置项，`DASHSCOPE_API_KEY` 专用）；**模型名**已可配（`EMBEDDING_MODEL_MAIN`）。⚠️ 换它**必须连维度 + 表定义一起**（§2.1）—— 只换模型名 ⇒ **最危险的是"维度恰好相同"时不报错，而检索结果全是垃圾** |
| 「缓存命中的是"这句话的向量"」 | 🔴 **不是** —— 命中是「**这句话 + 这个模型**」的向量。键里**没有模型名**的那一版（2026-10-11 之前）会让切模型后的查询**静默拿回旧向量** |
| 「LLM 侧也一样修好了」 | ⛔ **没有** —— 同族的 `make_llm()` **仍在 import 期构造**，本批**只把「报什么」改对**。真惰性要动 3 张图的模块级 `llm` + `bind_tools`，**与 「运行时可切」是同一件事**，⛔ 不单独立项 |

## 关联

本仓的决策记录（⛔ 不随产品仓提供）（本批）· `docs/待办总表.md` §三 （**已销账**）·
`docs/契约/环境变量.md` §4（Embedding 与 LLM 不是同一家）· `app/core/llm_factory.py`（LLM 侧同族，只改了报错）
