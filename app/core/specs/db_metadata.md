# `app/core/db_metadata.py`

| 项 | 内容 |
|---|---|
| **状态** | ⚰️ **不是运行时模块** —— 它是 **Alembic autogenerate 用的声明式镜像**，**⛔ 不是表结构的真值** |
| **对外提供** | `metadata` · `documents_table` · `api_keys_table` · `cost_records_table` · `cost_records_archive_table` |
| **谁在用** | 🔴 **全仓只有 1 处**：`app/alembic/env.py:39` 的 `from db_metadata import metadata`（`target_metadata = metadata`）。⛔ **零运行时调用点** |
| **测试** | ⛔ **无**（`app/tests/test_auth_api_key_active.py` 只是**注释里引用**它的行号） |

## ✅ 做了什么

2026 年重构（重构计划 ⑥ 切开点 1）从 `db.py` 切出来的**纯 sqlalchemy 声明**。切的**目的**是解 import 期耦合：

> 让 `import db` 不再被迫拉起 `sqlalchemy` —— 此前 `auth.py` / `cost_dashboard.py` 等**只想用 `get_db()`** 的模块，也会连带把重包拖起来。

**依赖方向是单向的**：本模块**不依赖** `db.py`（只用 `sqlalchemy`）⇒ `db.py` 可以安全引用它，**不会成环**。

声明了 4 张表：`documents` · `api_keys` · `cost_records` · `cost_records_archive`。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| 🔴 **与真实 DDL 已知不一致，且【不打算修】** | 本模块的 docstring 自己写着：与 `db.py:create_table()` 的 DDL **并不完全一致**（例：`documents` 缺 `requested_by`、`embedding` 写成 `Text`）。⇒ **原样搬移、未做任何修正**（修正属裁决工作） |
| ⚠️ **Alembic 迁移链** | ⛔ **没核过**本仓的 Alembic 是否真在跑 —— 见下方 §⚠️ 第 1 条 |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🔴 它**看着像**"表结构的真值"，**其实不是** —— 真值在 `db.py`

**运行时真正的建表语句是 `db.py:create_table()` 的 DDL。** 本模块只是给 Alembic 用的**声明式镜像**，
**两者不一致时以 DDL 为准**（这句写在本模块的 docstring 里）。

⚠️ **为什么会误导**：文件名叫 `db_metadata`、内容是一份完整的 `Table(...)` 声明，
**读起来像"照它建库"**。而实际上**它一次都不会被执行** —— 全仓唯一引用点是 Alembic 的 `env.py`。

🔴 **判据（可打印）**：
```bash
grep -rn 'from db_metadata import\|import db_metadata' app/ scripts/ main.py
grep -rn 'CREATE TABLE\|create_table' app/core/db.py | head
```
⇒ 第一条**只应命中 `app/alembic/env.py`**；第二条才是真正建表的地方。

⇒ **改这个文件不会改任何运行行为**；反过来，**照着它改库结构会改错**。

### 2. 🔴 `api_keys.is_active` 的"既有差异"**已经没有了**（2026-10-06 更正）

本模块的 docstring 原先举例说「而 `api_keys` 多一个 `is_active`」。🔴 **那句现在不成立**：
`is_active` 已补进 `db.py:create_table()` 的建表语句**与**老库补列那句 `ALTER TABLE api_keys ADD COLUMN`。

⇒ **这一列两边一致了**，⛔ **别再把「多一个 `is_active`」当成既有差异**去"修"。
（本 spec 是**顺着 docstring 读会读到旧事实**的第二个现场 —— 第一个在 `app/core/db.py:88`。）

### 3. ⚠️ `cost_records` 与 `cost_records_archive` **在这里是两张完整的表** —— 但**归档这件事的落点不在这**

本文件只是**声明**它们。**谁往里写、什么时候搬**（归档任务、定时器）**在本文件里一个字都看不出来**。
⛔ 别据本文件推断"归档机制已实现"。

### 4. ⚠️ `embedding` 列声明成 `Text`，**不是 pgvector 类型**

文件里那行还带注释：`# pgvector 在 Alembic 中可能需要特殊处理，先简化`。
⇒ **声明是简化过的**，⛔ 别把它当成"库里就是 Text"的真值（`db.py` 的 DDL 另有写法）。

### 5. ⚠️ 同一个"表清单"在本仓有**两个来源**

| 来源 | 是不是真值 |
|---|---|
| `app/core/db.py` 的 `create_table()` | ✅ **是**（运行时执行） |
| 本文件 | ⛔ **不是**（给 autogenerate 看的） |

⇒ 这与本仓反复强调的「**一个名字两个来源必然漂移，而漂移是静默的**」**是同一族问题**。
⚠️ 本仓对此**已知且接受**（有明文"以 DDL 为准"）—— 但**新加一张表时，两处都要改**，⛔ 只改一处会**静默**留下不一致。

## 关联

| 文档 | 说明 |
|---|---|
| `app/core/specs/db.md` | ⭐ **真正建表/连接/检索的那个模块** —— 找表结构以它为准 |
| `docs/契约/数据模型.md` · `app/schema.sql` | 表结构的**契约文档**与**导出快照** |
| `app/alembic/env.py` | 全仓唯一使用者 |
| 本仓的决策记录（⛔ 不随产品仓提供） | `is_active` 归 DDL 保证 ⇒ §⚠️ 第 2 条的来由 |
