"""压测场景（`T5-8`）—— **给别人看的证据**，不是内部调优。

## 口径（需求方 2026-10-07 裁定，别自作主张改）

> 「**locust 要装，压测本机 docker 那套，但是放在最后不要进 pytest，单独做**」
> 「**做之前必须先核实代码参数，不要太大，核实本机状态，调整参数**」
> 「用途：**放在 GitHub 仓库给来仓库看代码和内容的人看的，固定环境下的压测负载数据**」

⇒ 三条硬约束：
1. **不进 pytest**（`app/test_*.py` 一条都不许引它）；
2. **不写进 `app/requirements.txt`** —— 那份清单会进 **本服务镜像**，塞个压测工具等于给交付物增肥
   （直接 `./venv/bin/pip install locust` 或另开 venv）；
3. **别压太大** —— 这台机器 **8 GB 内存，Docker 只分到 3.84 GB**。

## 两个档：**免费**与**付费**

| 档 | 端点 | 开关 | 说明 |
|---|---|---|---|
| **免费（默认）** | `/health` · `/agent/tool_versions` · `/agent/available_tools` · `/agent/budget/estimates` | 默认开 | 不调 LLM/RAG ⇒ **零花费、可反复跑** |
| **付费（opt-in）** | `POST /rag/search` | `BENCH_PAID=1` | **真调 embedding（DashScope）+ 真打库** ⇒ **每次请求都花钱** |

**付费档默认【关】** —— 本仓有 API key 用量看板，别让一次压测把额度打光。
   要跑就**明确设 `BENCH_PAID=1`**，而且**并发压小**（见 README 的建议档位）。

## 怎么跑

```bash
export RAG_API_KEY=...      # 绝不写进本文件 / 绝不进仓（凭据在仓外受管地）
export RAG_BASE_URL=http://localhost:8000
locust -f locust/locustfile_bench.py --headless -u 10 -r 2 -t 60s --only-summary
```
详见 `locust/README.md`。
"""
import os
import sys

from locust import HttpUser, between, task

BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("RAG_API_KEY", "").strip()
PAID = os.getenv("BENCH_PAID", "").strip() == "1"

#: 鉴权头名 —— **必须是 `X-API-Key`**（见 `app/main.py`）。
#: 魔搭创空间那边**平台会往每个请求注入 `Authorization`**，所以本仓**不用** `Authorization`；
#:    本机 docker 那套与它保持一致，别在这里换成 Bearer。
AUTH_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

#: 付费档的查询（别改成长文本 —— 长度直接换算成 embedding 的钱）
PAID_QUERY = {"query": "压测探针", "top_k": 3}


class BenchUser(HttpUser):
    """一个虚拟用户。`wait_time` 决定**单人节奏**，并发数由 `-u` 给 —— 两件事别混。"""

    host = BASE_URL
    wait_time = between(0.5, 1.5)

    def on_start(self):
        """开跑前**先验一次鉴权** —— 别让"全是 401"混进结果里当成性能问题。

        旧基线就是这么废掉的：那三份 2026-06-25 的数据**错误率 77% / 96% / 97%**，
           它们的 QPS 与延迟**没有意义**（见 `archive/性能基线报告模板-旧-20260625.txt`）。
        """
        r = self.client.get("/api/v1/agent/tool_versions", headers=AUTH_HEADERS, name="/agent/tool_versions")
        if r.status_code == 401:
            print(
                "🔴 401 —— `RAG_API_KEY` 没设对。压测前先解决鉴权，"
                "⛔ 否则跑出来的是一堆 401 的延迟数据（本仓 2026-06 那三份旧基线就是这么废的）。",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # ---------- 免费档（默认开）----------
    @task(5)
    def health(self):
        self.client.get("/health", name="/health")

    @task(3)
    def tool_versions(self):
        self.client.get("/api/v1/agent/tool_versions", headers=AUTH_HEADERS,
                        name="/agent/tool_versions")

    @task(3)
    def available_tools(self):
        self.client.get("/api/v1/agent/available_tools", headers=AUTH_HEADERS,
                        name="/agent/available_tools")

    @task(2)
    def budget_estimates(self):
        self.client.get("/api/v1/agent/budget/estimates", headers=AUTH_HEADERS,
                        name="/agent/budget/estimates")

    # ---------- 付费档（opt-in · 默认关）----------
    if PAID:
        @task(1)
        def rag_search(self):
            """**每次请求都花钱**（真 embedding + 真打库）。只在 `BENCH_PAID=1` 时注册。"""
            self.client.post("/api/v1/rag/search", json=PAID_QUERY, headers=AUTH_HEADERS,
                             name="/rag/search (付费)")
