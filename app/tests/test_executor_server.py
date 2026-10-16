"""执行器服务的口径守卫—— **全离线**，不起容器、不打外网。

## 这个服务是什么

`app/tools/executor_server.py` —— 跑在**独立执行器容器**里的极小 HTTP 服务（**热启动常驻**）。
应用侧（`code_executor_impl`）通过 `EXECUTOR_URL` 调它，**应用不碰 `docker.sock`**。

## 本文件钉住三条（不是"文件里有这个函数"）

| # | 钉什么 | 为什么是这条 |
|---|---|---|
| ① | 响应形状 = `{"ok": bool, "out": str}` | 应用侧要**复用同一套解析**；形状漂了就是静默错 |
| ② | **请求之间不串状态** | 一个"常驻解释器"的实现会让变量跨请求活着 —— 那是**安全缺陷**，不是性能问题 |
| ③ | **超时是硬杀，且杀完服务还活着** | 线程超时**杀不掉**（本仓 `code_executor_impl` 文件头写着这段）；只有**可杀的子进程**才做得到 |

**白名单 / 子进程脚本都复用 `code_executor_impl`**（`_SANDBOX_CHILD` / `MAX_EXEC_TIME`）。
   本服务**不许抄第二份白名单** ——  刚把"手抄 5 份 `calculator`"收口掉。
"""

import pytest
from fastapi.testclient import TestClient

import tools.executor_server as E


@pytest.fixture
def client():
    return TestClient(E.app)


# ===========================================================================
# ① 响应形状
# ===========================================================================
def test_execute_returns_the_program_output(client):
    """`print(6*7)` ⇒ `{"ok": True, "out": "42\n"}` —— 形状就是应用侧要解析的那个。"""
    r = client.post("/execute", json={"code": "print(6*7)"})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "out": "42\n"}


def test_execute_reports_a_sandbox_error_with_ok_false(client):
    """被执行的代码抛异常 ⇒ `ok=False`，**不是 500**。

    判据是 `ok` 这个**字段**，不是"状态码不是 200" ——
       `ok` 是**给调用方程序读的**，状态码是给**运维**读的，两者都要对。
    """
    r = client.post("/execute", json={"code": "1/0"})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "ZeroDivisionError" in body["out"]


def test_missing_code_is_rejected(client):
    """没有 `code` ⇒ 4xx。别静默当成空串去执行。"""
    r = client.post("/execute", json={})

    assert 400 <= r.status_code < 500


# ===========================================================================
# ② 不串状态 —— 「常驻解释器」会让这条红
# ===========================================================================
def test_execute_does_not_leak_state_between_requests(client):
    """上一次执行留下的名字，**下一次看不见**。

    **这条是本文件的重点**：如果实现图省事、把 `exec` 的 `globals` **长期留着**
       （"常驻解释器"那一类），变量就会**跨请求活着** ⇒ 访客之间互相污染，
       而且下一次执行的行为**取决于别人跑过什么**。那是**安全缺陷**，不是性能取舍。

    **反证（已实测）**：写一个"常驻 globals"的漏实现（`exec(code, 同一个 dict)`）
       判据输出 **`True`** ⇒ 本用例红。⇒ **这条尺子确实在量它该量的事。**

    **判据为什么不用 `globals`**：沙箱的**白名单里没有它**（实测 ⇒ `NameError`）。
       白名单里有的是 **`dir`** —— `exec(code, g)` 里 `dir` 列的就是 `g` 的键，
       正是我们要看的那个东西。
    """
    client.post("/execute", json={"code": "_LEAK = 'yes'"})

    r = client.post("/execute", json={"code": "print('_LEAK' in dir())"})

    assert r.json()["out"].strip() == "False", "上一次执行的名字漏到下一次了"


# ===========================================================================
# ③ 超时硬杀 + 杀完还活着
# ===========================================================================
def test_timeout_is_hard_killed_and_server_still_works(client):
    """死循环 ⇒ 到点**强制终止**，且**服务本身不受影响**（下一个请求照常）。

    为什么这条重要：**线程超时杀不掉 Python 线程** —— 那个死循环会**一直转下去**，
       把 CPU 占死、最终拖垮整个进程。只有**独立子进程**才做得到"硬杀"。
       ⇒ 这条**同时**验了"超时生效"与"是子进程（可杀）"两件事。

    本用例会**真的等满 `MAX_EXEC_TIME`（5 秒）** —— 那是有意的：
       把时限调小再测，测的就不是**真实配置**下的行为了。
    """
    r = client.post("/execute", json={"code": "while True:\n    pass"})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "强制终止" in body["out"]

    # 关键的后半段：服务**还活着**
    r2 = client.post("/execute", json={"code": "print(1+1)"})
    assert r2.json() == {"ok": True, "out": "2\n"}


# ===========================================================================
# 健康检查（容器的 healthcheck 要用）
# ===========================================================================
def test_health_endpoint(client):
    """`GET /health` ⇒ 200 —— docker-compose 的 `healthcheck` 靠它。"""
    assert client.get("/health").status_code == 200


# ===========================================================================
# ④ 并发上限—— 别让一个客户把执行器打爆
# ===========================================================================
def test_concurrent_executions_are_capped(monkeypatch):
    """同时**正在跑**的代码数 ≤ `MAX_CONCURRENT_EXECUTIONS`。

    **为什么这条要单独钉**：执行器容器只有 **0.5 CPU / 256 MB**。
       没有上限的话，一个访客连打 50 个请求 ⇒ 50 个 Python 子进程同时起来
       ⇒ **内存打爆（`Swap=0` ⇒ 直接 OOM kill）** ⇒ **整个执行器没了**，
       别人的请求跟着一起死。

    **本用例有两条断言，缺一不可**：
      · `peak <= 上限` —— 上限**拦住了**
      · `peak >= 2`     —— **确实发生过并发**（否则一个"完全串行"的实现也能过第一条，
                            那测的就不是"限流"而是"串行"了）
    """
    import threading
    import time

    live = 0
    peak = 0
    lock = threading.Lock()

    def fake_run(code):
        """替身 = **一个慢活儿**。计数与 semaphore 都是真的，只有"跑代码"这一步是假的。"""
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.25)
        with lock:
            live -= 1
        return True, "ok"

    monkeypatch.setattr(E, "run_in_sandbox_subprocess", fake_run)
    # 上限在 **import 期**就建好了 semaphore ⇒ 改常量没用，得把 semaphore 也换掉
    monkeypatch.setattr(E, "_EXEC_SEMAPHORE", threading.Semaphore(2))

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: E.execute(E.ExecuteRequest(code="x")), range(8)))

    assert all(r == {"ok": True, "out": "ok"} for r in results), "有请求没被正常处理"
    assert peak <= 2, f"🔴 并发到过 {peak} —— 上限没拦住"
    assert peak >= 2, f"并发峰值只有 {peak} —— 根本没并发起来，这条测不到限流"
