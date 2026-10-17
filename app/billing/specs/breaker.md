# `app/billing/breaker.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **部分** —— `global:` 这一条 key **已生效**；`model:` 那类**还没做**（留给） |
| **对外提供** | `circuit(key, estimated_tokens=0) -> (bool, str)` · `global_key() -> str` |
| **谁在用** | 9 处（8 个端点函数）：`api_v1_agent.py` 5 处（`:109 :186 :224 :280 :527`）· `api_v1_rag.py` 2 处（`:609 :832`，后者在 WS 里）· `api_v1.py` 1 处（`:244`） |
| **测试** | `test_breaker.py`（9 条 · 函数本身）· `test_breaker_wiring.py`（9 条 · **接线**）· `test_global_daily_budget.py::test_yesterdays_usage_does_not_count`（**跨天恢复**） |

## ✅ 做了什么

 的落点：一个**通用按 key 断路器**。它是**派发器**，不是判定器 ——
`circuit("global:<日期>")` 把判断**委托**给 `token_tracker.check_global_daily_budget()`
（已建好的那个函数）。

| 部分 | 说明 |
|---|---|
| 阈值 | `1,000,000` tokens / 天（`token_config.GLOBAL_DAILY_TOKEN_LIMIT`） |
| 触发动作 | **直接拒绝** —— 调用方抛 `AppException(ErrorCode.QUOTA_EXCEEDED, why)` ⇒ **429**（要素②，作者 裁） |
| 可见提示 | `why` 里带「已使用 X / 上限 Y，请明日再试」 |
| 恢复 | **跨天自然重置**（见下 §⚠️ 第 1 条） |

**它是怎么"顺带"支持  的**：（十几个 model 的免费额度）**不需要任何新东西** ——
只是**同一个函数换一个 key 前缀**（`model:<模型>:<日期>`）。⇒ 现状里没有为  预建任何结构。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| **`model:` 前缀没实现** |  要的模型级降级链（TTL /  可见标记 /  人工排序）**都还没做** —— 见  §一·附 |
| ⚠️ **没有"半开 / 探测恢复"** | **这是有意的**，不是漏了 —— 理由见 §⚠️ 第 2 条 |
| ⚠️ **`benchmark-embedding` 是近似计量** | 见 §⚠️ 第 5 条 |
| **匿名那层仍是个洞（另一个问题）** | 配额那层对匿名请求**完全绕过**（`app/main.py:314` 的 `if not user_name:`）。⚠️ **旧写「`quota_limiter.py` 绕过」—— 那个模块 已删，但这条行为原样保留**。 没有解决它，只是**不再让它连带烧钱**（`benchmark-embedding` 现在会问断路器） |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🔴 「恢复」与 Redis **无关** ——  源文档在这里写错过

 的要素④标着「⚠️ **未核**：Redis 日级 key 的 TTL 从来没实测过」。
**那个猜测是错的**：全站用量**在 PG 里**（`token_usage_logs`），**不在 Redis**
⇒ **根本没有 TTL 可核**。恢复靠的是那条 SQL 的 `WHERE created_at >= CURRENT_DATE`
**自己翻页** —— **没有需要"到期释放"的东西**。

📌 已补 `test_yesterdays_usage_does_not_count`：塞一条**昨天**的 999999 tokens，
断言今日合计**不涨**。（静态的 `test_global_query_is_today_only` 只能证明"SQL 里**写着** `CURRENT_DATE`"，证明不了"昨天的行**真的**被排除"。）

### 2. 🔴 没有"半开 / 探测恢复" —— **是有意的，⛔ 别当缺陷补上**

教科书熔断器有半开态（放一个探针请求试上游）。**这里不需要**：
熔断的对象是**预算**，周期是**天** ⇒ 恢复天然就是"跨天"。
做了半开反而引入「预算没到却被当探针放行」。

### 3. 🔴 **未知 key ⇒ fail-open** —— 看着像漏判，其实是有意的（而且**会静默失效**）

额度是**成本控制**，⛔ **不是安全边界**（与 `app/routing/deps.py` 鉴权 fail-closed **方向相反**）。
⇒ 认不出的 key **放行** + 打日志。

⚠️ **代价要知道**：**key 拼写一旦对不上，熔断会静默失效** —— 不报错、不红、照常放行。
⇒ 这正是 `global_key()` 存在的原因（⛔ 别在调用点手拼日期），
且 `test_global_key_is_actually_recognized_by_circuit` 专门把「生成端」和「识别端」**接起来**验。

### 4. ⚠️ **`circuit()` 自己不取日期、不算额度**

它**不变量**：日期由调用方经 `global_key()` 显式给出；额度由 `check_global_daily_budget()` 判。
⇒ 读本文件**看不出**阈值是多少（在 `token_config.py`）、也看不出**谁在调它**（在 3 个路由文件里）。
**判据：改本文件不会改阈值；改阈值不会过本文件。**

### 5. ⚠️ `benchmark-embedding` 接的是**代理指标**，不是精确计量

那个端点花的是 **embedding** 的钱，**未必**计入 `token_usage_logs`（那是 LLM token 的账）。
⇒ 拿 **token** 预算闸它是**策略**（"全站超预算了就别再拿调试端点烧账号"），
**不是**"它的花费被算进去了"。

它为什么**必须**由  管：**它是全仓唯一一条「匿名可打、且真花钱」的端点** ——
签名是 `async def benchmark_embedding(req: QuestionRequest)`，**没有 `Depends` 鉴权**。
⚠️ 也因此它**接不上 **（会话级要 `user_name`/`thread_id`，这里两者都没有）。

### 6. 🔴 **全绿 ≠ 熔断生效** —— 本仓栽过三次的同型陷阱

`circuit()` 对了、测试全绿，**但只要没人调它，就等于没有熔断**。
⇒ 接线由 `test_breaker_wiring.py` 单独钉住（AST 查 8 个函数体里有没有真的调 `circuit`）。
📌 同型先例： 之前「常量建好没接上」·  本身「有函数零调用点」。

### 7. ⚠️ `admin` **不是无限的了**

 已去掉 `"admin": float("inf")`（现在是 `premium` = `100000`/天）。
⚠️ **但全局日级是另一回事** —— `get_global_daily_token_usage()` 按 `SUM(所有行)` 算、**含 admin**，
⛔ **不是**"按各角色上限求和"。改了这条 SQL 会让熔断在 admin 刷额度时**不响**。

## 关联

| 文档 | 说明 |
|---|---|
| `app/billing/specs/token_tracker.md` | `check_global_daily_budget()` / `get_global_daily_token_usage()` 的出处 |
| `app/billing/specs/token_config.md` | `GLOBAL_DAILY_TOKEN_LIMIT` 的值与改法 |
| `app/specs/main.md` · `app/specs/归档/quota_limiter.md` | ⚠️ **另一层**（配额 —— 且**对匿名绕过**）—— 别与这里混。<br>🔴：配额那层**已原位改成 token 口径**（现役 spec = `main.md`）；`quota_limiter.md` 是**已删模块的归档** |
| `qingyun-rag-agent-TODO待办/后端补齐清单-待裁-20260929.md` §B11 | 四要素原文 + 我方建议 |


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/billing/breaker.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ 整段**原样搬来**，⛔ 未改写。

## 它是什么 / 不是什么

**是**：一个**派发器**。`circuit(key)` 按 key 前缀找到对应的判定器，
把"该不该拦"这个决定**委托出去**。

**不是**：一个自己判额度的东西。别在这里另写一套判定 ——
那会和 `token_tracker` 里的口径**悄悄分叉**，而且**两边都不报错**。

## 为什么不按教科书做「半开 / 探测恢复」

标准熔断器有"半开"态（放一个探针请求试上游）。**这里不需要**，理由：
本仓熔断的对象是**预算**，而预算的周期是**天** ⇒ **恢复机制天然就是"跨天自然重置"**。
做半开只会引入「预算没到却被当探针放行」的复杂度。

> 恢复的**实现**其实早就有了 —— 全站用量走的是
> `WHERE created_at >= CURRENT_DATE`（`token_tracker.get_global_daily_token_usage`），
> **跨天查询窗口自己翻页**，与 Redis TTL 无关（源文档曾标"Redis 日级 key TTL 未核"，
> 那是**对存储的误判** —— 日级用量在 PG，不在 Redis）。

## 未知 key ⇒ **fail-open**，这是有意的

额度是**成本控制**，**不是安全边界**（与 `app/routing/deps.py` 鉴权 fail-closed **方向相反**）。
⇒ 拼错的 key、或  将来加了新前缀而本文件还没跟上，
**都不该把服务打死** ⇒ 放行 + 打日志，让人看得。

## 本文件【自己】没有调用点也是白搭

接线在 `①b`：**与  同一批调用点**
（`api_v1_agent.py` 5 处 + `api_v1_rag.py` 3 处 + 匿名烧钱的 `/api/v1/rag/benchmark-embedding`）。
