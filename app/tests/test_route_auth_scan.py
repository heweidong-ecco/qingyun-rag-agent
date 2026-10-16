""" 的**扫描覆盖**守卫 —— 补 WebSocket 的结构盲区。

## 这条测试为什么存在（2026-10-05）

 是本仓"新端点必须带鉴权依赖"的机械判据
（`docs/规范/开发规范.md` §1.5）。但它**结构上看不见 WebSocket** ——
它的收集函数只认 `APIRoute`（`_collect_apiroutes`）。

这条盲区  已经**写在文字里**了。但**文字不是门** —— 实测（**2026-10-04 当时**）：

```
/api/v1/ws/agent    dependant.dependencies == []   # 无鉴权
/api/v1/ws/test     dependant.dependencies == []   # 无鉴权
                    而该脚本对 /api/v1/ws/* 零输出
```

**上面那段是【当时的实测记录】，不是现在的事实** ——
2026-10-05`/ws/agent` 已挂 `require_ws_user`，`/ws/test` **已删**。
⇒ 现在真 app 上只剩 **1 条 WS，且带鉴权**。保留这段是因为它是**盲区存在过的证据**，
别拿它当现状读（同型： 那条墓碑也是保留的当时事实）。

⇒ 本文件把"看得见"做成结构：**WS 路由也必须进那份「无鉴权路由」清单**。

## 两个刻意的设计

1. **本文件不自己抄一份 `AUTH_NAMES`** —— 从脚本里 import。
2. 本文件**不启服务、不连 DB/Redis**，也**不 `chdir`** ——
   脚本的 `scan` 会 `os.chdir(app/)`，在 pytest 进程里调它会污染整个会话的工作目录。
   ⇒ 只测它的**纯函数**那一半（`find_routes_without_auth`）。
"""
import importlib.util
from pathlib import Path

import pytest
from fastapi import APIRouter, WebSocket

_p = Path(__file__).resolve().parents[2] / "scripts" / "check_route_auth.py"
_spec = importlib.util.spec_from_file_location("_check_route_auth", _p)
check_route_auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_route_auth)


def _scan(routes, exempt=frozenset()):
    """调脚本的纯函数那一半，返回 (无鉴权路由列表, HTTP 总数, WS 总数)。"""
    return check_route_auth.find_routes_without_auth(routes, exempt)


# ---------------------------------------------------------------- ① 真 app

def test_scanner_sees_websocket_routes():
    """扫描口径必须覆盖 WS —— 用**合成**路由钉死，不拿真 app 那两条当样本。

    **为什么不用真 app 当样本了**（2026-10-05）：
    2026-10-04 写这条时，`/api/v1/ws/agent` 与 `/api/v1/ws/test` **都是无鉴权的**，
    于是「扫描器看得见 WS」与「它俩在清单里」恰好是同一件事。
    现在它俩**已经有鉴权**（`require_ws_user`）⇒ **样本没了**。

    若继续拿真 app 当样本，这条会**逼着人把鉴权改回去才能绿** ——
    测试在保护 bug（本仓  记过同型：留着一个永远绿/反向的用例比删掉更坏）。
    ⇒ 样本换成**合成**路由；真 app 那条改成断言**相反**的事实（见下一条）。
    """
    api = APIRouter()

    @api.websocket("/ws-synth-no-auth")
    async def _ws_no_auth(websocket: WebSocket):  # pragma: no cover —— 只建路由，不连
        pass

    rows, _, n_ws = _scan(api.routes, frozenset())
    assert n_ws == 1, f"合成 WS 路由没被收进 ws 桶（n_ws={n_ws}）"
    assert ("WS", "/ws-synth-no-auth") in rows, "无鉴权的 WS 路由没进清单"


def test_real_websocket_routes_are_protected_now():
    """真 app 上那条 WS（`/ws/agent`）**必须带鉴权** ⇒ 不该出现在「无鉴权」清单里。

    与 `app/tests/test_ws_auth.py::test_the_ws_route_carries_the_auth_dependency` **互补**：
    那条钉「路由上挂的是不是 `require_ws_user` 这一条依赖」，
    这条钉「**扫描器**也认账」—— 两条一起才挡得住"挂了但名单里没登记"的漂移。

    **2026-10-05 起是 1 条不是 2 条** —— `/ws/test` 已删（§十）。
    删它「不靠这条用例兜着」：兜它的是 `app/tests/test_removed_endpoints.py::test_ws_test_stays_removed`。
    """
    from main import MIDDLEWARE_EXEMPT_PATHS, app

    rows, _, n_ws = _scan(app.routes, MIDDLEWARE_EXEMPT_PATHS)
    assert n_ws == 1, f"真实 WS 路由数变了（现在是 {n_ws}）—— 请人工核一遍再改这条"

    ws_rows = [p for _, p in rows if p.startswith("/api/v1/ws/")]
    assert ws_rows == [], (
        f"这条 WS 又变成无鉴权了：{ws_rows} —— 它必须挂着 `require_ws_user`"
    )


def test_websocket_rows_are_labelled_as_websocket():
    """WS 行的方法标签必须是 `WS` —— 标 `GET` 会让读清单的人以为它是个 HTTP 端点。"""
    api = APIRouter()

    @api.websocket("/ws-synth")
    async def _ws(websocket: WebSocket):  # pragma: no cover —— 只建路由，不连
        pass

    @api.get("/http-synth")
    async def _http():  # pragma: no cover —— 只建路由，不请求
        return {}

    rows, _, _ = _scan(api.routes, frozenset())
    assert ("WS", "/ws-synth") in rows, f"WS 行标签不对：{rows}"
    assert ("GET", "/http-synth") in rows, "HTTP 那半也要照旧带动词（别为了 WS 把它改坏）"


def test_http_scan_still_works_after_the_change():
    """改扫描口径时最容易顺手改坏的那半 —— HTTP 侧必须一条不少。

    改动前它报 1 条（`/api/v1/`）；这条把那个数钉住，防止"加了 WS 就丢了 HTTP"。
    """
    from main import MIDDLEWARE_EXEMPT_PATHS, app

    rows, n_http, n_ws = _scan(app.routes, MIDDLEWARE_EXEMPT_PATHS)
    http_rows = [(m, p) for m, p in rows if m != "WS"]

    assert ("GET", "/api/v1/") in http_rows, (
        "`/api/v1/` 从 HTTP 清单里消失了 —— 扫描口径被改坏了"
    )
    assert n_http > 50, f"扫到的 HTTP 路由只有 {n_http} 条，像是没递归进 _IncludedRouter"
    assert n_http + n_ws == n_http + 1, "自检算式（⚠️ 2026-10-05 起 WS 只剩 1 条 · 见  §十）"


# ------------------------------------------------- ② 自证：盲区真实存在过

def test_old_collector_would_have_missed_websockets():
    """**自证盲区真实存在** —— 不给"我声称它看不见"留余地。

    判据：用**只认 `APIRoute`** 的老口径去扫同一个真实 app，
    WS 必须**一条都扫不到**；而新口径看得见（上面那条已断言）。

    **2026-10-05 起真 app 只剩 1 条 WS**（`/ws/test` 已删 ·  §十）——
    样本变少了，但**这条自证仍然成立**：只要 WS 还有一条，老口径就该漏掉它。
    """
    from main import app

    http, ws = [], []
    check_route_auth._collect(app.routes, http, ws)
    assert len(ws) == 1, "前提变了：新口径本该看见 1 条 WS"

    paths_old = {r.path for r in http}
    assert "/api/v1/ws/agent" not in paths_old


def test_scanner_flags_an_endpoint_that_drops_auth():
    """**自证能红** —— 造一条真·无鉴权 HTTP 路由，扫描必须报它。

    没有这条，"上面的断言能不能测出无鉴权"就只是我的声称。
    （这正是  里那条**假守卫**救回来时用的同一招。）
    """
    api = APIRouter()

    @api.get("/definitely-no-auth")
    async def _no_auth():
        return {}

    rows, _, _ = _scan(api.routes, frozenset())
    assert ("GET", "/definitely-no-auth") in rows


def test_scanner_does_not_flag_an_endpoint_with_auth():
    """反向自证：带了鉴权依赖的路由**不许**被报（否则守卫会天天误报，然后被无视）。"""
    from access.auth import verify_api_key  # 真依赖，不是假的
    from routing.deps import get_current_user_hybrid
    from fastapi import Depends

    api = APIRouter()

    @api.get("/has-auth-dependency", dependencies=[Depends(get_current_user_hybrid)])
    async def _with_auth():
        return {}

    rows, _, _ = _scan(api.routes, frozenset())
    assert ("GET", "/has-auth-dependency") not in rows, (
        f"带了 get_current_user_hybrid 仍被报成无鉴权 —— AUTH_NAMES 口径坏了；verify_api_key={verify_api_key!r}"
    )


def test_exempt_paths_are_still_skipped():
    """豁免名单里的路径不许被报 —— 这条口径没变（`/health` 之类本来就该豁免）。"""
    api = APIRouter()

    @api.get("/health")
    async def _h():
        return {}

    rows, _, _ = _scan(api.routes, frozenset({"/health"}))
    assert rows == [], f"豁免名单失效了：{rows}"


@pytest.mark.parametrize("exempt", [frozenset(), frozenset({"/health"})])
def test_scan_never_chdirs(exempt):
    """纯函数那一半**不许**改工作目录 —— 否则 pytest 会话会被污染。

    脚本自己的 `scan` 有 `os.chdir`，那是它的权利（它是独立进程）；
    但 `find_routes_without_auth` 是**被 pytest 直接调**的，越界就是事故。
    """
    api = APIRouter()

    @api.get("/x")
    async def _x():
        return {}

    before = Path.cwd()
    _scan(api.routes, exempt)
    assert Path.cwd() == before, "find_routes_without_auth 动了 cwd —— pytest 会话已被污染"
