"""add role to api_keys (B1)

Revision ID: c7d9e1a4b2f8
Revises: 828721f77ef2
Create Date: 2026-10-09

**为什么有这一份**（B1）：`get_user_role` 从**按用户名硬编码**
改成**查 `api_keys.role`**，所以这一列要真存在。

**它与 `app/core/db.py:create_table` 里那段 `DO $$ … ALTER` 做的是【同一件事】** ——
   两条路**都必须有**，因为**跑哪条取决于你怎么起服务**：
     · 正常起服务（`uvicorn main:app`）⇒ 走 `create_table`
     · 只跑迁移（`alembic upgrade head`）⇒ 走这份
   **别删任何一条**（删了 `create_table` 那条 ⇒ 本机老库补不上；
   删了这份 ⇒ 只跑迁移的环境补不上）。

`IF NOT EXISTS` 是**故意加的**：本机真库已经通过 `create_table` 补过这一列，
   迁移再跑一次不该炸（两条路并存的状态**是正常的**）。

**`role` 必须【可空】** —— 加列之前写进去的老行没有这个值，
   而"**没写**"要走**回退**（探针身份 + admin），"**写了 free**"是明确裁决，**两者不是一回事**。
   ⇒ 在这里加 `NOT NULL` 或 `DEFAULT` 都会把那条区别抹掉。

## 本机【跑不了】这份迁移 —— 别把"文件写对了"当成"验过了"

2026-10-09 实测：**`alembic` 既没装、也不在 `app/requirements.txt` 里** ——
    从仓根 `import alembic` ⇒ `ModuleNotFoundError`；`venv/bin/alembic` ⇒ 不存在。
   ⇒ **这份文件在本机【没被执行过一次】**，它的正确性**只经过人工审读**。

**那 B1 在本机是靠什么加列的**？靠 **`app/core/db.py:create_table`** 里那段 `DO $$ … ALTER`
   —— 那条路**已经在真库上跑过并核过**（判据：`information_schema.columns` 里查到 `role`，
   `pg_indexes` 里查到 `api_keys_user_name_idx`）。
   **两条路的关系**见上面「为什么有这一份」那段：**哪条生效取决于你怎么起服务**。

**待裁**（已记进 `docs/待办总表.md`）：**要不要把 `alembic` 加进 `requirements.txt`** ——
   加了它就进 本服务镜像（同 `scripts/CLAUDE.md` 那条「别往这份清单塞 linter」的顾虑）。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7d9e1a4b2f8'
down_revision: Union[str, Sequence[str], None] = '828721f77ef2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """加 `api_keys.role`（**可空**）+ 按 `user_name` 的索引。"""
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS role TEXT")
    # 这条索引也是 B1 的一部分，别留到"以后优化"：
    #    `get_user_role` 按 `user_name` 查 role，而全库**原本没有任何 user_name 索引**
    #    ⇒ 那是全表扫；而它在**配额热路径**上（一个请求最多调 10 次）。
    op.execute("CREATE INDEX IF NOT EXISTS api_keys_user_name_idx ON api_keys (user_name)")


def downgrade() -> None:
    """这一步会**丢掉已写的角色**（索引先删，再删列）。"""
    op.execute("DROP INDEX IF EXISTS api_keys_user_name_idx")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS role")
