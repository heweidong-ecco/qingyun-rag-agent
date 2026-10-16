"""公共语料库—— **它人人可，而用户之间仍互相看不见**。
"""
import inspect

import pytest


# ===========================================================================
# ① 结构守卫 —— 不需要库
# ===========================================================================

def test_public_owner_is_a_single_named_constant():
    """`PUBLIC_OWNER` 的字面值钉住 —— 改名要走一次 DEC，不许悄悄换。"""
    from core.db import PUBLIC_OWNER

    assert PUBLIC_OWNER == "public"


def test_vector_search_mentions_public_owner():
    """向量检索（`core.db.search_similar`）必须把公共语料算进去。

    钉的是**源码里引用了那个常量**（不是钉某一行 SQL 的写法）——
       本仓的规矩是「三处引用同一个常量」，不许各写一遍字面量。
    """
    from core.db import search_similar

    assert "PUBLIC_OWNER" in inspect.getsource(search_similar)


def test_bm25_index_mentions_public_owner():
    """BM25 那条路（`rag.bm25_index.get_all_documents`）同上。

    这一条**容易漏** —— BM25 是**另一条**取语料的路（它不在 `db.py` 里），
       只改向量那条 ⇒ 混合检索里 BM25 那一半看不见公共语料，而**不会有任何报错**。
    """
    from rag.bm25_index import get_all_documents

    assert "PUBLIC_OWNER" in inspect.getsource(get_all_documents)


def test_pg_search_endpoint_mentions_public_owner():
    """`POST /rag/pg_search` 是**第三条**取语料的路（它自己写了一条 SQL）。

    改这次时差点漏掉它 —— 前两处都在 `rag/` 与 `core/`，
       而这条在 `routing/` 里**自己写了一遍**。
    """
    from routing.api_v1_rag import pg_search

    assert "PUBLIC_OWNER" in inspect.getsource(pg_search)


def test_delete_still_checks_the_owner():
    """**写入/删除必须仍按用户** —— 否则访客能删掉公共语料。

    判据（源码级）：删除端点里那段「非 admin 且 owner != 自己 ⇒ 403」还在。
    """
    from routing.api_v1_rag import delete_document

    src = inspect.getsource(delete_document)
    assert "requested_by" in src, "删除端点必须先把 owner 查出来"
    assert "FORBIDDEN" in src, "非本人 ⇒ 必须 403"


# ===========================================================================
# ② 行为用例 —— `isolation_b` 到底看得见什么
# ===========================================================================
_PROBE_DIM = 1024  # `documents.embedding` = `vector(1024)`（B 段）
_PROBE_SRC = "public-corpus-probe-test"
_PROBE_PUBLIC = "公共语料探针 · PUBLIC-DOC · 归属 public · 不含任何真实资料"
_PROBE_PRIVATE = "私有语料探针 · PRIVATE-DOC · 归属 isolation_a · 不含任何真实资料"


def _probe_cleanup():
    from core.db import get_db
    from rag.bm25_index import invalidate_bm25_cache

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE source = %s", (_PROBE_SRC,))
        conn.commit()
    invalidate_bm25_cache()


@pytest.fixture
def probe_docs():
    """造两篇探针：一篇 `public` 的、一篇 `isolation_a` 的；跑完删掉（含公共语料那篇）。

    与 `test_isolation.py` 同款闸：**必须带 `POSTGRES_DB=rag_test`** ——
       本 fixture 会 INSERT/DELETE `documents`，误写**真库**的后果比那次重。
    """
    import os

    if os.environ.get("POSTGRES_DB") != "rag_test":
        pytest.skip("needs_db 用例必须带 POSTGRES_DB=rag_test 跑（app/pytest.ini）—— 防误写真库")

    from core.db import PUBLIC_OWNER, get_db
    from rag.bm25_index import invalidate_bm25_cache

    _probe_cleanup()
    with get_db() as conn:
        with conn.cursor() as cur:
            vec = [0.11] * _PROBE_DIM
            cur.executemany(
                "INSERT INTO documents (content, source, embedding, requested_by) "
                "VALUES (%s, %s, %s::vector, %s)",
                [
                    (_PROBE_PUBLIC, _PROBE_SRC, vec, PUBLIC_OWNER),
                    (_PROBE_PRIVATE, _PROBE_SRC, vec, "isolation_a"),
                ],
            )
        conn.commit()
    invalidate_bm25_cache()

    yield

    _probe_cleanup()


@pytest.mark.needs_db
def test_empty_user_sees_the_public_corpus(probe_docs):
    """**本条就是这一摊存在的理由** —— 一个名下什么都没有的用户，也查得到公共语料。

    （访客进来的那一刻就是这个状态。）
    """
    from core.db import search_similar

    contents = [r[1] for r in search_similar([0.11] * _PROBE_DIM, 10, user_id="isolation_b")]
    assert any(_PROBE_PUBLIC in c for c in contents), "空用户必须看得见公共语料"
    # **反向控制必须有** —— 否则「一条都不返回」也能让上面那句绿不了但看不出原因
    assert not any(_PROBE_PRIVATE in c for c in contents), "⛔ 绝不能看见别人的私有语料"


@pytest.mark.needs_db
def test_bm25_path_also_sees_the_public_corpus(probe_docs):
    """BM25 那条路**也要**看得见 —— 两条路各有一段 SQL，改一条不算改。"""
    from rag.bm25_index import get_all_documents

    contents = [r[1] for r in get_all_documents("isolation_b")]
    assert any(_PROBE_PUBLIC in c for c in contents), "BM25 必须也看得见公共语料"
    assert not any(_PROBE_PRIVATE in c for c in contents), "⛔ 绝不能看见别人的私有语料"
