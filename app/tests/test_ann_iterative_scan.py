"""· ANN 索引「先扫索引、后套 WHERE」那个坑 —— **真库往返**（`needs_db` · ⛔ 不进 CI）。

```bash
POSTGRES_DB=rag_test venv/bin/python -m pytest app/tests/test_ann_iterative_scan.py -q -m needs_db
```
"""
from contextlib import contextmanager

import pytest

from core.db import (
    _VECTOR_INDEX_AM,
    enable_ann_iterative_scan,
    get_db,
    search_similar,
)

pytestmark = pytest.mark.needs_db


# ==================== ① 索引类型与代码一致 ====================

def test_索引类型与代码一致():
    """🔴 **这一条正是本仓当初漂移了很久都没人发现的那个洞。**

    旧记录（`docs/契约/数据模型.md` §六）写着：代码里是 `ivfflat`、活库是手工建的 `hnsw`，
    **两边不一致，而全仓没有任何东西盯着它** ⇒ 现在盯上了。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT am.amname FROM pg_class c
                JOIN pg_am am ON am.oid = c.relam
                WHERE c.relname = 'documents_embedding_idx'
            """)
            row = cur.fetchone()
    assert row, "🔴 库里没有 documents_embedding_idx ⇒ 向量检索退化成全表扫"
    assert row[0] == _VECTOR_INDEX_AM, (
        f"🔴 活库索引是 `{row[0]}`，而代码声明的是 `{_VECTOR_INDEX_AM}` —— 两边漂移了。"
        f"（`create_table()` 的自愈段本该在启动时重建它；跑一次应用启动即可）"
    )


# ==================== ② 防护真的写进去了、而且不是占位符 ====================

def test_防护真的写进去了_而且不是占位符():
    """🔴 **判据是 `pg_settings`，⛔ 不是 `SHOW`** —— 这是本条门的关键。

    旧版拿 `SHOW ivfflat.iterative_scan` 判"开关生效"。而 **`SHOW` 会被占位符骗**：
    在 **`pgvector < 0.8`**（或共享库还没加载）时，`SET` 会被 PG 当**占位符**收下，
    `SHOW` **照样回你设进去的那个值** ⇒ 看着是绿的，实际什么都没设。

    判别方法：**占位符不出现在 `pg_settings` 里，真 GUC 才出现**（本机实测过）。

    ⚠️ 顺序也照产品来（**先 `SET`、后加载库**）—— 产品那两处就是这么调的，
    而"占位符的值会被后来注册的真 GUC 接收"这个行为**是我们要依赖的契约**，
    所以在这里钉住它：哪天 pgvector 改了，这条门就该红。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            enable_ann_iterative_scan(cur)                      # ← 先设（此时库可能还没加载）
            cur.execute("SELECT '[1,2,3]'::vector IS NOT NULL")  # ← 再加载共享库
            cur.execute("""
                SELECT name, setting FROM pg_settings
                WHERE name IN ('hnsw.iterative_scan', 'ivfflat.iterative_scan')
            """)
            got = dict(cur.fetchall())

    assert got, (
        "🔴 这两个 GUC 根本不在 `pg_settings` 里 ⇒ 它们是**占位符**，防护是空转的。"
        "（多半是 pgvector < 0.8 —— 启动自检 `assert_pgvector_supports_iterative_scan()` 本该拦住）"
    )
    for name in ("hnsw.iterative_scan", "ivfflat.iterative_scan"):
        assert got.get(name) == "relaxed_order", (
            f"🔴 `{name}` 实际是 {got.get(name)!r}，应为 'relaxed_order' "
            f"⇒ 防护没生效，或值被改错了"
        )


def test_换一个事务就复位_不污染连接池():
    """🔴 `SET LOCAL` 的特性 —— 本仓最容易写错的地方。

    写成 `SET`（而不是 `SET LOCAL`）时，设置会**留在这条连接上**，
    而 `get_db()` 是**连接池** ⇒ 下个请求拿到另一条连接 ⇒ 「**时有时无**」。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            enable_ann_iterative_scan(cur)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT '[1,2,3]'::vector IS NOT NULL")
            cur.execute("SHOW hnsw.iterative_scan")
            assert cur.fetchone()[0] != "relaxed_order", (
                "🔴 设置漏到别的请求上了 ⇒ 一定是 `SET` 而不是 `SET LOCAL`"
            )


# ==================== ③ 接线：search_similar 真的调了它 ====================

def test_search_similar_确实接了防护(monkeypatch):
    """⚠️ 本条只证「**接上了**」，效果由 ② 证 —— 两条合起来才成立。

    ⛔ 别把它当成"功能测试"：它不检查检索结果，只检查那一行**还在不在**。
    """
    import core.db as db

    called = []
    _orig = db.enable_ann_iterative_scan

    def _spy(cur):
        called.append(True)
        return _orig(cur)

    monkeypatch.setattr(db, "enable_ann_iterative_scan", _spy)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT embedding::text FROM documents LIMIT 1")
            row = cur.fetchone()
    assert row, (
        "🔴 防空跑：`rag_test.documents` 是空的 ⇒ 这条门无从判定。"
        "⛔ 不许改成 `skip` 蒙混过去（那正是旧版那条装饰门的形态）"
    )
    search_similar(row[0], 1, user_id="admin")
    assert called, (
        "🔴 `search_similar()` 没有调 `enable_ann_iterative_scan()` ⇒ "
        "ANN 防护在检索主链路上断线了（N25 那个坑会立刻复发）"
    )


# ==================== ④ 启动自检的判据边界 ====================

class _FakeCur:
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    @contextmanager
    def cursor(self):
        yield _FakeCur(self._row)


def _fake_get_db(row):
    @contextmanager
    def _cm():
        yield _FakeConn(row)
    return _cm


@pytest.mark.parametrize("ver", ["0.7.4", "0.8.0", "0.8.2", "1.0.0"])
def test_版本自检的边界(monkeypatch, ver):
    """`< 0.8` 必须**拒绝启动**，`>= 0.8` 必须放行 —— 两边都钉。

    ⚠️ 只测"放行"那一侧是不够的：本仓的原版那条门就是**只会绿**的形态。
    """
    import core.db as db

    monkeypatch.setattr(db, "get_db", _fake_get_db((ver,)))
    if ver.startswith("0.7"):
        with pytest.raises(EnvironmentError):
            db.assert_pgvector_supports_iterative_scan()
    else:
        assert db.assert_pgvector_supports_iterative_scan() == ver


@pytest.mark.parametrize("row", [None, (None,), ("abc",)])
def test_版本自探查不到扩展时也要断(monkeypatch, row):
    """三种"查不到"都算故障 ⇒ **都要断**，⛔ 不许降级成 warning。

    ⚠️ `(None,)` 与 `None` 是**两种不同的返回形态**（有扩展但版本是 NULL / 压根没这扩展）——
    只判 `not row` 会让前者掉进"认不出版本号"的分支、**报出错的理由**（反证时实测到过）。
    """
    import core.db as db

    monkeypatch.setattr(db, "get_db", _fake_get_db(row))
    with pytest.raises(EnvironmentError):
        db.assert_pgvector_supports_iterative_scan()
