# `app/tools/tool_cache.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **已接进产品路径** —— 🔴 **此前它是"没接上的模块"**（整块死代码，见下） |
| **对外提供** | `cached_tool()`（装饰器）· `get_ttl()` · `TTL_BY_TOOL`（**唯一**的 TTL 事实源）<br>底层：`get_tool_cache_key()` · `get_cached_tool_result()` · `set_cached_tool_result()` |
| **谁在用** | `simple_tools.py`（`calculator` / `date_today` / **`date_calc`** / **`json_extract`** / **`stats`** —— 🔴 后三个 新增）· `search_tools.py`（`web_search`）· `code_executor.py`（`execute_python`）<br>⇒ ⭐ **缓存包在 `@tool` 那一层**，不是调用点、不是 handler<br>⚠️ **本行原带 `:11` / `:42` / `:17` 三个行号，已删** —— 本仓：行号锚点**系统性漂移**⇒ 一律改用**模块名 / 函数名** |
| **测试** | `app/tests/test_tool_cache.py`（**8**）· `app/tests/test_tool_cache_wiring.py`（**5**）<br>⚠️ 数字会变，判据：`cd app && ../venv/bin/python -m pytest test_tool_cache.py test_tool_cache_wiring.py --collect-only -q \| tail -1` |

## ✅ 做了什么

- **三大防护**：防穿透（空值也缓存，60 秒）· 防击穿（Redis `nx` 互斥锁）· 防雪崩（TTL 加随机抖动）
- ⭐ **TTL 表只有一份**：`TTL_BY_TOOL` —— 2026-10-08 从
  `agent_graph_advanced.CACHE_TTL_MAP` **搬过来并收口**（那份只盖住 `/agent/mcp_chat` 一条路）
  · 🔴 **2026-10-08：4 条 → 7 条**（+`date_calc` / `json_extract` / `stats`，**全是 86400**）
- ⭐ **缓存接在工具函数体上** ⇒ 四条执行路径**天然全覆盖**，将来加第 5 条路径也不用动
  （改动的点是 3 个**工具定义文件**，⛔ 不是 4 条路径）
- **抢锁有上限**（`_LOCK_WAIT_SECONDS = 2.0`，`:94`）：等超了就**降级直通**。
  ⚠️ 改前是 `else` 分支里递归调用自己 ⇒ 锁一直拿不到**把栈打爆**（实测 `RecursionError`）
- 🔴 **Redis 不通 ⇒ fail-open**（`:96`）：只捕 `redis.RedisError`，直通执行 + 一条 ERROR

## 🟡 做到哪 / 缺什么

- ⬜ **`web_search` 的缓存键不含"结果页改版"这个维度** —— 必应改版那天，旧键会在 TTL 内继续返回旧内容（无害，300 秒后自愈）
- ⬜ **没有"缓存命中率"这类观测指标** —— Redis 挂掉时的**静默降级**只能靠日志发现（见 ⚠️ 表最后一行）
- ⚠️ **`execute_python` 的 TTL 是 0（直通）**，⛔ 不是"忘了包缓存"

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「TTL 表里没写的工具 ⇒ 用默认 60 秒」 | 🔴 **抛 `KeyError`**（`get_ttl`，`:44`）—— **故意不给默认值**。`.get(name, 60)` 会把「新加了工具但忘了登记」变成「它被缓存 60 秒」：**不报错、没人会知道** |
| 「`date_today` 没包缓存」 | 🔴 **包了**，只是 **TTL=0 ⇒ 直通**。让「哪些工具带缓存」**只有 `TTL_BY_TOOL` 一处**回答（⛔ 不是"有的没包、有的 TTL=0"两种形状混着） |
| 「`date_calc` 的 TTL 是 86400 ⇒ 那 `date_today` 也该给 86400」 | 🔴 **两回事**。`date_calc` 的 `start_date` **必填**（`simple_tools_impl.date_calc_impl` 的红线）⇒ 它**没有"今天"这种隐藏输入**，同一组入参**永远同一个答案**。`date_today` 的入参是**空** ⇒ 缓存键不含日期 ⇒ 跨天会返回昨天。🔴 **反悔成本**：哪天给 `date_calc` 加了"默认今天" ⇒ **必须同时把它的 TTL 改成 0** |
| 「命中缓存时会返回 `[缓存命中]` 后缀」 | ⛔ **不会了** —— 那是 `agent_graph_advanced` 那份内联缓存的产物，2026-10-08 已并掉。⚠️ **别把这读成"缓存没了"**：缓存还在，只是**不再往结果里写字** |
| 「`should_cache` 是个通用的失败判定」 | 🔴 它是**逐工具给的谓词**（现在只有 `web_search` 用）。⛔ **别改成"含『失败』就跳过"那种猜法** —— 每条工具失败的形状不同，猜出来的规则是**静默**的 |
| 「Redis 挂了 ⇒ 工具会 500」 | ⛔ **不会** —— fail-open。⚠️ 但**降级是静默的**：「缓存从不命中」与「根本没在缓存」在机器痕迹上**长得一样**（本仓原话：**「『从不命中』与『没人违规』在机器痕迹上完全一样」**）⇒ **只能靠那条 ERROR 日志与健康检查发现** |
| 「这是个内部工具模块，改动随便」 | 🔴 **它是 `calculator` 唯一的安全落点** —— `calculator` 的实现（`safe_math.calculate`）现在**全仓只有一处定义**（`simple_tools_impl`），`app/tests/test_safe_math_wiring.py` 守着它 |

## ⚠️ 看代码【真的】会误判的一条（历史）

2026-10-08 之前，本文件的 `@cached_tool` **一个产品调用点都没有** ——
 里它被登记为「**整块死代码**」，和 `tools_with_cache.py` 并列。
⚠️ **那一栏现在已经过期**：本批之后它是**唯一的**缓存实现（`tools_with_cache.py` 已被删除）。

📌 **教训**：登记"死代码"时若只按**当时**的引用数算，**接上之后没人会回头改那一栏**。

## 关联

- **决策** ⇒ （Redis 不通 fail-open）· （缓存的唯一落点）·
- **守卫** ⇒ `app/tests/test_tool_cache_wiring.py`（"四条工具**真的**带缓存"—— 调的是
  `mcp_server.TOOLS` 里那个 `@tool` 对象，与 Agent 拿到的是同一个）·
  `app/tests/test_tool_cache.py::test_every_mcp_registered_tool_has_a_ttl`（**新工具忘了登记 TTL 的唯一拦网**）
- **邻居** ⇒ `app/tools/specs/safe_math.md`（`calculator` 的安全实现）
- ⚠️ **`app/tools/simple_tools.py` / `app/tools/search_tools.py` / `app/tools/code_executor.py` 目前【没有 spec】**
  （=`spec_status.sh` 的「没 spec」名单里）—— 本批**没给它们建**，⛔ 别以为这里漏写了路径
- ⚠️ **`app/access/rate_limiter.py` 的 ** —— fail-open 的**同形先例**，本文件的捕获范围照抄它的形状
