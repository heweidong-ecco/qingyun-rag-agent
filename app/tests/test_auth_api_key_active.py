"""契约 D：`api_keys.is_active` 真的参与校验。

本文件的**核心手法**：把 `verify_api_key` **真发给库的那句 SQL** 抓下来，
   把 `%s` 换成 `?` 丢进 `sqlite3` **原地执行** ——
   ⇒ 被测的是**产品代码产出的谓词本身**，不是我在测试里照抄一份
     （照抄只能测"我抄对没有"，产品代码改成什么都照样绿）。
"""
import datetime
import re
import sqlite3
from pathlib import Path

import pytest

import access.auth as auth

#: 仓根 —— 从 `__file__` 推（`app/tests/test_*.py` ⇒ 上两级），不靠 cwd。
REPO = Path(__file__).resolve().parents[2]
DB_PY = REPO / "app" / "core/db.py"
SCHEMA_SQL = REPO / "app" / "schema.sql"

#: `api_keys` **最初**在真库里的列 —— **故意写死**：这是**冻结的历史事实**，不是"当前应有"的规范。
#: 过去不会变；改这个集合等于伪造历史，那样这两条用例就废了。
#: （下一条用例会拿 `schema.sql` 交叉核对它，别担心是我凭记忆写的。）
ORIGINAL_API_KEYS_COLUMNS = {"id", "user_name", "key_hash", "created_at", "expires_at"}

_CT_RE = r"CREATE TABLE(?: IF NOT EXISTS)? (?:public\.)?{table}\s*\((.*?)\)\s*;"


def _sql_without_comments(block: str) -> str:
    """去掉 `--` 行尾注释 —— 列名抠取必须做这步：本仓 DDL 里注释很长，
    而且注释**以中文开头**，不去掉就会把注释的第一个词当成列名。"""
    return "\n".join(ln.split("--")[0] for ln in block.splitlines())


def _ddl_columns(text: str, table: str) -> list[str]:
    """从一段真 SQL 里抠出 `<table>` 建表语句的列名（按出现顺序）。"""
    m = re.search(_CT_RE.format(table=table), text, re.S)
    assert m, (
        f"没在给定文本里找到 `{table}` 的 CREATE TABLE ⇒ **抠取本身失效了**，"
        "⛔ 别当成通过（本仓「空清单静默假通过」那一族）"
    )
    return [
        ln.strip().rstrip(",").split()[0]
        for ln in _sql_without_comments(m.group(1)).splitlines()
        if ln.strip().rstrip(",")
    ]


def _api_keys_ddl_for_sqlite() -> str:
    """`db.py` 里 `api_keys` 的**真**建表语句，改到 sqlite 能跑。

    **本函数的全部意义：表来自产品代码，不是测试手写的。**
       那次事故正是"手写表"造成的（见文件头）。
    """
    src = DB_PY.read_text(encoding="utf-8")
    m = re.search(r"(CREATE TABLE IF NOT EXISTS api_keys\s*\(.*?\)\s*;)", src, re.S)
    assert m, "没在 db.py 里找到 api_keys 的建表语句 —— 抠取失效了"
    # 只做这一处替换：`SERIAL` 是 PG 的自增写法，sqlite 不认。其余类型两者都收。
    return _sql_without_comments(m.group(1)).replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY")


def test_table_in_db_py_has_every_column_the_query_touches(captured_sql):
    """**这条就是 那次事故的尺子**：拿 `db.py` 的**真 DDL** 建表，
    跑 `auth.py` 产出的**真 SQL** —— 对不上就红。

    别再改成"测试自己写一张带 `is_active` 的表"：那样它测的是**你心里那张表**，
       而产品将来跑的是 `db.py` 那张 —— 两者一旦不同，本用例**照样全绿**（当时就是）。
    """
    ddl = _api_keys_ddl_for_sqlite()
    cols = _ddl_columns(ddl, "api_keys")
    assert len(cols) >= 5, f"只从 db.py 里抠出 {len(cols)} 列 —— 抠取失效了"

    conn = sqlite3.connect(":memory:")
    conn.execute(ddl)
    conn.execute(
        "INSERT INTO api_keys (user_name, key_hash, expires_at) VALUES (?,?,?)",
        ("alice", "hash-x", "2999-01-01"),
    )
    try:
        hit = [r[0] for r in conn.execute(captured_sql.replace("%s", "?"), ("hash-x",))]
    except sqlite3.OperationalError as e:
        raise AssertionError(
            f"用 `db.py` 的真 DDL 建出来的表，跑产品那句查询就炸了：{e}\n"
            f"  · db.py 建表语句里的列：{cols}\n"
            f"  · 产品那句查询：{captured_sql.strip()}\n"
            "  ⇒ 查询引用了建表语句里没有的列。**这正是 2026-10-06 的事故形态**"
            "（`auth.py` 按 `is_active` 过滤，而 DDL 没这一列 ⇒ 真库每次认证 503，"
            "且限流/额度同时 fail-open）。\n"
            "  ⇒ 要么把列补进 `db.py` 的建表语句**和**老库补列那句 ALTER），"
            "要么别在查询里用它。"
        ) from e
    finally:
        conn.close()
    assert hit == ["alice"], f"用一个应通过的行试查询，命中却是 {hit}"


def test_columns_newer_than_exported_schema_have_an_alter_in_db_py():
    """另一半：**新加的列必须同时有"老库补列"那句 `ALTER`**。

    为什么建表语句不够：`CREATE TABLE IF NOT EXISTS` 对**已存在的表什么都不做**
    ⇒ 光把列写进建表语句，**老库永远补不上**，而新库有 ⇒ 两台机器行为不同，
       其中一台（线上那台）**每次认证 503**。

    「老库」的口径取 `app/schema.sql`（它是**从真库导出的**），不是我凭记忆列的。
    """
    db_src = DB_PY.read_text(encoding="utf-8")
    schema_cols = set(_ddl_columns(SCHEMA_SQL.read_text(encoding="utf-8"), "api_keys"))
    ddl_cols = set(_ddl_columns(db_src, "api_keys"))

    # 防"尺子有读数"：写死的那个历史集合必须真的在这张表里 —— 否则下面全是空转
    assert ORIGINAL_API_KEYS_COLUMNS <= schema_cols, (
        f"写死的历史列 {sorted(ORIGINAL_API_KEYS_COLUMNS)} 不在 schema.sql 的列 "
        f"{sorted(schema_cols)} 里 —— 本用例的前提不成立了，先核对 `ORIGINAL_API_KEYS_COLUMNS`"
    )

    added = ddl_cols - ORIGINAL_API_KEYS_COLUMNS
    alters = set(re.findall(r"ALTER TABLE api_keys ADD COLUMN (\w+)", _sql_without_comments(db_src)))
    missing = added - alters
    assert not missing, (
        f"这些列进了建表语句，却**没有**对应的 `ALTER TABLE api_keys ADD COLUMN`：{sorted(missing)}\n"
        f"  · 建表语句的列：{sorted(ddl_cols)}\n"
        f"  · 已有的补列 ALTER：{sorted(alters)}\n"
        "  ⇒ 新库有这些列、**老库没有** ⇒ 只有老库会炸，而报错是一句无关的"
        "「认证服务不可用（数据库连接失败）」。\n"
        "  ⇒ 照 `documents.requested_by` / `api_keys.is_active` 那两块的写法补一个"
        " `DO $$ ... ALTER TABLE ... END $$;`。"
    )



class _FakeCursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchone(self):
        # 未过期的一行 —— 走到 SQL 之后那几步不该成为本文件的变量
        return ("alice", datetime.datetime.now() + datetime.timedelta(days=1))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def captured_sql(monkeypatch):
    """跑一次真的 `verify_api_key`，把它发给库的那句 SQL 抓出来。

    打的是 `auth.get_db` —— `app/access/auth.py:7` 用的是 `from db import get_db`
       （**直接绑定**）⇒ 打 `db.get_db` 是**空操作**，改源模块 `auth` 自己才有效。
    """
    cur = _FakeCursor()
    monkeypatch.setattr(auth, "get_db", lambda: _FakeConn(cur))
    monkeypatch.setattr(auth, "hash_api_key", lambda k: "hash-" + k)
    assert auth.verify_api_key("sk-x") == "alice"      # 顺带：正常路径仍要通
    return cur.sql


def _run_against_sqlite(sql: str, rows):
    """在真 `sqlite3` 上执行**这句真 SQL**（`%s` → `?`），返回命中的 `user_name` 列表。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE api_keys ("
        " user_name TEXT, key_hash TEXT, is_active INTEGER, expires_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO api_keys (user_name, key_hash, is_active, expires_at) VALUES (?,?,?,?)",
        rows,
    )
    out = [r[0] for r in conn.execute(sql.replace("%s", "?"), ("hash-x",))]
    conn.close()
    return out


def test_a_null_is_active_row_still_authenticates(captured_sql):
    """本条的要点：`is_active IS NULL` 的**老行必须照样通过**。

    迁移里那一列是 `nullable=True` ⇒ 真库里可能有 NULL 行。
       写成 `WHERE is_active = 1` 会让它们**集体失效**，而报错只有一句"凭据无效"。
    """
    rows = [
        ("alice_null", "hash-x", None, "2999-01-01"),   # ← 老行：NULL
        ("bob_off", "hash-x", 0, "2999-01-01"),         # ← 已撤销
        ("carol_on", "hash-x", 1, "2999-01-01"),        # ← 正常
    ]
    assert _run_against_sqlite(captured_sql, rows) == ["alice_null", "carol_on"]


def test_the_predicate_really_excludes_revoked_rows(captured_sql):
    """反面：`is_active = 0` 的**必须被排除** —— 否则"撤销"这个动作是装饰。"""
    rows = [("dave_off", "hash-x", 0, "2999-01-01")]
    assert _run_against_sqlite(captured_sql, rows) == []


def test_the_raw_predicate_would_have_dropped_null_rows(captured_sql):
    """把结论**取反**：把 `COALESCE(...)` 拆掉 ⇒ 上面第一条**必须变红**。

    这条用例证明的是「**防 NULL 那半句不是多余的**」，不是产品代码 ——
       所以它**不替代**上一条，两条一起才构成"谓词对 + 产品用的就是它"。
    """
    naive = captured_sql.replace("COALESCE(is_active, 1) = 1", "is_active = 1")
    assert naive != captured_sql, "SQL 形状变了 —— 本用例的前提（COALESCE 那半句）不在了"
    rows = [("alice_null", "hash-x", None, "2999-01-01")]
    assert _run_against_sqlite(naive, rows) == []
