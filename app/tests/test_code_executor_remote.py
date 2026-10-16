"""接线守卫：`execute_python_impl` 会不会**真的**走执行器。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import tools.code_executor_impl as C


class _Handler(BaseHTTPRequestHandler):
    """记录收到的请求，并回一个**带标记**的响应（好证明结果来自远端而不是本地）。"""

    received = []

    def do_POST(self):                                     # noqa: N802 —— stdlib 的命名约定
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        type(self).received.append(body)

        payload = json.dumps({"ok": True, "out": f"REMOTE:{body['code']}"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):                             # 别往 stderr 刷日志
        pass


@pytest.fixture
def fake_executor(monkeypatch):
    """起一个真服务 + 把 `EXECUTOR_URL` 指过去。产出 `received` 列表。"""
    _Handler.received = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(C, "EXECUTOR_URL", f"http://127.0.0.1:{srv.server_port}")
    try:
        yield _Handler.received
    finally:
        srv.shutdown()
        srv.server_close()


# ===========================================================================
# ① 有 URL ⇒ 真的走远端
# ===========================================================================
def test_url_set_means_the_request_really_goes_out(fake_executor):
    """判据是**远端收到了那段代码**，不是"函数返回了"。

    响应带 `REMOTE:` 前缀 ⇒ 结果**只可能来自替身**，不可能是本地跑出来的。
    """
    out = C.execute_python_impl("print(1)")

    assert fake_executor == [{"code": "print(1)"}], "执行器没收到请求 ⇒ 根本没走远端"
    assert out == "REMOTE:print(1)"


# ===========================================================================
# ② 没 URL ⇒ 回落本地（现有 6 条用例就是这条路，一条不改）
# ===========================================================================
def test_no_url_falls_back_to_local_subprocess(monkeypatch, fake_executor):
    """本机开发 / 离线 CI 走的就是这条 —— **不许**把它变成"必须有容器"。"""
    monkeypatch.setattr(C, "EXECUTOR_URL", "")

    assert C.execute_python_impl("print(6*7)") == "42\n"
    assert fake_executor == [], "回落路径⛔ 不该发出任何请求"


# ===========================================================================
# ③ 远端不通 ⇒ 如实报错，不静默回落
# ===========================================================================
def test_remote_failure_is_reported_and_does_not_silently_fall_back(monkeypatch):
    """**本文件最要紧的一条**。

    判据是**结果里没有本地跑出来的 `42`**：
       如果实现"失败就悄悄回落本地"，`print(6*7)` 会**照常返回 `42\\n`** ——
       而运维**看不出任何异常**，代码却已经回到了宿主同权限的进程里。

    指向一个**没人监听**的端口（`127.0.0.1:1`）来制造失败，不靠 mock 抛异常。
    """
    monkeypatch.setattr(C, "EXECUTOR_URL", "http://127.0.0.1:1")

    out = C.execute_python_impl("print(6*7)")

    assert out != "42\n", "🔴 远端不通却悄悄回落本地了 —— 隔离变成装饰性的"
    assert "执行器" in out, f"错误信息该说清是执行器的问题，实际：{out!r}"


# ===========================================================================
# ④ 「意图检测」是【产品策略】，留在应用侧 —— 不许被远端路径跳过
# ===========================================================================
def test_intent_check_still_applies_before_any_remote_call(fake_executor):
    """「帮我写一段代码」这种**需求描述** ⇒ 在**发出去之前**就被拒。

    这条钉的是**分层**：那个检测是**产品策略**（"这个工具只执行、不生成"），
       **不是执行机制** ⇒ 它**留在应用侧**，不搬进执行器容器。
    """
    out = C.execute_python_impl("帮我写一段计算器代码")

    assert "不是可执行的 Python 代码" in out
    assert fake_executor == [], "🔴 需求描述被发到执行器了 —— 策略层被绕过"
