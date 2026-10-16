"""全站【embedding】的两个滚动窗口 ——  §二·8 裁「甲」的那道闸。

## 它在补什么洞

 只重定了**全站 LLM 的日顶**，**embedding 这条没人管** ——
而约束它的是**另一把尺子**：DashScope 那把 key 的免费额度 = **100 万 token【总量】/ 90 天**。
缺口**算得出来**：发号闸 300 人/天 × 访客 10 000/天 = **一天上限 300 万**
⇒ **8 小时就能把 90 天的额度烧光**，而没有任何门会红。

## 本文件量两件事

1. **判定函数本身**（单测：没触顶 / 日窗触顶 / 短窗单独触顶）；
2. 🔴 **它真的接在中间件上了吗**（端到端 —— ⛔ 单测证不了**接线**）。
   ⚠️ 外加一条 **正控**：**非 embedding 池**的路径⛔ **不许被它拦** ——
   没有那一条，一个"把整站都拦掉"的实现也能让上面那条绿。
"""
from datetime import datetime

import billing.token_tracker as tt
from billing.token_config import (
    GLOBAL_EMBEDDING_DAILY_LIMIT,
    GLOBAL_EMBEDDING_HOURLY_LIMIT,
)


def _force_over(monkeypatch):
    """把判定函数换成"恒触顶" —— ⚠️ 它在 `main.py` 里是**函数内 import**，
    所以 patch 模块属性就够（`from X import Y` 是在**调用时**去取 `X.Y` 的）。"""
    monkeypatch.setattr(tt, "check_global_embedding_budget",
                        lambda: (False, "模拟触顶", 3600))


# ===========================================================================
# 一 · 接线（端到端）
# ===========================================================================
def test_gate_returns_429_on_an_embedding_path(client, monkeypatch):
    """🔴 embedding 池的路径在触顶时**必须**回 429 + `Retry-After`。"""
    _force_over(monkeypatch)

    r = client.post("/api/v1/rag/insert", json={"content": "x", "source": "t.md"})

    assert r.status_code == 429, f"embedding 池的路径没被拦：{r.status_code}"
    assert r.headers.get("Retry-After"), "429 没带 `Retry-After`（客户端不知道该等多久）"
    body = r.json()
    assert body.get("scope") == "global_embedding", f"没标清是谁拦的：{body}"
    assert body.get("code") == "QUOTA_EXCEEDED"


def test_the_gate_does_not_touch_a_non_embedding_path(client, monkeypatch):
    """**正控**：同一条被写坏的判定 ⇒ **LLM 池**那条路⛔ **不许**变成 429。

    ⚠️ 判据是 `!= 429`（不是 `== 200`）—— 这条路上没有有效凭据，401/422 都可能，
       而**本条只关心"是不是这道闸拦的"**。
    """
    _force_over(monkeypatch)

    r = client.post("/api/v1/rag/search", json={"question": "x"})

    assert r.status_code != 429, "🔴 这道闸拦到了它不该管的池（`/rag/search` 是 llm 池）"


# ===========================================================================
# 二 · 判定函数（单测）
# ===========================================================================
def test_passes_when_under_both_limits(monkeypatch):
    """没触顶 ⇒ 放行，且 `Retry-After` 是 **0**（放行时它没意义）。"""
    monkeypatch.setattr(tt, "get_global_embedding_usage", lambda hours=0: 0)

    ok, why, wait = tt.check_global_embedding_budget()

    assert ok and wait == 0, (ok, why, wait)


def test_daily_window_trips(monkeypatch):
    """**日窗**到顶 ⇒ 不放行；`Retry-After` 是"到次日 0 点"（⛔ 不是固定一小时）。

    🔴 **2026-10-15 修**：这条原先断的是 `wait > 3600` —— 那是拿「到 0 点还剩多久」**当代理量**，
       而它**每天最后一小时必然 ≤ 3600** ⇒ **23:00 之后跑必红**
       （实测 23:08 得 3110 秒 · 本仓有一份复盘就叫「**一道硬币做的门**」）。
       ⇒ 换成**真正的不变量**：它必须**逐字等于 `_seconds_to_tomorrow()`**
       （⛔ 不是"比 3600 大"），而 `_seconds_to_tomorrow()` 本身**用冻结的钟**单独钉死（下一条）。
    """
    monkeypatch.setattr(
        tt, "get_global_embedding_usage",
        lambda hours=0: GLOBAL_EMBEDDING_DAILY_LIMIT if hours == 0 else 0,
    )

    ok, why, wait = tt.check_global_embedding_budget()

    assert not ok and "日" in why
    assert wait == tt._seconds_to_tomorrow(), (
        f"日窗触顶该给「到次日 0 点」的秒数，实得 {wait} 秒"
    )
    assert 0 < wait <= 86400, f"「到次日 0 点」跑出这个范围就是算错了：{wait} 秒"


def test_seconds_to_tomorrow_is_pinned_with_a_frozen_clock(monkeypatch):
    """🔴 **用冻结的钟**把这个量本身钉死 —— ⛔ 别再让上面那条看"现在几点"。

    原来那条 `> 3600` 就是栽在这里：它**每天最后一小时必红**。
    """
    class _FrozenLateNight:
        @staticmethod
        def now():
            return datetime(2026, 10, 15, 23, 8, 9)

    monkeypatch.setattr(tt, "datetime", _FrozenLateNight)
    assert tt._seconds_to_tomorrow() == 3111, "23:08:09 ⇒ 到 0 点 51 分 51 秒 = 3111"

    class _FrozenNoon:
        @staticmethod
        def now():
            return datetime(2026, 10, 15, 12, 0, 0)

    monkeypatch.setattr(tt, "datetime", _FrozenNoon)
    assert tt._seconds_to_tomorrow() == 43200, "12:00 ⇒ 到 0 点 12 小时 = 43200"


def test_hourly_window_trips_on_its_own(monkeypatch):
    """🔴 **短窗单独触顶也要拦** —— 只留日窗的话，一个人几秒内就能把当天额度吃光，
    后面来的**真人全进不来**（§2.1 用"两个窗口"的理由）。"""
    monkeypatch.setattr(
        tt, "get_global_embedding_usage",
        lambda hours=0: 0 if hours == 0 else GLOBAL_EMBEDDING_HOURLY_LIMIT,
    )

    ok, why, wait = tt.check_global_embedding_budget()

    assert not ok and "小时" in why, (ok, why)
    assert wait == 3600, f"短窗触顶该等一小时，实得 {wait} 秒"


def test_both_windows_are_counted_from_the_same_table(monkeypatch):
    """⚠️ 两个窗口读的**必须是同一张表、同一个 `purpose` 过滤** ——

    这条钉的是"别哪天有人给短窗换一张表/换一个 purpose 而没人发现"。
    """
    seen = []

    def spy(hours=0):
        seen.append(hours)
        return 0

    monkeypatch.setattr(tt, "get_global_embedding_usage", spy)
    tt.check_global_embedding_budget()

    assert sorted(seen) == [0, 1], f"两个窗口没各查一次：{seen}"
