"""错误响应的契约（对应 B12）。

为什么要有这个文件：本仓**有一条文案是说反的** ——
   `app/main.py` 全局限流触发时返回 429，`error` 字段却写着 "Internal server error"。
   那正是 `通用方法 §7.1` 的 R3.3 要防的：「熔断时静默/白屏/500 → 对方以为你的系统坏了」。
"""
from core.exceptions import AppException, ErrorCode


def test_app_exception_carries_retry_after():
    """`retry_after` 要能挂到异常上 —— 否则处理器没法把它写进响应。"""
    exc = AppException(ErrorCode.RATE_LIMITED, "请求过于频繁", retry_after=30)
    assert exc.retry_after == 30


def test_app_exception_retry_after_defaults_to_none():
    """不给就是 None —— 不是 0（0 会被读成'立刻可重试'）。"""
    exc = AppException(ErrorCode.INTERNAL_ERROR, "boom")
    assert exc.retry_after is None


# ==================== 处理器把 retry_after 写进响应 ====================

def _handler_response(exc):
    """直接调全局异常处理器 —— 不必起服务、不必造路由。"""
    import asyncio
    import json
    import main
    resp = asyncio.run(main.app_exception_handler(None, exc))
    return resp, json.loads(resp.body)


def test_handler_writes_retry_after_only_when_given():
    """挂了 `retry_after` ⇒ 响应体带字段 + 响应头 `Retry-After`。

    没挂的（如 500）**不许**出现这个字段 —— 0 会被读成"立刻可试"。
    """
    resp, body = _handler_response(
        AppException(ErrorCode.RATE_LIMITED, "请求过于频繁", retry_after=42)
    )
    assert body["retry_after"] == 42
    assert resp.headers["retry-after"] == "42"

    resp2, body2 = _handler_response(AppException(ErrorCode.INTERNAL_ERROR, "boom"))
    assert "retry_after" not in body2
    assert "retry-after" not in {k.lower() for k in resp2.headers}


# ==================== 那 4 处文案 ====================

from fastapi.testclient import TestClient


def _client():
    # 惰性导入：`main` 导入期会建 Gradio Blocks（见 app/conftest.py 的注释）
    from main import app
    return TestClient(app)


def test_rate_limited_response_is_not_internal_server_error():
    """本条钉的是那条【说反了的文案】（`app/main.py` 全局限流分支）。

    改前：`{"error": "Internal server error", "code": "RATE_LIMITED", "status_code": 429}`
    改后：文案必须说清"是限流"，不许出现 "Internal server error"。
    """
    from main import _rate_limited_payload      # 见 Step 7 抽出的纯函数
    body = _rate_limited_payload()
    assert body["code"] == "RATE_LIMITED"
    # 🔴：**全局限流**那条出口必须自报家门 —— 「全站过载」与「你自己太快」
    #    的处置**相反**，而它们原来共用同一个 `code` ⇒ 调用方分不出。
    assert body["layer"] == "global", (
        f"这个纯函数只服务【全局限流】那一条出口 ⇒ `layer` 必须是 `global`：{body!r}"
    )
    assert "Internal server error" not in body["error"], (
        f"限流却写着内部错误 —— 正是 R3.3 要防的：{body['error']!r}"
    )
    assert "频繁" in body["error"] or "限流" in body["error"], (
        f"文案要说清是限流，不是故障：{body['error']!r}"
    )


def test_service_unavailable_copy_is_not_internal_error():
    """503 的三处也必须说清"未就绪" —— 它们与 500 不同。

    判据是**payload 那一行的形态**（`"error": "…"`），不是"源码里出现过这个词" ——
       本仓栽过：**注释里也有同样的串**（`开发规范 §3.1`「批量替换后按位置核」）。
       若按后者写，修注释的人会让这条测试红，而真正的 payload 早就改对了。
    """
    import inspect
    import main
    src = inspect.getsource(main)
    bad = '"error": "Internal server error"'
    # 允许 500 处理器保留那句英文，但 503 的 payload 里不许再有
    unavailable_blocks = [b for b in src.split("status_code=503") if bad in b[:400]]
    assert not unavailable_blocks, (
        f"有 503 响应体还在用 {bad} —— 那是'未就绪'，不是'内部错误'"
    )
