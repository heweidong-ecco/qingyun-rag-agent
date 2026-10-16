"""`GET /agent/token/recent` 的**身份范围**守卫（§1.3）。

## 为什么单开一份

2026-10-09 做**刀 4 成本看板**的可见性预检时核出：

    `/agent/token/recent` 的签名**收了 `user_name`**（`Depends(get_current_user_hybrid)`），
    而函数体写的是 `get_recent_usage(limit + 1)` —— **身份根本没往下传**。
    它读的 `_usage_records` 是**进程内所有人共用的一张表**
    **返回的是所有人的记录，每条还带着别人的 `user_name`**。

⇒ 那是  /  那一族的同型：**身份收了不用 ⇒ 静默查全库**。
   而且**端点的 docstring 一直写着"获取最近的 Token 使用记录"**、没有一个字说这是全站的
   ⇒ 调用方**看不出它越权**（本仓最恨的那种形态：不报错、页面照常出数）。

## 本文件钉什么

| # | 钉什么 | 不钉会怎样 |
|---|---|---|
| 1 | **两个用户各记一笔 ⇒ 查一个只回他自己的** | 又回到"返回所有人"的状态，而**没有任何东西会红** |
| 2 | **过滤发生在切片【之前】** | 总记录多、这个人占得少时 ⇒ **回空**，看起来像"他没有记录"（不报错） |
| 3 | 给了 `user_name` 时，返回的每条都**真的是他本人的** | 过滤写错字段（如按 `thread_id` 比）不会被第 1 条抓到 |

**本文件不连库**（`_usage_records` 是进程内存）—— 。

判据（可打印）：`venv/bin/python -m pytest app/tests/test_token_recent_scope.py -q -p no:warnings`
"""
import pytest

import billing.token_tracker as tt


@pytest.fixture(autouse=True)
def _clean_records():
    """每个用例前后都把进程内那张表清干净 —— 它是**模块级共享**的。"""
    with tt._lock:
        saved = list(tt._usage_records)
        tt._usage_records.clear()
    yield
    with tt._lock:
        tt._usage_records.clear()
        tt._usage_records.extend(saved)


def _record(user_name: str, *, purpose: str = "answer_generation"):
    tt.record_usage(
        model="deepseek-v4-flash",
        prompt_tokens=10, completion_tokens=5,
        purpose=purpose, user_name=user_name, thread_id="t-" + user_name,
    )


def test_two_users_each_record_but_each_only_sees_their_own():
    """**本文件的主判据**（反证：把 `user_name` 从 `get_recent_usage` 里去掉 ⇒ 本条红）。"""
    _record("alice", purpose="alice_only")
    _record("bob", purpose="bob_only")

    alice = tt.get_recent_usage(20, "alice")
    bob = tt.get_recent_usage(20, "bob")

    assert [r["purpose"] for r in alice] == ["alice_only"], (
        f"alice 查到了不属于她的记录：{alice!r}\n"
        f"⇒ 端点的 user_name 又没传下去（那个缺陷回来了）"
    )
    assert [r["purpose"] for r in bob] == ["bob_only"], f"bob 查到了别人的记录：{bob!r}"


def test_returned_rows_never_carry_someone_elses_name():
    """给了 `user_name` ⇒ 每一条的 `user_name` 必须**就是他本人**（别按别的字段过滤）。"""
    _record("alice")
    _record("bob")
    _record("carol")

    rows = tt.get_recent_usage(20, "alice")
    assert rows, "alice 明明记过一笔，却一条都没回来 —— 过滤写歪了"
    assert {r["user_name"] for r in rows} == {"alice"}, (
        f"返回里混进了别人的名字：{[r['user_name'] for r in rows]!r}"
    )


def test_filter_happens_before_the_limit_slice():
    """**过滤要在切片【之前】** —— 反过来会在"总记录多、他占得少"时**回空**。

    构造：bob 记 30 笔（把 `limit=5` 的窗口全占满），alice 只记 1 笔且在最前面。
    · 正确（先过滤再切片）⇒ 回 alice 那 1 笔
    · 错误（先 `[-5:]` 再过滤）⇒ **回空**，而 **alice 明明有记录**（看着像"她没有记录"，不报错）
    """
    _record("alice", purpose="alice_first")
    for _ in range(30):
        _record("bob")

    rows = tt.get_recent_usage(5, "alice")
    assert [r["purpose"] for r in rows] == ["alice_first"], (
        f"alice 的记录被 bob 的记录挤出窗口了：{rows!r}\n"
        f"⇒ 说明是【先切片再过滤】—— 那在真实数据下会静默回空"
    )


def test_without_user_name_it_still_returns_everyone():
    """不传 `user_name` ⇒ **保持原样**回全体（那个分支还在，供进程内自省用）。

    与上面三条**不是矛盾**：上面钉的是"**端点**必须传名字"，
    这里钉的是"**函数**在不传名字时的语义没被顺手改掉"（`get_user_summary` 同款形状）。
    """
    _record("alice")
    _record("bob")
    assert len(tt.get_recent_usage(20)) == 2
