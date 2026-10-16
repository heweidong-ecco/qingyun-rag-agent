"""启动自检 `ensure_admin_exists` 必须问【和认证侧同一个问题】。

## 本文件要钉的那条缺陷（2026-10-06 核实、真库 + 事务 + 回滚复现）

`ensure_admin_exists` 原先只数**行数**：

```
app/access/auth.py:46   SELECT COUNT(*) FROM api_keys WHERE user_name = 'admin'
```

而认证侧 `verify_api_key` 要的是 `COALESCE(is_active, 1) = 1` **且**未过期。
⇒ **两个门量的不是同一件事**。真库里跑
（它按用户名撤，撤的是**该用户名下的全部行**）之后：

```
门① 启动自检看到                      ⇒ 1     ⇒ 日志:「管理员账户已存在，跳过自动创建」
门② 认证时真能通过的                  ⇒ 0     ⇒ 没有一把 key 能认证
```

**admin 锁死，而日志说一切正常。** 近失不在假设里：2026-10-06 本机真跑过那个
`--revoke`（撤掉了 `id=2` 那把 09-10 建的主 key），当时靠**手工**
`UPDATE api_keys SET is_active = 1 WHERE id = 2` 才复原。

## 本文件的尺子

**不照抄谓词**（同族：`app/tests/test_auth_api_key_active.py` 2026-10-06 那次事故 ——
测试手写一张"心里那张表"，产品代码改成什么都照样绿）。
本文件的 SQL 侧用例一律**抓产品代码产出的真 SQL**，丢进真 `sqlite3` 原地跑。
"""
import datetime
import re
import sqlite3
from pathlib import Path

import access.auth as auth

REPO = Path(__file__).resolve().parents[2]

_HELLO_MSG = "管理员账户已存在，跳过自动创建"


# ─────────────────────────── 假连接 / 假游标 ───────────────────────────

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


class _AdminCursor:
    """模拟"`api_keys` 里 admin 名下的行"。

    **两条查询故意分开喂** —— 这正是要测的那个差：

    | 查询 | 喂什么 | 含义 |
    |---|---|---|
    | ① `SELECT COUNT(*) ...`（**不过滤**） | `total` | 名下一共有几行 |
    | ② `SELECT expires_at ... WHERE ... <is_active 谓词>` | `usable_rows` | 过滤后还剩几行 |

    缺陷形态 = `total > 0` 而 `usable_rows == []`（有行，一把都用不了）。
    """

    def __init__(self, total, usable_rows):
        self.total = total
        self.usable_rows = list(usable_rows)
        self.sqls: list[str] = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)

    def fetchone(self):
        return (self.total,)

    def fetchall(self):
        return list(self.usable_rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecordingLogger:
    """记下 (级别, 正文) —— 不接真 logging（那要去改 root logger，会串到别的用例）。"""

    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def _rec(self, level, msg, *a, **k):
        self.lines.append((level, str(msg)))

    def info(self, msg, *a, **k):
        self._rec("info", msg, *a, **k)

    def warning(self, msg, *a, **k):
        self._rec("warning", msg, *a, **k)

    def error(self, msg, *a, **k):
        self._rec("error", msg, *a, **k)

    def levels(self):
        return [lv for lv, _ in self.lines]


def _setup(monkeypatch, total, usable_rows, created):
    """把 `ensure_admin_exists` 的外部世界全换成假的，返回 (游标, 日志)。"""
    cur = _AdminCursor(total, usable_rows)
    log = _RecordingLogger()
    monkeypatch.setattr(auth, "get_db", lambda: _FakeConn(cur))

    def _fake_create(user_name, expire_days=30):
        created.append({"user_name": user_name, "expire_days": expire_days})
        return "sk-" + "0" * 32

    monkeypatch.setattr(auth, "create_user_api_key", _fake_create)
    return cur, log


def _future():
    return datetime.datetime.now() + datetime.timedelta(days=1)


def _past():
    return datetime.datetime.now() - datetime.timedelta(days=1)


# ─────────────────────── ① 缺陷本体（行为侧）───────────────────────

def test_撤销后仅剩死行时不算管理员已存在(monkeypatch):
    """**本文件的核心用例** —— 旧代码在这条上必红。

    旧行为：`COUNT(*) = 1 > 0` ⇒ `logger.info("管理员账户已存在，跳过自动创建")`、返回 `None`。
    ⇒ 说"管理员在"，而**认证侧一把都过不去** ⇒ 锁死且无人被告知。
    """
    created = []
    _cur, log = _setup(monkeypatch, total=1, usable_rows=[], created=created)

    assert auth.ensure_admin_exists(logger=log) is None

    assert created == [], (
        "⛔ **不许自动补发** —— 自动补发会把操作员的撤销决定**在下一次重启静默还原**，"
        "而且新 key 的**明文会打进日志**（那正是我们要清掉的那类残渣）。"
    )
    assert ("info", _HELLO_MSG) not in log.lines, (
        "这就是缺陷本身：名下一把可用的 key 都没有，却报「管理员账户已存在，跳过自动创建」。"
    )
    assert "error" in log.levels(), (
        "必须是**响亮告警**（error），⛔ 不是静默返回 —— "
        "锁死而日志说一切正常，正是 2026-10-06 那次近失的形态。"
    )


def test_仅剩已过期的admin行时同样告警(monkeypatch):
    """过期 = 不可用 —— 与 `verify_api_key`（`expires_at < datetime.now`）同一判据。

    过期的行**同样**过不了认证，所以「有行」照样不等于「管理员在」。
    """
    created = []
    _cur, log = _setup(monkeypatch, total=1, usable_rows=[(_past(),)], created=created)

    auth.ensure_admin_exists(logger=log)

    assert created == []
    assert ("info", _HELLO_MSG) not in log.lines
    assert "error" in log.levels()


# ─────────────── ② 取反的另一半：两条既有行为不能被改坏 ───────────────

def test_一行都没有时照旧自动创建(monkeypatch):
    """**首次启动**（名下 0 行）⇒ 照旧自动建一把、并打印明文。

    这条是 `test_撤销后仅剩死行时不算管理员已存在` 的**取反面**：
       这次修复必须**只**改"有行但全不可用"那一支，不能把首次启动也一起改掉。
    """
    created = []
    _cur, log = _setup(monkeypatch, total=0, usable_rows=[], created=created)

    key = auth.ensure_admin_exists(logger=log)

    assert len(created) == 1, f"首次启动必须照旧自动建，实际 create 调用 = {created}"
    assert created[0]["user_name"] == "admin"
    assert key and key.startswith("sk-")
    assert "warning" in log.levels(), "首次启动要照旧把明文打出来（仅此一次）"


def test_有可用的admin时照旧静默跳过(monkeypatch):
    """正常情形行为**一个字都不能变**（别把「正常」也喊成 error）。"""
    created = []
    _cur, log = _setup(monkeypatch, total=1, usable_rows=[(_future(),)], created=created)

    assert auth.ensure_admin_exists(logger=log) is None

    assert created == []
    assert ("info", _HELLO_MSG) in log.lines
    assert "error" not in log.levels()


# ────────────────── ③ SQL 侧尺子：抓真 SQL，丢进真 sqlite ──────────────────

def _captured_admin_sqls(monkeypatch):
    """跑一次真的 `ensure_admin_exists`，把它发出去的两句 SQL 抓下来。"""
    created = []
    cur, _log = _setup(monkeypatch, total=1, usable_rows=[], created=created)
    auth.ensure_admin_exists(logger=_RecordingLogger())
    assert len(cur.sqls) == 2, (
        f"预期「不过滤的行数」与「过滤后的可用行」两条查询，实际 {len(cur.sqls)} 条：{cur.sqls}"
    )
    counting = [s for s in cur.sqls if "COUNT(*)" in s]
    usable = [s for s in cur.sqls if "COUNT(*)" not in s]
    assert len(counting) == 1 and len(usable) == 1
    return counting[0], usable[0]


def _run_sqlite(sql, rows):
    """在真 `sqlite3` 上跑**产品产出的那句 SQL**，返回命中的 `expires_at` 列表。

    表是现搭的 —— 本用例只关心**谓词**，列名沿用产品 DDL 里的真名字。
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE api_keys (user_name TEXT, is_active INTEGER, expires_at TEXT)")
    conn.executemany(
        "INSERT INTO api_keys (user_name, is_active, expires_at) VALUES (?,?,?)", rows
    )
    out = [r[0] for r in conn.execute(sql.replace("%s", "?"), ())]
    conn.close()
    return out


def test_数可用那半句真的过滤了_is_active(monkeypatch):
    """**尺子**：把产品产出的那句查询丢进真 sqlite 跑 —— 撤销的行必须**数不进去**。

    不许改成"测试自己写一句带 `is_active` 的 SQL"：那样测的是**我抄对没有**，
       产品代码把过滤删掉它照样绿（2026-10-06 那次事故就是这么骗过去的）。
    """
    _counting, usable_sql = _captured_admin_sqls(monkeypatch)
    rows = [
        ("admin", 0, "2999-01-01"),   # ← 已撤销
        ("admin", 1, "2999-01-01"),   # ← 正常
        ("someone_else", 1, "2999-01-01"),
    ]
    assert _run_sqlite(usable_sql, rows) == ["2999-01-01"], (
        f"这句本该只命中 1 行（admin 名下 is_active=1 的那行），实际命中 {_run_sqlite(usable_sql, rows)}\n"
        f"  · 产品那句查询：{usable_sql.strip()}"
    )


def test_把过滤拆掉这条尺子必须变红(monkeypatch):
    """**取反检验**：把谓词拆掉，"撤销的行"必须**冒出来** —— 证明那半句真在干活。

    这条证明的是「**尺子有读数**」，不是产品代码 —— 所以它**不替代**上一条。
    """
    _counting, usable_sql = _captured_admin_sqls(monkeypatch)
    naive = usable_sql.replace(auth.ACTIVE_PREDICATE, "1 = 1")
    assert naive != usable_sql, (
        "SQL 形状变了 —— 本用例的前提（`auth.ACTIVE_PREDICATE` 那半句）不在了"
    )
    rows = [("admin", 0, "2999-01-01")]
    assert _run_sqlite(naive, rows) == ["2999-01-01"], "拆掉过滤后撤销的行照样命中 ⇒ 尺子没问题"
    assert _run_sqlite(usable_sql, rows) == [], "带过滤时撤销的行必须被排除"


# ────────────── ④ 结构性守卫：两条路径不许各写一遍谓词 ──────────────

def test_两条路径的_is_active_口径是同一句(monkeypatch):
    """**这条才是防复发的结构** —— 缺陷的**成因**就是两处各写了一遍。

    谁把其中一处改成 `is_active = 1`（丢掉 `COALESCE`）或另写一句，这条就红。
    同族：本仓 `CLAUDE.md`「**只有文字就漏，结构才执行**」。
    """
    _counting, usable_sql = _captured_admin_sqls(monkeypatch)
    assert auth.ACTIVE_PREDICATE in usable_sql

    # 认证侧那句：从 `verify_api_key` 抓真 SQL（同 `app/tests/test_auth_api_key_active.py` 的手法）
    verify_cur = _AdminCursor(0, [])
    verify_cur.fetchone = lambda: ("alice", _future())
    monkeypatch.setattr(auth, "get_db", lambda: _FakeConn(verify_cur))
    monkeypatch.setattr(auth, "hash_api_key", lambda k: "hash-" + k)
    assert auth.verify_api_key("sk-x") == "alice"
    verify_sql = verify_cur.sqls[0]

    assert auth.ACTIVE_PREDICATE in verify_sql
    assert re.search(r"COALESCE\(is_active, 1\) = 1", verify_sql), (
        f"认证侧那句没带 COALESCE（NULL 行会被静默排除）：{verify_sql}"
    )
    assert re.search(r"COALESCE\(is_active, 1\) = 1", usable_sql), (
        f"自检侧那句没带 COALESCE：{usable_sql}"
    )


# ────────────── ⑤ 启动顺序守卫：自检要读 is_active，必须在建表之后 ──────────────

def test_自检在建表之后调用():
    """`app/main.py` 的 startup 顺序必须是 `create_table` ⇒ `ensure_admin_exists`。

    **为什么**：`is_active` 这一列在**老库**里是靠 `create_table` 里那句
    `ALTER TABLE api_keys ADD COLUMN` 补上的。自检若跑在它前面，
    对着老库就是 `UndefinedColumn` ⇒ **应用起不来**（比 2026-10-06 那次"认证 503"更早、更响）。
    """
    src = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    m = re.search(r"@app\.on_event\(\"startup\"\)\s*\nasync def \w+\(\):(.*?)(?=\n@app\.|\Z)", src, re.S)
    assert m, "没在 main.py 里找到 startup 事件 —— **抠取失效了**，⛔ 别当成通过"
    body = m.group(1)
    i_create = body.find("create_table()")
    i_ensure = body.find("ensure_admin_exists(")
    assert i_create != -1 and i_ensure != -1, (
        f"startup 里少了 `create_table()`({i_create}) 或 `ensure_admin_exists()`({i_ensure})"
    )
    assert i_create < i_ensure, (
        "`ensure_admin_exists()` 排到了 `create_table()` 前面 —— "
        "老库里 `is_active` 列还没补上，自检会 UndefinedColumn 起不来。"
    )
