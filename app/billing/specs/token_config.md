# `app/billing/token_config.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **新建（B7）** —— 额度类常量的**唯一落点**<br>✅ **`MAX_TOKENS_*` 已接线**（``，15 处构造点；**· 起收进 `app/core/llm_factory.py`**）<br>✅ **`SESSION_TOKEN_LIMIT` 已有判定函数 + 7 个调用点**<br>✅ **`GLOBAL_DAILY_TOKEN_LIMIT` 已接线**（``，经 `app/billing/breaker.py` **8 处**）<br>⚠️ **当天它曾是"有函数、无调用点"—— 那句已作废** |
| **对外提供** | `MAX_TOKENS_ANSWER` · `MAX_TOKENS_AGENT` · `MAX_TOKENS_REWRITE_*` · `SESSION_TOKEN_LIMIT` · `GLOBAL_DAILY_TOKEN_LIMIT` · 🔴 **`LLM_DAILY_TOKEN` + `EMBEDDING_DAILY_TOKEN`**（**按池两张表**）· `DEFAULT_DAILY_TOKEN_BUDGET` · `MODEL_PRICING` · `DEFAULT_MODEL_PRICING` · `MAX_SINGLE_CALL_COST` · `MAX_THREAD_COST` · `GLOBAL_LIMIT_*` · `USER_LIMIT_*` |
| **谁在用** | `token_tracker.py`（别名 `PRICING` / …）🔴 **`ROLE_TOKEN_BUDGET` 那个别名已删**（两池分开后没有"每个角色一个数"）· `rate_limiter.py`（现 `:137` / `:140` 两个既有 `TokenBucketLimiter` 的实例化）<br>✅ **`MAX_TOKENS_*` 的 15 个调用点** —— ⚠️ **它们不再直接 import 本模块**，而是经 `app/core/llm_factory.py` 的 `make_llm` 读。<br>📌 清单见 `app/tests/test_max_tokens_wiring.py` 的 `EXPECTED_ROLES`（旧名 `EXPECTED_MAX_TOKENS`， 随门禁改写） |
| **规模** | 89 行（`wc -l app/billing/token_config.py`） |

## ✅ 做了什么

- **把散在 4 个文件 6 处的额度常量收到一处** —— 这是 「两套口径差 35 倍」与
  `决策一`「三套怎么合」的**物理原因**（单位混着：次数 / token / 元 / 秒）。
- **按 （决策一 = 统一到 token）**：`admin` 的日额度 **不再是 `float("inf")`**，
  = `premium` = **100000 token/天**（作者 裁）。
- **补 `deepseek-v4-flash` 单价**（`🅗 S5`）—— 原先表里**只有 qwen 系列**，
  而 `.env` 实际走 DeepSeek ⇒ 金额落兜底价，**且看不出来是兜底**。
- **收限流参数**（`🅗 S6`）—— `rate_limiter.py` 原先把 `100/150` `3/20` 写死在 `:129/:132`。

## 🟡 做到哪 / 缺什么

- ✅ **`MAX_TOKENS_ANSWER` / `MAX_TOKENS_AGENT` 已接线**（`①b`）——
  **15 处**构造点全带上，分类见 `app/tests/test_max_tokens_wiring.py` 的 `EXPECTED_ROLES`。
  ⚠️ **这就是那句"常量对了、没接上 = 单次上限不存在"的兑现** ——
  接之前，`MAX_TOKENS_*` 只是两个没人读的数字。
  🔴 **又收了一层**：15 处**不再各写 `ChatOpenAI(max_tokens=…)`**，
  统一 `make_llm("fast"|"chat", "answer"|"agent")` ⇒ 本模块的读取点**从 15 个降到 1 个**
  （`app/core/llm_factory.py`）。⛔ **但"要重启才生效"没变**（见下方 ⚠️ 表）。
- ✅ **`SESSION_TOKEN_LIMIT` 已接线**（`①b`）——
  `token_tracker.check_session_token_budget` 读它，**接在 7 条真调 LLM 的对话链上**
- ✅ **`GLOBAL_DAILY_TOKEN_LIMIT` 已接线**（`①b`）——
  `token_tracker.check_global_daily_budget` 读它，而它经 `app/billing/breaker.py` 的 `circuit()`
  **接在 8 个真花钱的端点上** ⇒ **改这个值 + 重启 ⇒ 会改变行为**。
  ⚠️ 值与  不同，它**是作者裁定过的**。
  📌 **判据**：`grep -rn "circuit(global_key())" app/ --include="*.py" | grep -v test_` ⇒ 8 处
  🔴 **它只当【容量】，⛔ 不当【止损】**。
  「给真人用够」（该大）与「被刷时少损失」（该小）**必然二选一**。
  🔴 **两个数要一起看**：**每天发号上限 × 每把号的日额度 ≥ 全站日顶** ——
  否则会「**预算还没花完就发不出号**」（真人被挡在门外，而钱还剩着）。
  现取值：发号闸的日上限 × `LLM_DAILY_TOKEN["free"]` = `300 × 50 000` = **15 000 000** = 本常量默认值 ⇒ **正好对齐**。
  📄 为什么这么定（含量级依据与钱）。
- ⬜ **`MAX_TOKENS_REWRITE_*` 只登记、不改行为** —— `query_rewriter.py:65/143` 仍是自己的字面量
- ⬜ **零 env 覆盖测试** —— 默认值测了，`os.getenv` 那条路只对限流参数用子进程验过一次
- ⚠️ **`permission.ROLE_QUOTA`（次数）本轮【没动】** —— 按  它要在
  **`①b · `【最后】**降级（先撤次数会开出"谁都不拦"的窗口，见该 DEC 的「顺序陷阱」）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「常量集中了 ⇒ 就动态了 / 能热加载了」** | ⛔ **不是** —— **只集中，不做运行时路由**。`MAX_TOKENS_*` 仍是在 **import 时求值**（`make_llm()` 的 15 个调用点全在模块层）⇒ **改了值本来就要重启容器**。这两件事**不是一回事** |
| 🔴 **「在 `token_config` 里改个值，服务行为立刻变」** | ⛔ **不是** —— 见上（**要重启**）。<br>🟢 **的实际状态（逐项，别一起读）**：<br>· `MAX_TOKENS_*` ⇒ **改值 + 重启 ⇒ 生效**（`①b`，15 处； 收进 `llm_factory`，**生效条件不变**）<br>· `SESSION_TOKEN_LIMIT` ⇒ **改值 + 重启 ⇒ 生效**（`①b`，已接 7 条链）<br>· ✅ `GLOBAL_DAILY_TOKEN_LIMIT` ⇒ **改值 + 重启 ⇒ 生效**（`①b` · 已接 8 处；那句"什么都不会发生"**已作废**） |
| ⚠️ **「`from token_config import …` 放哪一行都行」** | ⛔ **不是** —— 本模块**在 import 时读 env** ⇒ 必须排在 **`load_dotenv` 之后**。<br>📌 **唯一受影响的是 `evaluate_with_ragas.py`**（它是全仓唯一显式调 `load_dotenv` 的文件）。<br>🔴 **这条约束【换了落点但没消失】**：该文件已不再 `from token_config import …`，<br>改为 `make_llm` —— 而 `llm_factory` **只在 `make_llm` 函数内**才 `import token_config`，<br>⇒ 现在管的是**调用 `make_llm(...)` 的时机**（该调用点在 `load_dotenv` 之后 ✅）。<br>🔴 **本仓目前两者相等 ⇒ 放错也看不出来**（`.env` 里没有 `TOKEN_MAX_*`）。**正因为看不出来，才写死在正确的一侧。** |
| ⚠️ **「`MODEL_PRICING` 里的 `deepseek` 价就是账单价」** | 🟡 **是近似** —— 本表**不区分缓存命中**（拿不到命中/未命中的拆分）⇒ 按**未命中**价记 ⇒ **偏高估**（偏保守）。来源见文件内注释 |
| 🔴 **「兜底价是个上界 / 它不会低报花费」** | ⛔ **不是，而且旧注释就是这么写错的**。原注释写「取偏保守的一组（**不低报**花费）」——**那是 qwen-turbo 年代的说法**（0.003/0.006 恰好等于 qwen-turbo 的价）。实测：**`qwen-plus`（0.008/0.016）本来就高于兜底**。<br>⇒ **兜底价不是任何意义上的上界**；**⛔ 别拿它当规矩**。<br>📌 **真正防"静默低报"的是「在用模型必须登记」**（登记了就走自己的价，**永远走不到兜底**）。<br>🔴 **本行是两个坑的合体**：① 我先把那句旧注释当成了规矩；② 又把它写成测试 ⇒ **CI 直接红**（CI 无 `.env` ⇒ `config.py:55` **当时**默认 `qwen-plus`；⚠️ 已改 `deepseek-v4-flash`）⇒ 已删该测试，见 `app/tests/test_token_config.py` 顶部 docstring |
| ⚠️ **「日额度那张表的键是枚举」** | ⛔ **是字符串**（`"free"`/`"premium"`/`"admin"`）。能对上是因为 `permission.UserRole` 继承 `str` ⇒ ⚠️ **改 `UserRole` 的值就会静默错配**（`.get(role, 默认值)` 不报错，直接吃默认值）。<br>🔴 **现在是【两张表】**（`LLM_DAILY_TOKEN` / `EMBEDDING_DAILY_TOKEN`）—— ⚠️ **两张都要登记每个角色**（有守卫逐表比）；旧名 `ROLE_DAILY_TOKEN` **已删**（两池分开后"每个角色一个数"不存在了） |
| ⚠️ **「建了 `token_config` ⇒ 另三套口径没了」** | ⛔ **当时没没，现在没了** —— 本行原写「`permission.ROLE_QUOTA`（次数）与 `quota_limiter.py`（每日次数）**本轮一行没动**」（`①a` 当时的事实）。<br>🔴 **（`①b`）两地都已删** ⇒ **现在额度只剩 token 一套**（金额那套是同一物不同单位，经本文件的 `MODEL_PRICING` 换算）。 |
| 🔴 **「换 embedding 模型 ⇒ 把旧模型那行删掉就行」** | ⛔ **删的时机有讲究**：作者裁过「旧那行不留、直接删」，但**不能与"加新模型"同一笔做**——<br>① embedding 的切换分 **A 段（代码就绪）/ B 段（真切）**，A 段的要求是「**行为与改前逐字相同**」，而**删掉在用模型的价 = 改行为**（每次 embedding 记账从 `0.0005` 落到兜底 `0.003`，**高报 6 倍**）；<br>② 删完到真切之间会有一段「**在用的模型没登记**」，而**旧的那条守卫写死了模型名、对它无感**。<br>⇒ **加新行放 A 段，删旧行放 B 段**（与 `.env` 翻 flash 同一次）。📌 判据见 `app/billing/token_config.py` 里那一段注释。 |
| 🔴 **「换 embedding 模型时有一道门会拦」** | ⛔ **之前一道都没有** —— `test_models_actually_in_use_have_explicit_pricing` **只管 `LLM_MODEL_FAST`/`LLM_MODEL_CHAT`**；而 embedding 那条**写死了 `"text-embedding-v2"` 这个字面量** ⇒ 「新模型没登记」它**无感**。<br>⇒ 改成**推导型**（从 `core.config.EMBEDDING_MODEL_MAIN` 取）——现在它真的会红。 |

> 🔴 **补（`①b` + B 段 · 本文件当天两次改动）**：
> · **新增两道【全站 embedding】滚动窗口** —— `GLOBAL_EMBEDDING_HOURLY_LIMIT` / `GLOBAL_EMBEDDING_DAILY_LIMIT`。
>   ⚠️ 它们**不是** LLM 那个全站日顶（`GLOBAL_DAILY_TOKEN_LIMIT`）的别名：约束 embedding 的是
>   **另一把尺子**（DashScope 那把 key 的免费额度 = 100 万 token **总量** / 90 天）。
>   📄 来龙去脉与数值的推法 ⇒  §二·8 · 落地 ⇒ `billing/token_tracker.py::check_global_embedding_budget`
> · 🔴 **删掉了 `MODEL_PRICING` 里的 `"text-embedding-v2"` 那一行**（B 段随「`.env` 翻 flash」一起）。
>   ⚠️ **删它【不会】让守卫变红** —— 那条 `test_models_actually_in_use_have_explicit_pricing` 是
>   **推导型**（从 config 取在用的模型）⇒ 删掉**不再在用**的旧模型，它照样绿 ✓。
> 反证（⛔ 别只看它绿）：**把新模型那行注释掉 ⇒ 必须红**（跑过）。
> ⚠️ **已知代价**：历史 `token_usage_logs` 里那些 `text-embedding-v2` 记录会落**兜底价** ⇒ 旧账**偏高估**（作者 裁过「不留，直接删」）。

## 关联

`app/billing/specs/token_tracker.md`（`①a`/`①b` 的实施计划全文）· `app/access/specs/rate_limiter.md` · `app/access/specs/permission.md`（⏳ 待建）


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/billing/token_config.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ 整段**原样搬来**，⛔ 未改写。

为什么建这个文件：在此之前，额度类常量**散在 4 个文件 6 处**，且**单位混着**：
  · `permission.ROLE_QUOTA`（**次数**）
  **`ROLE_TOKEN_BUDGET` 这个别名已从 `token_tracker` 删掉**——
     日额度按池拆成两张表，见本模块下方。**别再按老样子去 `token_tracker` 找它**。
  · `token_tracker.PRICING`（元/1000token）
  · `plan_execute.PLAN_TOTAL_BUDGET_SECONDS`（**秒**）
⇒ 这正是 「两套口径差 35 倍」与 `决策一`「三套怎么合」的**物理原因**。
口径的裁定。

**上面那份清单是【建本文件当时】的实况，不是现状** ——
   `permission.ROLE_QUOTA`（含 `get_user_quota`）**已于 删除**，
   现在额度类常量**只剩 3 个文件 5 处**。别拿这份清单去 grep 那个常量。

**本模块只读 env，不做热加载** —— 故意的。
   读它的那 15 处都**在模块层**求值（`①b`  起统一经 `app/core/llm_factory.py` 的 `make_llm`，
   而调用点仍在 import 时执行）⇒ 改了值本来就要重启。**只求"集中"，不求"热加载"。**
