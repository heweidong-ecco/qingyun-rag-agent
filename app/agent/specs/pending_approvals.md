# `app/agent/pending_approvals.py`

| 项 | 内容 |
|---|---|
| **状态** | 🆕 **新建（·）** —— 待接管队列（硬门 D 的"数据"那一半）<br>🔴 **丙段改键**：登记键由**裸 `thread_id`** 改成 **`session_key(user_name, thread_id)`**，并新增 `raw_thread_id` / `graph` 两个字段<br>🔴 **加轮次上限**：新增 `rounds` 字段 + `approval_round_cap`，让"放行后又停"**有界** |
| **对外提供** | 函数 `register` / `resolve` / `list_pending` / **`find_by_raw_thread_id`** / **`approval_round_cap`** / `clear` / `warn_if_backend_mismatch`；<br>经路由 **`GET /agent/pending`** 露出 |
| **谁在用** | `api_v1_agent.py` —— `langgraph_chat`（登记/注销）· `langgraph_chat_stream`（登记/注销）· **`memory_chat`（登记/注销）** · `approve_agent_action`（**反查属主** + 注销）· `list_pending_approvals` 端点 |

## ✅ 做了什么

- **一张内存注册表**：`{会话键 → {thread_id, raw_thread_id, user_name, graph, tool_calls, rounds, since}}`，`threading.Lock` 保护
- `register`（同一键**覆盖**）· `resolve`（**幂等**，注销不存在的**是正常的**）· `list_pending`（**卡得最久的排最前**）
- **`find_by_raw_thread_id(raw)`** —— 丙段新增：`/agent/approve` 拿到的只有**原 `thread_id`**，
  靠它**反查属主**，再按属主拼键（见「看代码会误判」第 1 行）
- 🆕 **`rounds`**：登记时记"这条会话**第几轮**停在审批点"
  （首次 = **1**，每次"放行后又停"由 `/agent/approve` **+1**）。
  ⚠️ **本模块只【存】它，不做裁决** —— "到没到上限、到了怎么办"在 `approve_agent_action`。
- 🆕 **`approval_round_cap()`（同日）**：一次会话允许的轮次上限，**默认 3**；
  env **`MAX_APPROVAL_ROUNDS`** 可覆盖，**非法值回退默认**（⛔ 不抛 —— 与  那个"写错就静默失效"不同：这里**退化成保守默认值而非零行为**）。
- `warn_if_backend_mismatch()` —— 启动警告（见下）

## 🟡 做到哪 / 缺什么

- ⚠️ **只在内存** ⇒ **进程重启即清空**（v1 有意如此，见下）
- ⬜ **没有分页 / 过滤** —— `list_pending()` 一次返回全部。⚠️ 队列长起来会一次性打给调用方
  · 🔴 **说明（缺口③）**：端点 `GET /agent/pending` 的**可见性**已收窄为
    **本人默认 · admin 全量**，但 ⚠️ **那两行过滤写在【端点里】（`app/routing/api_v1_agent.py`），
    ⛔ 本模块一行都没改** —— `list_pending()` 仍然**返回全部**。
    ⇒ **别以为"收窄了"就轮到本模块了**：在这里加过滤**等于把授权逻辑搬进数据层**，
    而本模块**没有**、也**拿不到** `user_name`（它只按调用方给的键找）。
    📄 裁定与理由 ⇒  §二·发现③。
- ⬜ **没有"超时自动清理"** —— 登记后没人处理，它会**一直留在队列里**（在进程活着的前提下）
  ⚠️ **轮次上限（`rounds`）不等于超时清理** —— 它只封"**同一个人反复批**"这一条路；
     登记后**根本没人批**的会话，照样**永久留队**。两件事，别混。
- ➡️ **（接管后续跑）已做**—— ⚠️ **但续跑逻辑不在本模块里**，
  在 `app/routing/api_v1_agent.py` 的 `approve_agent_action`（见 `app/routing/specs/api_v1_agent.md`）。
  **本模块只负责"谁停了 / 是谁的 / 停在哪张图"**，⛔ 不碰"批了之后图怎么接着跑"。
- 🔴 **队列【也】按身份分桶了**（丙段）：键 = `session_key(user_name, thread_id)`。
  ⚠️ 改动前它和 checkpoint 一样按**裸 `thread_id`** 记账 ⇒ 两个人用同一个 `thread_id`
  时，**队列里也串号** —— `/agent/approve` 会拿着**别人的**键去续跑。
- ✅ **有测试**：`app/tests/test_pending_approvals.py`（7 条 · **纯离线** ⇒ 进 CI）
  · 🆕 丙段另有 `app/tests/test_approve_ownership.py`（归属校验 · 歧义拒绝）
  · `app/tests/test_memory_chat_approval.py`（图名登记 + `/agent/approve` 按图路由）
  · 🆕 **轮次上限**：`app/tests/test_approval_resume.py` §⑥（4 条：首次 = 1 · 每次 +1 · 触顶强制收尾 · 收尾不住则 error 且不入队）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「`MemorySaver` 里存着全部会话，反查一下就知道谁卡住了」** | ⛔ **不能** —— `MemorySaver` **只按 `thread_id` 取，没有"列出全部"的 API**。**这正是本模块存在的唯一理由**。⚠️ 别试图绕过它去读 checkpoint |
| 🔴 **「重启后队列还在」** | ⛔ **不在** —— 它是**进程内存**，重启即空。⚠️ **与默认的 `MemorySaver` 一致**（那个也在内存）⇒ 两边一起丢，**不会对不上** |
| 🔴 **「设了 `AGENT_CHECKPOINT_BACKEND=sqlite` 也没事」** | ⚠️ **有事** —— 那时**图落盘、队列不落盘** ⇒ **重启后图仍在等审批、队列里查不到** ⇒ **会话变孤儿**（谁也找不到它，它自己也走不下去）。<br>⚠️ **这个组合不会自己报错** ⇒ **唯一防线是启动时那条警告**（`warn_if_backend_mismatch`）。<br>⇒ **本模块 v1 只支持内存后端**，⛔ 别在没改这个的前提下开 sqlite |
| ⚠️ **「`resolve` 注销一个不存在的 thread 会报错」** | ⛔ **不报** —— **幂等**。`/agent/approve` 会调到它，而那条路**本来就会遇到"没有待审批任务"**（那时要求它不抛） |
| ⚠️ **「`list_pending` 随便什么顺序都行」** | ⛔ **有契约**：**按 `since` 升序 = 卡得最久的排最前**（最该先处理）。⚠️ 排错了**不会报错**，只会让人**从最不紧急的那条开始处理** |
| ⚠️ **「返回的 dict 可以随便改」** | ⚠️ **浅拷贝** —— 改**顶层字段**不影响注册表，但 **`tool_calls` 那个 list 是共享的**，⛔ 别去改 |
| ⚠️ **「`clear()` 是个重置接口」** | ⛔ **不是** —— **仅供测试隔离**。**生产代码里零调用点** |
| 🔴 **「`/agent/approve` 按【调用方】拼键就行」** | ⛔ **不行** —— `/agent/pending` 是**跨用户队列**（硬门 D）：按调用方拼 ⇒ admin 会拼出 `admin:…`、属主是 `alice:…` ⇒ **admin 永远批不了别人的**。⇒ 必须**先 `find_by_raw_thread_id` 查属主、再按属主拼**。🔴 **这条是 的裁定**（作者），守卫 ⇒ `app/tests/test_approve_ownership.py` |
| 🔴 **「`thread_id` 字段 = 调用方传的原值」** | ⛔ **不是** —— 它是**拼过的会话键**（`11:isolation_a:default` 这种）。**原值**在 **`raw_thread_id`** 里（丙段新增）。⚠️ 拿错一个，`approve` 就会去一个**不存在的桶**里找，然后答"没有待审批任务"（**不报错**） |
| 🔴 **「`graph` 字段可以省，反正只有一张图」** | ⛔ **有两张**：`/agent/langgraph_chat` 走 `agent_graph`、`/agent/memory_chat` 走 **`checkpointer_agent`**（丙段给它加了审批门）。`/agent/approve` **按这个字段路由** ⇒ 写错/不写 ⇒ 会话**永远放行不了**。⚠️ 默认值 `"agent_graph"` 只是兼容老调用点 |
| ⚠️ **「一个原 `thread_id` 只会有一条登记」** | ⚠️ **不保证** —— 两个人用同一个 `thread_id="default"` 就**有两条**。`find_by_raw_thread_id()` 因此返回 **list**；`/agent/approve` 遇到多条会**如实拒绝**（⛔ 不"挑第一条" —— 那是随机批一个人的会话） |
| 🔴 **「`rounds` 只是个展示字段」** | ⛔ **不是** —— 它是**封顶的依据**：`/agent/approve` 放行后若模型**又**停在审批点，会拿 `candidates[0]["rounds"]` 与上限比，**到了就不再入队**。⚠️ **不传 `rounds` 的调用点会退回默认 1**（`register` 的形参默认值）⇒ 那个调用点**永远在第 1 轮**。老调用点（`langgraph_chat` 首登记）**本就该是 1**，故无碍；⚠️ **但将来若新增"非首次"的登记点，必须显式传 `rounds`** |
| ⚠️ **「上限到了就报错」** | ⛔ **不是** —— 先**努力收尾**：注入一条"已达上限、请直接作答"的 `ToolMessage` 并**续跑**，收尾成功就**正常返回答案**（`forced_finish=True`）。只有"连收尾提示都拦不住"才 `status="error"`。**报错是最后一档，不是第一档** |
| 🔴 **「`list_pending` 返回的就是调用方能看的那批」** | ⛔ **不是**（缺口③，）—— 本函数**返回全部**，**可见性收窄在端点**（`api_v1_agent.py` 的 `list_pending_approvals`，本人默认 · admin 全量）。<br>⚠️ **为什么不在这一层收**：本模块是**纯数据层**、**拿不到调用方身份**（它的入参只有"找哪条"）⇒ 在这里过滤就得**把身份传进来**，那是把授权搬进存储。<br>📌 判据 ⇒ `app/tests/test_pending_visibility.py`（**改的是端点，⛔ 不是本模块**）。 |

## 关联

· `app/agent/specs/agent_graph.md` / `app/agent/specs/agent_checkpointer.md`（`interrupt_before=["approval"]` 那一侧 —— **本模块记的是"谁停了、停在哪张图"，它管的是"为什么停"**）
· `app/access/specs/session_key.md`（**键怎么拼** —— 本模块的键就是它生成的）
·  **决策 2 / 丙段**（按属主拼 + 「本人或 admin」· 按登记的图路由）
· **§六·2**（轮次上限 · 作者 裁「上限 3 轮」）
· 🔴 **§二·发现③**（**可见性收窄发生在【端点】，⛔ 本模块一行没改** —— ）


---

## 📎 原先写在代码 docstring 里的设计说明

> 来源 = `app/agent/pending_approvals.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ 整段**原样搬来**，⛔ 未改写。

**已知限制（v1）**：本表在**内存**里 ⇒ **进程重启即清空**。
   与**默认的** `MemorySaver` **是一致的**（那个也在内存）⇒ 两边一起丢，不会对不上。

   **但若设了 `AGENT_CHECKPOINT_BACKEND=sqlite`**：**图的状态落盘了、本表还在内存**
      **重启后图仍在等审批，而队列里查不到** ⇒ 会话变孤儿（谁也找不到它，它自己也走不下去）。
      **这个组合不会自己报错** —— 要等人重启后去队列里找才发现。
      ⇒ 启动时由 `warn_if_backend_mismatch` 给警告（唯一的防线）。

计划 / 裁定 ⇒ `app/routing/specs/api_v1_agent.md` 的「实施计划 ② · 」·
   模块 spec ⇒ `app/agent/specs/pending_approvals.md` · 判据 ⇒ `app/tests/test_pending_approvals.py`
