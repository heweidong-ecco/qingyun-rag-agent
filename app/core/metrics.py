from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

# 请求总数（按接口和状态码分类）
REQUEST_COUNT = Counter(
    "api_requests_total",
    "Total API requests",
    ["endpoint", "method", "status"]
)

# 请求延迟分布
REQUEST_LATENCY = Histogram(
    "api_request_duration_seconds",
    "API request duration in seconds",
    ["endpoint", "method"]
)

# 当前活跃请求数
REQUEST_IN_PROGRESS = Gauge(
    "api_requests_in_progress",
    "Number of requests currently in progress"
)

# 流式响应被客户端中途断开的次数（· 硬门 C）。
# **为什么需要它**：上面前三个指标数的是**请求**（HTTP 层），
#    而"上游到底停没停"是**另一件事**；本仓的 token 记账走 `token_tracker` → PG，
#    而那条链**在流式生成阶段一行都不写** ⇒ 没有这个计数器，取消**没有任何可观测对象**
STREAM_CANCELLED = Counter(
    "stream_cancelled_total",
    "流式响应被客户端中断、服务端已停止生成并关闭上游流的次数",
    ["endpoint"],
)


def track_stream_cancel(endpoint: str):
    """记一次「流式被取消」。幂等由调用方保证 —— 每条流**最多记一次**。"""
    STREAM_CANCELLED.labels(endpoint=endpoint).inc()


def track_request(endpoint: str, method: str, status: int, duration: float):
    """记录一次请求的指标"""
    REQUEST_COUNT.labels(endpoint=endpoint, method=method, status=str(status)).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint, method=method).observe(duration)


def get_metrics():
    """返回Prometheus格式的指标数据"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )