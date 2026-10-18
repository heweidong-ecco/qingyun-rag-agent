# `app/access/session_key.py`

| 项 | 内容 |
|---|---|
| **状态** | 🆕 **新建（丙段）** —— 会话键：**把身份拼进 checkpoint / 会话 id** |
| **对外提供** | 一个函数：`session_key(user_name, thread_id) -> str`（**两个形参都必填、都无默认值**） |
| **谁在用** | `api_v1_agent.py` —— 4 张图的 **7 处** `{"configurable": {"thread_id": …}}`：<br>`langgraph_chat` **150** · `langgraph_chat_stream` **234 / :294** · `advanced_agent_chat` **480** · `memory_chat` **577** · `mcp_agent_chat` **868** · `approve_agent_action`（续跑）**:372**<br>+ 待接管队列的登记/注销（见 `app/agent/specs/pending_approvals.md`） |

## ✅ 做了什么

- **一个无歧义的拼接**：`f"{len(user_name)}:{user_name}:{thread_id}"`
- `_require_identity()` —— **fail-closed**：缺身份（`""` / `None`）⇒ **抛 `ValueError`**，
  ⛔ **不是**"当成匿名 / 退回默认桶"

## 🔴 为什么必须存在（这个 bug 长什么样）

4 张图（`agent_graph` · `checkpointer_agent` · `advanced_agent` · `mcp_agent`）、
**7 处** config 的 key **里都没有人**。而 `thread_id` 在**本文件 6 条端点上**
**默认值就是 `"default"`** ⇒ **两个用户都用默认值 ⇒ 共用一个 checkpoint 桶**。
而 state 的 reducer 是 `Annotated[List, operator.add]` ⇒ **消息是 append 的**
⇒ **B 的提问接在 A 的历史后面，模型两边的都看得到**。

⚠️ **不是"理论上会串"，是"默认就串"** —— 默认值本身就是 `"default"`。

### 对照：**花费**那条轴没有这个问题

`check_session_token_budget(user_name, thread_id)` 的键**含 `user_name`**（`token_tracker.py:847`）。
⇒ 同一个仓里**两套口径**：**花钱的账按人算，对话记忆不按人算。**
⚠️ 这也意味着**修的时候别顺手去改花费那条轴** —— 它本来就是对的，改了等于动别人的桶。

## 🟡 做到哪 / 缺什么

- ⚠️ **只解决"对话记忆串号"** —— ⛔ 不解决**文档**的可见性（那是**甲/乙段**）·
  ⛔ 也不解决 `/agent/trace/{thread_id}` 的**追踪**可见性（**第三条轴**，见下）
- ⬜ **没有做键的【反解】**（`session_key -> (user, thread)`）。当前**不需要**：
  `/agent/approve` 是从**待接管队列**里读**属主**（`raw_thread_id` 字段），⛔ 不是反解键。
  ⚠️ 将来的代码**别**去"切分这个字符串" —— 切错了**不报错**，只会静默指向另一个桶。

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「`f"{user}:{thread}"` 就够了」** | ⛔ **不够，有歧义**：`("a","b:c")` 与 `("a:b","c")` **都拼成 `"a:b:c"`** ⇒ **两个不同的人共用一个桶**。而用户名由 `create_user_api_key()` 创建、**没有字符校验** ⇒ 含 `:` 是可能的。⚠️ 在一个**专门修隔离**的改动里留一条歧义拼法 = 没修。**长度前缀**让"用户名到哪儿结束"**写死** ⇒ 切分点唯一 |
| 🔴 **「响应里的 `thread_id` 就是图里用的那个键」** | ⛔ **不是** —— 响应回显的是**调用方传进来的原值**（对内对外**两套**）。⚠️ 这个区分是**故意的**：调用方后续要拿它去 `/agent/approve`，契约一变**所有调用方都得改**。守卫 ⇒ `app/tests/test_session_isolation.py::test_response_echoes_the_raw_thread_id` |
| 🔴 **「4 张图就是 4 处 config」** | ⛔ **7 处** —— `langgraph_chat_stream` 一条端点就占 **3 处**（`astream` :234 · `aget_state` :294，两处必须用**同一个**键，否则「流到一半」和「收尾判定」看的是**两个桶**） |
| 🔴 **「`mcp_agent_chat` 里那个 `"thread_id": thread_id` 也应该一起改成 `sess`」** | ⛔ **别改** —— 那是喂给 **state** 的，走的是**追踪/花费轴**（`agent_graph_advanced.py:239/334/359` → `record_tool_*`），而它的**读**端点 `/agent/trace/{thread_id}`（`:1029`）用的**也是原值**。⚠️ **两条轴，别合并**：合并就得连追踪的读写一起改（那是另一件事） |
| ⚠️ **「传空就当匿名，别抛错」** | ⛔ **抛错**（`ValueError`）—— 本仓已裁定 **fail-open 是根因**（§二）⇒ 与 `db._require_identity` / `bm25_index._require_identity` **同一条约定**。守卫 ⇒ `app/tests/test_session_key.py::test_missing_identity_raises` |
| ⚠️ **「两个形参给个默认值方便些」** | ⛔ **不给** —— 有默认值 = "可以忘记传" = **还是 fail-open**（漏传即 `TypeError`，⛔ 不是静默串号）。同 `hybrid_search(..., *, user_id)` 的约定 |

## 判据（可打印）

```
venv/bin/python -m pytest app/tests/test_session_key.py app/tests/test_session_isolation.py -q -p no:warnings
grep -c 'thread_id": sess' app/routing/api_v1_agent.py        # ⇒ 7（= 该文件里 config 的处数；实测）
grep -c 'session_key(user_name, thread_id)' app/routing/api_v1_agent.py   # ⇒ 5（sess 的赋值点）
```

## 关联

 **决策 1 · 丙段**（拼法裁定）
· `app/agent/specs/pending_approvals.md`（**键的另一半** —— 队列也按它记账）
· `app/routing/specs/api_v1_agent.md`（7 处接线的现场）
· `app/core/specs/db.md` / `app/rag/specs/bm25_index.md`（**甲段**的承重层 —— 那条轴管**文档**，这条管**会话记忆**）


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/access/session_key.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ **内容不改写**；只清理内部编号 / 批次号这类工作日志坐标。

## 为什么要有这个模块

4 张图（`agent_graph` · `checkpointer_agent` · `advanced_agent` · `mcp_agent`）、
**7 处** `{"configurable": {"thread_id": thread_id}}` —— **key 里都没有人**。
而 `thread_id` 在 **6 条端点上**的默认值就是 `"default"`
（`api_v1_agent.py` 的 `:112` `:157` `:389` `:428` `:484` `:735`）
⇒ **两个用户都用默认值 ⇒ 共用一个 checkpoint 桶**。
而 reducer 是 `Annotated[List, operator.add]` ⇒ **消息是 append 的**
⇒ B 的提问接在 A 的历史后面，**模型两边的都看得到**。

**不是"理论上会串"，是默认就串** —— 默认值本身就是 `"default"`。

## 对照：花费那条轴【没有】这个问题

`check_session_token_budget(user_name, thread_id)` 的 key **含 `user_name`**（`token_tracker.py:847`）
⇒ 同一个仓里两套口径：**花钱的账按人算，对话记忆不按人算。**

## 为什么要"长度前缀"而不是 `f"{user}:{thread}"`

朴素拼法**有歧义**：`("a", "b:c")` 与 `("a:b", "c")` 都会拼成 `"a:b:c"`
⇒ **两个不同的人共用一个桶**。
而用户名由 `create_user_api_key` 创建，**没有字符校验** ⇒ 含 `:` 是可能的
⇒ 在一个**专门修隔离**的改动里留一条歧义拼法，等于没修。
⇒ 用**长度前缀**：用户名占多少字符是写死的 ⇒ **切分点唯一** ⇒ 无歧义。

裁定 ⇒ （决策 1 · 丙段）
模块 spec ⇒ `app/access/specs/session_key.md` · 判据 ⇒ `app/tests/test_session_key.py`
