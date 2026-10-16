# `app/tools/safe_math.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **新建（2026-10-03）** —— `calculator` 工具的**求值实现**，替代 `eval(expression)` |
| **规模** | 235 行 |
| **对外提供** | `calculate(expression) -> str`（**永不抛**）· `evaluate(expression)`（**会抛**）<br>异常 `InvalidExpression` / `UnsafeExpressionError`（子类）· `DivisionByZero`（子类）<br>常量 `MAX_EXPRESSION_LENGTH` / `MAX_POWER_EXPONENT` / `MAX_RESULT_BITS` |
| **谁在用** | **6 处**：`agent_graph.py` · `agent_checkpointer.py` · `agent_graph_advanced_learning.py` · `simple_tools_impl.py`（→ `simple_tools` → `mcp_server`）· `tools_with_cache.py`（⚰️ 生产不可达）· **`api_v1_rag.py`（第 6 处，2026-10-04 才挖出来 ——）**<br>🔴 **原来写「5 处」是错的** —— 漏的那一处**一直写的是 `eval`**（`2c1a922` · 2026-07-17 引入），**而且（当时）匿名 WS 可达**。<br>✅ **2026-10-05**：那处 WS 已加首帧认证 ⇒ **不再是匿名可达**。⚠️ **但"可达的人"只从路人换成了持凭据的用户** —— 而 `expression` 由 LLM 生成、上下文含用户输入 ⇒ **注入面没消失，闸不能撤**。 |
| **依赖** | **只有标准库**（`ast` / `math` / `operator`）⇒ ⛔ **别加第三方依赖**（`simple_tools_impl.py` 的「只 import 标准库」不变量靠这条成立） |

## ✅ 做了什么

- **AST 白名单求值**：`ast.parse(..., mode="eval")` 后递归求值，**只放行**
  `+ - * / // % **`、一元 `+ -`、括号、`int` / `float` 字面量
- **三道闸**：表达式长度 ≤ 200 · 指数 ≤ 1000 且预判结果位宽 · 结果 ≤ 4096 位
- **两种入口**：`evaluate` 抛异常（给测试与内部用）· `calculate` 兜一切（给工具当最后一道，⛔ 不许抛出去）
- **错误契约与改前一致**：成功 `str(结果)`；失败 `计算错误: {原因}`

## 🟡 做到哪 / 缺什么

- ✅ **有测试**：`app/tests/test_safe_math.py`（**55 条**，单元）+ `app/tests/test_safe_math_wiring.py`（**23 条**，接线）⇒ 合计 **78 条，全离线进 CI**
- ⚠️ **可接受的算式比 `eval` 窄**（**有意的**）：`'a'*3` / `len([1,2])` 这类**以前"能算"**，现在被拒
- ⬜ **不支持具名函数**（`sqrt(2)` / `abs(-1)`）—— 要支持就得往 `ast.Call` 开一个**具名白名单**，
  ⛔ **不许直接放开 `ast.Call`**（那等于回到 `eval`）。见
- ⬜ **`tools_with_cache.calculator` 没有行为守卫**（只进了静态那道）—— 它被 `@cached_tool` 包着，
  调一次要连 Redis ⇒ 只能是静态。**见  与 `test_safe_math_wiring.py` 的文件头**

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「`calculate` 里那个 `except Exception` 是偷懒」** | ⛔ **不是** —— 它是**契约**：这个函数是给 LLM 当工具用的，**抛出去 = 一次 500**。改前那 5 处也都是 `try/except` 包着返回字符串。**删掉它就是改了对外行为** |
| 🔴 **「限流/DoS 那些闸是防御性编程，删了也不影响正确性」** | ⛔ **影响可用性**：`9**9**9` 右结合 ⇒ 指数是 **387420489**；**没有那道闸就是一次 CPU 打满**。⚠️ 它**天然没法做变异自证** —— 拆掉闸之后连"跑一次看它红"都会把机器卡死 ⇒ 改用 `test_the_right_gate_fires` **断言"是哪道闸拦的"**来间接守 |
| ⚠️ **「`evaluate` 和 `calculate` 差不多」** | ⛔ **契约相反**：`evaluate` **抛**（测试要用它分辨"为什么不合法"），`calculate` **永不抛**。<br>⚠️ 另有第三个区别：**`1/0` 走 `evaluate` 抛的是 `DivisionByZero`**，**文案是 `division by zero` 原文** —— 对外文案，⛔ 别顺手改成中文 |
| ⚠️ **「`bool` 跟 `int` 一样，不用特判」** | ⛔ **要特判** —— Python 里 `isinstance(True, int)` 是 `True` ⇒ 不特判 `True+True` 会算出 `2` |
| ⚠️ **「指数上限是"检查字面量"」** | ⛔ **是检查算出来的值**：`9**(9*9*9*9*9*9*9)` 的指数**字面上是个算式**，算出来 4782969 ⇒ 一样拒。⚠️ 判据是**大小**，不是"是不是字面量" |
| 🔴 **「`().__class__.__bases__[0].__subclasses__()` 这种是靠"过滤关键词"挡的」** | ⛔ **不是** —— 本模块**没有黑名单**：它是**白名单**（名单外的一切节点都进不来），所以这类链**不是被"认出来"的，是"根本不在名单里"**。⚠️ 这正是它比 `eval(expr, {"__builtins__": {}}, {})` 结实的原因（后者**实测放行**这条链，） |
| ⚠️ **「`tools_with_cache` 那份也收口了，所以它也安全了」** | ⚠️ **半对**：它**确实**改成 `calculate` 了，但**没有行为测试守着** ⇒ 只有 AST 那道静态闸。⚠️ 别把它和另外 4 处等同看待 |
| 🔴 **「守卫说"全仓没有 `eval`" ⇒ 那就真没有」** | ⛔ **曾经是假的，而闸是绿的**。原判据只认**调用的形状** —— `ast.Call` 且 `func` 是裸名 `eval` ⇒ 认得出 `eval(x)`，**认不出 `asyncio.to_thread(eval, x)`**（`eval` 在那儿是**实参**）。<br>🔴 **`api_v1_rag.py:801` 正是这个形状**，写着真的 `eval`、**（当时）匿名 WS 可达**、**两道闸全返回空**。<br>✅ **2026-10-04 改判据**：只认**名字**（`ast.Name.id == "eval"`），与调用形状无关 ⇒ 一次抓住两种（⛔ 不误伤 `redis_client.eval(...)`，那是 `ast.Attribute`）。<br>⚠️ **该处的行为守卫做不到** —— `get_agent_executor()` 里的 `calculator` 是**嵌套函数、import 不到** ⇒ 只有静态闸。**成因与 `tools_with_cache` 不同**（那份是够不着 Redis），**⛔ 别合并成一条读** |

## 关联

（**为什么换、为什么否掉另外三个方案** —— 含实测证据）·
（**是它记下来的**，本条把它结掉）·
`app/agent/specs/agent_graph.md`（⚠️ 表里"`calculator` 不进白名单 ≠ 它安全"那条）·
`app/tests/test_safe_math.py` · `app/tests/test_safe_math_wiring.py` · `app/tests/test_impl_modules.py`（**既有契约**：`1/0` 文案）
