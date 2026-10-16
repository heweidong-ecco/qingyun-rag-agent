"""代码执行器服务 —— 跑在**独立执行器容器**里的极小 HTTP 服务。
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

#    为什么必须限：执行器容器只有 **0.5 CPU / 256 MB**，且 **`Swap=0`**。
#    没有上限 ⇒ 一个访客连打 50 个请求 ⇒ 50 个 Python 子进程同时起来
#    ⇒ **内存打爆 ⇒ 直接 OOM kill ⇒ 整个执行器没了**，别人的请求跟着一起死。
MAX_CONCURRENT_EXECUTIONS = int(os.getenv("EXECUTOR_MAX_CONCURRENCY", "2"))

_EXEC_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_EXECUTIONS)

# **排队最多等多久**（秒）—— 别改成"无限等"。
#    理由：/execute 是**同步端点**，占的是 Starlette 线程池的线程（默认 40）。
#    无限等 ⇒ 40 个线程全被占住 ⇒ **连 `/health` 都拿不到线程**
#    容器的 healthcheck 超时 ⇒ **Docker 把执行器判成不健康并重启它**。
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
