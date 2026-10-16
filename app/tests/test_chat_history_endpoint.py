"""`GET /api/v1/rag/chat_history` —— **读回对话历史**（前端整体调整 · 批 5 · 2026-10-11）。

本文件**不连库、不连 Redis**（裸 `TestClient(app)` + 把取历史那个函数打桩）——
   `check.txt` 那族的理由是 ：别写成 `with TestClient(app) as client:`，那会触发 lifespan。

## 为什么会有这条端点

改前**全仓没有任何"读回对话历史"的出口** —— `get_chat_history` 只被服务端用来喂模型，
而对话页**从来没做过回显**。⇒ 访客换个页面再回来，**页面上是空的、而模型还记得**
（那页自己写着「本页不显示历史消息；这个会话的上下文模型仍然记得」）。
**那不是创空间的限制**（那只是单容器）。需求方 2026-10-11 实测报的：
「进行了一个对话，然后点执行轨迹，再点会话，**原来的对话就没有了**」。

## 本文件管什么

| # | 钉什么 | 不钉会怎样 |
|---|---|---|
| 1 | 没凭据 ⇒ 401 | 对话历史成了**公开可读**的 |
| 2 | **不带 `thread_id` ⇒ 422**（不是回一个默认桶） | 「忘了传」会**静默读回另一个会话**，而页面上看着像"历史空了" |
| 3 | **它去取的是【调用者自己】的历史** —— `get_chat_history` 收到的第一个参数**必须来自鉴权依赖**，不是请求里给的任何东西 | **那是一条越权读别人对话的口子** |

判据（可打印）：`venv/bin/python -m pytest app/tests/test_chat_history_endpoint.py -q -p no:warnings`
"""
from fastapi.testclient import TestClient

from main import app
from routing.deps import get_current_user_hybrid


def _as(user_name: str):
    """**不碰库**地把"调用者是谁"固定下来。

    2026-10-11 实测栽过：一开始用 `create_user_api_key(...)` 真发一把——
       它**要写库**，于是**本机绿、`ci-local`/CI 红**（那边没有 Postgres）。
       ⇒ 用 FastAPI 自己的 `dependency_overrides`，一个字节都不碰库。
    """
    app.dependency_overrides[get_current_user_hybrid] = lambda: user_name


def _client():
    return TestClient(app)


def test_requires_auth():
    """没有 `X-API-Key` ⇒ 401。反证：把 `Depends(get_current_user_hybrid)` 删掉 ⇒ 本条红。"""
    resp = _client().get("/api/v1/rag/chat_history?thread_id=default")
    assert resp.status_code == 401, (
        f"没带凭据却回了 {resp.status_code} —— 对话历史成了公开可读的"
    )


def test_thread_id_is_required():
    """**必填、无默认值** —— 别给它一个默认桶（那会让"忘了传"静默读错会话）。"""
    _as("history_guard_user")
    try:
        resp = _client().get("/api/v1/rag/chat_history", headers={"X-API-Key": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422, (
        f"不带 thread_id 回了 {resp.status_code} —— 它必须有默认值吗？"
        "（本仓口径：有默认值 ⇒ 「忘了传」静默读回另一个桶）"
    )


def test_it_reads_the_callers_own_history_not_somebody_elses(monkeypatch):
    """**隔离**：端点在 `get_chat_history` 里用的用户名，**必须来自鉴权依赖**。

    判据是**记下它真收到什么**，不是"代码里看着像"。
       ⇒ 反证：把端点签名改成收一个 `user_name: str = Query(...)` ⇒ 本条当场红。
    """
    import routing.api_v1_rag as rag

    seen = {}

    def _fake(user_name, *, thread_id):
        seen["user_name"] = user_name
        seen["thread_id"] = thread_id
        return [{"role": "user", "content": "我自己的问题", "status": "done"}]

    monkeypatch.setattr(rag, "get_chat_history", _fake)

    _as("history_guard_user")
    try:
        resp = _client().get(
            "/api/v1/rag/chat_history?thread_id=thread-abc&user_name=someone-else",
            headers={"X-API-Key": "x"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert seen.get("user_name") == "history_guard_user", (
        "传给 `get_chat_history` 的不是调用者自己的名字 "
        f"（实得 {seen.get('user_name')!r}）⇒ **有人能读别人的对话**"
    )
    assert seen.get("thread_id") == "thread-abc"
    body = resp.json()
    assert body["thread_id"] == "thread-abc"
    assert body["messages"][0]["content"] == "我自己的问题"
    # 窗口要说出来 —— 别让页面把"更早的没回来"读成"丢了"
    assert body["window"] == "最近 5 轮"
