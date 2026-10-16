"""
SQLAlchemy 表结构声明（供 Alembic autogenerate 使用）。

从 `db.py` 切出（重构计划 ⑥ 切开点 1）。目的：让 `import db` 不再被迫拉起 `sqlalchemy` ——
此前 `auth.py` / `cost_dashboard.py` 等只想用 `get_db` 的模块，也会连带把重包拖起来。

**依赖方向是单向的**：本模块**不依赖** `db.py`（只用 sqlalchemy），
所以 `db.py` 可以安全地引用它，不会成环。

**以下的声明与 `db.py:create_table` 里的 DDL 并不完全一致** ——
例如 `documents` 缺 `requested_by`、`embedding` 写成 `Text`。这是**既有差异**，
本次切模块**原样搬移、未做任何修正**（修正属裁决工作）。
**2026-10-06 更正**：原文这里还举了「而 `api_keys` 多一个 `is_active`」——
   **那句现在不成立了**：`is_active` 已补进 `db.py:create_table` 的建表语句**与**老库补列那句
   `ALTER TABLE api_keys ADD COLUMN`。⇒ **这一列两边一致了**，别再把「多一个 is_active」当既有差异。

运行时**真正的建表语句是 `db.py:create_table` 的 DDL**；本模块只是给
Alembic autogenerate 用的声明式镜像，两者不一致时**以 DDL 为准**。
"""
from sqlalchemy import MetaData, Table, Column, Integer, Text, String, DateTime, Float
from sqlalchemy.sql import func

metadata = MetaData()

# 定义 documents 表的结构（与现有表一致）
documents_table = Table(
    "documents",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("content", Text, nullable=False),
    Column("source", String),
    Column("embedding", Text),  # pgvector 在 Alembic 中可能需要特殊处理，先简化
)

# 定义 api_keys 表的结构
api_keys_table = Table(
    "api_keys",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_name", Text, nullable=False),
    Column("key_hash", Text, nullable=False, unique=True),
    Column("created_at", DateTime, server_default=func.now()),
    Column("expires_at", DateTime, nullable=False),
    Column("is_active", Integer, server_default="1"),  # 新增字段，默认1表示激活
    # 2026-10-09（B1）：角色接 DB 的落点。
    #    **可空、无 server_default** —— 与 `app/core/db.py` 的 DDL 保持一致：
    #    "没写"要走**回退**（探针身份 + admin），"写了 free"是明确裁决，两者不是一回事。
    #    本模块只是给 Alembic autogenerate 用的**声明式镜像**，运行时建表**不读它**
    #      （以 `db.py:create_table()` 的 DDL 为准，见本文件顶部那段）。
    Column("role", Text, nullable=True),
)

# 新增：花费明细表
# 定义 cost_records 表的结构
cost_records_table = Table(
    "cost_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_name", Text, nullable=False),
    Column("thread_id", Text, default="unknown"),
    Column("model", Text, nullable=False),
    Column("purpose", Text, nullable=False),
    Column("prompt_tokens", Integer, nullable=False, default=0),
    Column("completion_tokens", Integer, nullable=False, default=0),
    Column("total_tokens", Integer, nullable=False, default=0),
    Column("input_cost", Float, nullable=False, default=0.0),
    Column("output_cost", Float, nullable=False, default=0.0),
    Column("total_cost", Float, nullable=False, default=0.0),
    Column("tool_name", Text),
    Column("tool_args", Text),
    Column("created_at", DateTime, server_default=func.now()),
)

# 在 metadata 定义区域增加 增加归档表
# 定义 cost_records_archive 增加归档表的结构
cost_records_archive_table = Table(
    "cost_records_archive",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_name", Text, nullable=False),
    Column("thread_id", Text, default="unknown"),
    Column("model", Text, nullable=False),
    Column("purpose", Text, nullable=False),
    Column("prompt_tokens", Integer, nullable=False, default=0),
    Column("completion_tokens", Integer, nullable=False, default=0),
    Column("total_tokens", Integer, nullable=False, default=0),
    Column("input_cost", Float, nullable=False, default=0.0),
    Column("output_cost", Float, nullable=False, default=0.0),
    Column("total_cost", Float, nullable=False, default=0.0),
    Column("tool_name", Text),
    Column("tool_args", Text),
    Column("created_at", DateTime, server_default=func.now()),
)
