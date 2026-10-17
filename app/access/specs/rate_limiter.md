# `app/access/rate_limiter.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用** —— 基于 Redis 的令牌桶，**全局 + 用户两层**<br>🔴 它曾有 **3 个"看代码看不出来"的性质** ⇒ **起 已修、 早于 已修**（`B9-b`）<br>⚠️ **但 ⚠️ 节还在** —— 仍然是"看代码会误判"的（其中 是**已知未修**），⛔ 别因为"修了"就当整节过时了 |
| **对外提供** | `TokenBucketLimiter`（`is_allowed` / `get_remaining` / `get_limit_info`，**三处都 fail-open**）· 两个模块级实例 `global_limiter` · `user_limiter` · `_TOKEN_BUCKET_LUA`（**提成模块级常量** —— 提出来才测得到） |
| **谁在用** | `main.py` 的 `RateLimitMiddleware`（**唯一的生产消费者**）· `api_v1.py` 的 `/debug/*` 查询端点 |
| **规模** | 🔴 **别写死行数** —— 跑 `wc -l app/access/rate_limiter.py`（时为 **210**） |

## ✅ 做了什么

- **令牌桶**（`:32` `is_allowed`）：整段判定写在 **Lua 脚本**里（`:41-68`）⇒ **原子**，不会被并发拆开
- **回填是【惰性】的**：在 Lua 里 `elapsed = now - last_time` ⇒ `tokens += elapsed*rate`（`:56-58`）——
  ⚠️ 这条曾经被**误报成"桶不回填"**（N16），核了消费入口才撤回（见 `:103-107` 的注释）
- **只读展示**：`get_remaining`(`:81`) · `get_limit_info`(`:90`，返回 `remaining`/`reset`/`limit` 三个值供响应头用)
- **两层实例**（`:129/132`）：`global_limiter` = 100/s · 容量 150 ｜ `user_limiter` = 3/s · 容量 20

## 🟡 做到哪 / 缺什么

- ✅ ~~**桶【永不过期】**~~ ⇒ **已修**：Lua 里加了 `EXPIRE`（走 `ARGV[4]`），
  值来自 `token_config.RATE_LIMIT_BUCKET_TTL`（**推导过**：`capacity/rate` 的若干倍）。
  ⚠️ **存量键仍是永久的** —— 本次只让**新写入**带上 TTL。见 ⚠️②
- ✅ ~~**Redis 不通 ⇒ 非公开路径【全站 500】**~~ ⇒ **已修**：三处各包
  `except redis.RedisError` ⇒ **fail-open + `logger.error`**。见 ⚠️③
- ✅ ~~**零单测**~~ ⇒ ** 13 条**（`app/tests/test_rate_limiter_resilience.py`）。
  ⚠️ **12 条离线**（替身）、**1 条必须真连 Redis**（`test_新桶在真Redis里真的带上了TTL` ——
  它是  唯一的真凭证）。实测：`REDIS_PORT=6399 pytest app/tests/test_rate_limiter_resilience.py`
  ⇒ **1 failed, 12 passed**
  ⚠️ `docs/说明/测试.md` 里那句「前者间接、后者零覆盖」**已过时**
- ✅ ~~⚠️ **限流参数写死在代码里**（`:129/132`），⛔ 不是 env~~ ⇒ **已收口**（`🅗 S6`）：
  两个 `TokenBucketLimiter` 的 `rate`/`capacity` 改从 **`app/billing/token_config.py`** 取
  （`GLOBAL_LIMIT_RATE` / `GLOBAL_LIMIT_CAPACITY` / `USER_LIMIT_RATE` / `USER_LIMIT_CAPACITY`）。
  ⚠️ **默认值逐字相同 ⇒ 行为不变**；📌 它**不是热加载**（`token_config` 只读 env，改值仍要重启）
- ✅ ~~**死导入**：`Request` / `HTTPException` / `os`~~ ⇒ **已删**（批 3 顺手）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **① 形参 `user_name` 是"用户名"** | ⛔ **不是** —— 它是**桶名**。实际传进来的有 `"global"`（`:153`）· `"anonymous"` · `"user:<真实用户名>"`。<br>⚠️ **（B9-b）之前**，`"user:..."` 那段是 **`X-API-Key` 的前 8 个字符**，**不验签** ⇒ **编一个串就换一个桶**。<br>📌 **所以本文件的 `user_name` 一词是误导性的** —— 读它时请替换成"桶名"。<br>（B9-b 已修：验不过 ⇒ 落 `anonymous` 桶。见 `app/specs/main.md`） |
| ✅ **② 「桶会自动过期」—— 【会了】** | 改前：全文**只有 `HSET`、没有 `EXPIRE`** ⇒ 键**永久留在 Redis**。**现已加**。<br>⚠️ **存量键不会因此消失** —— 本次改的是**写入路径**；已经在 Redis 里的 `rate_limit:*` **仍然是永久的**（要么让它自然被下一次写入刷新出 TTL，要么手工清）。<br>⚠️ **收益要说准（⛔ 别写成"堵了 DoS"）**：**B9-b 早把伪造 key 堵住了**（验不过 ⇒ 落匿名桶 ⇒ 造不出新桶）⇒ `EXPIRE` 现在管的是**卫生**（离网用户 / `anonymous` / `global` 各一个），**不是内存耗尽**。 |
| ✅ **③ 「Redis 挂了只是限流失效，请求照过」—— 【真的是这样了】** | 改前：⛔ **反了 —— 是非公开路径【全站 500】**（**实测**：`app/tests/test_rag_search.py` 早就写着这条）。<br>**现已改成 fail-open**：`is_allowed` / `get_remaining` / `get_limit_info` 三处各包 `except redis.RedisError` ⇒ **放行 + `logger.error`**。<br>⚠️ **`logger.error` 不是装饰** —— 没有它就成了「**限流悄悄失效**」，比报到错更危险。<br>⛔ **与 `token_tracker` 的取舍【仍故意相反】**：那边查**库**失败也是 fail-open，但**安全边界（`deps.py` 鉴权）依然 fail-closed** —— 三者都**有意识**（口径）。 |
| ⚠️ **④ `get_limit_info` 给的是"此刻的剩余"** | ⛔ **不是** —— 它算的是**消费【前】**的值：`RateLimitMiddleware.dispatch` 里**先**调 `user_limiter.get_limit_info(user_name)`、**再**调 `is_allowed`；而放行分支**用的就是那个 `info`**，**没有重算**。<br>⇒ **响应头 = 上一个状态的剩余**，⛔ 不是本次请求之后的。<br>⚠️ **本次没修**（只加容错与过期）—— 见末表。<br>📌 **原先这里写的是 `main.py:175`/`:179`/`:203-208` 三个行号** —— 换成**函数名**：行号一改就全烂（本次编辑就让它全错），而名字不会。 |

> ### ✅ ④ 的实测（**服务真跑着时验的**）
>
> ```bash
> # 快发 8 次（快过 3/秒 的回填），每次记下响应头
> for i in $(seq 1 8); do
>   curl -s -o /dev/null -D - "http://127.0.0.1:8000/api/v1/agent/available_tools" \
>     | grep -i x-ratelimit-remaining
>   sleep 0.1
> done
> # 然后直接问 Redis 桶里真实的 tokens
> docker exec qingyun-rag-agent-redis-1 redis-cli HGETALL rate_limit:anonymous
> ```
>
> | 次 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | **Redis 里实际** |
> |---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
> | `X-RateLimit-Remaining` | 19 | **19** | 18 | 17 | 16 | 15 | 14 | **13** | **12** |
>
> **✅ 证实的事实**：
> 1. **限流确实在工作** —— 计数在降（13 → 说明确实在扣）
> 2. 🔴 **响应头比 Redis 里的真实值【恒定多 1】**（末次 `13` vs 实际 `12`）
>    ⇒ **正是"消费前 vs 消费后"那一格之差**
>
> **⚠️ 尚未解释的**：**第 1、2 次都是 19**（没降）。
> 我**试过几种解释都对不上**（回填速率 / 惰性回填 / 中间件顺序），**所以不写结论**。
> ⬜ **谁要接着查**：先量一次**单轮迭代的真实耗时**（`sleep 0.1` + curl 往返可能已 > 0.33 秒，
> 那会让"回填 1、消耗 1"净持平）——**这是最可能的解释，但没验过**。
>
> 📌 **本条是一次"差点写进 spec 的未验证结论"** —— 初稿我只写了"多报 1"，
> 那是**从代码推的**；实测把"多 1"坐实了，**却同时暴露出一个我解释不了的现象**。
> ⇒ **先量、再写**（同）。
| ⚠️ **⑤ 「两个 limiter 是分开的两层，互不影响」** | ✅ **对，但有个隐含前提**：`global_limiter.is_allowed("global")` 用的是**同一个 `TokenBucketLimiter` 类**、只是桶名不同 ⇒ **全局那层也是"每人一份"的算法**，只是所有人共用一个叫 `global` 的桶。<br>⇒ 它**不是**"全站总速率的另一种实现"，**就是同一个桶**。 |

# ✅ 实施计划 · 批 3 · + （立 · **同日做完**）

> **结果**：`app/tests/test_rate_limiter_resilience.py` **13 条全绿** · 全量 **654 passed**
> **变异自证 17/17**（其中 / 相关 **10 条** —— ⚠️ **一次性脚本，⛔ 没入库**，
> 名字与预期红点逐条记在 `CHANGELOG` 的同名条目里）。
> 🔴 **RED 阶段是实测的**：真 Redis 那条在改前拿到 **`ttl = -1`**（**有键但永不过期**）——
>  那个 bug 是**验出来的**，⛔ 不是从代码推出来的。
> 📄 **决策（含  的取向与四个落点的分工）**

> **方向早就裁过了**（见上「三条"要不要做"」表，作者）：**① 要加 `EXPIRE`**
> **② fail-open + 必须响的日志**。本节只把它落成可执行的步骤 + 定下**唯一那个没定的值**（TTL 取多少）。
> ⛔ **不再重新论证方向。**

## 步骤

| # | 事项 | 落点 |
|---|---|---|
| 1 | ✅ `EXPIRE` 进 Lua 脚本 | `_TOKEN_BUCKET_LUA`（**已提成模块级常量**）<br>🔴 **实际做法与计划不同**：原计划是"**两个分支都要写**"，落地时**把那两个重复分支合并成了一条**（改前 `tokens>=1` 与 `else` 各写一遍 `HSET`+`return`）⇒ **`EXPIRE` 天生只有一条路径**，⛔ 不可能出现"某个分支忘了加" |
| 2 | ✅ TTL 常量收进 `token_config.py`（与 `*_LIMIT_RATE/CAPACITY` 同处 ——  先例） | `RATE_LIMIT_BUCKET_TTL`（默认 **60**） |
| 3 | ✅ 三处各包 `except redis.RedisError` ⇒ **放行 + `logger.error`** | `is_allowed` / `get_remaining` / `get_limit_info` |
| 4 | ✅ 用例（**离线 · ⛔ 不连 Redis**） | 🆕 `app/tests/test_rate_limiter_resilience.py`（13 条，**其中 1 条真连 Redis** —— 见下） |

⚠️ **第 4 条的偏差**：计划写的是"**全部离线**"，落地时发现 ** 只有一条真凭证** ——
「`EXPIRE` 真的生效了」**没法靠读脚本证明**（读文本只能证明"写了这句"）。
⇒ 加了 `test_新桶在真Redis里真的带上了TTL`（问 Redis 要 `TTL`；键名带 `uuid4` 隔离、`finally` 里删）。
📌 **本仓已有同一取舍**：`app/tests/test_rag_search.py` 的 L1/L2 同样必须真连 Redis。

##  · TTL 取多少 —— **本计划唯一的取值决定**

**推导，⛔ 不是拍的**：桶的语义是「**回满即无意义**」（回满了，留着它和新建一个完全等价）。
回满耗时 = `capacity / rate`：

| 桶 | capacity / rate | 回满耗时 |
|---|---|---:|
| `user_limiter` | 20 / 3.0 | **6.7 s** |
| `global_limiter` | 150 / 100.0 | **1.5 s** |

TTL 只要 **≫ 回满耗时**，语义就完全不变 —— 因为 **`EXPIRE` 写在每次请求都会跑的 Lua 里**
⇒ 它是「**空闲** TTL 秒才过期」；而**空闲的桶必然已经回满**。

⇒ **取 60 秒**（`RATE_LIMIT_BUCKET_TTL`，env 可调）：比 `user_limiter` 的回满时间大 **9 倍**。
🔒 **配一条推导型守卫**：`ttl >= ceil(capacity / rate)`，**对两个实例各算一次**
（⛔ 不写死 60）—— 将来谁把 `capacity` 调大、`rate` 调小到回满超过 TTL，**它立刻转红**。

⚠️ ** 的收益要说准（⛔ 别写成"堵了 DoS"）**：**B9-b 已经把伪造 key 堵住了**
（验不过 ⇒ 落 `anonymous` ⇒ 攻击者**造不出新桶**）。所以 `EXPIRE` 现在管的是
**「用过的键不会永久留着」**（离网用户 / `anonymous` / `global` 各一个）——
**是卫生问题，不是内存耗尽**。⛔ 别把两条混成一条说。

##  · fail-open 三处各自的返回值（**要各写各的**，⛔ 不能统一返回一个值）

| 处 | Redis 不通时返回 | 为什么 |
|---|---|---|
| `is_allowed` | `True`（放行） | 限流是**保护**，不是边界 ⇒ 失效的代价 ≪ 全站 500 |
| `get_remaining` | `self.capacity`（满桶） | 它是**只读展示**；报 0 会让调用方以为"被限死了" |
| `get_limit_info` | `{remaining: capacity, reset: now, limit: capacity}` | 同上，且响应头要能算出来 |

⚠️ **日志会按请求刷**（Redis 一直不通就一直打）—— **有意如此**：
「限流悄悄失效」比「日志吵」危险得多（本 spec ⚠️③ 的 ④ 条）。
⚠️ **捕获范围 = `redis.RedisError`**（含 `ConnectionError` / `TimeoutError` / `ResponseError`）。
⛔ **别写 `except Exception`** —— 那会把**代码 bug**（如 `TypeError`）也伪装成"Redis 不通"。

## 本轮**不动**（已看，⛔ 别读成"一起修了"）

| 处 | 现状 |
|---|---|
| ⚠️④「响应头 = 消费**前**的剩余」（恒多 1） | 仍是那样。**只加容错与过期**，⛔ **不碰取值时机**（那会动到响应头的口径，是另一条账） |
| ⚠️④ 里「第 1、2 次都是 19」那个未解释现象 | ⬜ 仍未解释（原样留着，**没验过的不写结论**） |
| `Request` / `HTTPException` / `os` 三个死导入 | ✅ **已删**（它们就在要改的那几行旁边，且 `pyflakes` 已在报） |
| 桶的 `last_time` 那两行死变量 | ✅ 已删（⚠️ 曾误报为 「桶不回填」，**已撤回** —— 见 `get_limit_info` 里的注释） |

---

## 关联

| 文档 | 说明 |
|---|---|
| `app/specs/main.md` | **唯一的生产消费者**（`RateLimitMiddleware`）· `resolve_rate_limit_identity()` 的「分桶 ≠ 鉴权」 |
| `app/specs/main.md` · `app/specs/归档/quota_limiter.md` | **配额**那一层（⚠️ 与限流**不是一回事**：**限流管频率，配额管总量**）。<br>🔴：配额那层**已在 `main.QuotaMiddleware` 原位改成 token 口径**（见 `main.md`）；`quota_limiter.py` **已删**，其 spec 移入 `归档/` |
| `后端补齐清单`  | 匿名按 IP 分桶 · 配额对匿名生效（⏸ **已挂起，挂了钩子**） |
| `后端补齐清单` **B9-b** | ✅ **已实施** —— `X-API-Key` 分桶加验签（本文件 ⚠️① 的修复） |
| `docs/说明/测试.md` §六 | 覆盖缺口：「**前者间接**、后者零覆盖」 |
| `app/tests/test_rag_search.py`（文件头） | ⚠️ 上面 ⚠️③ 那条实测的**原始出处** —— **它一直在仓里**。<br>🔴 **已就地更正**：那里原写「没有 `except RedisError` ⇒ 全站 500」，** 之后那句是假的** ⇒ 加了更正块（⛔ 不是删掉 —— 它是**当时的实况**）。<br>⚠️ **同型更正另做了两处**：`.github/workflows/ci.yml`（redis service 的理由）· （补注：结论「必需」没变，理由换了） |
| 🆕 `app/tests/test_rate_limiter_resilience.py` |  +  的 **13 条**用例（含**唯一**那条真 Redis 的 TTL 凭证） |
| 🆕  | ** 的取向**（fail-open）+ **四个落点的 fail-open/fail-closed 分工** + 同批的  |

> ### ✅ 三条"要不要做"—— **作者已裁**
>
> | # | 问题 | 裁定 | 落点 |
> |---|---|---|---|
> | **1** | **该不该给桶加 `EXPIRE`？** | ✅ **要** ⇒ **已实施** | Lua 里 `redis.call('EXPIRE', KEYS[1], ARGV[4])`<br>⚠️ **TTL 取多少？** ⇒ ✅ **已定 = 60 秒**（`RATE_LIMIT_BUCKET_TTL`）—— **推导过程见本节「 · TTL 取多少」**，⛔ 不是拍的 |
> | **2** | **Redis 不通时 fail-open 还是 fail-closed？** | ✅ **已裁：fail-open + 必须响的日志**（作者 同意）⇒ **已实施** | `is_allowed` / `get_limit_info` / `get_remaining` 三处各包 `except redis.RedisError` ⇒ **放行 + `logger.error`** |
> | **3** | **限流参数该不该收进 `token_config.py`？** | ✅ **要进** | `global_limiter` / `user_limiter` 的 `rate` / `capacity` 改从 `token_config` 取<br>⚠️ **`token_config.py` 还没建**（那是计划 ①a 的）⇒ **本条并进 ①a 的 **，⛔ 别另开一轮 |
>
> #### 🔵 ② 的理由（我方建议的完整论证，⬜ 待点头）
>
> | 维度 | 说明 |
> |---|---|
> | **① 限流是"保护"，不是"边界"** | 本仓的**安全边界是 `deps.py` 的鉴权**（那里 fail-closed **是对的**）⇒ ⛔ 混起来会把"**保护措施失效**"升级成"**服务不可用**" |
> | **② 代价差一个量级** | **fail-closed（现状）**：Redis 抖 3 秒 ⇒ **全站 3 秒 500**<br>**fail-open**：Redis 抖 3 秒 ⇒ **限流失效 3 秒** |
> | **③ 与同族对齐** | `token_tracker` 是**有意的 fail-open 且写了理由**；这边是**无意中的 fail-closed 且没写理由** |
> | **④ 配一条** | ⚠️ **`logger.error` 要响** —— 否则"限流悄悄失效"没人知道（另一种「**静默降级比报错更危险**」） |
>
> **⚠️ 一个例外**：`global_limiter` fail-open ⇒ **过载保护也失效**。
> Redis 挂了 **且** 流量暴涨 ⇒ 雪上加霜。**但仍选 fail-open** —— 低概率叠加 vs **确定的全站 500**。
