# conftest.py
import os

# 必须在 `import main` **之前**设置（2026-09-17 · M6 期间实测发现）
#
# 病因：`cost_dashboard.py:218` 在**导入期**就建 Gradio Blocks，Gradio 随即起
#   **非 daemon** 线程去连 `huggingface.co` 发匿名遥测（`gradio/analytics.py`
#   → `huggingface_hub._telemetry._send_telemetry_in_thread`），外加两条
#   `posthog/consumer.py` 上报线程。本机网络不通时它们**卡在 TCP connect 上永不返回**，
#   主线程永远停在 `threading._shutdown` ⇒ **22 条用例全 PASSED，进程却退不出去**。
#
# 实测：不设该开关 → 20s+ 不退出；设了 → 11s 内正常退出（同一个套件）。
# 它会**随机复现**（取决于当次 DNS/TCP 是快速失败还是挂住），不是稳定失败 ——
#   这正是它一直没被发现的原因。卡住的 CI job 会一直耗到超时上限。
#
# 位置不能挪到各测试模块里：`conftest.py` 先于所有测试模块被导入，
#   而本文件第 20 行左右就要 `from main import app` —— 那之后设什么都晚了。
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import pytest
from fastapi.testclient import TestClient
from main import app
from core.config import LOGIN_USER_NAME, LOGIN_PASSWORD


class FakeRedis:
    """假 redis：只实现 `chat_history` 用到的 `rpush` / `lrange` / `expire`。

    **`lrange` 必须仿真 Redis 语义，不能写成 `items[start:end]`** ——
       `get_chat_history` 调的是 `lrange(key, -10, -1)`，而 Python 的 `[-10:-1]`
       **不含最后一个元素**，与真 Redis 的 `LRANGE -10 -1`（**含**）相反
       ⇒ 照抄会让「成对写入」的断言**假红/假绿**。
    """

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.expired: list[tuple[str, int]] = []

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if start < 0:
            start = max(len(items) + start, 0)
        if end < 0:
            end = len(items) + end
        return items[start:end + 1]          # ← 含 end（Redis 语义）

    def expire(self, key, seconds):
        self.expired.append((key, seconds))

    def history(self, user_name, thread_id):
        """按**契约 C 的键**取历史—— 键里现在是 `(user, thread)` 的复合键。

        `thread_id` **必填、无默认值**（同产品侧那三个函数）。
           **不许给它一个 `"t1"` 之类的默认值**：本仓各测试文件用的会话名并不相同
           （`t-cancel` / `t-sse` / `t-chain` / `t1`），一个"顺手"的默认值会让**读错桶**，
           而 `assert history(...) == []` 那类断言会**静默变绿**（读了个空桶，
           看起来像"确实没写"）—— 那正是  §六 ③ 裁定的 fail-open 形状。
        """
        import json

        from access.session_key import session_key
        return [
            json.loads(x)
            for x in self.lists.get(f"chat_history:{session_key(user_name, thread_id)}", [])
        ]


@pytest.fixture
def fake_redis():
    """一条用例一个干净的假 redis（不碰本机/CI 的真 redis）。"""
    return FakeRedis()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """登录管理员账号，返回 JWT 认证头

    口令从 `config` 读（= `.env` 的 `LOGIN_PASSWORD`），**不硬编码** ——
    """
    assert LOGIN_PASSWORD, "需在 .env 设置 LOGIN_PASSWORD（应用启动也会因此失败）"
    response = client.post("/api/v1/auth/login", json={
        "user_name": LOGIN_USER_NAME,
        "password": LOGIN_PASSWORD
    })
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def sample_document():
    return {
        "content": "Python是一门强大的编程语言，广泛应用于AI和数据科学领域。",
        "source": "test_docs"
    }

# ══════════════════════════════════════════════════════════════════════
# B1：**每条用例前后都清掉角色缓存**
# ══════════════════════════════════════════════════════════════════════
#
# `permission.get_user_role` 带**进程内 TTL 60s 缓存**（它在配额热路径上，
# 一个请求最多调 10 次 —— 见 `app/access/permission.py` 的 docstring）。
#
# **缓存是【进程级】的** ⇒ 它**会跨用例残留**。那会带来两种**都不出声**的坏结果：
#   ① 某条用例先替身出了一个角色 ⇒ 后面真调 `get_user_role` 的用例**拿到那个替身值**；
#   ② 某条用例改了库里的 `role` ⇒ 后面 60 秒内的用例**仍看到旧值**。
# 两者都会让结论**取决于用例的【执行顺序】** —— 而"顺序换了就红/就绿"在本仓是明令要消的形态。
#
# ⇒ 用 autouse fixture 清掉，让每条用例都从**冷缓存**开始。
#    它**不是**"为了让测试过"的开关 —— 缓存本身有 `test_user_role_from_db.py`
#      那 10 条专门钉它（TTL / 写侧失效 / 不误伤别人）。这里清掉的只是**跨用例的脏残留**。
@pytest.fixture(autouse=True)
def _clear_role_cache_between_tests():
    """见上方长注释：消掉「结论随执行顺序变」这个不确定性。"""
    from access.permission import invalidate_role_cache

    invalidate_role_cache()
    yield
    invalidate_role_cache()


# ══════════════════════════════════════════════════════════════════════
#  · 真库闸：**要真库的用例，库名必须以 `_test` 结尾**
# ══════════════════════════════════════════════════════════════════════
#
# ## 它修的是什么
#
# `needs_db` / `integration` 那些用例会**真的写库**（往 `token_usage_logs` / `documents` /
# `api_keys` 里 INSERT）。而本仓**有前科**：
# ——R2 冒烟忘带库名，`record_usage` 往**真库**写了 4 行。
#
# 2026-10-13 实查：真库 `rag_db` 里躺着**一批测试写进去的假记录** ——
#   `purpose` 那一列写着 test、`user_name` 是 test_user / user-a，
#   其中一条 **`total_tokens = 999 999`**。
#   **这里不许把那个 `purpose` 的字面量原样写出来**（本仓那一族的坑，已栽 5 次）：
#   `app/tests/test_daily_token_split.py` 会**正则扫全 `app/`**（含本文件、不含 `tests/`）
#   找它，写成字面量 ⇒ **那条门会报在本注释头上**（2026-10-13 当场发生过一次）。而**全站日 token 顶**是
# `SUM(所有人)`、**不带 `user_name` 过滤** ⇒ **一条这种假记录就能吃掉当天近 100% 的预算**
#
# ## 为什么必须做成【结构】
#
# `app/pytest.ini` 与 `app/tests/CLAUDE.md` 都写着「**本机跑 `needs_db` 必须带
# `POSTGRES_DB=rag_test`**」—— **但那是一句【文字】，没有任何东西执行它**。
# 本仓立场：**「只有文字就漏，结构才执行」**。
# 而且这个漏法**特别隐蔽**：写进去了**不会有任何异常**，
# 要到**当天晚些时候**全站额度突然耗尽才看得出来，而那时**已经找不到成因了**。
#
# ## 它管不到谁（如实记下）
#
# 只拦**带 marker** 的用例。**不带 marker 却真连库**的用例不受这条闸管 ——
# 那种写法本身就是另一条账（它连"我要真库"这句话都没说）。
#
# ## 反证（跑过）
#
# `venv/bin/python -m pytest app/tests/test_session_budget.py -q`（**不带库名**）⇒ **红**；
# 前面加 `POSTGRES_DB=rag_test` ⇒ 正常跑。


def needs_db_isolation_problem(db_name) -> str | None:
    """库名不满足隔离要求 ⇒ 返回**给人看的那句话**；满足 ⇒ `None`。

    **抽成纯函数**是为了让它**能被单测**（真跑那条 fixture 要起子进程，
       而本仓已实测：**子进程里 import `main` 会撞 mem0 的 Qdrant 单实例锁**，
       所以"起个子进程跑一条用例看它红不红"这条路在本仓**走不通**）。
    """
    if str(db_name or "").endswith("_test"):
        return None
    return (
        f"🔴 这条用例要【真库】，而 `POSTGRES_DB` 现在是 {db_name!r} —— "
        "它**不是**以 `_test` 结尾的隔离库。\n"
        "⇒ 它真跑起来会**往真库写**（本仓前科）。\n"
        "⇒ 跑法：`POSTGRES_DB=rag_test venv/bin/python -m pytest app/tests/… -q`\n"
        "⚠️ 这条闸是在它**能写库之前**拦的；⛔ 别为了「让它跑起来」把库名改成别的 —— "
        "要跑真库用例，就去建/用那个 `_test` 库。"
    )


@pytest.fixture(autouse=True)
def _needs_db_tests_must_run_against_a_test_database(request):
    """见上方长注释—— **真库闸**。"""
    if not (request.node.get_closest_marker("needs_db")
            or request.node.get_closest_marker("integration")):
        return

    from core.config import POSTGRES_DB

    problem = needs_db_isolation_problem(POSTGRES_DB)
    if problem:
        pytest.fail(problem, pytrace=False)
