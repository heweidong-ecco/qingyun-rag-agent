"""WebSocket 首帧认证 —— 「**要用就做完整**」的那条链。

## 为什么有本文件

`/api/v1/ws/agent` **整条没有鉴权**（遗留·1）：它**真花钱**（跑 LangChain Agent、
调 chat LLM、还能 `web_search`），却是全站唯一一个**匿名可达、又真的花钱**的入口。
 只是把它**从看不见变成看得见**（补进 `scripts/check_route_auth.py` 的扫描口径），
没有修它 —— 那是"把灯打开"，不是"把门装上"。

## 浏览器为什么不能照抄 HTTP 那套

`new WebSocket(url)` **不能自定义请求头** ⇒ `X-API-Key` / `Authorization` 两条路都走不通
（照抄 HTTP 是本仓最容易踩的形态：**看着像接了鉴权，其实客户端根本送不上来**）。

## 三层判据（别混，缺一层就挡不住"看起来像有门"）

| 层 | 钉什么 | 要 DB 吗 | 进 CI 吗 |
|---|---|---|---|
| ① 纯函数 | 首帧怎么解析成身份、**哪种失败给哪个关闭码** | | |
| ② 依赖 | `require_ws_user` 的**顺序**（先 `accept` 才让浏览器看得到 1008）、超时 | | |
| ③ 端点 | 真 `app` 上**两条 WS 路由都真的挂了这道依赖**，且**认证前端点体一次都不跑** | （打桩验证器） | |

**③ 是重点**：①② 全绿而端点没接线，**本仓有前科**
（本仓的复盘记录（⛔ 不随产品仓提供）：「**门挂在别处，就等于没有门**」）。

**本仓没装 `pytest-asyncio`** ⇒ 协程一律用下面那个 `_run` 跑，
别写 `@pytest.mark.asyncio`（它不会失败，它会让用例**看起来没跑**）。

## 判据（可打印）

```bash
venv/bin/python -m pytest app/tests/test_ws_auth.py -q -p no:warnings
venv/bin/python scripts/check_route_auth.py        # ⇒ 无鉴权路由 1 条（只剩 /api/v1）
```
"""
import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect


def _run(coro):
    """跑一个协程 —— 无 `pytest-asyncio`，见模块 docstring。"""
    return asyncio.run(coro)


# ===========================================================================
# 打桩用的"假凭据验证器" —— 真验证器要连库，而 CI 没有库
# ===========================================================================

class FakeVerifier:
    """记下"被喂了什么"，并按要求返回 user_name / None / 抛异常。"""

    def __init__(self, result=None, boom=False):
        self.result = result
        self.boom = boom
        self.seen = []

    def __call__(self, credential):
        self.seen.append(credential)
        if self.boom:
            raise RuntimeError("库连不上（打桩）")
        return self.result


@pytest.fixture
def deps_mod(monkeypatch):
    """拿真的 `deps` 模块 —— 但**两个验证器一律换成打桩**（CI 无 DB）。"""
    import routing.deps as deps

    monkeypatch.setattr(deps, "verify_key", FakeVerifier(result=None))
    monkeypatch.setattr(deps, "verify_access_token", FakeVerifier(result=None))
    return deps


def _auth_frame(key="sk-good"):
    return json.dumps({"type": "auth", "api_key": key})


# ===========================================================================
# ① 纯函数层 —— `resolve_ws_identity`
# ===========================================================================

def test_valid_api_key_resolves_to_user_name(deps_mod):
    deps_mod.verify_key = FakeVerifier(result="alice")
    user, code, _ = deps_mod.resolve_ws_identity({"type": "auth", "api_key": "sk-good"})
    assert user == "alice"
    assert code == 0, "认证通过时不带关闭码"


def test_valid_jwt_resolves_to_user_name(deps_mod):
    """JWT 也要收 —— 与 HTTP 那边的 `get_current_user_hybrid` 同一口径。"""
    deps_mod.verify_access_token = FakeVerifier(result="bob")
    user, code, _ = deps_mod.resolve_ws_identity({"type": "auth", "token": "eyJhbGciOi..."})
    assert user == "bob"
    assert code == 0


def test_whitespace_is_stripped_before_verifying(deps_mod):
    """复制粘贴常带首尾空白 —— 剥掉再验，别拿带空白的串去算哈希（那样必然验不过）。"""
    v = FakeVerifier(result="alice")
    deps_mod.verify_key = v
    deps_mod.resolve_ws_identity({"type": "auth", "api_key": "  sk-good\n"})
    assert v.seen == ["sk-good"]


@pytest.mark.parametrize("payload", [
    {"api_key": "sk-good"},                        # 没有 type
    {"type": "question", "api_key": "sk-good"},    # 业务帧，不许当认证用
    {"type": "auth"},                              # 有 type，却一个凭据都没带
    {"type": "auth", "api_key": ""},               # 空串
    {"type": "auth", "api_key": "   "},            # 空白
    {"type": "auth", "token": None},               # 不是字符串
    "不是对象",                                      # 顶层不是 dict
    None,
    ["type", "auth"],
])
def test_malformed_or_incomplete_first_frame_is_rejected(deps_mod, payload):
    user, code, reason = deps_mod.resolve_ws_identity(payload)
    assert user is None
    assert code == 1008, f"凭据类失败必须是 1008（策略违规），得到 {code}"
    assert reason, "关闭帧要带原因 —— 前端只有这一条线索"


def test_invalid_api_key_is_rejected_with_1008(deps_mod):
    deps_mod.verify_key = FakeVerifier(result=None)
    user, code, _ = deps_mod.resolve_ws_identity({"type": "auth", "api_key": "sk-wrong"})
    assert (user, code) == (None, 1008)


def test_invalid_jwt_is_rejected_with_1008(deps_mod):
    deps_mod.verify_access_token = FakeVerifier(result=None)
    user, code, _ = deps_mod.resolve_ws_identity({"type": "auth", "token": "bad.jwt"})
    assert (user, code) == (None, 1008)


def test_verifier_crash_is_1011_not_1008(deps_mod):
    """**库挂了 ≠ 你的 key 错了。**

    两种失败对客户端是**完全不同**的处置：前者该重试，后者该换 key。
    都报 1008 是**对客户端说谎**，还会把一次 DB 抖动放大成"全网用户以为凭据失效"。

    `deps.py` 的鉴权是 **fail-closed**（`get_session_token_usage` 那边 fail-open，**故意相反**）
    ⇒ 照样挡住，但要说清是**我们这边**不行。
    """
    deps_mod.verify_key = FakeVerifier(boom=True)
    user, code, reason = deps_mod.resolve_ws_identity({"type": "auth", "api_key": "sk-good"})
    assert user is None
    assert code == 1011, f"认证服务自身不可用 ⇒ 1011（内部错误），得到 {code}"
    assert reason


# ===========================================================================
# ② 依赖层 —— `require_ws_user`
# ===========================================================================

class FakeWebSocket:
    """只实现依赖真正用到的那几个方法；**记下调用顺序**。"""

    def __init__(self, first_frame="__silent__", silent_forever=False):
        self.first_frame = first_frame
        self.silent_forever = silent_forever
        self.calls = []
        self.sent = []

    async def accept(self, *a, **kw):
        self.calls.append("accept")

    async def receive_text(self):
        self.calls.append("receive_text")
        if self.silent_forever:
            await asyncio.sleep(30)
        if isinstance(self.first_frame, Exception):
            raise self.first_frame
        return self.first_frame

    async def send_text(self, text):
        self.calls.append("send_text")
        self.sent.append(json.loads(text))


def test_dependency_accepts_before_reading_the_frame(deps_mod):
    """顺序不能反。

    `accept` **之前**关连接 ⇒ 浏览器侧只看到**握手失败**，**拿不到 1008**
    （星型 1.6.0 实测）⇒ 前端无法区分"key 错了"和"网线掉了"。
    """
    from fastapi import WebSocketException

    ws = FakeWebSocket(first_frame=_auth_frame("sk-wrong"))
    with pytest.raises(WebSocketException):
        _run(deps_mod.require_ws_user(ws))
    assert ws.calls[0] == "accept", f"必须先 accept 再读首帧，实际顺序 {ws.calls}"


def test_dependency_raises_1008_on_bad_key(deps_mod):
    from fastapi import WebSocketException

    ws = FakeWebSocket(first_frame=_auth_frame("sk-wrong"))
    with pytest.raises(WebSocketException) as ei:
        _run(deps_mod.require_ws_user(ws))
    assert ei.value.code == 1008
    assert ei.value.reason


def test_dependency_raises_1011_on_verifier_crash(deps_mod):
    from fastapi import WebSocketException

    deps_mod.verify_key = FakeVerifier(boom=True)
    ws = FakeWebSocket(first_frame=_auth_frame("sk-good"))
    with pytest.raises(WebSocketException) as ei:
        _run(deps_mod.require_ws_user(ws))
    assert ei.value.code == 1011


def test_dependency_sends_ready_frame_and_returns_the_user(deps_mod):
    """认证通过 ⇒ 先回一帧 `ready`；前端据此才发第一个问题（否则是竞态）。"""
    deps_mod.verify_key = FakeVerifier(result="alice")
    ws = FakeWebSocket(first_frame=_auth_frame("sk-good"))
    user = _run(deps_mod.require_ws_user(ws))
    assert user == "alice"
    assert ws.sent == [{"type": "ready", "user": "alice"}]


def test_dependency_times_out_when_no_frame_arrives(deps_mod, monkeypatch):
    """没有超时 ⇒ 一个**匿名的空连接**就能永久占住一个协程
    （而且它会**绕过**"非认证不得处理"这条 —— 它压根没进入处理流程，只是挂着）。"""
    from fastapi import WebSocketException

    monkeypatch.setattr(deps_mod, "WS_AUTH_TIMEOUT_SECONDS", 0.05)
    ws = FakeWebSocket(silent_forever=True)
    with pytest.raises(WebSocketException) as ei:
        _run(deps_mod.require_ws_user(ws))
    assert ei.value.code == 1008
    assert "超时" in ei.value.reason


def test_dependency_propagates_client_disconnect(deps_mod):
    """客户端连上就跑了 ⇒ 原样抛 `WebSocketDisconnect`，别包装成"认证失败"。

    包装了会有两个坏处：① 日志里全是假告警；② socket 已经没了，再 close 是多余动作。
    """
    ws = FakeWebSocket(first_frame=WebSocketDisconnect(code=1006))
    with pytest.raises(WebSocketDisconnect):
        _run(deps_mod.require_ws_user(ws))


# ===========================================================================
# ③ 端点层 —— 真 `app`，不启服务、不连库（裸 TestClient）
# ===========================================================================

def _client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)          # 不用 with —— 那会跑 lifespan，


def test_ws_agent_rejects_a_connection_that_never_authenticates(deps_mod, monkeypatch):
    """端点**不能再等用户先发问题** —— 首帧必须是认证帧，否则连不上。"""
    monkeypatch.setattr(deps_mod, "WS_AUTH_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(WebSocketDisconnect) as ei:
        with _client().websocket_connect("/api/v1/ws/agent") as ws:
            ws.receive_text()
    assert ei.value.code == 1008


def test_ws_agent_rejects_a_bad_key_with_1008(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "verify_key", FakeVerifier(result=None))
    with pytest.raises(WebSocketDisconnect) as ei:
        with _client().websocket_connect("/api/v1/ws/agent") as ws:
            ws.send_text(_auth_frame("sk-wrong"))
            ws.receive_text()
    assert ei.value.code == 1008


def test_ws_agent_body_never_runs_before_auth(deps_mod, monkeypatch):
    """这条才是"门装上了"的判据 —— 不是"连不上"，是**端点体一次都没进**。

    只断言"连接被关"不够：端点**先跑一半再关**同样会绿 ——
       而 `/ws/agent` 的端点体里就有 `get_agent_executor`（**构造 LLM，真花钱**）。
    """
    import routing.api_v1_rag as api_v1_rag

    called = []

    def _spy(*a, **kw):
        called.append("get_agent_executor")
        raise AssertionError("认证没过就构造了 Agent")

    monkeypatch.setattr(api_v1_rag, "get_agent_executor", _spy)
    with pytest.raises(WebSocketDisconnect):
        with _client().websocket_connect("/api/v1/ws/agent") as ws:
            ws.send_text(_auth_frame("sk-wrong"))
            ws.receive_text()
    assert called == [], "认证未通过，端点体却跑了"


def test_the_ws_route_carries_the_auth_dependency():
    """结构守卫：**路由表里**那条 WS 必须挂着 `require_ws_user`。

    它与 `scripts/check_route_auth.py` **不是重复**：那个脚本靠 `AUTH_NAMES` 名单认人
    （名单漂了它就静默失效），这条直接钉**具体那一条依赖**。

    **2026-10-05 起是 1 条不是 2 条** —— `/ws/test` 已删（§十）。
    **别在这里加回 `/ws/test`** —— 那条路径的守卫在
    `app/tests/test_removed_endpoints.py::test_ws_test_stays_removed`（它的判据是**不存在**，
    与这条的判据「存在且挂着鉴权依赖」**恰好相反**，别合并）。
    """
    from main import app

    found = {}
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            for sub in getattr(r.original_router, "routes", []) or []:
                if type(sub).__name__ == "APIWebSocketRoute":
                    found[sub.path] = sub
        elif type(r).__name__ == "APIWebSocketRoute":
            found[r.path] = r

    assert set(found) == {"/api/v1/ws/agent"}, (
        f"WS 路由集合变了：{sorted(found)} —— 新增的请一并在此登记，"
        f"已删的（如 `/ws/test`）请改用 test_removed_endpoints.py 那条守卫"
    )
    deps = found["/api/v1/ws/agent"].dependant.dependencies or []
    names = {getattr(d.call, "__name__", str(d.call)) for d in deps}
    assert "require_ws_user" in names, (
        f"/api/v1/ws/agent 没有挂 require_ws_user —— 它又变成匿名可达了（deps={names}）"
    )


# ===========================================================================
# ④ 记账 —— 「装了闸却没人写计数器」= 闸恒不触发（§六 备选 B）
# ===========================================================================

class _FakeLLMResult:
    """够 `on_llm_end` 用的最小 `LLMResult` 替身。"""

    def __init__(self, message):
        self.generations = [[type("G", (), {"message": message})()]]


def test_ws_agent_bills_llm_usage_under_the_authenticated_user(deps_mod, monkeypatch):
    """端到端把「身份」和「落账」串起来：**认成谁，就记在谁头上**。

    为什么这**不算"顺手多做"**：`/ws/agent` 上**已经挂着** `check_session_token_budget`，
    而它的数据源是 `token_usage_logs` 里 `(user_name, thread_id)` 的**今日累计**。
    那条链**从来没人写过账** ⇒ 它每次都读到 0 ⇒ **闸永远放行**。
    ⇒ 不补记账，前面那道预算闸就是**摆设**（§六 备选 B 明文否掉的形态）。
    """
    import routing.api_v1_rag as api_v1_rag
    import billing.token_tracker as token_tracker

    recorded = []
    monkeypatch.setattr(token_tracker, "record_usage", lambda **kw: recorded.append(kw))
    monkeypatch.setattr(deps_mod, "verify_key", FakeVerifier(result="alice"))

    class FakeLLM:
        model_name = "qwen-turbo"

    class FakeExecutor:
        async def ainvoke(self, inputs, config=None):
            # 不吃真 LLM，只把端点交下来的回调喂一个**带 usage_metadata 的响应**
            # （判据必须是 `usage_metadata` —— ：`.usage` 恒为假）
            from langchain_core.messages import AIMessage

            msg = AIMessage(
                content="答案是 42",
                usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            )
            for cb in (config or {}).get("callbacks", []):
                await cb.on_llm_end(_FakeLLMResult(msg))
            return {"output": "答案是 42"}

    monkeypatch.setattr(api_v1_rag, "get_agent_executor", lambda: FakeExecutor())
    monkeypatch.setattr(api_v1_rag, "get_agent_llm", lambda: FakeLLM())

    with _client().websocket_connect("/api/v1/ws/agent") as ws:
        ws.send_text(_auth_frame("sk-good"))
        assert json.loads(ws.receive_text()) == {"type": "ready", "user": "alice"}
        ws.send_text(json.dumps({"message": "6*7 等于几"}))
        for _ in range(10):                       # 上界：防止协议写错时死循环
            if json.loads(ws.receive_text())["type"] == "done":
                break
        else:
            raise AssertionError("没等到 done 帧 —— 端点的帧序列变了")

    assert recorded, "这一轮 LLM 调用**一分钱没记** —— 预算闸读的那张表永远是 0"
    row = recorded[0]
    assert row["user_name"] == "alice", f"记到了 {row['user_name']!r} 头上（旧实现写死 'unknown'）"
    assert row["thread_id"], "thread_id 不能空 —— 它与 user_name 一起构成预算桶"
    assert (row["prompt_tokens"], row["completion_tokens"]) == (11, 7)
    assert row["model"] == "qwen-turbo", "模型名必须从对象取，⛔ 不许写死（前科）"


def test_callback_skips_recording_when_the_response_has_no_usage(monkeypatch):
    """不许"估一个数记上去" —— 账本里写假数比漏记更坏（**无法与真数区分**）。"""
    import billing.token_tracker as token_tracker
    from langchain_core.messages import AIMessage
    from routing.websocket_callback import WebSocketAgentCallback

    recorded = []
    monkeypatch.setattr(token_tracker, "record_usage", lambda **kw: recorded.append(kw))

    class FakeLLM:
        model_name = "qwen-turbo"

    class FakeWS:
        async def send_text(self, *_):
            pass

    cb = WebSocketAgentCallback(FakeWS(), user_name="alice", thread_id="ws-1", llm=FakeLLM())
    _run(cb.on_llm_end(_FakeLLMResult(AIMessage(content="没有用量字段"))))
    assert recorded == []


def test_callback_requires_identity_at_construction():
    """不给默认值：漏传 = `TypeError`（**响亮**），而不是静默记成 `"unknown"`（**假记账**）。

    与  §四 对 `query_rewriter` 的处理**同一条取向**。
    """
    from routing.websocket_callback import WebSocketAgentCallback

    class FakeWS:
        async def send_text(self, *_):
            pass

    with pytest.raises(TypeError):
        WebSocketAgentCallback(FakeWS())
