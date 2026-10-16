from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

import os
import sys

# 将 api 目录加入 Python 路径，以便导入 config 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.config import POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB

# 构造数据库 URL
DB_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

config = context.config
fileConfig(config.config_file_name)

# 覆盖 alembic.ini 中的 sqlalchemy.url
config.set_main_option("sqlalchemy.url", DB_URL)

# ... 后面自动生成的代码保持不变 ...

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
from core.db_metadata import metadata
target_metadata = metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# 🔴🔴 2026-10-16· **本链【已作废】—— 让它在真库上【跑不动】**

# 为什么加这道**结构拦**、而不是继续只写"别跑"：
#   链上第一份 `f12a761ae626`（init）的 `upgrade()` 里有
#       `op.drop_column('documents', 'requested_by')` —— 那是**多用户隔离的承重列**
#   ⇒ **跑一次就把隔离拆了，而且不报错**（本仓 2026-10-10 真跑离线模式才看见的）。
#   ⚠️ 那份是 **autogenerate 的产物，而当年 `db_metadata.py` 与活库对不上 ⇒ diff 方向是反的**。
#
# 🔴 **而"别跑"此前只写在注释与三份文档里** —— **没有任何执行体**。
#   本仓立场（`CLAUDE.md` §六 / `agent-pitfalls-kb` 的 `A2`）：**只有文字就漏，结构才执行。**
#
# ⚠️ **只拦【在线】模式** —— `alembic history` 与 `alembic upgrade head --sql`（离线）
#   **照旧可用**：它们是 `app/alembic/CLAUDE.md` 里写着的**既有判据**，⛔ 别一起弄坏。
#   （离线模式只**打印 SQL**、不碰任何库 ⇒ 它没有这个风险。）
#
# ⚠️ **将来真要跑它**（比如重做 baseline —— 那正是  的未裁问题）：
#   **先把那条链修对**（或按  定下"迁移入口是谁"），再来删这个判断。
#   ⛔ **那时也⛔ 不是"设个环境变量绕过去"** —— 那等于把这道拦变成一句可跳过的提示。
_ONLINE_REFUSAL = (
    "🔴 本仓的 alembic 迁移链【已作废】，⛔ **拒绝在真库上执行**。\n"
    "   原因：`f12a761ae626`（init）会 `drop_column('documents', 'requested_by')` —— "
    "那是多用户隔离的承重列，跑了会把隔离拆掉**且不报错**。\n"
    "   表结构的【真值】= `app/schema.sql`（活库 pg_dump 生成）+ "
    "`app/core/db.py` 的 `create_table()`，⛔ **不是这条链**。\n"
    "   📄 完整说明 ⇒ `app/alembic/CLAUDE.md` · `docs/待办总表.md` 的  / \n"
    "   ⚠️ 只看不碰的用法照旧：`alembic history` · `alembic upgrade head --sql`（离线）。"
)


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # ⚠️ **本函数现在【调不到了】** —— 分发处（文件末尾）对在线模式直接抛。
    #    留在这里是为了**解封时只删一行**；⛔ 别以为它还活着。
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # 🔴🔴 2026-10-16：**在线模式（真连库）一律拒绝** —— 见上面 `_ONLINE_REFUSAL` 的说明。
    #    ⚠️ 这一行**在建 engine 之前**就抛 ⇒ **连不上库也拦得住**（有专门的用例钉这件事）。
    #    解封 = 把这一行换成 `run_migrations_online()`，**前提是那条链已经修对**。
    raise RuntimeError(_ONLINE_REFUSAL)
