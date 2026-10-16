# conftest.py
import os

# 必须在 `import main` **之前**设置（· M6 期间实测发现）
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
