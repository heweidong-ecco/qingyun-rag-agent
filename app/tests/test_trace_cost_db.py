"""`thread_cost_breakdown`（Trace 页成本轴）—— **需要真 Postgres** 的那一半。

与 `test_trace_cost.py` 的分工：那边用**假 pg** 断言 SQL 的形状（⇒ 能进 CI）；
本文件在**真库**上验**真越权**。**两边都要跑**，别拿假 pg 那份当越权证据。

## 本文件钉的是：**光测"看不到别人的"不够，必须同时测"看得到自己的"**

只断言 `A 查 B 的线程 ⇒ 0 条` 的话，一个**永远回 0 条**的实现（比如 SQL 打错、列名打错、
`user_name` 传反）会**绿着通过** —— 而这恰恰是最可能发生的坏法。
⇒ 每条越权断言旁边**必配阳性对照**（`test_positive_control_*`）。
同族纪律 ⇒ 本仓的复盘记录（⛔ 不随产品仓提供） 的**反证检验**。

## 跑之前必须带库名隔离：`POSTGRES_DB=rag_test`

（本仓有前科：R2 冒烟忘带库名，`record_usage` 往**真库**写了 4 行 ——
本仓的复盘记录（⛔ 不随产品仓提供）。）

本文件**不进 CI**（CI 无 postgres service，见 `app/pytest.ini`）
⇒ **CI 全绿【不代表】本文件绿**。改了这半边，必须本机单独跑一次并把输出贴进 PR。

判据（可打印）：
```bash
POSTGRES_DB=rag_test venv/bin/python -m pytest app/tests/test_trace_cost_db.py -q -p no:warnings
```
"""
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.needs_db


# 本文件专用前缀 —— 三个 test_ 文件共用 rag_test 库，别用 `t1` / `test` 这种会撞上别人的名字
_PREFIX = "fx2-trace-"
_USER_A = _PREFIX + "a"
_USER_B = _PREFIX + "b"
_THREAD = _PREFIX + "shared"     # **两个用户故意用同一个 thread_id** —— 那才是要防的场景
_MODEL = "qwen-turbo"


@pytest.fixture()
def cleanup():
    """前后各清一次 —— 前清是为了**断言精确的条数**（残留会让 count 变大）。"""
    _purge()
    yield
    _purge()


def _purge():
    from core.db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM token_usage_logs WHERE user_name LIKE %s",
                (_PREFIX + "%",),
            )


def _seed(user_name, thread_id, n, prompt=100, completion=0, model=_MODEL):
    """往库里写 n 笔。走 `record_usage`（**生产路径**），不是自己拼 INSERT。"""
    import billing.token_tracker as tt

    for _ in range(n):
        tt.record_usage(
            model=model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            purpose="test",
            user_name=user_name,
            thread_id=thread_id,
        )


# ==================== ① 阳性对照 —— 先证明"看得到自己的" ====================

def test_positive_control_sees_own_rows(cleanup):
    """**本文件全部越权断言的前提**：A 必须看得到自己写的 3 笔。

    这条红了，下面那些"看不到别人的"**一条都不算数** ——
       因为一个永远回 0 的实现会让它们全绿。
    """
    import billing.token_tracker as tt

    _seed(_USER_A, _THREAD, 3)
    out = tt.thread_cost_breakdown(_USER_A, _THREAD)

    assert out["total"]["count"] == 3, f"阳性对照挂了 —— 看不到自己写的行：{out['total']}"
    assert len(out["items"]) == 3
    assert out["total"]["total_tokens"] == 300
    assert out["total"]["total_cost"] > 0, "单价算出来是 0 ⇒ 后面比金额没意义"


def test_positive_control_totals_match_the_rows(cleanup):
    """未被截断时，`total` 应与 `items` 逐笔之和一致。

    这条只在 `truncated=False` 时成立 —— 截断时**必须不一致**（`total` 覆盖整条线程）。
       反过来说：这条绿着，`total` 就不是"随手把 items 加起来"（那样截断时会静默偏小）。
    """
    import billing.token_tracker as tt

    _seed(_USER_A, _THREAD, 4)
    out = tt.thread_cost_breakdown(_USER_A, _THREAD)

    assert out["truncated"] is False
    assert out["total"]["total_tokens"] == sum(i["total_tokens"] for i in out["items"])
    assert out["total"]["total_cost"] == pytest.approx(sum(i["cost"] for i in out["items"]))


# ==================== ② 越权 —— 同一个 thread_id，两个用户 ====================

def test_cannot_see_another_users_rows(cleanup):
    """**本文件存在的理由**。

    A 与 B **用同一个 `thread_id`**（真实场景：`"default"` 这种人人都传的值）。
    以 A 的身份查 ⇒ **只该看到 A 的 3 笔**，不是 5 笔。
    """
    import billing.token_tracker as tt

    _seed(_USER_A, _THREAD, 3)
    _seed(_USER_B, _THREAD, 2)

    a = tt.thread_cost_breakdown(_USER_A, _THREAD)
    b = tt.thread_cost_breakdown(_USER_B, _THREAD)

    assert a["total"]["count"] == 3, f"A 看到了 {a['total']['count']} 笔，跨用户了"
    assert b["total"]["count"] == 2, f"B 看到了 {b['total']['count']} 笔，跨用户了"
    assert a["total"]["count"] + b["total"]["count"] == 5, "总行数不对 ⇒ 播种那步有问题，不是过滤对了"


def test_other_users_rows_do_not_leak_into_totals(cleanup):
    """**合计也要防** —— 只防明细会得到最坏的形态：

    列表只显示自己的 3 笔，**合计却是 5 笔的钱** ⇒ 页面看起来正常，数字是混的。
    """
    import billing.token_tracker as tt

    _seed(_USER_A, _THREAD, 3, prompt=100)
    _seed(_USER_B, _THREAD, 2, prompt=100)

    a = tt.thread_cost_breakdown(_USER_A, _THREAD)
    assert a["total"]["total_tokens"] == 300, f"A 的 token 合计混进了别人：{a['total']}"


# ==================== ③ admin 例外 ====================

def test_include_all_sees_everyone(cleanup):
    """`include_all=True` ⇒ 5 笔。它是**显式开关**，不是"不传用户就不过滤"。"""
    import billing.token_tracker as tt

    _seed(_USER_A, _THREAD, 3)
    _seed(_USER_B, _THREAD, 2)

    out = tt.thread_cost_breakdown(_USER_A, _THREAD, include_all=True)
    assert out["total"]["count"] == 5, f"admin 例外没生效：{out['total']}"


def test_include_all_does_not_widen_without_it(cleanup):
    """成对的反证：**不带** `include_all` 时，哪怕调用者名字是 admin，也只看自己那份。

    这一条防的是"默认放开" —— 本仓最恨的 `user_name=None ⇒ 不过滤` 那种写法。
       （谁能传 `True` 由**端点**判，见 `test_trace_cost.py` 的两条接线用例。）
    """
    import billing.token_tracker as tt

    _seed("admin", _THREAD, 1)
    _seed(_USER_B, _THREAD, 2)

    try:
        out = tt.thread_cost_breakdown("admin", _THREAD)
        assert out["total"]["count"] == 1, f"不带 include_all 却看到了别人的：{out['total']}"
    finally:
        from core.db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM token_usage_logs WHERE user_name = %s AND thread_id = %s",
                            ("admin", _THREAD))


# ==================== ④ 时间必须带区（具体化 B） ====================

def test_created_at_is_an_absolute_instant(cleanup):
    """`created_at` 列是 **`TIMESTAMP`（无时区）**、PG 容器是 `Etc/UTC`。

    ⇒ 输出必须是**带区**的 ISO，且解出来的**绝对时刻**要≈"此刻"。
       比的是**绝对时刻** ⇒ **主机时区怎么设都不影响这条断言**（这正是它比"比字符串"强的地方）。

    反证检验：把 `_iso_utc` 改成裸 `.isoformat` ⇒ 第一句 `tzinfo is not None` 红；
       把时区标成 `+08:00` ⇒ 第二句红（差 8 小时，恰好超出 300 秒窗口）。
    """
    import billing.token_tracker as tt

    _seed(_USER_A, _THREAD, 1)
    iso = tt.thread_cost_breakdown(_USER_A, _THREAD)["items"][0]["created_at"]

    dt = datetime.fromisoformat(iso)
    assert dt.tzinfo is not None, f"时间没带时区 ⇒ JS 会当本地时间解析：{iso!r}"
    skew = abs((datetime.now(timezone.utc) - dt).total_seconds())
    assert skew < 300, f"落库时刻与此刻差了 {skew:.0f} 秒 ⇒ 时区标错了：{iso!r}"


# ==================== ⑤ 空 / 边界 ====================

def test_unknown_thread_returns_empty_not_error(cleanup):
    """从没出现过的 thread_id ⇒ **空结果，不抛**（端点据此回 `200` 而非 404）。"""
    import billing.token_tracker as tt

    out = tt.thread_cost_breakdown(_USER_A, _PREFIX + "never-existed")
    assert out == {"items": [], "total": {"count": 0, "total_tokens": 0, "total_cost": 0.0},
                   "truncated": False}


def test_newest_row_comes_first(cleanup):
    """明细**按时间倒序**（最新在前）—— 账单页从上往下看就是"最近的"。

    光断言"有 3 条"是过不了反证检验的（换成正序照样 3 条）⇒ 断言**顺序本身**。
    """
    import time as _time
    import billing.token_tracker as tt

    for _ in range(3):
        _seed(_USER_A, _THREAD, 1)
        _time.sleep(0.02)          # 必须拉开时间：`CURRENT_TIMESTAMP` 在同一事务/瞬间可能相同

    stamps = [i["created_at"] for i in tt.thread_cost_breakdown(_USER_A, _THREAD)["items"]]
    assert stamps == sorted(stamps, reverse=True), f"明细没按时间倒序：{stamps}"


if __name__ == "__main__":       # `POSTGRES_DB=rag_test venv/bin/python app/tests/test_trace_cost_db.py`
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
