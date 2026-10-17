"""`thread_id` 查询参数的空串守卫（裁定 #12）。
"""
import importlib.util
from pathlib import Path

from fastapi.routing import APIRoute

from main import app

# 与本仓 `app/tests/test_route_auth_scan.py` 同一条口径：用**脚本里那一份**遍历器，
#    别在测试里再实现一遍递归（两份实现 = 迟早只剩一份是对的）。
_p = Path(__file__).resolve().parents[2] / "scripts" / "check_route_auth.py"
_spec = importlib.util.spec_from_file_location("_check_route_auth_for_guard", _p)
check_route_auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_route_auth)

# 故意【不】要求 min_length 的两条 —— 理由见  Step 8 那张表。
#    这里是**唯一**允许存在的例外；多一条 ⇒ 用例红 ⇒ 强迫下一个例外走一次有意识的裁定。
ALLOWLIST = {
    ("/api/v1/agent/approve", "thread_id"),        # 必填；空串 ⇒ "查不到"，不是 500（另裁）
    ("/api/v1/agent/token/thread", "thread_id"),   # 语义是「不传 = 所有线程」，加了会改功能
}


def _min_length(field_info):
    """从 **Pydantic v2 的元数据**里取 `MinLen`（不是 `field_info.min_length`，那个不存在）。"""
    for md in getattr(field_info, "metadata", []) or []:
        if type(md).__name__ == "MinLen":
            return getattr(md, "min_length", None)
    return None


def _thread_id_query_params():
    """扫全 app，返回 [(路径, min_length), …] —— 每个 `thread_id` **查询参数**一条。"""
    http, ws = [], []
    check_route_auth._collect(app.routes, http, ws)
    out = []
    for r in http:
        if not isinstance(r, APIRoute):
            continue
        for q in getattr(r.dependant, "query_params", []):
            if q.name == "thread_id":
                out.append((r.path, _min_length(q.field_info)))
    return sorted(out)


def test_the_scan_itself_finds_something():
    """先证明**尺子有读数** —— 扫到 0 条时，下面那条会**静默全绿**。

    这不是理论风险：起草时那条"发现命令"就是因为遍历口径漏了 `_IncludedRouter`
       （只看到 5 条 APIRoute）而**打印 0 条**的 —— 当时若没有这条守卫，
       下面那条用例会拿一个空集合去 `assert not loose`，**一路绿着放行**。
    """
    found = _thread_id_query_params()
    assert len(found) >= 13, f"只扫到 {len(found)} 个 thread_id 查询参数 —— 扫描本身失效了"


def test_no_thread_id_query_param_accepts_the_empty_string():
    """每一个 `thread_id` 查询参数，要么 `min_length=1`，要么在 ALLOWLIST 里。

    空串若放进来，会一路走到 `session_key` 的 `ValueError`（fail-closed），
       而那时**响应头已经发出去了**（流式端点）⇒ 只能变成 500 或一个半截的流。
    """
    loose = [
        path for path, min_len in _thread_id_query_params()
        if (path, "thread_id") not in ALLOWLIST and min_len != 1
    ]
    assert not loose, (
        f"这些端点的 thread_id 还收得下空串（会一路走到 session_key 的 ValueError）：{loose}\n"
        f"⇒ 要么加 Query(..., min_length=1)，要么进 ALLOWLIST 并写明理由"
    )


def test_empty_thread_id_is_rejected_before_the_stream_opens(client, auth_headers):
    """行为层面：`?thread_id=` ⇒ **422**，不是 500、也不是一个半截的流。

    反证检验：把 `api_v1_rag.py` 那处的 `min_length=1` 去掉 ⇒ 本条变红
       （且红的样子是 500 / 半截流，见  Step 10 的实测）。
    这条**不替代**上面那条推导型的：它只钉住**一个**端点"真的挡得住"，
       而"还有没有端点没收口"是上面那条管的。
    """
    resp = client.post(
        "/api/v1/rag/stream_search?thread_id=",
        json={"question": "你好"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, f"空 thread_id 应被挡成 422，实际 {resp.status_code}"


def test_the_same_endpoint_still_accepts_a_normal_thread_id(client, auth_headers, monkeypatch):
    """配对的那一半：加了守卫之后，**正常值照样进得去**（不是把所有请求都挡了）。

    没有这条，"把整个端点写坏"与"加了个守卫"在红绿上是分不开的。
    真调端点会真检索/真调模型 ⇒ 打桩到叶子（同 `app/tests/test_frontend_contract.py` 的手法）。
    """
    import routing.api_v1_rag as rag_mod

    monkeypatch.setattr(rag_mod, "get_embedding", lambda text, **k: [0.0] * 8)

    # 🔴：检索改走与 `/rag/search` 同一条链 ⇒ 桩点上移到
    #    `api_v1_rag._stream_retrieval`。本用例测的是 `thread_id` 的必填与透传，⛔ 不是检索结果。
    async def _no_retrieval(*_a, **_k):
        return []
    monkeypatch.setattr(rag_mod, "_stream_retrieval", _no_retrieval)
    monkeypatch.setattr(rag_mod, "get_chat_history", lambda *a, **k: [])

    resp = client.post(
        "/api/v1/rag/stream_search?thread_id=t-guard",
        json={"question": "你好"},
        headers=auth_headers,
    )
    assert resp.status_code != 422, (
        f"传了正常 thread_id 却被当成非法参数挡掉（{resp.status_code}）：{resp.text[:200]}"
    )
