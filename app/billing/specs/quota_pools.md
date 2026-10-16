# `app/billing/quota_pools.py` —— 两个额度池 · 按路径归池

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用**（2026-10-11 建 · 批 6） |
| **对应模块** | `app/billing/quota_pools.py` |
| **裁定** | （拆两个池 · 按路径归池）· （落地口径：总上限 = 两池相加） |
| **用例** | `app/tests/test_quota_pools.py`（归池表本身）· `app/tests/test_pool_quota_wiring.py`（接线） |

---

## ✅ 做了什么

- **归池表** `PATH_POOLS`（**74 条**路由模板 → `llm` / `embedding` / `none`）。
  逐条依据 ⇒ （作者 2026-10-11 逐条裁过）。
- **纯函数** `pool_for_path(path)` —— 按**分段**匹配模板（`{thread_id}` 那种是通配）。
- **每池的日额度** `pool_daily_limit(pool, role)` · **总额** `total_daily_limit(role)`。

## 🟡 做到哪 / 缺什么

- ✅ `QuotaMiddleware` 按路径选池后再判（判据：`test_pool_quota_wiring.py`，**真调中间件**）。
- ✅ 页头两个格子各带自己的「今日剩余」（`app/static/js/cred.js` + `/agent/token/budget` 的四个新键）。
- 🔶 **"已用"是按 `purpose` 分的，而拦截是按【路径】** —— 两者不总是一致，见下。

---

## ⚠️ 看代码会误判的地方

### 1. 🔴 **「归池」和「记账」是两套口径** —— 不一致是**有意的**

| | 按什么分 | 用在哪 |
|---|---|---|
| **拦不拦** | **路径**（本模块） | `QuotaMiddleware` |
| **记账** | **`purpose`**（`token_tracker` 的两张表） | 各 handler 里 |

⇒ 一条「两边都动」的路由（如 `/agent/mcp_chat`）**按路径进 LLM 池**，
但它产生的 embedding 用量**记在 embedding 那一栏**。
⚠️ 所以 **LLM 池的"已用"里不含这一笔的 embedding 部分**。
🔴 **要精确就得按路径逐笔记账** —— 那是另一件事（§三 备选 5）。**⛔ 别把它当 bug 去"修"。**

### 2. 🔴 为什么归池看**路径**，⛔ 不能看"这次会调什么"

额度是**请求进来之前**在 `QuotaMiddleware` 里判的 —— 那一刻 **handler 还没跑**，
**不可能知道**这次会是一次 LLM 调用还是 embedding 调用。
📌 所以  §三 的备选 5「按调用类型归池」是**做不到**，不是"没想到"。

### 3. 🔴 **表外路径 = 放行**（`POOL_NONE`），⛔ 别读成"忘了归"

作者 2026-10-11 裁「**表外路径不进池**」。理由：把 `/static/*` 当 LLM 用
⇒ 一次页面加载 ≈8 个请求全进 LLM 池 ⇒ **几刷就顶满**（那正是  那个 429 的同一件事）。
⚠️ **代价**：新加的路由**忘了归池 = 静默不受限** ⇒ 靠 `test_quota_pools.py` 的
**双向覆盖门**（路由模板集合 ⇔ 表键集合）兜住。

🔵 **那道门 2026-10-15 真拦下一次**：加 `/login` 页面路由（§七·1）时，
   全量 pytest 里**只有它红**（`test_every_route_has_a_pool`）—— 补一行 `/login: POOL_NONE` 即绿。
   ⇒ **这就是那道门存在的理由**：漏了它，那条路由 `pool_for_path` 回 `POOL_NONE` ⇒ 静默不受限，
   而**没有任何东西会说**。📌 同批还有一道同型的门也拦了同一次：
   `test_index_page.py::test_the_capability_map_lists_every_page`（盘上页面数 ⇔ 能力地图清单）。

### 4. 🔴 总额是**两池【相加】**，⛔ 不是「角色额度 × 2」

作者 2026-10-11 明确要**加法**：两个额度**将来可能不一样**（比如 embedding 单独调小）
⇒ 写成加法，**改了某一池总额自动跟着变**；写死倍数就得**手工同步第二个数**。
📌 判据 `test_quota_pools.py::test_total_is_the_sum_of_the_two_pools`（两池给**不同的数** 100/7 ⇒ 必须得 107）。

### 5. 🔴 两池**已经不一样了**（2026-10-11）

**本条原先写的是**「今天两池同源（都取 `ROLE_DAILY_TOKEN[角色]`）⇒ 总额恰好 2×；
⛔ 别因为'现在一样'就合成一个函数」。**那句话当天就过期了** —— 作者把 LLM 池调到了 50 000
（free），而 Embedding 池仍是 10 000。

| 角色 | LLM 池 | Embedding 池 | 总额 |
|---|---:|---:|---:|
| `free` | 50 000 | 10 000 | 60 000 |
| `premium` / `admin` | 500 000 | 100 000 | 600 000 |

⚠️ 配置在 `token_config.py` 的**两张表**里（`LLM_DAILY_TOKEN` / `EMBEDDING_DAILY_TOKEN`），
本模块只负责**按池取那一张**。
🔴 **新增了一条结构判据**：`test_each_pool_reads_its_OWN_table` —— 改一张表，另一池**不许跟着动**
（防"又合回一张表"而"某一池单独调"悄悄做不到）。

⛔ **下面第 4 条（总额用加法）没变，而且正是它让这次改动只动一处。**

---

## 关联

- 上游裁定
- 落地口径（总额怎么算 · 那 11 处图内闸）
- 🔴 **「总额 vs 两池」**：⛔ 别把它读成"两套打架的口径" —— 那**两个端点报的是同一个数的两种看法**
  （`/agent/token/budget` 的 `daily_budget` 与 `/debug/quota` 的 `daily_limit` **同源**，
  它**另外**才给两池）⇒ **对外口径的权威在 `docs/契约/接口契约.md` §一「角色与配额」**（2026-10-13 写清）
- 归池表逐条依据
- 用它的地方 ⇒ `app/main.py` 的 `QuotaMiddleware` · `app/billing/token_tracker.py` 的 `get_pool_budget_info`
