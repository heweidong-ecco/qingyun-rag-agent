# `app/agent/plan_execute.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用** —— 规划 + 逐步**真调用工具**；有超时、有总预算、有重规划、有降级<br>✅ **（批 1 ·）**：`⚠️`（重规划漏传 `user_name`）与 `⚠️`（成败判定读中文文案）**均已修** ⇒ **`⚠️` 表 6 行里还剩 共 4 行**（⚠️ 都是**说明**不是缺陷）<br>⛔ **原「1 处真缺陷 + 5 处会误判」的账已销**（见 `🟡 做到哪`）<br>🔵 **（剩余 4 条链）：`_invoke_llm` / `plan_task` 各加一个 `on_token` 形参** —— 给 `/agent/plan_execute/stream` 用。<br>· ⚠️ **默认 `None` ⇒ 行为一字符不变**（`on_token is None` 时仍走 `llm.invoke`，`:155`）。<br>· ⚠️ **⛔ 它只让「规划段」能流** —— `execute_plan` / `generate_dynamic_input` / 质量检查**都还是非流式**（作者 裁「只流规划段」）⇒ **规划段之后是一长段静默**。<br>· 🔴 **流出的是【正在生成的 JSON 片段】**（提示词要求严格 JSON）⇒ ⛔ 前端别把流到的文本直接渲染成计划，只当"规划中"指示器。<br>· ⭐ **一条实现约束**：聚合循环**必须遍历【所有】块**（含 `content` 为空的）—— provider 把 `usage_metadata` 挂在**最后一块**上，跳过它**账就没了**（实测，探针 `探针-流式与记账.py`）。<br>· 📄 端点在 `app/routing/specs/api_v1_agent.md` · 桥在 `_ThreadTokenBridge`<br>✅ ：三个 `_llm` 接上 `MAX_TOKENS_AGENT`<br>✅ （``）：三个 `_llm` **改走 `llm_factory.make_llm("chat", "agent")`**（现于 `:92` / `:279` / `:486`）—— `model`/`api_key`/`base_url`/`max_tokens` 不再写在本地。<br>⚠️ **超时/重试没丢**：`timeout` / `max_retries` 走 `make_llm` 的 `**extra` **逐点透传**，**值一字符未变**（30/20/15 + `LLM_MAX_RETRIES`）。<br>⚠️ `executor_llm` 的 `temperature=0.1` 是**本文件特有的**逐点调参，仍写在调用点上<br>⚠️ **行号口径**：本 spec 的行号为 **之后**的实测值（`grep -n` 复核）；批 4 之后**执行段整体下移 ~28 行**，⛔ 别拿旧行号去找 |
| **对外提供** | `plan_task(goal, user_name=…, on_token=None)` · `execute_plan` · `execute_plan_with_replan` · `BudgetExceededError` · 🆕 **`StepResult(ok, text, error)`**· 四个模块级常量（`PLANNER_LLM_TIMEOUT` 等三个超时 + 🆕 **`MAX_REPLANS`**） |
| **谁在用** | `api_v1_agent.py` 的 `POST /agent/plan_execute`（`:633` · **唯一生产入口**）· 🆕 `POST /agent/plan_execute/stream`（`:685`，经 `_ThreadTokenBridge`）· `app/tests/test_plan_execute_tools.py`（**26 条**，由 22 增）· 🆕 `app/tests/test_agent_stream_chains.py` · 🆕 **`app/tests/test_plan_task_user_name_wiring.py`**（AST 守卫） |
| **规模** | **696 行**（`wc -l`；⚠️ 由 620 增到 696 —— 批 1 的 // 在**代码与注释**上都加了量）· ⚠️ **文件内注释极厚**（绝大部分"为什么"已写在里面） |

> ⚠️ **本 spec 不复述文件里已有的注释** —— 那会变成"两处真相"。**这里只写【代码与注释里都没有的】**。

## ✅ 做了什么

- **两段式**：`plan_task`（规划，`llm` = `planner_llm`）→ `execute_plan_with_replan`（逐步执行 + 重规划）
- **⭐ 执行层是真调用**（重写，§十四·N15）：从 `TOOL_HANDLERS` 取真 handler，
  入参字段名从 `args_schema` **派生**。⚠️ 真调 **四** 个工具：`calculator` / `date_today` / `web_search` / `execute_python`
- **预算与记账**：`_invoke_llm()`（`:131`）是**统一入口** —— 查预算 → 调 → 记**真实** `usage_metadata`
- **可算的最坏时长**：单次超时 `30/20/15`（`:69-71`）· `max_retries=LLM_MAX_RETRIES`（`:96`）· 总预算 `120s`（`:87`）
- **降级**：同一工具连败 3 次 ⇒ 进 `failed_tools` ⇒ 后续走降级分支（**且保留真实失败原因**，`:340-342`）
- **防 ```` ``` ```` 围栏**：`_strip_code_fence()`（`:428`）—— 否则 `execute_python` 直接语法错

## 🟡 做到哪 / 缺什么

- ✅ **`:383` 的重规划调用漏传 `user_name`** —— **已修**（现于 `:428`，`plan_task(replan_context, user_name)`）。见 ⚠️①
- ✅ **重规划那条路的 `user_name` 没有测试覆盖** —— **已补两条**：
  `test_重规划把真实发起人传下去`（行为侧）+ **`app/tests/test_plan_task_user_name_wiring.py`**（AST 守卫）。
  ⚠️ **旧 stub 是 `lambda ctx: [...]`（只接一个参数）⇒ 发现不了漏传** —— 这个形状本身就是那个洞的旁证；
  现已改成收 `user_name`。
- ✅ **`max_replans` 已提成模块级常量 `MAX_REPLANS = 5`**（**· **，现于 `:96`，与三个超时放一起）
  ⇒ 原先是**写死在函数里**的局部变量（`max_replans = 5`），与那三个超时**做法不一致**。
  ⚠️ **有意【不加 env】** —— 三个超时都不是 env（硬编码常量），保持一致。
  📌 想调它就得改代码：它同时决定「**一次请求最多几次规划调用**」，而那正是**记账与预算**的输入。
- ⬜ **`_tool_arg_field` 只支持【单一入参】的工具** ⇒ 执行层**实际可用工具比注册表少**（见 ⚠️③）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| ✅ **① 「重规划也会算到发起人头上」** | **已修** —— 原先是 `:383` 的 **`plan_task(replan_context)`，漏传了 `user_name`** ⇒ 走默认 `"unknown"`。<br>**后果两条**：<br>· `check_budget_before_call("unknown")` ⇒ **不受该用户的预算约束**<br>· `record_usage(user_name="unknown")` ⇒ **算不到他头上**（`token_usage_logs` 里是 `unknown`）<br>⚠️ **最多 5 次重规划** ⇒ **最多 5 次"白跑且不记账"的规划调用**。<br>🔴 **它和 `:600` 附近记录的是同一类缺陷** —— 那里（`dynamic_input`）漏传已修，**并在注释里写了教训「改完要按行号核，别只看替换成功了几处」**。<br>📌 **教训写了（是文字），但这个文件里的另一处照样漏了 9 天。** ⇒ 本仓立场「**只有文字就漏，结构才执行**」<br>⇒ 现由 **`app/tests/test_plan_task_user_name_wiring.py`**（**从 AST 推出来**的门，覆盖直接调用 + `asyncio.to_thread` 回调两种写法）兜底。<br>⚠️ **`plan_task(` 全仓 3 处**（实测）：`:428` · `api_v1_agent.py:836` · `api_v1_agent.py:912` —— **后两处本来就没漏**。 |
| ✅ **② 「成败判定靠返回值/异常」** | **已修** —— 原先**靠【中文子串匹配】**（`:362` `if "执行失败（已重试" in step_result` · `:557` `if "执行失败" in step_result`）。<br>⚠️ 那条判据与 `:587` 那句**格式化文案**是**耦合**的：改一个字的措辞 ⇒ **失败判定静默失效**；<br>⚠️ 更险的是：**工具的返回内容里恰好出现「执行失败」四个字**，正常结果也会被判成失败、**凭空触发一次重规划**。<br>📌 与文件自己强调的「失败一律**抛异常**不吞成字符串」**方向相反**。<br>⇒ 现在 `execute_step_with_retry` / `execute_step_with_quality_check` **返回 `StepResult(ok, text, error)`**，调用方看 `.ok` 字段。<br>🔴 **`text`（用户可见文案）【不含异常原文】** —— 形如 `执行失败（已重试N次），请稍后重试（详细信息见服务端日志）`，
详情走 `logger.exception(...)`。<br>⚠️ **本仓「失败要说出来」那条不变** —— 仍然**说**「失败了、重试过几次」，只是**不再说内部细节**（`str(e)` 会带出路径 / 连接串 / SQL 片段）。<br>🔴 **`error=` 与「喂给模型的重试上下文」【保留 `str(e)`】** —— 它们**不到用户面前**；⛔ 去掉喂模型那份 ⇒ 模型不知道上一步为什么失败 ⇒ **改不出新参数 ⇒ 重试等于白试**。<br>📌 形状（同族三处）⇒ `app/routing/api_v1_agent.py` · `app/billing/token_tracker.py` · `app/routing/api_v1_rag.py`（WS 帧）。<br>⚠️ **`ok` 与 `text` 不总是同向**：质量不达标那条出口仍是 `ok=True`（改前也不触发重规划）。<br>📄 回归用例 3 条：`test_工具正常返回里恰好含那句失败文案时不许触发重规划`（决定性）· `test_真失败仍然会触发重规划`（反向守卫）· `test_重试耗尽返回结构化结果且文案不含异常原文`（契约） |
| ⚠️ **③ 「`failed_tools` 把这个工具拉黑了」** | ⛔ **只在【这一次】计划里** —— `failed_tools` / `tool_failure_counts` 都是 `execute_plan_with_replan` 的**局部变量**（`:298-299`）⇒ **不跨请求、不过期**。<br>📌 **这正是  /  要做的"模型级黑名单"缺的那一半**（那个需要**跨请求**的状态）。 |
| ⚠️ **④ 「注册表里 4 个工具都能被执行层用」** | ⚠️ **要看入参是不是单一字段** —— `_tool_arg_field` 只认**恰好一个**字段的工具；否则 `execute_single_step` 抛 `ValueError`（`:611` / `:615`）。<br>⇒ **执行层的可用工具 ⊆ 注册表**。 |
| ⚠️ **⑤ 「`execute_plan` 和 `execute_plan_with_replan` 是两个入口」** | ⛔ **不是** —— `execute_plan` 只是**一层别名**（`:422-426`，函数体就一行 `return execute_plan_with_replan(...)`）。 |
| ⚠️ **⑥ 「这个文件里为什么这么做，看注释」** | ✅ **大部分确实在注释里**（这是它的优点）。⚠️ **但也意味着**：**结论与代码在同一处** ⇒ 改了代码容易忘记同步注释，**而注释比代码更容易被当真**。<br>📌 本 spec 只收**注释里没有的**；**注释里有的别在这里再抄一遍**（本仓「一份内容只在一处」）。 |

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/api_v1_agent.md` | **唯一生产入口** `POST /agent/plan_execute`（`:633`，含 `asyncio.to_thread` 与 `BudgetExceededError` 的接法）· 🆕 **流式版** `POST /agent/plan_execute/stream`（`:685`）—— 见该 spec 的  |
| `app/routing/specs/sse.md` | 🆕 **本链流的骨架** —— ⚠️ 但本链的用法有个**独有之处**：`extract=None`（桥吐出来的**就是文本**，⛔ 不是图的消息块） |
| `app/billing/specs/token_tracker.md` | `check_budget_before_call` / `record_usage` 的本尊；✅ ** 的 3 处已在 接完**（现于 `:92` / `:279` / `:486`），**又收进 `app/core/llm_factory.py`** |
| `app/specs/main.md` | ⚠️ **中间件抛的异常接不住**（`:186` 那条警告）—— 本文件在**路由层**抛，安全 |
| `后端补齐清单` | ✅ 三个 `ChatOpenAI` **已接 `MAX_TOKENS_AGENT`**· **收进 `llm_factory`** |
| `qingyun-rag-agent-TODO待办/探针-流式与记账.py` | 🆕 ⚠️ ** 的依据**：链路改 `.stream()` 后 `usage_metadata` **还在不在**（🔴 会花钱）—— 结论：在，但**挂在最后一块**上 |

> ### ✅ 要不要做 —— **作者全部同意（三条都做）**
>
> | # | 事 | 裁定 | 怎么做（落点） |
> |---|---|---|---|
> | **1** | 🔴 **修 `:383` 的漏传** + 加一条**能发现它**的测试 | ✅ **修** | `:383` → `plan_task(replan_context, user_name)`<br>⚠️ **测试的 stub 必须收 `user_name`** —— 现有那条是 `lambda ctx: …`，**只接一个参数 ⇒ 发现不了漏传**（这正是它漏到现在的原因）<br>📌 **顺带**：把 `plan_task(` 的调用点**一起核**（就是上面那条教训说的）—— ⚠️ **全仓一共 3 处**：`:383`（**漏传，就是本条**）· `api_v1_agent.py:669`（非流式端点，**传了**）· `api_v1_agent.py:745`（流式端点，**传了**）。 |
> | **2** | ⚠️ **「成败判定」从中文子串匹配换成结构化返回** | ✅ **换** | `:362` `if "执行失败（已重试" in step_result` · `:557` `if "执行失败" in step_result`<br>⇒ 改成让 `execute_step_with_retry` **返回结构化结果**（成功/失败 + 原因），调用方看字段<br>⚠️ **`execute_step_with_quality_check` 的返回类型会变** ⇒ 它的调用点（`:359`）与测试要一起改 |
> | **3** | ⚠️ **`max_replans` 提成模块级常量** | ✅ **提** | `:301` 的 `max_replans = 5` → 模块级 `MAX_REPLANS = 5`（与 `PLANNER_LLM_TIMEOUT` 那一批放一起，`:69-87` 附近）<br>⬜ **要不要顺带支持 env？** —— 那三个超时**都不是 env**（是硬编码常量）⇒ **建议保持一致：也硬编码** |

---

# 🔵 实施计划 · **批 1 · `plan_execute` 收口**（立）

> **来源**：上面那张裁定表的 **1 / 2 / 3**（=  🅗 的 ** /  / **）。
> 三条 由 `/specs` 核账挖出，作者当天裁「**三条都做**」—— 本计划是把那个裁定**做掉**。
> **作者已批**：「**同意你的建议。开始 批 1**」。
>
> ## 🔴 顺序：**`2` → `1` → `3`**（⛔ 不是 1→2→3）
>
> **为什么把  提到最前**： 的用例和  的用例**都要驱动「这一步失败了」这条路径**
> （要逼出重规划、 要逼出「重规划到上限」）。
> 而  恰好**要把那条路径的取值方式换掉**（`str` → `StepResult`）。
> ⇒ **先立接口，后两条就不用各改一遍**；反过来做，/ 的用例写完还得再动一次。

## 目标

把 `app/agent/plan_execute.py` 上**已核实的**三条收掉：**成败判定读中文文案**· **重规划漏传 `user_name`**· **`max_replans` 是函数里的魔法数**。

**验收 = 三条各有一条【会真的红】的用例** —— 跑  全绿，且**把修复回退掉时它会红**（见每个 Task 的「变异自证」）。

## 架构

 先立**一个结构化返回类型** `StepResult(ok, text, error)`，让「成功 / 失败」变成**字段**、而不是**文案里的一段子串**；调用方按 `ok` 分流、按 `text` 取正文。
 /  在这个接口上**各改一处**，各配一条**能真发现它**的用例 —— 其中  另加一道**从 AST 推出来的**守卫（⛔ 不手工维护调用点清单）。

## Tech Stack

Python 3.10 · `pytest` · `monkeypatch` · 全**离线**（⛔ 不连真 LLM、不连库 —— 这是 `app/tests/test_plan_execute_tools.py` 现有的测法，照它）

## Global Constraints

| # | 约束 | 出处 / 判据 |
|---|---|---|
| **C1** | **测试一律 ** —— ⛔ 不许拿裸 `pytest app/ -m "not integration and not needs_db" -q` 顶替 |  文件头；**同款命令 ≠ 同款环境**（`.env` / Redis 两轴） |
| **C2** | 🔴 **⛔ 不改任何用户可见文案** ——  **只换判定依据**，`:587` 那句 `执行失败（已重试{N}次）：…` **必须逐字不动** | 本仓「一份内容只在一处」+  的定义就是**换依据不换话**； 有专门一条用例钉它 |
| **C3** | **⛔ 不碰 ** —— 扫描类判据要把它排除 | 作者 ：「worktree 在做的事情，不要动它」 |
| **C4** | **本计划的 `:NNN` 行号是 实测** —— 动手前用 `grep -n` **复核**，⛔ 别照着行号直接跳 | 本仓「拿动作成功当结果正确」族；`spec` 顶部那条「行号口径」已预警 |
| **C5** | **推送分次 ≠ 提交分次** —— 三个 Task **各一个 commit**，⛔ 不攒成一个 | 本仓 `CLAUDE.md` 推送节奏；「一件事没处理完不 commit」 |
| **C6** | 🔴 ** 有一个必须一起改的「旧测试替身」** —— 见  Step 1d | 不改它，**三条现有用例会以不可读的方式红**（`TypeError: argument of type 'StepResult' is not iterable`），而那是**假红** |

**现有测试替身清单（实测， 要动的就这三处）：**

| 位置 | 现在长什么样 |  之后 |
|---|---|---|
| `app/tests/test_plan_execute_tools.py:217-221` | `lambda step, context, goal, name: "执行失败（已重试1次）：假装失败"` | 要返回 `P.StepResult(ok=False, …)`；**顺带补上能收 `user_name` 的位置** |
| `app/tests/test_plan_execute_tools.py:393` | `lambda *a, **k: called.append(1) or "不该被调用"` | **不用改** —— 它**从不被调用**（那条用例是「预算超了 ⇒ 一步都不许执行」），字符串值不会被读 |
| `app/tests/test_plan_execute_tools.py:433` | `lambda *a, **k: "假结果"`（替身 `execute_step_with_retry`） | 🔴 **会炸** —— 改完 `"假结果".ok` 是 `AttributeError`；要改成 `P.StepResult(ok=True, text="假结果")` |

---

## · 成败判定：**中文子串 → 结构化返回**

**Files:**
- Modify: `app/agent/plan_execute.py` —— ① 新增 `StepResult`（放 `:127` `BudgetExceededError` 旁边）② `execute_step_with_retry`（`:571`）③ `execute_step_with_quality_check`（`:533`）④ `execute_plan_with_replan` 的**两处判定**（`:362` `:557`）与**取值处**（`:378` `:392` `:400`）
- Modify: `app/tests/test_plan_execute_tools.py` —— 上面 C6 表里的 **两处**替身（`:217` / `:433`）
- Test: `app/tests/test_plan_execute_tools.py` —— 新增 3 条

**Interfaces:**
- **Produces（给  / 3 用）**：`plan_execute.StepResult` —— `@dataclass(frozen=True)`，字段 `ok: bool` · `text: str` · `error: Optional[str] = None`
- **Produces**：`execute_step_with_retry(step, input_data, context, user_name="unknown", max_retries=2) -> StepResult`
- **Produces**：`execute_step_with_quality_check(step, context, user_goal, user_name="unknown", max_retries=3) -> StepResult`（**签名其余部分一字符不变**）

> ### ⚠️ 设计上唯一需要想一下的地方：`text` 与 `ok` **不总是同向**
>
> `execute_step_with_quality_check` 有一条**质量不达标但仍算完成**的出口（`:569`）：
> 它返回的是**正文 + 一句提醒**。⇒ 这里**`ok=True`、`text` 里带提醒** —— 因为
> **「质量差」不等于「这一步失败了」**（改前也没触发重规划，行为一致）。
> ⛔ 别顺手把它改成 `ok=False` —— 那是**改行为**，不在  范围内。

- [ ] **Step 1: 写会红的用例（3 条）** —— 追加到 `app/tests/test_plan_execute_tools.py` 末尾

> ⚠️ 三条是**一组**，各管一件事，⛔ 一条都别省：
> · **①** 决定性判据（改前**因为这个理由**红）
> · **②** 反向守卫（防「换成字段」最典型的坏形态：**恒 False**）
> · **③** 契约（类型 + **文案不含异常原文**，且**仍然说出失败**）

```python
# ===========================================================================
# 🔴 S10 · 成败判定不许读【中文文案】—— 必须看结构化字段
# ===========================================================================
def test_工具正常返回里恰好含那句失败文案时不许触发重规划(monkeypatch):
    """**决定性判据**：工具的**正常**返回文本里恰好出现了那句失败文案时，
    ⛔ 不许被判成「这一步彻底失败了」。

    🔴 它同时打到本文件的**两处**子串判定：
       · `:557` `if "执行失败" in step_result:` —— 连**质量检查都被跳过**
       · `:362` `if "执行失败（已重试" in step_result:` —— **凭空触发一次重规划**

    ⚠️ 为什么必须**驱动真函数**（⛔ 不 monkeypatch `execute_step_with_quality_check`）：
       把它替换掉，就等于把「被判定的那个值」自己造出来 —— 那样测的是**我的替身**，
       不是这条路径。这里只让**最外面那层**（LLM / 工具）是假的：
       `calculator` 的 handler **正常返回**一段含该文案的文本，于是
       `execute_single_step` → `execute_step_with_retry` → `execute_step_with_quality_check`
       **全是真的**。
    """
    import plan_execute as P

    _MARKER = "搜索结果：`执行失败（已重试3次）：boom` 是重试耗尽的标记"
    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", lambda args: _MARKER)
    monkeypatch.setattr(P, "generate_dynamic_input", lambda *a, **k: "1+1")
    monkeypatch.setattr(P, "check_step_quality", lambda *a, **k: True)

    replans = []
    monkeypatch.setattr(P, "plan_task", lambda *a, **k: replans.append(1) or [])

    out = P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "calculator"}], "目标", "u1")

    assert replans == [], (
        "工具**正常返回**的文本里恰好含那句失败文案 ⇒ 被判成「这一步彻底失败」"
        " ⇒ 触发了一次**凭空的重规划**（还把这句正常结果塞进了 replan_context）。\n"
        "⇒ 成败必须看 `StepResult.ok`，⛔ 不能读中文文案（改一个字的措辞就静默失效）。\n"
        f"实际输出：\n{out}"
    )
    assert "步骤1完成" in out, f"正常的一步被当成失败处理了：\n{out}"


def test_真失败仍然会触发重规划(monkeypatch):
    """反向守卫： 之后**失败仍然认得出** —— 防止有人把判定写成**恒 False**。

    ⚠️ **它在改动前后都是绿的**（改前靠文案也认得出）。留着是因为
       「换成字段」这件事最典型的坏形态就是**恒 False**，而那种坏法
       ⛔ **上面那条用例发现不了**（它对「判成功」的路径更敏感）。
    """
    import plan_execute as P

    def _explode(args):
        raise RuntimeError("boom")

    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", _explode)
    monkeypatch.setattr(P, "generate_dynamic_input", lambda *a, **k: "1+1")
    monkeypatch.setattr(P, "check_step_quality", lambda *a, **k: True)

    replans = []
    monkeypatch.setattr(P, "plan_task", lambda *a, **k: replans.append(1) or [])

    P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "calculator"}], "目标", "u1")

    assert replans == [1], "工具真的炸了，却没触发重规划 ⇒ 失败判定失效了（恒 False？）"


def test_重试耗尽返回结构化结果且文案不含异常原文(monkeypatch):
    """`execute_step_with_retry` 重试耗尽时**返回 `StepResult`**，
    且**用户可见的那句话一字不改**。

    ⚠️ 两半**都要**：
      · 类型那一半 —— 它是「调用方看字段」的前提
      · 文案那一半 ——  **只换判定依据**，⛔ 不许顺手改用户看到的话
    """
    import plan_execute as P

    def _explode(args):
        raise RuntimeError("boom")

    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", _explode)
    monkeypatch.setattr(P, "generate_dynamic_input", lambda *a, **k: "1+1")

    r = P.execute_step_with_retry(
        {"step": 1, "tool": "calculator"}, "1+1", "", "u1", max_retries=1)

    assert isinstance(r, P.StepResult), (
        f"重试耗尽返回的是 `{type(r).__name__}` —— 调用方只能回去读文案（的原始形态）")
    assert r.ok is False
    assert r.error == "boom"
    assert r.text == "执行失败（已重试1次）：boom", (
        f"用户可见文案被改了：{r.text!r}\n⇒  只换判定依据，⛔ 不改用户看到的话。")
```

- [ ] **Step 1b: 改【旧测试替身】两处**（⛔ 不改就是一堆 `TypeError` 假红）

`app/tests/test_plan_execute_tools.py:217-221`（`test_downgraded_step_keeps_the_real_reason`）：

```python
    # ⚠️  起要返回 `StepResult`；`*a, **k` 让它同时兼容  之后多传的 `user_name`
    monkeypatch.setattr(P, "execute_step_with_quality_check",
                        lambda *a, **k: P.StepResult(ok=False, text="执行失败（已重试1次）：假装失败",
                                                     error="假装失败"))
```

`app/tests/test_plan_execute_tools.py:433`（`test_dynamic_input_receives_the_real_user_name`）：

```python
    monkeypatch.setattr(P, "execute_step_with_retry", lambda *a, **k: P.StepResult(ok=True, text="假结果"))
```

> ⚠️ `:393` 那处（`lambda *a, **k: called.append(1) or "不该被调用"`）**不用改** ——
> 它**从不被调用**，值不会被读（那条用例断言的是「一步都没执行」）。

- [ ] **Step 2: 跑，确认【红】—— 且红的理由是「文案被当判据」，不是别的**

Run:
Expected: **红**，且**摘要**必须是 `1 failed, 1 passed, …` 里那 1 条失败是
`test_工具正常返回里恰好含那句失败文案时不许触发重规划`，报错是断言
`replans == []`（实际 `[1]`）。
⚠️ **另一条新用例 `test_重试耗尽返回结构化结果且文案不含异常原文` 也应当红**（`AttributeError: StepResult`）。
⚠️ **判据**：看到 `TypeError: argument of type 'str' is not iterable` 之类**别的**错 ⇒ **停**，
说明 RED 的理由不对 —— 先查 `Step 1b` 的替身有没有改全。

- [ ] **Step 3: 实现（`app/agent/plan_execute.py`）**

**(a)** `:27` 那行 typing 导入加 `Optional`；文件顶部 import 区加 `dataclass`：

```python
from dataclasses import dataclass
from typing import List, Dict, Optional
```

**(b)** 在 `:127` `class BudgetExceededError(Exception):` **上方**新增类型（放它旁边 = 同属「本模块的对外类型」）：

```python
@dataclass(frozen=True)
class StepResult:
    """一步执行的结果。

    ## 为什么要这个类型（🔴 ）

    **改前**：成败靠**中文子串**判 —— `if "执行失败（已重试" in step_result`。
    ⚠️ 那条判据与**格式化文案**耦合，两种坏法：
      ① 改一个字的措辞 ⇒ 失败判定**静默失效**（看起来在重试，其实没有）
      ② **工具返回的正文里恰好出现那四个字** ⇒ 正常结果被判成失败、**凭空触发重规划**

    ⇒ 现在「成功 / 失败」是**字段**，⛔ 不再是文案的一部分。

    ## ⚠️ `text` 与 `ok` 不总是同向（⛔ 别以为 `ok=True` 就万事大吉）

    `execute_step_with_quality_check` 有一条出口是「**跑通了、但没通过质量检查**」——
    它返回 `ok=True`、`text` 里带一句提醒（`:569` 附近）。**质量差 ≠ 这一步失败了**，
    改前它也不触发重规划 ⇒ 行为保持一致。

    ## ⚠️ `text` 是**用户可见**的

    ⛔ 别把它当内部日志改写 —— 它原样进 `results`、原样返回给调用方。
    """

    ok: bool
    text: str
    error: Optional[str] = None
```

**(c)** `:571-587` `execute_step_with_retry` —— **只改返回，⛔ 那句文案一个字不动**：

```python
def execute_step_with_retry(step: Dict, input_data: str, context: str,
                            user_name: str = "unknown", max_retries: int = 2) -> StepResult:
    # …（前面的循环体不动）…
            try:
                result = execute_single_step(step, input_data, context)
                return StepResult(ok=True, text=result)
            except Exception as e:
                last_error = str(e)
                if attempt == max_retries:
                    # ⚠️ 这句文案【用户可见】， 不许改它（Global Constraints · C2）
                    return StepResult(ok=False,
                                      text=f"执行失败（已重试{max_retries}次）：{last_error}",
                                      error=last_error)
```

**(d)** `:533-569` `execute_step_with_quality_check` —— 返回类型换成 `StepResult`：

```python
def execute_step_with_quality_check(step: Dict, context: str, user_goal: str,
                                    user_name: str = "unknown",
                                    max_retries: int = 3) -> StepResult:
    # …
        step_result = execute_step_with_retry(step, input_data, context, user_name)
        if not step_result.ok:            # 🔴 原 `:557` 的子串判定
            return step_result
        # ⚠️ 质量检查要的是【正文】（`step_result.text`），不是整个结果对象。
        # 🔴 **`check_step_quality` 收 5 个参数**（`:561` 实测）——
        #    改这里时只换第 2 个实参，⛔ 别顺手把后面三个删了。
        if check_step_quality(step, step_result.text, user_goal, context, user_name):
            return step_result
    # …重试循环结束…
    return StepResult(ok=True,               # ⚠️ 质量差 ≠ 失败（见 `StepResult` 的 docstring）
                      text=step_result.text + "\n[注意：此步骤经过多次重试，质量可能不达标]")
```

**(e)** `:362` 与 `:378` `:392` `:400` 三处取值：

```python
        if not step_result.ok:            # 🔴 原 `:362` 的子串判定
            tool_failure_counts[step["tool"]] = tool_failure_counts.get(step["tool"], 0) + 1
            # …
            replan_context = f"…{step_result.text}…"                    # `:378`
            results.append(f"步骤{step_num}失败且重规划失败：{step_result.text}")   # `:392`
        else:
            result_summary = f"步骤{step_num}完成：{step_result.text[:200]}"         # `:400`
```

- [ ] **Step 4: 跑，确认【绿】**

Run:
Expected: **绿**。⚠️ **判据是条数**：`plan_execute` 相关用例 `+3`（本 Task 新增的），
且**其余条数一条不少** —— ⛔ 别只看 "passed"。
📌 复核命令： 对比改动前后那一行摘要。

- [ ] **Step 5: 🔴 变异自证 —— 证明用例【真的】在管这件事**

把 `:362` 那行 `if not step_result.ok:` **改回** `if "执行失败（已重试" in step_result.text:`，
再跑一次 —— **`test_工具正常返回里恰好含那句失败文案时不许触发重规划` 必须转红**。
✅ 看到它红 ⇒ **恢复**原行，再跑一次确认全绿。

> ⚠️ **这一步不是仪式**：本仓的立场是「**只有文字就漏，结构才执行**」。
> 一条**从没红过**的用例，与「没有这条用例」在机器痕迹上**完全一样**。
> 📌 这也正是  能漏到现在的原因 —— 。

- [ ] **Step 6: 更新 spec 的状态栏** —— 本文件 `⚠️ 看代码会误判` 表里的 **②** 行（`:37`，做完了就该换口径），
      以及 `🟡 做到哪` 里与它相关的那条

- [ ] **Step 7: Commit**

```bash
cd <本仓根>
git add app/agent/plan_execute.py app/tests/test_plan_execute_tools.py app/agent/specs/plan_execute.md
git commit -m "fix(plan_execute) 成败判定从中文子串换成结构化返回（S10）"
```

---

## · 重规划漏传 `user_name`

**Files:**
- Modify: `app/agent/plan_execute.py:383` —— `plan_task(replan_context)` → `plan_task(replan_context, user_name)`
- Create: `app/tests/test_plan_task_user_name_wiring.py` —— AST 守卫（⚠️ `test_*` 前缀 ⇒ **不触发**模块 spec 门）
- Test: `app/tests/test_plan_execute_tools.py` —— 新增 1 条

**Interfaces:**
- **Consumes**：`plan_task(user_goal: str, user_name: str = "unknown", on_token=None) -> List[Dict]`（`:208`，**签名不变**，本 Task 只是「调用它时把 `user_name` 传上」）
- **Consumes**：`P.StepResult`

> ### 🔴 **执行记录：计划漏了一处**（实际做的时候才发现）
>
> 本计划**只列了 `app/tests/test_plan_execute_tools.py` 的两处替身**（那两处）。
> 实际一跑，**`test_downgraded_step_keeps_the_real_reason` 的 `plan_task` 替身也红了** ——
> 它是 `lambda ctx: [_step("calculator") for _ in range(5)]`，**只收一个参数**
> ⇒ 调用点改成传两个之后 `TypeError`。
>
> ⚠️ **这处漏列本身是个好证据**：那条替身**只接一个参数**，正好**兜住了漏传**
> （少传一个 ⇒ 不报错）—— 它**就是  那个洞能活 9 天的原因**
> （📌 `🟡 做到哪` 里原本就写着「那条 stub 发现不了漏传」，但计划没把它推成「**所以它也要改**」）。
> ⇒ 已改成 `lambda ctx, user_name="unknown": ...`。
>
> 📌 **教训（与  同型）**：清单是照**我读过的**整理的，而**漏掉的往往是"看起来没问题的那处"**。
> 这也是为什么  除了行为用例之外，还要一道**从 AST 推出来的**门。

> ### 🔴 为什么这条值得单开一道门（⛔ 不靠「记得搜一遍」）
>
> **同一个漏传在这个文件里是第二次**：修过 `generate_dynamic_input` 那处
> 「改完要按行号核，别只看替换成功了几处」——
> **而重规划那处照样漏着**，一直到 `/specs` 核账才被挖出来（就是本文件 ⚠️①）。
>
> ⇒ **教训写在注释里不管用** ⇒ 把它变成一道**从代码里推出来的**门。
> 📌 同型判据的先例：`app/tests/test_bm25_cache_invalidation_wiring.py`。

- [ ] **Step 1: 写会红的用例（`app/tests/test_plan_execute_tools.py` 末尾追加）**

```python
def test_重规划把真实发起人传下去(monkeypatch):
    """ · 重规划那一次 `plan_task` 必须收到**真实发起人**。

    🔴 改前是 `plan_task(replan_context)` —— 走默认 `"unknown"`，后果两条：
       · `check_budget_before_call("unknown")` ⇒ **不受该用户的预算约束**
       · `record_usage(user_name="unknown")` ⇒ **算不到他头上**
       ⚠️ 最多 **5** 次（`MAX_REPLANS`）⇒ 最多 5 次「白跑且不记账」的规划调用。

    ⚠️ **替身必须收 `user_name` 并记下来** —— 旧的 `lambda ctx: [...]` **只接一个参数**，
       它只能发现「多传了」，**发现不了「漏传」**。
       🔴 **这正是这个洞漏到现在的原因**（本文件 ⚠️① 的「测试没覆盖」那条）。
    """
    import plan_execute as P

    seen = []

    def _fake_plan_task(ctx, user_name="unknown", on_token=None):
        seen.append(user_name)
        return []          # 空计划 ⇒ 循环 break，不用真跑工具

    monkeypatch.setattr(P, "plan_task", _fake_plan_task)
    monkeypatch.setattr(P, "execute_step_with_quality_check",
                        lambda *a, **k: P.StepResult(ok=False, text="炸", error="炸"))

    P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "calculator"}], "目标", "someone_real")

    assert seen == ["someone_real"], (
        f"重规划的 `plan_task` 收到的是 {seen} —— **不是真实发起人**。\n"
        "⇒ 这次调用的 token 既不受他的预算约束、也不算在他头上。"
    )
```

- [ ] **Step 2: 跑，确认【红】**

Run:
Expected: **红**，且失败的是 `test_重规划把真实发起人传下去`，断言信息里 `seen == ['unknown']`。
⚠️ 若它**绿** ⇒ **先别继续**：说明 `Step 1` 里那条失败路径没被驱动到
（检查 `execute_step_with_quality_check` 的替身有没有返回 `ok=False`）。

- [ ] **Step 3: 实现 —— 一行**

`app/agent/plan_execute.py:383`：

```python
            # ⚠️ `user_name` 必须传下去 —— 否则这次规划走 `"unknown"`：
            #    `check_budget_before_call("unknown")` 不受该用户预算约束，
            #    `record_usage(user_name="unknown")` 也不记在他头上。
            # 📌 同族的漏传在本文件已犯过两次；现由 `app/tests/test_plan_task_user_name_wiring.py` 兜。
            new_plan = plan_task(replan_context, user_name)
```

- [ ] **Step 4: 写【AST 守卫】——新文件 `app/tests/test_plan_task_user_name_wiring.py`**

```python
"""守卫：**每一个 `plan_task` 调用点都必须把 `user_name` 传下去**。

## 为什么单开这道门（⛔ 不靠"记得搜一遍"）

同一个漏传**在这个文件里是第二次**：修过 `generate_dynamic_input` 那处
「改完要按行号核，别只看替换成功了几处」—— **而重规划那处照样漏着**，
直到 `/specs` 核账才挖出来（`app/agent/specs/plan_execute.md` ⚠️①）。

⇒ **教训写在注释里不管用**（本仓原话：**只有文字就漏，结构才执行**）
⇒ 把它变成一道**从代码里推出来的**门。📌 先例：`app/tests/test_bm25_cache_invalidation_wiring.py`。

## 判据：`plan_task` 出现在实参位置上时，那一组实参里必须带 `user_name`

覆盖本仓现存的**两种写法**（实测，全仓共 **3** 处）：
  ① **直接调用** —— `plan_task(replan_context, user_name)`（`plan_execute.py`）
  ② **当回调传给 `asyncio.to_thread`** —— `asyncio.to_thread(plan_task, goal, user_name)`
     （`api_v1_agent.py` ×2，两个端点）

⚠️ **已知边界**：只按**裸名字**认（`plan_task`）。用 `import ... as` 起别名、或跨模块重导出，
   **本文件认不出** —— 那时它会**漏**。那是**判据的边界**，⛔ 不是"这里已经安全"。
"""

import ast
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
_TARGET = "plan_task"


def _call_sites():
    """⇒ `[(文件名, 行号, 传给 plan_task 的实参, 那些实参上的关键字), ...]`"""
    sites = []
    for path in sorted(API_DIR.glob("*.py")):
        if path.name.startswith("test_"):     # ⚠️ 只扫生产模块（同 bm25 那道门的口径）
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # ① 直接调用：plan_task(...)
            if isinstance(node.func, ast.Name) and node.func.id == _TARGET:
                sites.append((path.name, node.lineno, node.args, node.keywords))
                continue
            # ② 当【回调】传给别人：`plan_task` 之后的那些实参才是它的
            #    （`to_thread(func, *args, **kwargs)` ⇒ func 后面的实参/关键字都归它）
            for i, arg in enumerate(node.args):
                if isinstance(arg, ast.Name) and arg.id == _TARGET:
                    sites.append((path.name, node.lineno, node.args[i + 1:], node.keywords))
    return sites


def test_每个_plan_task_调用点都传了_user_name():
    sites = _call_sites()

    # ⚠️ 先钉住"扫描本身没瞎" —— 名字/目录一变就成空集，而**空集会让下面恒过**
    assert sites, (
        "AST 扫描没找到任何 `plan_task` 调用点（实测应为 3 处）——"
        "判据本身可能失效了。先查：① 调用点是不是被挪去别的目录了 ② 是不是改成别名调用了。"
    )

    bad = [
        f"{fname}:{lineno}（只给了 {len(args)} 个实参、也没有 user_name= 关键字）"
        for fname, lineno, args, keywords in sites
        if len(args) < 2 and not any(kw.arg == "user_name" for kw in keywords)
    ]

    assert bad == [], (
        "以下 `plan_task` 调用点**没传 `user_name`** ⇒ 它会走默认 `\"unknown\"`：\n  - "
        + "\n  - ".join(bad)
        + "\n⇒ 这次调用的 token **既不受该用户的预算约束、也不算在他头上**。"
        "\n   ⚠️ 这个洞在本仓已经漏过两次（`generate_dynamic_input` 那次 + 重规划那次）。"
    )
```

- [ ] **Step 5: 跑 —— 全部绿；并【自证这道门会红】**

Run:
Expected: **绿**。

🔴 **然后手动自证这道门不是摆设**（⛔ 别跳）：把 `:383` 改回 `plan_task(replan_context)`，再跑。
Expected: **两条同时红** —— `test_重规划把真实发起人传下去` **和** `test_每个_plan_task_调用点都传了_user_name`
（报错里点名 `plan_execute.py:383`）。✅ 都红了 ⇒ **改回来**，再跑一次确认绿。

> ⚠️ 这一步是本 Task 的**核心验收**：它要证的不是"我修好了"，
> 是「**下次再漏，会有人喊**」。

- [ ] **Step 6: 更新 spec** —— `app/agent/specs/plan_execute.md` 的 ⚠️① 行 + `🟡 做到哪` 前两条

- [ ] **Step 7: Commit**

```bash
cd <本仓根>
git add app/agent/plan_execute.py app/tests/test_plan_execute_tools.py app/tests/test_plan_task_user_name_wiring.py app/agent/specs/plan_execute.md
git commit -m "fix(plan_execute) 重规划补传 user_name，并加一道从 AST 推出来的守卫（S9）"
```

---

## · `max_replans` → 模块级 `MAX_REPLANS`

**Files:**
- Modify: `app/agent/plan_execute.py` —— ① `:87` 附近加常量 ② `:301` 删局部变量 ③ `:312` `:407` `:408` 三处引用
- Test: `app/tests/test_plan_execute_tools.py` —— 新增 1 条

**Interfaces:**
- **Consumes**：`P.StepResult`、`plan_task(ctx, user_name)` 调用形式
- **Produces**：`plan_execute.MAX_REPLANS: int = 5`（模块级）

> ### ⚠️ 为什么**不加 env**（⛔ 别顺手加）
>
> 同一批的三个超时（`:69-71` `PLANNER_LLM_TIMEOUT` / `EXECUTOR_LLM_TIMEOUT` / `QUALITY_LLM_TIMEOUT`）
> **都是硬编码常量，没有一个是 env**。 的诉求是「**与那一批做法一致**」——
> 加 env 反而**制造新的不一致**，还会把「一次请求最多调几次 LLM」变成**部署期可变量**。
> ⇒ **硬编码 `MAX_REPLANS = 5`**，与那三个放一起。作者裁定（本文件裁定表第 3 行）同此口径。

- [ ] **Step 1: 写会红的用例**

```python
def test_重规划次数上限是可配的模块级常量(monkeypatch):
    """ · `MAX_REPLANS` 是**模块级常量**，且循环**真的按它**停。

    ⚠️ 光断言"有个常量叫 MAX_REPLANS"**不够** —— 那只验了名字。
       这里把常量**改成 2**，然后驱动一条「永远失败、永远能重规划」的路径，
       数 `plan_task` 被叫了几次 ⇒ 证明**循环读的是它**。
       ⚠️ 期望 **3** = `MAX_REPLANS + 1`：循环条件 `replan_count <= MAX_REPLANS`（`:312`），
       `replan_count` 从 0 起算 ⇒ 0/1/2 各进一次循环、每次重规划一次。

    🔴 **每一步必须换一个工具名**（⛔ 别一直用 `"calculator"`）——
       同一个工具连败 **3** 次会进 `failed_tools`，而那条降级分支（`:322-348`）
       是 `pop(0)` + `continue`，**⛔ 根本不调 `plan_task`**。
       ⇒ 那样数出来的次数**比 `MAX_REPLANS` 小**，用例会以一个**看不懂的理由**红。
       （⚠️ 本仓同族前科：`test_downgraded_step_keeps_the_real_reason` 就是那条分支的用例。）
    """
    import plan_execute as P

    monkeypatch.setattr(P, "MAX_REPLANS", 2)
    monkeypatch.setattr(P, "execute_step_with_quality_check",
                        lambda *a, **k: P.StepResult(ok=False, text="炸", error="炸"))

    calls = []

    def _fake_plan_task(ctx, user_name="unknown", on_token=None):
        # ⚠️ 记 `(次数, user_name)` 之外的信息没必要 —— 本条只钉【几次】
        calls.append(user_name)
        # 每次都给一个【新】计划（工具名递增 ⇒ 不触发 failed_tools 降级）⇒ 循环不提前 break
        return [{"step": 1, "action": "a", "tool": f"tool{len(calls)}"}]

    monkeypatch.setattr(P, "plan_task", _fake_plan_task)

    P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "tool0"}], "目标", "u1")

    assert len(calls) == 3, (
        f"`MAX_REPLANS=2` 时应重规划 3 次（0/1/2，循环条件是 `<=`），实际 {len(calls)} 次。\n"
        "⇒ 要么循环没读模块级常量、要么读的地方漏改了。\n"
        "   ⚠️ 若实际是 1 次，先查 `failed_tools` 那条降级分支有没有被踩到。"
    )
```

> ⚠️ **这条用例同时依赖 **：替身 `_fake_plan_task` 收 `user_name`，
> 而  之前它会被以「1 个位置参数」调用 —— 那也照样能收（有默认值），**不影响**。
> 它**不依赖** `user_name` 的**值**，所以  不做它也绿 —— 这是**有意**的：一条用例只钉一件事。

- [ ] **Step 2: 跑，确认【红】**

Run:
Expected: **红** —— `AttributeError: module 'plan_execute' has no attribute 'MAX_REPLANS'`
（`monkeypatch.setattr` 对不存在的属性默认 `raising=True`）。
✅ 这个错**正是**「它还不是模块级常量」的证明。

- [ ] **Step 3: 实现**

**(a)** `:87` `PLAN_TOTAL_BUDGET_SECONDS = 120` **下面**加：

```python
# 一次请求里最多重规划几次。
# ⚠️ 从 `execute_plan_with_replan` 的局部变量提上来 ——
#    原先写死在函数里（`max_replans = 5`），与上面那三个超时的做法不一致。
# ⚠️ **有意【不加 env】**：三个超时都不是 env（硬编码常量），保持一致。
#    📌 想调它就得改代码 —— 因为它同时决定「一次请求最多几次规划调用」= 记账与预算的输入。
MAX_REPLANS = 5
```

**(b)** `:301` **删掉** `max_replans = 5`（整行）。

**(c)** 三处引用改成 `MAX_REPLANS`：

```python
    while current_plan and replan_count <= MAX_REPLANS:          # :312
    if replan_count > MAX_REPLANS:                               # :407
        results.append(f"[系统] 已达到最大重规划次数（{MAX_REPLANS}次），执行终止。")   # :408
```

- [ ] **Step 4: 跑，确认【绿】**

Run:

- [ ] **Step 5: 🔴 两半自证 —— ① 没有残留的硬编码 ② 用例真的在管这件事**

**① 按位置核，⛔ 别只数「改了几处」**（本仓前科：盲替换命中注释、真行没改到 —— `:546-549` 那段注释记的就是这个）：

Run: `grep -n 'max_replans\|MAX_REPLANS' app/agent/plan_execute.py`
Expected: **4 行** —— 定义 1 行 + 引用 3 行，**全部是大写** `MAX_REPLANS`，
**小写 `max_replans` 零命中**。⚠️ 还剩小写 ⇒ 漏改了一处。

**② 变异**：把定义那行的 `5` 改成 `3`，跑这条新用例 —— **它不该红**
（用例自己 `monkeypatch` 成 `2`）。
✅ 不红 ⇒ 证明它钉的是「**循环读常量**」这个**行为**，⛔ 不是「常量等于 5」这个**值**。
🔴 然后把 `:312` 写回 `replan_count <= 5`（硬编码）再跑 —— **它必须红**。
✅ 红了 ⇒ 这条用例**真的在管这件事**。⇒ 恢复，再跑一次确认全绿。

> 📌 **为什么两半都要**：① 管「**有没有漏改**」，② 管「**用例本身有没有用**」。
> 一条**从没红过**的用例，与「没有这条用例」在机器痕迹上**完全一样**。

- [ ] **Step 6: 更新 spec** —— `🟡 做到哪` 第 3 条（`:28`）

- [ ] **Step 7: Commit**

```bash
cd <本仓根>
git add app/agent/plan_execute.py app/agent/specs/plan_execute.md
git commit -m "refactor(plan_execute) max_replans 提成模块级 MAX_REPLANS（S11）"
```

---

## 收尾（三个 Task 都绿之后）

- [ ] **Step 1: 全量复核** ——  绿，且**条数**与改动前对比 `+5`
- [ ] **Step 2: spec 顶部状态栏** —— `:5` 那句「🔴 **但查出 1 处真缺陷 + 5 处"看代码会误判"**」要改（`⚠️①②` 两条已消）· `:7` 的用例数（`22 条`）要跟着数
- [ ] **Step 3: ** —— 🅗 里  /  /  三条标 ✅
- [ ] **Step 4: `CHANGELOG.md`** —— 按仓里现有格式记三条（⚠️ **别照抄上份 commits 的「不用写 CHANGELOG」理由**）
- [ ] **Step 5: ⛔ 先别开 PR** —— 按作者 「#97 暂时不开，和后面的任务一起开」：
      这三条 commit **留在 `feat/gates-in-ci` 上**，等后面的批次做完一起走 `#97`。
      ⚠️ 但 **push 照常按段推**（推送分次 ≠ 提交分次）

### ⚠️ 本计划【没做】的三件事（⛔ 别读成"遗漏"）

| 事 | 为什么不做 |
|---|---|
| `⚠️` **③④⑤⑥** 四行（`failed_tools` 不跨请求 / 工具入参单字段 / `execute_plan` 是别名 / 注释厚） | **都是「说明」不是「缺陷」**。③ 的下一半是 /（跨请求黑名单），**不在批 1** |
| `⬜ _tool_arg_field 只支持单一入参` | 同上 —— 是**能力边界**，要扩是**另一个功能**，不在 `S9/S10/S11` 的定义里 |
| **加 env 开关控制 `MAX_REPLANS`** | **有意不做** —— 见  顶部那条「为什么不加 env」 |


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/agent/plan_execute.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ 整段**原样搬来**，⛔ 未改写。

> ## **执行层现在是【真调用工具】**（重写 · §十四 · N15）
>
> **此前（核实）**：`execute_single_step` **只对 `calculator` 真调用**，
> 其余全部走「请 LLM 模拟执行」—— 端点 `/agent/plan_execute` 看起来在跑真工具，
> **实际只有规划是真的**。
>
> **现在**：`execute_single_step` 从 MCP 注册表取**真 handler** 并**真调用**，
> 入参字段名从工具的 `args_schema` **派生**（不手写映射）。
>
> **真调的代价 —— 这些现在会真的发生**：
>   · **会真的发起网络请求**（`web_search`）
>   · **会真的执行代码**（`execute_python`，沙箱内、5 秒上限）
>   · **耗时与失败率都不再由 LLM"编"决定** —— 会有真超时、真报错
>   · **浏览器工具已按 N13 从注册表摘掉**（依赖未安装的 chromium）
>     执行层**当前只有 4 个工具可用**：`calculator` / `date_today` / `web_search` / `execute_python`
>
> 登记位置：`docs/待办登记--全仓审计与方向更正.md` §十四（N15）。
> 裁决记录：作者 「**从零新做**」，原话「**大改是必要的**」。
