"""已删端点的【反向守卫】 —— 别把它们默默改回来。

**为什么要有这条用例**：删掉一个端点之后，**没有任何东西会阻止它被重新加回来** ——
`grep` 只说明「现在没有」，不说明「以后不会有」（本仓
记的就是同族毛病：**拿一次观察当全称判断**）。这条用例把「它不该回来」变成一条**会红的检查**。
"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import app


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """**本模块只断「路由存不存在」，⛔ 不该被限流影响。**

    🔴🔴 **：实测这对用例【随机红】（`assert 429 == 404`），根因是限流的层级**。

    限流在**中间件**里，而**中间件跑在路由之前** ——

    ```
    第一层：全局限流（令牌桶，按秒补）   ← 前面用例把它耗掉 ⇒ 这里直接 429
    第二层：用户级限流
    路由匹配                            ← 才会给出 404
    ```

    ⇒ 请求一条**根本不存在的路径**时**先被限流拦下**，拿不到 404。
    而桶**按秒补** ⇒ 前面那些用例**跑得快慢不同，桶里剩的就不一样**
    ⇒ **有时够、有时不够** ⇒ **波动**。

    ⚠️ 它已经在 CI 上把**两个仓**打红过 **3 次**（开发仓 2 次 · 产品仓 1 次），
      而**每次重跑就好** —— 那正是"波动"的形状。
    ⚠️ 本机（~55s）过、CI（~37s）挂，也是同一件事：**时间轴不同**。

    ⇒ 换成**恒放行的桩**：这条用例要说的是「**它不该回来**」，
      ⛔ 不是"限流器今天忙不忙"。手法与同目录 `test_rate_limit_ip.py` 的
      `_RecordingLimiter` 一致（`get_limit_info` 必须回**真形状** ——
      中间件写响应头时要读那三个键）。
    """
    import main as _main

    class _AlwaysAllow:
        def is_allowed(self, key):
            return True

        def get_limit_info(self, key):
            return {"limit": 20, "remaining": 19, "reset": 0}

    monkeypatch.setattr(_main, "global_limiter", _AlwaysAllow())
    monkeypatch.setattr(_main, "user_limiter", _AlwaysAllow())


def test_rag_ask_stays_removed():
    """`/rag/ask` 已删—— 它回来就该红，别默默改回来。

    **判据是 404，不是「不是 200」** —— 端点还在但**没带鉴权**时是 401/403，
    那也是「它回来了」。只有 **404（路由不存在）** 才算删干净。

    **别把这里改成 `with TestClient(app) as client:`** —— 那样会**触发 lifespan 的 startup**
    ⇒ `init_pool` **真去连 Postgres** ⇒ **CI 没有 Postgres ⇒ `psycopg2.OperationalError`**
    （实测： ⇒ `1 failed, 411 passed`）。
    **本机看不出来**：本机 Postgres 真开着 ⇒ 连得上 ⇒ 照样绿。
    **本仓判据**：本用例只断**路由存不存在**，**不需要任何 startup** ⇒ 用**裸 `TestClient(app)`**
    （全仓不连库的用例都是这么写的；`with … as` 全仓**只有这一处**，就是它把自己坑了）。
    
    **裁决全文（含"为什么不标 `needs_db`"）⇒ **
    　　· 规矩落在 `docs/规范/开发规范.md §2.5·5`
    """
    client = TestClient(app)
    resp = client.post("/api/v1/rag/ask", json={"question": "x", "top_k": 3})

    assert resp.status_code == 404, (
        f"`/api/v1/rag/ask` 应已删除，却返回了 {resp.status_code} —— "
        "要么它被改回来了（⛔ 先读那份 DEC 里的删除理由再决定），要么本用例的路径写错了"
    )


def test_rag_jwt_ask_stays_removed():
    """`/rag/jwt_ask` 已删—— 它回来就该红，别默默改回来。

    **判据是 404，不是「不是 200」** —— 理由与上一条逐字相同：端点还在但**带了鉴权**时
    回的是 **401/403**，那也是「它回来了」。本条尤其容易踩：删之前它**本来就是 401**
    （缺 Bearer Token），所以「不是 200」这种写法**在这条上从第一天起就是绿的**，什么也没钉住。

    **为什么走 `get_current_user_jwt` 也一样要删**（最容易读反的一点）：
    它「只收 JWT、不收 API Key」看着像一项独有能力，其实是**限制** ——
    `/rag/pg_search` 的 `get_current_user_hybrid` **JWT 与 API Key 都收**，是它的**超集**。
    ⇒ 删掉它，**JWT 用户一个能力都没少**。

    **别把这里改成 `with TestClient(app) as client:`** —— 同上一份
    """
    client = TestClient(app)
    resp = client.post("/api/v1/rag/jwt_ask", json={"question": "x", "top_k": 3})

    assert resp.status_code == 404, (
        f"`/api/v1/rag/jwt_ask` 应已删除，却返回了 {resp.status_code} —— "
        "要么它被改回来了（⛔ 先读那份 DEC 里的删除理由再决定），要么本用例的路径写错了"
    )


# ====================  一次删掉的 4 条（A1 收口）====================
# 这 4 条删之前**全都回 200**（都是匿名可打的），所以「不是 200」这种写法在它们身上
#    当时就已经是红的 —— 但那**不是**本用例要钉的东西。本用例钉的是 **404**。


def test_users_by_id_stays_removed():
    """`GET /users/{user_id}` 已删—— 它**一行数据都不读**，回来就该红。

    它当初唯一的行为是 `return {"user_id": user_id, "detail": include_detail}` ——
    **不查库、不看 `user_name`**，纯粹是 **Path/Query 参数校验演示**，却很容易被当成真接口用
    （`app/routing/specs/api_v1.md` ④ 记的就是这个）。

    **别把这里改成 `with TestClient(app) as client:`** —— 见
    """
    client = TestClient(app)
    resp = client.get("/api/v1/users/1")

    assert resp.status_code == 404, (
        f"`/api/v1/users/{{user_id}}` 应已删除，却返回了 {resp.status_code} —— "
        "要么它被改回来了（⛔ 先读那份 DEC 里的删除理由再决定），要么本用例的路径写错了"
    )


def test_tool_benchmark_stays_removed():
    """`GET /tool/benchmark` 已删—— 它 benchmark 的是 **mock**，回来就该红。

    它比的是 `get_weather("Beijing")` 调两次的耗时，而 `get_weather` 本体是
    `time.sleep(2)` + 硬编码返回值 ⇒ **证明的只是「缓存装饰器在假函数上生效」**，
    **证明不了任何生产事实**。缓存装饰器本身由 `app/tests/test_audit_fixes.py` 单测覆盖。

    **别把这里改成 `with TestClient(app) as client:`** —— 同上那份。
    """
    client = TestClient(app)
    resp = client.get("/api/v1/tool/benchmark")

    assert resp.status_code == 404, (
        f"`/api/v1/tool/benchmark` 应已删除，却返回了 {resp.status_code} —— "
        "要么它被改回来了（⛔ 先读那份 DEC 里的删除理由再决定），要么本用例的路径写错了"
    )


def test_rag_async_ask_stays_removed():
    """`POST /rag/async_ask` 已删—— 纯 mock，回来就该红。

    它 `await asyncio.sleep(2)` 后返回 `[f"异步文档A({query})", …]` —— 无库、无 embedding、
    不花钱，但**也无消费者**。它一删，`tags=["模拟类测试"]` 只剩 `parallel_ask`，**再删即整组归零**。

    **它的「不接线」守卫去哪了**：原先它由两份反向守卫各钉一行
    （`test_breaker_wiring.py::test_the_two_non_spending_endpoints_stay_unwired`
    `test_session_budget_wiring.py::test_the_two_non_llm_endpoints_stay_unwired`）。
    端点删掉后**那两张清单一起空了** —— 空清单 = `for` 体一次都不跑 = **恒绿假通过**
    （正是本仓「空跑 = 静默假通过」那条纪律点的病）。⇒ 两条空守卫**已删**，
    其保护**收敛到这里**。**代价说清楚**：将来若有人再加一条 mock 端点，
    **不会有测试自动拉红**叫他去接线 —— 只能靠  里那条写明的要求。

    **别把这里改成 `with TestClient(app) as client:`** —— 同上那份。
    """
    client = TestClient(app)
    resp = client.post("/api/v1/rag/async_ask", json={"question": "x"})

    assert resp.status_code == 404, (
        f"`/api/v1/rag/async_ask` 应已删除，却返回了 {resp.status_code} —— "
        "要么它被改回来了（⛔ 先读那份 DEC 里的删除理由再决定），要么本用例的路径写错了"
    )


def test_rag_parallel_ask_stays_removed():
    """`POST /rag/parallel_ask` 已删—— 纯 mock，回来就该红。

    它调 `parallel_search` → `asyncio.gather` 几个 `async_search`，返回**同样是硬编码串**。
    **这一条删掉后 `tags=["模拟类测试"]` 整组归零。**

    **它的「不接线」守卫去哪了** —— 与上一条逐字相同（两条空守卫已删，见
    `test_rag_async_ask_stays_removed` 的说明与）。

    **别把这里改成 `with TestClient(app) as client:`** —— 同上那份。
    """
    client = TestClient(app)
    resp = client.post("/api/v1/rag/parallel_ask", json={"question": "x"})

    assert resp.status_code == 404, (
        f"`/api/v1/rag/parallel_ask` 应已删除，却返回了 {resp.status_code} —— "
        "要么它被改回来了（⛔ 先读那份 DEC 里的删除理由再决定），要么本用例的路径写错了"
    )


def _ws_paths(routes) -> set:
    """走一遍路由表，收所有 WebSocket 路由的路径（递归进 `_IncludedRouter`）。"""
    out = set()
    for r in routes:
        kind = type(r).__name__
        if kind == "_IncludedRouter":
            out |= _ws_paths(getattr(r.original_router, "routes", []) or [])
        elif kind == "APIWebSocketRoute":
            out.add(r.path)
    return out


def test_ws_test_stays_removed():
    """`WS /api/v1/ws/test` 已删—— 加回来就该红，别默默改回来。

    **为什么删**（判据三条，与  删那 4 条**同一套标准**，不是另立一套）：
    ① **消费者 = 0** —— 三处独立扫过：本仓（`app/static/*.html` 连的是 `/ws/agent`，不是它）；
       `git log -S 'ws/test' -- 'app/test_*.py'` ⇒ **本轮之前零命中**（它的全部命中都是
       / 这两轮"把它登记成债"留下的）；**仓外**（任何别的项目）⇒ 全 0。
    ② **它是「测试桩」** ——  的流式出口普查表里就写着 `WS /ws/test` = **「测试桩」**。
    ③ **它连"测试"都不服务** —— 首帧认证补上之后，它**自己也要凭据**，
       ⇒ 连"免鉴权的探活口子"这个唯一可能的用途**也没了**。

    **判据形态：WS 没有状态码，所以不能照抄 HTTP 那套 `== 404`。**
    实测：

    | 情形 | `client.websocket_connect(path)` 的行为 |
    |---|---|
    | 路由**不存在** | **建连那一刻**（`__enter__`）就抛 `WebSocketDisconnect` |
    | 路由**存在且要鉴权** | 建连**成功**；退出上下文时才抛 `WebSocketDisconnect(1000)` |

    **所以「抛了 `WebSocketDisconnect` 就算删干净」是【假判据】** ——
    一条活着的、带鉴权的 WS 路由**也会抛**（上面第二行）。
    那正是 / 里那个「不是 200」的同型坑：**判据成立，却什么也没钉住**。
    ⇒ 本用例取**两个不同的可观测量**，任一成立为"没删干净"：
    · **端到端**：建连**当场**被拒（不是"连上了、随后被关"）—— 这是 404 在 WS 上的对应物；
    · **路由表**：路径不在 WS 路由集合里。
    两者**都取自路由配置**（同源），故**不是两次独立证明** —— 放在一起是为了
    在"哪里变了"时能一眼看出是**建连层**还是**注册层**出的问题。

    **别把这里改成 `with TestClient(app) as client:`** —— 会触发 lifespan 的
    `init_pool` 真连 Postgres ⇒ CI 没有 Postgres ⇒ 红。
    """
    from main import app

    client = TestClient(app)

    # ① 端到端：建连当场就被拒
    conn = client.websocket_connect("/api/v1/ws/test")
    with pytest.raises(WebSocketDisconnect):
        conn.__enter__()

    # ② 注册层：路由表里没有这条路径
    assert "/api/v1/ws/test" not in _ws_paths(app.routes), (
        "`/api/v1/ws/test` 应已删除，却又出现在 WS 路由表里 —— "
        "要么它被改回来了（⛔ 先读那份 DEC 的删除理由再决定），要么本用例的路径写错了"
    )
