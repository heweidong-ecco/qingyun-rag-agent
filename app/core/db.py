from contextlib import contextmanager
from psycopg2.extras import execute_values
import asyncio
from core.config import DB_MIN_CONN, DB_MAX_CONN, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_PASSWORD,POSTGRES_USER

from psycopg2 import pool

# 连接池（全局唯一）使用环境变量设置 连接池的上下限，DB_MIN_CONN, DB_MAX_CONN
_connection_pool = None

#   语义：`requested_by = PUBLIC_OWNER` 的那些行是**全站共享**的 ⇒ **检索时人人可见**。
#   它 **不削弱**  的用户隔离 —— 隔离说的是「用户之间互相看不到」，
#      这条说的是「有一份大家都能看的公共语料」= `WHERE requested_by = %s OR requested_by = PUBLIC_OWNER`。
#   **只在【检索】通路上生效**；**写入与删除仍严格按用户**（访客删不掉它）。
PUBLIC_OWNER = "public"

#    建索引（`create_table()`）与那道"验类型"的自愈段**都引用它** ——
#    ⛔ 别在两处各写一个字面量（本仓立场：**两处写死 = 必漂**，`PUBLIC_OWNER` 那条同理）。
#    为什么用 `hnsw` 而不是 `ivfflat` ⇒ 见 `create_table()` 里那一段注释 · 。
_VECTOR_INDEX_AM = "hnsw"


def enable_ann_iterative_scan(cur) -> None:
    """🔴 **让 ANN 索引「继续往下扫，直到凑够 `LIMIT`」** —— 关掉一个**静默回空**的坑。

    ## 为什么要有它

    近似索引（`ivfflat` / `hnsw`）都是 **先扫索引、再对结果套 `WHERE`**
    若扫到的那几行**恰好都不满足过滤条件**，结果就会**少于 `LIMIT`** ——
    ⛔ **不报错、也不告诉你「是被索引吃掉的」**。
    而本仓那两句向量 SQL 一定带 `WHERE requested_by = %s OR requested_by = %s`
    （多用户隔离 +  公共语料）⇒ **正好在这个坑的射程里**。

    **实测它能兜住**（· 本机活库 · 拿 `hnsw.ef_search=2` 人为饿死搜索）：

    | 配置 | 回满 `LIMIT 3` |
    |---|---|
    | 不带这两行 | **0/30** |
    | 带 `relaxed_order` | **30/30** |

    ⇒ **它⛔ 不是装饰**，是真在兜底的（⛔ 别因为"没见它红过"就删掉）。

    ## 🔴 必须用 `SET LOCAL`，⛔ 不是 `SET`

    `SET` 会**留在这条连接上**，而 `get_db()` 用的是**连接池** ⇒ 下个请求拿到的是
    **另一条连接** ⇒ 设置**时有时无**。
    `SET LOCAL` **只在本事务内有效、`commit` 时自动失效** —— 而 psycopg2 会给每条语句
    开一个**显式事务块**（实测：`get_db()` 里 `SET LOCAL` 之后读 `SHOW` 就是设定值）
    ⇒ **正是我们要的语义**。

    ## ⚠️ 为什么【两句都设】

    索引类型由 `create_table()` 的自愈段保证与代码一致（现在是 **`hnsw`**），
    但**迁移窗口与回滚**期间库里可能还是 `ivfflat` ⇒ 两句都设，任何一侧都不至于静默失效。
    代价是零：库里没有那种索引时，另一句**无害**（`pgvector ≥ 0.8` 把两者都注册了）。

    ⚠️ **前提是 `pgvector ≥ 0.8`** —— 低于它这两个 GUC **根本不存在**，
    而 `SET` 会被 PG 当**占位符**收下、`SHOW` 照样回你设的值
    ⇒ **静默空转，且一条门都不会红**。
    ⇒ 由 `assert_pgvector_supports_iterative_scan()` 在启动时**响亮地断**。
    """
    cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
    cur.execute("SET LOCAL ivfflat.iterative_scan = relaxed_order")


def assert_pgvector_supports_iterative_scan() -> str:
    """🔴 **启动自检：`pgvector` 版本够不够开 `iterative_scan`** —— 不够就**响亮地断**。

    ## 为什么必须有它（⛔ 不是"锦上添花"）

    `enable_ann_iterative_scan()` 靠两句 `SET LOCAL`。而在 **`pgvector < 0.8`** 上
    那两个 GUC **根本不存在** —— `SET` 会被 PG 当**占位符**收下、**不报错**，
    而且 **`SHOW` 照样回你设进去的那个值**。
    ⇒ 表现是：**防护静默失效，而日志正常、`SHOW` 正常、用例也正常。**

    本仓为此栽过一整轮：当时验证防护的那些断言，
    在**假 GUC** 上同样是绿的 —— 正是 `CLAUDE.md` §六 那条
    「**拿「动作成功」当「结果正确」**」。

    ## 判据：读【扩展自己的版本号】，⛔ 不是读 `SHOW`

    `pg_extension.extversion` 与"库到底注册没注册那个 GUC"**同源**，
    且**不受共享库加载顺序影响**。
    ⚠️ **⛔ 别改用 `pg_settings` 判** —— GUC 是 `_PG_init`（首次用到 `vector` 类型）时才注册的，
    在一个还没碰过向量类型的新连接上查它**查不到**，那是**假阴性**（本机实测过）。

    ## 两处失败都抛（⛔ 都别降级成 warning）

    - **没有 `vector` 扩展** ⇒ 整条向量检索路不可用，属更早的故障；
    - **版本 < 0.8** ⇒ 就是上面那个静默失效。

    ⚠️ 与 `main.py` 里 embedding 维度自检**同一条立场**：
    「**维度恰好相同**」那类假相它挡不住，但**配置漂移**它挡得住 —— 这条同理：
    它挡不住"GUC 名字写错"，但挡得住"整个特性在你这台机上不存在"。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cur.fetchone()
    # ⚠️ **两种都算"查不到"，⛔ 别只判 `not row`**：
    #    · 库没有这个扩展 ⇒ `fetchone()` 回 `None`；
    #    · 有扩展但 `extversion` 是 NULL ⇒ 回 `(None,)`（那样会掉进下面"认不出版本号"的分支，
    #      报出去的话是**错的** —— 反证时实测到过）。
    if not row or not row[0]:
        raise EnvironmentError(
            "🔴 查不到 `vector` 扩展的版本 ⇒ 向量检索整条路不可用。"
            "（`create_table()` 那句 `CREATE EXTENSION IF NOT EXISTS vector` 没跑成？）"
        )
    ver = str(row[0])
    try:
        major, minor = (int(x) for x in ver.split(".")[:2])
    except (ValueError, TypeError):
        raise EnvironmentError(f"🔴 认不出 pgvector 版本号：{ver!r}（期望形如 `0.8.2`）")
    if (major, minor) < (0, 8):
        raise EnvironmentError(
            f"🔴 pgvector 版本过低：{ver}（**需要 ≥ 0.8**）。"
            "0.8 以下没有 `hnsw.iterative_scan` / `ivfflat.iterative_scan` 这两个 GUC —— "
            "而 `SET` 会被 PG 当**占位符**静默收下、`SHOW` 还会回设定值 "
            "⇒ **防护会静默空转，且没有任何门会红**。"
            "⇒ 升级 pgvector（`pgvector/pgvector:pg17` 镜像自带 0.8+）。"
            "⛔ **别靠放宽这条自检绕过** —— 那正是它要防的事。"
        )
    return ver


def init_pool():
    """初始化连接池（应用启动时调用，参数从环境变量读取）"""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=DB_MIN_CONN,
            maxconn=DB_MAX_CONN,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT
        )
        print(f"✅ 数据库连接池已初始化（最小{DB_MIN_CONN}，最大{DB_MAX_CONN}）")

def close_pool():
    """关闭连接池（应用关闭时调用）"""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        print("数据库连接池已关闭")

@contextmanager
def get_db():
    """从连接池中获取一个连接，使用完后归还

    """
    if _connection_pool is None:
        init_pool()
    conn = _connection_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _connection_pool.putconn(conn)

#    （`grep -rn '\bDB_CONFIG\b' app/` 只有定义那一行）。真正的连接配置走连接池，不用它。

# 注意：仅保留上方基于连接池的 get_db()（自动提交/回滚/归还连接）。

def embedding_dim_in_db():
    """从【表定义】里读出 `documents.embedding` 的维度。

       原先的结论（写在 `rag/embedding_client.py` 的 docstring 里）是：
       「全仓**没有那个来源**（`vector(N)` 是行内字面量）⇒ **接线留到 B 段**」。
       ✅ **B 段到了，而答案是【问表自己】**：
       · ⛔ **不抽常量**（作者 裁过"逐处改"，见 Step 3）；
       · ✅ PG 的 `pg_attribute.atttypmod` 对 `vector(N)` **就是 N** ⇒ **表的定义本身**就是那个单一来源。
       ⇒ **从此不再有"第三处写死的维度"**。

    🔴 **查不到就回 `None`**（表/列还没建出来时）。
    ⚠️ **⛔ 不是"当成通过"** —— 调用方必须显式区分 `None`（查不到）与一个数。
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'documents'::regclass AND attname = 'embedding'"
                )
                row = cur.fetchone()
        # `atttypmod` 对 `vector(N)` 就是 N；表还没建 / 列不在 ⇒ 回 None
        return int(row[0]) if row and row[0] and int(row[0]) > 0 else None
    except Exception as e:
        print(f"[DB] 读 embedding 维度失败（回 None）: {e}")
        return None


def create_table():
    try:
        with get_db() as conn:#连接管理
            with conn.cursor() as cur:#游标创建
                #SQL 执行
                # 确保 pgvector 扩展已启用（必须放在建表之前）
                # 表中增加 owner 字段用requested_by表示，owner是内部关键字直接用owner会冲突报错，所有查询和修改都带上当前用户的ID进行过滤，从而实现多用户数据隔离。
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id SERIAL PRIMARY KEY,
                        content TEXT NOT NULL,
                        source TEXT,
                        embedding vector(1024),
                        requested_by TEXT NOT NULL DEFAULT 'anonymous'  -- 新增字段
                    );
                """)
                # API Key 表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS api_keys (
                        id SERIAL PRIMARY KEY,
                        user_name TEXT NOT NULL,
                        key_hash TEXT NOT NULL UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP NOT NULL,
                        -- 🔴  契约 D /：`app/access/auth.py` 的查询按
                        --    `COALESCE(is_active, 1) = 1` 过滤 ⇒ **这一列必须真实存在**。
                        --    ⚠️ 故意**可空**（⛔ 别加 NOT NULL）：`COALESCE` 就是为了让
                        --    那批"加列之前写进去的老行（NULL）"照样能通过认证。
                        -- 🔴 **类型必须是 INTEGER（0/1），⛔ 别改成 BOOLEAN** ——
                        --    写入侧写的是整数： 的
                        --    `UPDATE api_keys SET is_active = 0` ⇒ 建成 boolean 那句直接报
                        --    "column is of type boolean but expression is of type integer"。
                        --    ⚠️ 全仓另外两处也是**整数**口径，别只改这里：
                        --    `app/core/db_metadata.py:41`（`Integer, server_default="1"`）·
                        --    `app/tests/test_auth_api_key_active.py:78`（sqlite `INTEGER`）。
                        is_active INTEGER DEFAULT 1,
                        -- 🔴 2026-10-09（B1）：**角色接 DB** 的落点。
                        --    读侧唯一入口 = `app/access/permission.py::get_user_role()`。
                        --    ⚠️ **故意可空**（⛔ 别加 `NOT NULL`、⛔ 别加默认值）——
                        --    理由与上面 `is_active` **逐字同款**：加列之前写进去的老行
                        --    **没有这个值**，而"**没写**"与"**写了 free**"**不是一回事**：
                        --    前者要走**回退**（那三个探针身份 + admin），后者是明确的裁决。
                        role TEXT
                    );
                """)
                # 登录账号表—— ⚠️ 它与 `api_keys` 是【两件事】，⛔ 别合并：
                #    · `api_keys`    = 【API Key】那套（`X-API-Key`）：一个用户名可有多把、
                #                      每把带自己的 `expires_at` 与 `role`
                #    · `login_users` = 【用户名 + 口令】那套（登录换 JWT）：**一个用户名一行**
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS login_users (
                        user_name     TEXT PRIMARY KEY,
                        -- 口径：`pbkdf2_sha256$轮数$盐$哈希`（三段 hex）—— 见 `access/login_users.py`
                        -- ⚠️ 轮数与盐**写在串里** ⇒ 将来把轮数调大**不会让已有口令失效**。
                        password_hash TEXT NOT NULL,
                        is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        -- NULL = 永不过期。⚠️ 与 `api_keys.expires_at`（NOT NULL）**口径不同**，
                        --    那也是有意：那边是"每把 key 必须有过期时间"，这边是"可以给他设一个到期日"。
                        expires_at    TIMESTAMP
                    );
                """)
                conn.commit()
                # 如果表已存在但缺少列，则添加（兼容旧表）
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='documents' AND column_name='requested_by'
                        ) THEN
                            ALTER TABLE documents ADD COLUMN requested_by TEXT NOT NULL DEFAULT 'anonymous';
                        END IF;
                    END $$;
                """)
                # 同一款：`api_keys.is_active`
                #    病因：这个库是**先建的**（`CREATE TABLE IF NOT EXISTS` 那句对已存在的表
                #    **什么都不做**）⇒ 光把列写进上面的建表语句，**老库永远补不上**。
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='api_keys' AND column_name='is_active'
                        ) THEN
                            ALTER TABLE api_keys ADD COLUMN is_active INTEGER DEFAULT 1;
                        END IF;
                    END $$;
                """)
                # 同一款：`api_keys.role`（· B1）
                #    理由与上面 `is_active` 那段**逐字同款**：本机这条 PG 是**先建的**，
                #    光把列写进建表语句 ⇒ **老库永远补不上**。
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='api_keys' AND column_name='role'
                        ) THEN
                            ALTER TABLE api_keys ADD COLUMN role TEXT;
                        END IF;
                    END $$;
                """)
                #    而 `get_user_role` 正是按 `user_name` 查 `role` ⇒ 那是**全表扫**。
                #    这条要**和 B1 一起进**，别留到"以后优化" ——
                #    它正是"每请求最多调 10 次"那条热路径上唯一的查询。
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS api_keys_user_name_idx
                    ON api_keys (user_name);
                """)
                # 为向量字段创建索引，加速检索
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS documents_embedding_idx
                    ON documents
                    USING {_VECTOR_INDEX_AM} (embedding vector_cosine_ops);
                """)
                cur.execute("""
                    SELECT am.amname FROM pg_class c
                    JOIN pg_am am ON am.oid = c.relam
                    WHERE c.relname = 'documents_embedding_idx';
                """)
                _idx = cur.fetchone()
                if _idx and _idx[0] != _VECTOR_INDEX_AM:
                    print(
                        f"⚠️ 向量索引类型与代码不符（活库={_idx[0]} · 代码={_VECTOR_INDEX_AM}）"
                        f" ⇒ 正在重建 documents_embedding_idx"
                    )
                    cur.execute("DROP INDEX IF EXISTS documents_embedding_idx;")
                    cur.execute(f"""
                        CREATE INDEX documents_embedding_idx
                        ON documents
                        USING {_VECTOR_INDEX_AM} (embedding vector_cosine_ops);
                    """)
                    print(f"✅ 向量索引已重建为 {_VECTOR_INDEX_AM}")
                # 为 owner 字段用requested_by表示，建立索引，加速按用户过滤
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS documents_requested_by_idx
                    ON documents (requested_by);
                """)
                # Token使用日志表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS token_usage_logs (
                        id SERIAL PRIMARY KEY,
                        user_name TEXT NOT NULL,
                        thread_id TEXT DEFAULT 'unknown',
                        model TEXT NOT NULL,
                        purpose TEXT NOT NULL,
                        prompt_tokens INTEGER NOT NULL,
                        completion_tokens INTEGER NOT NULL,
                        total_tokens INTEGER NOT NULL,
                        cost REAL NOT NULL DEFAULT 0.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                # 为常用查询字段创建索引
                cur.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage_logs(user_name);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_date ON token_usage_logs(created_at);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_thread ON token_usage_logs(thread_id);")
                # 新增：花费明细表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cost_records (
                        id SERIAL PRIMARY KEY,
                        user_name TEXT NOT NULL,
                        thread_id TEXT DEFAULT 'unknown',
                        model TEXT NOT NULL,
                        purpose TEXT NOT NULL,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        input_cost REAL NOT NULL DEFAULT 0.0,
                        output_cost REAL NOT NULL DEFAULT 0.0,
                        total_cost REAL NOT NULL DEFAULT 0.0,
                        tool_name TEXT,
                        tool_args TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                # 索引
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_records_user ON cost_records(user_name);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_records_date ON cost_records(created_at);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_records_thread ON cost_records(thread_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_records_purpose ON cost_records(purpose);")
                # 增加归档表 建表语句：
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cost_records_archive (
                        id SERIAL PRIMARY KEY,
                        user_name TEXT NOT NULL,
                        thread_id TEXT DEFAULT 'unknown',
                        model TEXT NOT NULL,
                        purpose TEXT NOT NULL,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        input_cost REAL NOT NULL DEFAULT 0.0,
                        output_cost REAL NOT NULL DEFAULT 0.0,
                        total_cost REAL NOT NULL DEFAULT 0.0,
                        tool_name TEXT,
                        tool_args TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
            # 关键：显式提交，防止上下文管理器未提交
            conn.commit()
        print("✅ 表创建/确认成功，表结构更新成功,已包含 owner的requested_by 字段")
    except Exception as e:
        print("❌ 数据库初始化失败：", e)
        raise
def insert_document(content: str, source: str, embedding: list, requested_by: str = "anonymous"):
    """插入一条文档块及其向量。

    ：**插入后自己清 BM25 缓存** —— 不变量放在这一层，
       调用方就不必记得再补一句（`/rag/upload_document` 正是漏了那一句，
       而它调的又恰好是这里 ⇒ 上传的新文档在 BM25 召回里"不存在"，直到重启）。
       清缓存是**惰性**的（只是把桶丢掉，重建发生在下次搜索）⇒ 逐条调用不会造成 N 次重建。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (content, source, embedding, requested_by) VALUES (%s, %s, %s::vector, %s)",
                (content, source, embedding, requested_by)
            )
            conn.commit()
    invalidate_bm25_cache()

def insert_batch_documents(docs: list[tuple[str, str, list,]]):
    """批量插入文档块。docs: [(content, source, embedding), ...]

    本函数**当前全仓无调用方**（`grep -rn 'insert_batch_documents' app/` 只剩定义与 import）。
       这里仍按同一条不变量清缓存：守卫 `app/tests/test_bm25_cache_invalidation_wiring.py`
       是按「**凡写 documents 就清缓存**」统一判的，不给死代码开例外 ——
       开了例外，将来它被启用时就正好少了这一句。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO documents (content, source, embedding) VALUES %s",
                [(c, s, e) for c, s, e in docs],
                template="(%s, %s, %s::vector)"
            )
            conn.commit()
    invalidate_bm25_cache()

def _require_identity(user_id, where: str) -> None:
    """fail-closed：没有身份 ⇒ **抛错**，不是"当成匿名、查全库"（§六 ③）。

    为什么放在**取连接之前**：必须在**任何查询发出去之前**就挡住 ——
       否则"传 None ⇒ 不过滤 ⇒ 返回全库"这条 fail-open 路仍然存在。
    """
    if not user_id:
        raise ValueError(
            f"{where} 需要 user_id（非空）—— 传空就等于「查全库」，"
            f"那是本仓已裁定的 fail-open 反模式（§二 根因 / §六 ③）"
        )


def search_similar(query_embedding: list, top_k: int = 3, *, user_id: str):
    """根据向量相似度检索文档 —— **该用户自己的文档 + 公共语料库**。

    ：加 `WHERE requested_by = %s`（决策 5：共享层承重）。
       原先无 WHERE ⇒ 查全库 ⇒ 这里**就是**「A 能看见 B 的文档」那条路。
    ：加 `OR requested_by = PUBLIC_OWNER` ——
       公共语料**人人可见**，而**用户之间仍然互相看不见**（两件事，别读成"隔离被取消"）。
    """
    _require_identity(user_id, "search_similar")
    with get_db() as conn:
        with conn.cursor() as cur:
            enable_ann_iterative_scan(cur)      # 🔴：别让 ivfflat 静默回空
            cur.execute("""
                SELECT id,content, source, 1 - (embedding <=> %s::vector) AS similarity
                FROM documents
                WHERE requested_by = %s OR requested_by = %s   -- 自己的 + 公共语料
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
            """, (query_embedding, user_id, PUBLIC_OWNER, query_embedding, top_k))
            return cur.fetchall()

# ==================== 兼容转发层（重构计划 ⑥ 切开点 1）====================
# 本文件原先在**模块层** `import sqlalchemy / numpy / jieba / rank_bm25`，
# 于是**任何** `from db import get_db` 的调用方（auth.py / deps.py / cost_dashboard.py …）

def bm25_search(query: str, top_k: int = 10, *, user_id: str):
    """转发到 `bm25_index.bm25_search`（惰性导入，见上方说明）。

    ：加 `user_id`—— 过滤实现在 `bm25_index` 里，本层只转发。
    """
    from rag.bm25_index import bm25_search as _impl
    return _impl(query, top_k, user_id=user_id)

#    两个**转发壳**（各自 `return _impl()`），但**全仓零外部调用**：
#      grep -rn 'db\.get_bm25_index\|from db import.*get_bm25_index' app/    → 0
#      grep -rn 'db\.get_all_documents\|from db import.*get_all_documents' app/ → 0
#    真正在用的是 `bm25_index` 里那两份（模块内部自己调）。⇒ 这两个壳是重构留下的残壳。

def invalidate_bm25_cache():
    """转发到 `bm25_index.invalidate_bm25_cache`（惰性导入）。"""
    from rag.bm25_index import invalidate_bm25_cache as _impl
    return _impl()

# ==================== 异步包装 ====================
async def search_similar_async(query_embedding: list, top_k: int = 3, *, user_id: str):
    return await asyncio.to_thread(search_similar, query_embedding, top_k, user_id=user_id)

async def bm25_search_async(query: str, top_k: int = 10, *, user_id: str):
    from rag.bm25_index import bm25_search as _impl
    return await asyncio.to_thread(_impl, query, top_k, user_id=user_id)
