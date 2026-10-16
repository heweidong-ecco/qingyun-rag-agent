"""按 IP 限流（§二·3 的 **L1「per-IP 短窗」** —— 原先**从没实现过**）。

## 它修的是什么

改前：**所有匿名访客共用【一个】桶**（`"anonymous"`，3/秒 · 容量 20）
⇒ **一个人刷就能把别人的页面打成 429**（那次实测到的就是它）。
改后：匿名请求的桶按**来源 IP** 分。

## 一条必须钉死的安全性质

`X-Forwarded-For` **只有在真代理后面才可信**：
· 创空间实测（2026-10-11）**会丢掉客户端伪造的那一跳** ⇒ 第一跳是真的；
· 但在**本机 / 自建**部署下**没有代理** ⇒ 谁都能自己塞一个 `X-Forwarded-For`。
⇒ 代码**默认不信它**，要显式开 `TRUST_PROXY_HEADERS` 才读。
若默认就信，本仓那种直连部署会**比不做还糟** —— 填个头就能换桶 = 限流等于没有。

## 本文件不需要 Redis / DB
判据分两层：**纯函数**（多数）+ **一处真调中间件**（用假的 limiter 记下桶名）。
"""
import asyncio
import inspect


# ═══════════════════════════════════════════════════════════════════
# 一 · 纯函数：`resolve_client_ip`
# ═══════════════════════════════════════════════════════════════════

def test_does_not_trust_forwarded_header_by_default():
    """**本文件最重要的一条** —— `trust_proxy=False` 时**必须无视** `X-Forwarded-For`。

    反证：把实现改成"总是读 XFF" ⇒ **本条必须红**。
    不红的话，任何一个直连部署都能靠一个请求头换桶。
    """
    from main import resolve_client_ip

    got = resolve_client_ip(
        {"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
        "10.0.0.9",
        trust_proxy=False,
    )
    assert got == "10.0.0.9", f"没开开关却读了 XFF ⇒ 拿到了 {got}（伪造的 IP 生效了）"


def test_reads_first_hop_when_proxy_is_trusted():
    """开了开关 ⇒ 取 `X-Forwarded-For` 的**第一跳**（创空间实测：第一跳 = 真实客户端）。

    **为什么是第一跳而不是最后一跳**：实测平台是**覆盖/丢弃**客户端塞的那一跳，
    所以列表里最前面那个就是真客户端。`docs/说明/魔搭创空间-部署与平台约束.md` §4.2。
    """
    from main import resolve_client_ip

    got = resolve_client_ip(
        {"x-forwarded-for": "1.2.3.4, 5.6.7.8, 9.9.9.9"},
        "10.0.0.9",
        trust_proxy=True,
    )
    assert got == "1.2.3.4", f"没取第一跳：{got}"


def test_strips_whitespace_from_the_header():
    """`X-Forwarded-For` 里逗号后有空格是常态 —— 不 strip 的话会分出一堆 **不同的桶**。"""
    from main import resolve_client_ip

    got = resolve_client_ip(
        {"x-forwarded-for": "  1.2.3.4 ,5.6.7.8"},
        None,
        trust_proxy=True,
    )
    assert got == "1.2.3.4", f"没去掉首尾空白：{got!r}"


def test_falls_back_to_x_real_ip():
    """有的代理只给 `X-Real-IP`（实测创空间两个都给）⇒ 缺 XFF 时要用它。"""
    from main import resolve_client_ip

    got = resolve_client_ip({"x-real-ip": "1.2.3.4"}, "10.0.0.9", trust_proxy=True)
    assert got == "1.2.3.4", f"没回落 x-real-ip：{got}"


def test_falls_back_to_the_socket_peer_when_headers_are_absent():
    """两个头都没有 ⇒ 用 `request.client.host`（连接对端）。别返回 `None` 就完事。"""
    from main import resolve_client_ip

    assert resolve_client_ip({}, "10.0.0.9", trust_proxy=True) == "10.0.0.9"
    assert resolve_client_ip({}, "10.0.0.9", trust_proxy=False) == "10.0.0.9"


def test_returns_none_when_there_is_no_ip_at_all():
    """连对端都没有（合成的 scope）⇒ 返回 `None` ⇒ 调用方落回共用匿名桶。

    **防空：不许拿空串当 IP** —— `"ip:"` 会变成**所有这种情况共用的一个桶**，
    看着像"按 IP 分了"，其实又是一个全站桶。
    """
    from main import resolve_client_ip

    assert resolve_client_ip({}, None, trust_proxy=True) is None
    assert resolve_client_ip({}, None, trust_proxy=False) is None


def test_empty_header_values_fall_through():
    """`X-Forwarded-For: ""` 或一串逗号 ⇒ 当成**没有**，不许落出 `"ip:"` 这种桶名。"""
    from main import resolve_client_ip

    assert resolve_client_ip({"x-forwarded-for": ""}, "10.0.0.9", trust_proxy=True) == "10.0.0.9"
    assert resolve_client_ip({"x-forwarded-for": " , "}, "10.0.0.9", trust_proxy=True) == "10.0.0.9"


# ═══════════════════════════════════════════════════════════════════
# 二 · 桶名：`anonymous_rate_limit_bucket`
# ═══════════════════════════════════════════════════════════════════

def test_bucket_name_is_per_ip():
    from main import anonymous_rate_limit_bucket

    assert anonymous_rate_limit_bucket("1.2.3.4") == "ip:1.2.3.4"
    assert anonymous_rate_limit_bucket("5.6.7.8") != anonymous_rate_limit_bucket("1.2.3.4"), (
        "两个 IP 拿到同一个桶名 ⇒ 根本没按 IP 分"
    )


def test_bucket_name_falls_back_to_the_shared_anonymous_bucket():
    """没有 IP ⇒ 回落到**原来那个**共用桶（那条 fail-open 纪律：不拒绝、不编一个 IP）。"""
    from main import anonymous_rate_limit_bucket

    assert anonymous_rate_limit_bucket(None) == "anonymous"


# ═══════════════════════════════════════════════════════════════════
# 三 · 真调中间件：**桶名真的按 IP 走了吗**
# ═══════════════════════════════════════════════════════════════════
#
# 这一节才是"接线"判据 —— 上面那些纯函数全绿、而中间件忘了调它，
#    是完全可能的（本仓那类"写了但没接上"栽过多次）。
# **不连 Redis**：把两个 limiter 换成假的，只**记下**它们收到的桶名。

class _RecordingLimiter:
    """假的限流器：只记下 `is_allowed` / `get_limit_info` 收到的桶名。

    `get_limit_info` 必须回**真形状** —— 中间件在放行那条路上要读
    `info["limit"] / ["remaining"] / ["reset"]` 三个键来写响应头，
    回 `{}` 会当场 `KeyError`（那是**测试夹具不全**，不是产品代码的问题）。
    """

    def __init__(self):
        self.seen = []

    def is_allowed(self, key):
        self.seen.append(key)
        return True

    def get_limit_info(self, key):
        return {"limit": 20, "remaining": 19, "reset": 0}


def _request(*, headers=(), client=("1.2.3.4", 5000)):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        # 用一个**非豁免**路径 —— 豁免路径会在第一行就 return，桶名根本不产生
        "path": "/api/v1/agent/langgraph_chat",
        "raw_path": b"/api/v1/agent/langgraph_chat",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": client,
        "server": ("testserver", 80),
        "scheme": "http",
        "app": None,
    }
    return Request(scope)


async def _call_next(_request):
    from starlette.responses import Response

    return Response("ok")


def test_middleware_buckets_anonymous_requests_by_ip(monkeypatch):
    """**接线判据**：匿名请求的桶名必须是 `ip:<对端 IP>`，不再是那个共用桶。

    显式把开关设成关 —— 否则本机 `.env` 里若开了它，这条测的就不是同一件事了。
    """
    import main

    fake_global, fake_user = _RecordingLimiter(), _RecordingLimiter()
    monkeypatch.setattr(main, "global_limiter", fake_global)
    monkeypatch.setattr(main, "user_limiter", fake_user)
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", False)

    mw = main.RateLimitMiddleware(main.app)
    asyncio.run(mw.dispatch(_request(client=("1.2.3.4", 5000)), _call_next))

    assert fake_user.seen == ["ip:1.2.3.4"], (
        f"用户级限流收到的桶名不是按 IP 分的：{fake_user.seen}"
    )


def test_two_different_ips_get_two_different_buckets(monkeypatch):
    """**两个人不该共用一个桶** —— 这正是改前那个"一个人刷、所有人 429"的成因。"""
    import main

    monkeypatch.setattr(main, "global_limiter", _RecordingLimiter())
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", False)
    mw = main.RateLimitMiddleware(main.app)

    seen = []
    for ip in ("1.2.3.4", "5.6.7.8"):
        fake = _RecordingLimiter()
        monkeypatch.setattr(main, "user_limiter", fake)
        asyncio.run(mw.dispatch(_request(client=(ip, 5000)), _call_next))
        seen.append(fake.seen[0])

    assert seen[0] != seen[1], f"两个不同的 IP 落进了同一个桶：{seen}"


def test_middleware_reads_the_forwarded_header_only_when_enabled(monkeypatch):
    """**端到端的那条安全性质**：开关关着时，塞 `X-Forwarded-For` **换不到桶**。

    反证：把实现改成"总是读 XFF" ⇒ 本条必须红。
    """
    import main

    monkeypatch.setattr(main, "global_limiter", _RecordingLimiter())

    # ① 开关关（默认）⇒ 伪造的头**不该**生效
    fake_a = _RecordingLimiter()
    monkeypatch.setattr(main, "user_limiter", fake_a)
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", False)
    mw = main.RateLimitMiddleware(main.app)
    asyncio.run(mw.dispatch(
        _request(headers=[("X-Forwarded-For", "9.9.9.9")], client=("1.2.3.4", 5000)),
        _call_next,
    ))
    assert fake_a.seen == ["ip:1.2.3.4"], f"开关关着，伪造的头却生效了：{fake_a.seen}"

    # ② 开关开 ⇒ 头**该**生效（那才是代理后面的真实形态）
    fake_b = _RecordingLimiter()
    monkeypatch.setattr(main, "user_limiter", fake_b)
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
    asyncio.run(mw.dispatch(
        _request(headers=[("X-Forwarded-For", "9.9.9.9")], client=("1.2.3.4", 5000)),
        _call_next,
    ))
    assert fake_b.seen == ["ip:9.9.9.9"], f"开了开关却没读头：{fake_b.seen}"


# ═══════════════════════════════════════════════════════════════════
# 四 · 开关本身的默认值
# ═══════════════════════════════════════════════════════════════════

def test_the_switch_defaults_to_off_in_source():
    """**默认必须是关** —— 这是「本机/自建部署的安全性质」。

    判据只能看**源码里的默认值**（本机 `.env` 可能显式开了它，那是操作者的选择）
    ⇒ 断言默认字面量就是 `"false"`。别把它改成 `"true"`。
    """
    import core.config as config

    src = inspect.getsource(config)
    assert 'os.getenv("TRUST_PROXY_HEADERS", "false")' in src, (
        "TRUST_PROXY_HEADERS 的默认值不是 false —— 直连部署会变得可以被伪造的 IP 换桶"
    )


_PROXY_HEADER_NAMES = {"x-forwarded-for", "x-real-ip"}


def _reads_a_proxy_header(source: str) -> list:
    """这段源码里**作为字符串字面量**出现过的代理头名字。

    **AST 级，注释与 docstring 不算** —— 判据必须是「**它真的会去读那个头**」。
    用纯文本匹配的话，**解释这件事的注释本身**就会被判成违规
    （实测：`core/config.py` 那段讲"为什么要小心 XFF"的注释先被误报了）。
    这与 `test_quota_middleware.py::_find_references` 是同一条理由。
    """
    import ast

    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().lower() in _PROXY_HEADER_NAMES:
                hits.append(f"line {node.lineno}: {node.value!r}")
    return hits


def test_the_header_scanner_catches_code_but_not_comments():
    """**自证有效性**：扫描器对「真去读」要报，对「注释里提到」**不许**报。"""
    assert _reads_a_proxy_header('h = request.headers.get("X-Forwarded-For")'), "真实读取没抓到"
    assert not _reads_a_proxy_header('# 注意：x-forwarded-for 会被伪造'), "注释被误判成读取"
    assert not _reads_a_proxy_header('"""说明 x-real-ip 是什么。"""'), "docstring 被误判成读取"


def test_no_other_module_reads_forwarded_headers():
    """**只有一个地方读 IP** —— 别让第二个模块自己去 `request.headers.get("x-forwarded-for")`。

    本仓同一件事写两遍已经栽过多次（两条中间件各写一份豁免名单）。
    触发面 ⇒  §五「同一件事写两遍」那一族。
    """
    import pathlib

    api_dir = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    scanned = 0
    for path in sorted(api_dir.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        scanned += 1
        if path.name == "main.py":
            continue                      # 收口就在这里（`resolve_client_ip`）
        hits = _reads_a_proxy_header(path.read_text(encoding="utf-8"))
        if hits:
            offenders.append(f"{path.relative_to(api_dir)} {hits}")
    # 防空跑：扫到 0 个模块 ⇒ 这条恒真（本仓栽过，见 `app/tests/CLAUDE.md` 第 1 条）
    assert scanned > 10, f"只扫到 {scanned} 个模块 —— 枚举口径又变了，这条判据已经没在测东西"
    assert not offenders, f"这些模块自己读了代理头 —— 应该走 main.resolve_client_ip：{offenders}"
