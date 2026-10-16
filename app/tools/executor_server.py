"""代码执行器服务 —— 跑在**独立执行器容器**里的极小 HTTP 服务。

## 它为什么存在

作者 2026-10-07 原话：

> 「`execute_python` 这个代码执行器要在 **docker 容器中执行**，使用 docker 容器**热启动**的方式，
> **预先加载**，`execute_python` 直接进容器中执行，**不要暴露在系统中执行，是安全事故**」

⇒ 现状（**宿主同权限的子进程 + 白名单**）不够；要一层**容器**。
⇒ 但**应用不能自己 `docker run`** —— 那需要挂 `/var/run/docker.sock`，
   而 **`docker.sock` = 宿主 root 等价**。**本应用自己就在容器里**
   把 sock 挂给它 = 把宿主 root 交给它 ⇒ **把"代码执行逃逸"的洞换成更大的"容器逃逸"洞**。

⇒ 所以：**另起一个容器跑本服务**，应用**只发 HTTP**，永远不认识 Docker。
   本容器由 `docker-compose.yml` 的 `executor` 服务定义（硬化配置见那里）。

## 本文件的两条不变量

1. **执行逻辑一行都不在这里** —— 全部复用 `code_executor_impl.run_in_sandbox_subprocess`。
   **不许在这里抄第二份白名单 / 超时文案**。
2. **每次请求起一个【新】子进程** —— 那是 `run_in_sandbox_subprocess` 的性质。
   **不许改成常驻解释器**（`exec` 的 `globals` 会跨请求活着 ⇒ 访客互相污染）。
   守卫：`app/tests/test_executor_server.py::test_execute_does_not_leak_state_between_requests`。

## 边界（知道再改）

- **不做鉴权** —— 它**只在 compose 的内部网络里**被 `api` 调，**不映射端口到宿主**。
  改这个前提（比如给它开 `ports:`）⇒ **先加鉴权**。
- **不暴露 `/docs`**（下面 `docs_url=None` 等）—— 内部机制，不需要给人看的接口文档，
  少一个面就少一个面。
- **真正的隔离在容器那一层**（只读根 · 无网 · `cap_drop` · 非 root · mem/pids 限制），
  **不是在本文件里** —— 本文件只是那个容器的入口。
"""

import os
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tools.code_executor_impl import run_in_sandbox_subprocess

app = FastAPI(
    title="code executor",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# 2026-10-08：**同时最多跑几段代码**。
#    为什么必须限：执行器容器只有 **0.5 CPU / 256 MB**，且 **`Swap=0`**。
#    没有上限 ⇒ 一个访客连打 50 个请求 ⇒ 50 个 Python 子进程同时起来
#    ⇒ **内存打爆 ⇒ 直接 OOM kill ⇒ 整个执行器没了**，别人的请求跟着一起死。
#
# **上限放在【执行器侧】而不是只放应用侧**：应用侧限的是"**自己**发多少"，
#    而执行器是**收口点** —— 谁来调都从这儿过。两侧都放，**这一侧是硬上限**。
#
# **默认 2 是【实测定的】，不是拍的**（2026-10-08，宿主 4 核 / 本容器 `cpus: 0.5`）：
#    `range(20000000)` 单体耗时 **1.38s**；
#    cap=4 ⇒ 4 个并发各拿 0.125 核 ⇒ ≈5.5s ⇒ **撞上 `MAX_EXEC_TIME` 的 5 秒硬杀** ——
#    用户会看到"超时"，而那**是假的**（活儿本来 1.5 秒就能完）。
#    cap=2 ⇒ 各拿 0.25 核 ⇒ ≈2.8s ⇒ 仍在承诺内。
#    ⇒ **本常量与 `docker-compose.yml` 的 `cpus:` 是一对，别单独调。**
MAX_CONCURRENT_EXECUTIONS = int(os.getenv("EXECUTOR_MAX_CONCURRENCY", "2"))

_EXEC_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_EXECUTIONS)

# **排队最多等多久**（秒）—— 别改成"无限等"。
#    理由：/execute 是**同步端点**，占的是 Starlette 线程池的线程（默认 40）。
#    无限等 ⇒ 40 个线程全被占住 ⇒ **连 `/health` 都拿不到线程**
#    容器的 healthcheck 超时 ⇒ **Docker 把执行器判成不健康并重启它**。
#    ⇒ 到点**如实拒掉**（503），不是把线程池填满。
EXEC_QUEUE_TIMEOUT = float(os.getenv("EXECUTOR_QUEUE_TIMEOUT", "10"))


class ExecuteRequest(BaseModel):
    """请求体。`code` **必填** —— 缺了由 FASTAPI 给 4xx，不静默当空串去执行。"""

    code: str


@app.post("/execute")
def execute(req: ExecuteRequest) -> dict:
    """在**一个新子进程**里跑 `req.code`，把结果原样报回。

    返回 `{"ok": bool, "out": str}` —— **形状与 `_SANDBOX_CHILD` 的 payload 同形**，
    这样应用侧（`code_executor_impl` 的远端路径）能**复用同一套解析**。

    **被执行的代码抛异常 ⇒ `ok=False`，但 HTTP 仍是 200** ——
       那个异常是**业务结果**（"你给的代码错了"），不是"服务坏了"。
       服务坏了（比如子进程起不来）也走 `ok=False`，因为对调用方而言**处置一样**。

    **排队超时 ⇒ `503`**（不是 200）—— 形状**故意不同**：
       "执行器忙不过来"和"你的代码错了"是**两件事**，调用方该能分开。
       守卫：`test_executor_server.py::test_concurrent_executions_are_capped`
    """
    if not _EXEC_SEMAPHORE.acquire(timeout=EXEC_QUEUE_TIMEOUT):
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "out": (
                    f"执行器忙：排队超过 {EXEC_QUEUE_TIMEOUT:g} 秒仍未轮到"
                    f"（同时在跑的上限 = {MAX_CONCURRENT_EXECUTIONS}）。请稍后重试。"
                ),
            },
        )
    try:
        ok, out = run_in_sandbox_subprocess(req.code)
    finally:
        _EXEC_SEMAPHORE.release()
    return {"ok": ok, "out": out}


@app.get("/health")
def health() -> dict:
    """容器 healthcheck 用。**不返回任何环境变量 / 凭据**（本仓红线）。"""
    return {"status": "ok"}
