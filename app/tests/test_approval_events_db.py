"""`approval_events` 的**真库**那一半 —— （+ §五 判据②）。

与 `app/tests/test_approval_events.py` 的分工：本文件**只放"必须真读写库"的**。
"""
import pytest

pytestmark = pytest.mark.needs_db


# 本文件专用前缀，便于事后精确清理（别用 t1 / test 这种会撞上别人的名字）
_PREFIX = "n12-approval-"


#: **补的清理** —— 补之前这个文件**只能跑一次**。
#:
#: **怎么发现的**：加分页用例时，**原本绿的 4 条转红了**，报「读到 **3** 条」——
#: 那不是被改坏的，是**同一批行被写了 3 次**（我第 3 次跑这个文件）。
@pytest.fixture(autouse=True)
def _clean_own_rows():
    from core.db import get_db

    def _purge():
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM approval_events WHERE owner LIKE %s",
                            (_PREFIX + "%",))
            conn.commit()

    _purge()          # 进用例前先清（跑第 2 次也不受上一次影响）
    yield
    _purge()          # 出用例后再清（别把垃圾留给下一个）


def _row(**kw):
    """把 `list_decisions` 返回的一条记录补齐默认值，方便逐格断言。"""
    base = {"owner": None, "actor": None, "decision": None,
            "edited": None, "rounds": None, "reason": None}
    base.update(kw)
    return base


def test_ddl_is_accepted_by_postgres_and_the_row_comes_back():
    """**本文件存在的理由** —— 真写一次、再真读回来。

    改前：`_DDL` 从没在 PG 上执行过（离线那 12 条喂的是假 pg）⇒ 它写错了也**永远绿**。

    **判据是"读回来了"，不是"没报错"** —— `record_decision` 是 fail-open。
    """
    from agent.approval_audit import record_decision, list_decisions

    owner = _PREFIX + "core-o"
    reason = _PREFIX + "core-r"

    record_decision(owner=owner, actor=_PREFIX + "core-a", decision="approve",
                    edited=False, rounds=2, reason=reason)

    rows = [r for r in list_decisions(owner=owner) if r["reason"] == reason]

    assert len(rows) == 1, (
        f"写完再从库里读，这条裁决不在（读到 {len(rows)} 条）⇒\n"
        "  ⛔ 别去看返回值，去看服务端日志里有没有 `[ApprovalAudit] 记录裁决失败` 那一行 ——\n"
        "     本函数 fail-open，写失败**只会打日志**。"
    )
    assert rows[0]["decision"] == "approve"
    assert rows[0]["rounds"] == 2

    # 自证（别删）：换个**不存在**的 owner 必须读不到东西。
    #    ⇒ 证明上面那句"读回来了"不是因为 `list_decisions` 无视 owner 返回全量。
    assert list_decisions(owner=_PREFIX + "no-such-owner") == [], (
        "查一个不存在的 owner 居然有结果 ⇒ `WHERE owner = %s` 没生效，"
        "上面那条断言就不是证据。"
    )


def test_owner_and_actor_are_two_separate_identities():
    """`owner`（会话是谁的）与 `actor`（谁做的裁决）**必须分开存**。

    admin 接管 alice 的会话时**必然不同** —— 合成一个字段 ⇒ **留痕当场变假话，且不报错**。
    这是  立这张表时的第一条理由，所以它值得一条**真库**上的守卫。
    """
    from agent.approval_audit import record_decision, list_decisions

    owner = _PREFIX + "id-o"
    actor = _PREFIX + "id-a"

    record_decision(owner=owner, actor=actor, decision="edit", edited=True, rounds=1)

    rows = [r for r in list_decisions(owner=owner) if r["actor"] == actor]
    assert len(rows) == 1, "owner/actor 分开存的行读不回来"

    assert rows[0]["owner"] == owner, (
        f"读回来 owner 变成了 {rows[0]['owner']!r} ⇒ `_COLUMNS` 与 SELECT 的列顺序错位了。\n"
        "  ⚠️ 那个 `zip(_COLUMNS, row)` 是**按位置**配名的 —— 错位**不报错**，只把值串到别的列上。"
    )
    assert rows[0]["actor"] == actor


def test_edited_lands_as_a_real_boolean_column():
    """`edited` 在**真库里**必须是 `boolean`（不是 text/int）。

    `record_decision` 里传的是 `bool(edited)` ⇒ 若 DDL 写成 `TEXT`，
    **PG 也会照收**（psycopg2 会把 `True` 送成字符串）⇒ 不报错、读回来是 `'true'`。
    ⇒ **只有查 `information_schema` 才知道类型对不对**，所以本条直接问库。
    """
    from core.db import get_db

    # ⚠️ 这里原先有个 `if db.backend_name() == "sqlite": pytest.skip(...)` 的守卫。
    #    🔴 **删掉** —— 理由不是"它多余"，是**它引用的东西已经不存在**：
    #    SQLite 通道 **就整条删了**⇒ `core/db.py` 里
    #    **已经没有 `backend_name` 这个函数** ⇒ 那一句直接 `AttributeError`
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'approval_events' AND column_name = 'edited'"
            )
            row = cur.fetchone()

    assert row is not None, (
        "`information_schema` 里找不到 `approval_events.edited` ⇒ 表没建出来，"
        "或者建它的那份 DDL 与 `app/agent/approval_audit.py` 的 `_DDL` 不是同一份。"
    )
    data_type, is_nullable = row
    assert data_type == "boolean", (
        f"`approval_events.edited` 在真库里的类型是 `{data_type}`，不是 `boolean`。\n"
        "  ⚠️ 症状：写入不报错，读出来是 'true'/'1' —— 于是 `edited=True` 这种判断静默失效。"
    )
    assert is_nullable == "NO", "`edited` 该是 NOT NULL（没写就说明那条 DDL 没落地）"


def test_newest_first_ordering():
    """`list_decisions` 的契约是**最新在前**（`ORDER BY created_at DESC, id DESC`）。

    只写 `created_at DESC` 会有**同毫秒并列**（本测试连写三条就是这种情况）
    ⇒ 第二条排序键 `id DESC` 不是装饰，本条钉的就是它。
    """
    from agent.approval_audit import record_decision, list_decisions

    owner = _PREFIX + "order-o"
    tags = [_PREFIX + "order-1", _PREFIX + "order-2", _PREFIX + "order-3"]

    for t in tags:
        record_decision(owner=owner, actor=owner, decision="approve",
                        edited=False, reason=t)

    got = [r["reason"] for r in list_decisions(owner=owner) if r["reason"] in tags]

    assert got == list(reversed(tags)), (
        f"顺序不对：期望最新在前 {list(reversed(tags))}，实得 {got}。\n"
        "  ⚠️ 三个 `reason` 是连写的 ⇒ `created_at` 极可能完全相同 —— 检查 SQL 里有没有 `id DESC`。"
    )


def test_owner_none_means_all_but_a_name_means_only_that_name():
    """`owner=None` ⇒ **全量**（admin 那条路）；给了名字 ⇒ **只出那一个人的**。

    `owner` 在签名里是**必填关键字参数**（没有默认值）—— 本条同时钉住这两半：
    不给 / 给错都不会静默变成"全量"。
    """
    from agent.approval_audit import record_decision, list_decisions

    a, b = _PREFIX + "scope-a", _PREFIX + "scope-b"
    record_decision(owner=a, actor=a, decision="approve", edited=False,
                    reason=_PREFIX + "scope-ra")
    record_decision(owner=b, actor=b, decision="reject", edited=False,
                    reason=_PREFIX + "scope-rb")

    only_a = list_decisions(owner=a)
    assert any(r["reason"] == _PREFIX + "scope-ra" for r in only_a)
    assert not any(r["owner"] == b for r in only_a), "给了 owner 却读到了别人的行"

    everything = list_decisions(owner=None)
    assert any(r["owner"] == a for r in everything)
    assert any(r["owner"] == b for r in everything), (
        "`owner=None` 应当是**全量**，但读不到另一个 owner 的行。"
    )


def test_nullable_columns_accept_none_and_ddl_is_idempotent():
    """两件事一起钉（它们都是"惰性建表"这条路的一部分）：

    ① `raw_thread_id` / `graph` / `rounds` / `reason` **可空** ——
       真实调用里这四格经常不给（见 `record_decision` 的签名默认值）。
    ② `CREATE TABLE IF NOT EXISTS` 跑第二次**不炸** —— 它就在写入路径里，
       每次裁决都会执行一遍。
    """
    from agent.approval_audit import _DDL, record_decision, list_decisions

    owner = _PREFIX + "null-o"

    # ② 先单独把 DDL 再跑一遍（模拟"表已存在时的第二次"）
    from core.db import get_db

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(_DDL)
        conn.commit()

    # ① 全空的那三格 + rounds 都不传
    record_decision(owner=owner, actor=owner, decision="reject", edited=False)

    rows = list_decisions(owner=owner)
    assert len(rows) == 1, "只传必填参数的那次写入没落库"

    r = _row(**rows[0])
    assert r["rounds"] is None, f"`rounds` 没传却读回 {r['rounds']!r}（该是 NULL）"
    assert r["reason"] is None, f"`reason` 没传却读回 {r['reason']!r}（该是 NULL）"


def test_offset_paging_neither_skips_nor_repeats():
    """**分页最容易出的错是【漏行 / 重行】，而且它不报错**（`frontend/README.md` §六）。

    只有 `ORDER BY` 是**全序**时才不会漏 —— 本表用 `created_at DESC, id DESC`，
       而**连写的几条 `created_at` 极可能完全相同**（同毫秒）⇒ 第二条排序键 `id` 是承重的。

    判据：把同一批数据按 `offset` 翻完，**合起来必须恰好等于不分页读到的集合**（不重不漏）。
    """
    from agent.approval_audit import record_decision, list_decisions

    owner = _PREFIX + "page-o"
    n = 7
    for i in range(n):
        record_decision(owner=owner, actor=owner, decision="approve", edited=False,
                        reason=f"{_PREFIX}page-{i}")

    # 不分页（足够大）⇒ 基准集合
    all_rows = list_decisions(owner=owner, limit=1000)
    baseline = [(r["reason"],) for r in all_rows]
    assert len(baseline) == n, f"应写入 {n} 条，实得 {len(baseline)}"

    # 每页 3 条翻完
    page, seen, offset = 3, [], 0
    while True:
        got = list_decisions(owner=owner, limit=page, offset=offset)
        if not got:
            break
        seen += [(r["reason"],) for r in got]
        offset += page
        if offset > 100:                      # 护栏：别把用例挂死
            raise AssertionError("翻页没有终点 —— `offset` 没起作用，在原地打转")

    assert seen == baseline, (
        "翻页合起来与不分页读到的不是同一个集合 ⇒ 漏行或重行。\n"
        f"  分页得 {len(seen)} 条 / 基准 {len(baseline)} 条\n"
        "  ⚠️ 检查 `ORDER BY` 是不是【全序】（同 created_at 由 id 打平）—— 不是全序就会这样，且不报错。"
    )


def test_has_more_contract_is_the_plus_one_trick():
    """端点的 `has_more` 判法是「**多取一条**」（`api_v1_agent.agent_approval_history`）。

    这条钉住那个**前提**：拿 `limit+1` 去读，**恰好**在"还有"时能多拿到一条。
    ⇒ 若有人把 `LIMIT %s OFFSET %s` 改成不带 offset、或把 `+1` 漏掉，这里会先红。
    """
    from agent.approval_audit import record_decision, list_decisions

    owner = _PREFIX + "hm-o"
    for i in range(4):
        record_decision(owner=owner, actor=owner, decision="reject", edited=False,
                        reason=f"{_PREFIX}hm-{i}")

    # 要 3 条、给 4 条 ⇒ 说明"还有"
    assert len(list_decisions(owner=owner, limit=3 + 1, offset=0)) == 4
    # 要 4 条、给 5 条 ⇒ 拿不到 5 条 ⇒ 说明"到底了"
    assert len(list_decisions(owner=owner, limit=4 + 1, offset=0)) == 4
    # 第二页：offset 真起作用
    assert len(list_decisions(owner=owner, limit=10, offset=4)) == 0
    assert len(list_decisions(owner=owner, limit=10, offset=3)) == 1
