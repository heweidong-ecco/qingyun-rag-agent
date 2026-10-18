# `app/agent/approval_audit.py`

## ✅ 做了什么

- 审批留痕的**唯一落点**：惰性建表 + 写（`record_decision`）+ 读（`list_decisions`）
  + `summarize_tool_calls`（留痕里「为什么」那栏的摘要）。
- 表 `approval_events`：`owner`（会话是谁的）· `actor`（谁做的裁决）· `raw_thread_id`
  `graph` · `decision` · `edited`（有没有代替模型给结论）· `rounds` · `reason` · `created_at`。
- 写入方：`app/routing/api_v1_agent.py` 的 `POST /agent/approve`（每次**真的落到图上的裁决**写一条）。
- 读取方：`GET /agent/approvals/history` → 接管页（`app/static/web/approvals.html`）下半栏。
- 建表方式与 `budget_intercepts` **完全同款**（`token_tracker.py` 里那段）：
  **不在 `db.py` 的 `create_table()` 里**，第一次写入时惰性 `CREATE TABLE IF NOT EXISTS`。

## 🟡 做到哪 / 缺什么

- ✅ 四个出口里**「真的落到图上的裁决」**都记（`approved` / `rejected`，含 `forced_finish=True` 那次
  与「放行后模型又停在审批点」那次）。
- ⛔ **不记**「根本没批成」的**四条**：没有可待批任务 · 歧义（多条）· 无权限 · **登记陈了**
  （图没停在审批点）。⚠️ 前三条是  §3.2 列的，**第四条是施工时核出来的**
  （见下面「看代码会误判的地方」）。
- ⛔ **不记「越权审批的尝试」** —— 要它得另定保留期与读取权限，属另一件事（§六·3）。
- ⛔ **无保留期 / 无清理任务** —— 表会一直长。

## ⚠️ 看代码会误判的地方

- 🔴 **留痕的落点位置是刻意的，⛔ 别"顺手往上挪"。** 它写在 `/agent/approve` 的
  `current_state.next != ("approval",)` **那道守卫之后** —— 那条出口（登记陈了）发生在
  **归属校验通过之后**，把留痕挪到它前面，就会记下一次**根本没落到图上**的裁决。
  判据：`app/tests/test_approve_audit_wiring.py::test_stale_registry_records_nothing`。
- 🔴 **`edited=True` ≠「改了模型的答案」。** 审批点上模型**还没有生成答案** —— 它停下是因为
  「我要调这个敏感工具」（`tool_calls` 就是这个请求）。`edited` 的真实语义是
  「**人工代替模型给出了这次工具调用的结果**」（回填成 `ToolMessage` + 显式 `as_node="tools"`，
  走 `api_v1_agent._tool_rulings`）。⇒ 界面主文案必须如实写，⛔ 别写「改写答案」。
- **fail-open 是刻意的**：写失败只 `print` 一行、读失败返回 `[]`。
  ⇒ **表里少一条 ≠ 那次裁决没发生**。查历史时⛔ 别把"查不到"读成"没批过"。
- **`owner` 与 `actor` 是两个身份**：admin 接管 alice 的会话时 `owner="alice"`、`actor="admin"`。
  合成一个字段 ⇒ 留痕当场变假话，**而且不报错**。
- **`list_decisions(owner=..., limit=50, offset=0)` 是必填关键字参数**：`None` = 全量。
- 🔴 **`offset` 是 加的**（分页 · 口径 ⇒ 下面那条「⛔ 不许拿 `count == limit` 猜」· 门 ⇒ `app/tests/test_truncation_declared.py`）——
  ⚠️ **翻页要正确，`ORDER BY` 必须是【全序】**：本表是 `created_at DESC, id DESC`，
  第二条排序键 `id` 是**承重的**（连写的几条 `created_at` 极可能同毫秒）。
  ⇒ **不是全序就会漏行/重行，而且不报错**。真库用例 ⇒ `app/tests/test_approval_events_db.py::test_offset_paging_neither_skips_nor_repeats`。
  ⛔ **别给它加默认值** —— 那会让「我忘了传」与「我要查所有人」再也分不出来（口径）。
- **`_COLUMNS` 的顺序必须与 `list_decisions` 的 `SELECT` 列顺序一致** ——
  靠 `zip` 配名，**错位不报错**，只会把 `decision` 显示成 `actor`。

## 关联

 · `app/agent/specs/pending_approvals.md`
`app/routing/specs/api_v1_agent.md` · `app/schema.sql` · `app/tests/test_approval_events_db.py`（真库那一半）

> ✅ **已结清 —— `approval_events` 进 `app/schema.sql` 了**
>
> * **主判据（⛔ 不会把自己数进去 —— 靠【行首锚】）**：
>   `grep -c '^CREATE TABLE public\.approval_events' app/schema.sql` ⇒ **1**（**改前 0**）。
>   🔴 **这条判据被写歪过两次（都是"尺子自我指涉"）** ⇒ 过程留在 `app/schema.sql` 的文件头里：
>   `grep -c 'approval_events'` 得 13 · `grep -c 'CREATE TABLE public.approval_events'`（无锚）得 2
>   —— **都是因为它把写它的那句说明自己也数了进去**。加 `^` 后恒为 1。
>   ⚠️ ** 原写「应为 1」—— 那个数是猜的**（写它的人没见过真 dump）⇒ **真实判据是「0 → 非 0」**。
> * 🔴 **「先真跑一次 approve」这一步【不需要再做】** —— 那次早就发生过：
>   本机真库 `approval_events` **已有 456 行**，表**早就被惰性建出来了**
>   （判据：`docker compose exec -T postgres psql -U postgres -d rag_db -tAc "SELECT count(*) FROM approval_events"`）。
>   实测列与类型**逐格等于** `_DDL` ⇒ **PG 认这份 DDL**。
>   ⚠️ **⛔ 别再为了"跑一次"往真库补写一条** —— 那是**假留痕**（比脏数据更糟，见下）。
> * ✅ **补了真库用例** ⇒ `app/tests/test_approval_events_db.py`（**6 条** · `-m needs_db` · 在 `rag_test` 上跑）：
>   真写 → **真读回来** → 逐格断言。🔴 **反证跑过**：把 `_DDL` 的 `reason` 改成 `reasons` ⇒ **6 failed**；
>   还原 ⇒ **6 passed** ⇒ 这条用例**真能抓住 DDL 写错**，⛔ 不是"恰好绿"。
>
> ⚠️ **一条仍要记住的**：这表是**惰性**建的（`CREATE TABLE IF NOT EXISTS` 在写入路径里）
> ⇒ **在一个全新的库上，不先真跑一次 approve，`pg_dump` 照样导不出它**。
> 本文件上面那句「`_DDL` 是唯一权威」**现在可以改成**：**`_DDL` 与 `app/schema.sql` 是同一份东西的两个视角**。


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/agent/approval_audit.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ **内容不改写**；只清理内部编号 / 批次号这类工作日志坐标。

**为什么不塞进 `pending_approvals.py`**：那个模块的 docstring 全篇在论证
   「**为什么必须是内存**」，往里加 PG 写会把它变成"两种存储各说一半"的模块。

本模块是 **fail-open** 的：写/读失败一律**打一行日志继续** ——
   留痕是**旁路**，不该让「批准」这个动作 500，也不该让页面整页崩。

表形状 ⇒ 下面 `_DDL` · spec ⇒ `app/agent/specs/approval_audit.md`
