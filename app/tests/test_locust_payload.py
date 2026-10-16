"""压测脚本「请求形状」 vs 「接口契约」的一致性测试（对应 docs/重构计划-.md 的 #3）
"""
import re
from pathlib import Path

from main import app

REPO = Path(__file__).resolve().parents[2]
#    （下面按 `REPO / name` 读文件，不是按模块名 import）。
LOCUSTFILES = ("locust/locustfile.py", "locust/locustfile_v2.py", "locust/locustfile_hybrid.py")

# 只检查这两个"形状出过错"的端点；其余端点（如 /rag/insert 用 body）不动
QUERY_ONLY_PATHS = ("/api/v1/agent/mcp_chat", "/api/v1/rag/search")


# ==================== ① 契约侧 ====================

def test_mcp_chat_takes_query_not_body():
    """`/agent/mcp_chat` 的 `question` 是 query 参数，且**没有 requestBody**。"""
    post = app.openapi()["paths"]["/api/v1/agent/mcp_chat"]["post"]
    params = {p["name"] for p in post.get("parameters", [])}
    assert "question" in params, "question 应当是 query 参数"
    assert "requestBody" not in post, (
        "该端点本就不收请求体。若这里变成 True，说明有人给它加了 body 模型 —— "
        "那么 locustfile 里改用 params= 发也就失效了，两边都得跟着改"
    )


def test_rag_search_mode_is_query():
    """`/rag/search` 的 `mode` 是 query 参数（不是 body 字段）。"""
    post = app.openapi()["paths"]["/api/v1/rag/search"]["post"]
    qs = {p["name"] for p in post.get("parameters", [])}
    assert "mode" in qs, "mode 必须是 query 参数"


# ==================== ② 脚本侧 ====================

def _extract_kwarg(block: str, kwarg: str):
    """从请求块里取出 `kwarg={...}` 的**花括号内容**（做简单括号配对）。

    为什么需要它：判"mode 还在不在 body 里"时，**不能直接在该块里搜 `"mode"`** ——
    `params={"mode": ...}` 与 `json={"mode": ...}` 的写法**长得一模一样**，
    直接搜会把**已经改对**的 params 形态误判成 body 里的（本测试第一版就是这么误报的）。
    必须先把 `json={…}` 的**内容**切出来，只在它里面找。
    """
    i = block.find(kwarg + "=")
    if i < 0:
        return None
    j = block.find("{", i)
    if j < 0:
        return None
    depth, k = 0, j
    while k < len(block):
        if block[k] == "{":
            depth += 1
        elif block[k] == "}":
            depth -= 1
            if depth == 0:
                return block[j:k + 1]
        k += 1
    return None


def _scan_locustfile(name: str, src: str):
    """扫一个 locustfile，返回 (问题列表, 检查过的请求数)。

    按 `self.client.post(` 切块，取块内第一段路径字符串与是否用到 json=/params。
    """
    problems, checked = [], 0
    for m in re.finditer(r"self\.client\.post\((.*?)\n\s*\)", src, re.S):
        block = m.group(1)
        path_m = re.search(r'"(/api/v1/[^"]+)"', block)
        if not path_m:
            continue
        path = path_m.group(1)
        if path not in QUERY_ONLY_PATHS:
            continue
        checked += 1
        body = _extract_kwarg(block, "json")      # 只取 json 体的**内容**
        uses_params = "params=" in block

        if path == "/api/v1/agent/mcp_chat":
            if not uses_params:
                problems.append(f"{name}: {path} 必须用 params=（该端点只收 query 参数）")
            if body is not None:
                problems.append(f"{name}: {path} 不应再传 json=（无 body 模型 ⇒ 必 422）")
        elif path == "/api/v1/rag/search":
            if not uses_params:
                problems.append(f"{name}: {path} 必须用 params={{'mode': ...}}（mode 是 query 参数）")
            if body is not None and '"mode"' in body:
                problems.append(f"{name}: {path} 的 mode 留在 json 体里了（会被静默忽略）")
    return problems, checked


def test_locustfiles_match_the_contract():
    """三个 locustfile 里，打到这两个端点的请求必须用 `params=`。"""
    problems, checked = [], 0
    for name in LOCUSTFILES:
        p, n = _scan_locustfile(name, (REPO / name).read_text(encoding="utf-8"))
        problems += p
        checked += n

    # 防"空转"：正则若匹配不到任何请求，下面断言会**恒过**（本仓刚在 N1 踩过这个坑）
    assert checked > 0, (
        "扫描正则一条请求都没匹配到 —— 测试自身失效了，不是代码没问题"
    )
    assert not problems, "\n".join(problems)


def test_scan_actually_inspects_the_expected_endpoints():
    """自证：确认扫描器**真的**扫到了 mcp_chat 与 /rag/search，而不是漏扫。"""
    found = set()
    for name in LOCUSTFILES:
        src = (REPO / name).read_text(encoding="utf-8")
        for m in re.finditer(r"self\.client\.post\((.*?)\n\s*\)", src, re.S):
            pm = re.search(r'"(/api/v1/[^"]+)"', m.group(1))
            if pm and pm.group(1) in QUERY_ONLY_PATHS:
                found.add(pm.group(1))
    assert "/api/v1/agent/mcp_chat" in found, "没扫到 mcp_chat 的调用 —— 扫描器或脚本结构变了"
    assert "/api/v1/rag/search" in found, "没扫到 /rag/search 的调用 —— 扫描器或脚本结构变了"


def test_would_have_caught_the_original_shape():
    """**自证有效性**：用修复**前**的写法喂给扫描器，必须判它不过。

    修复前的形态：
      - mcp_chat 用 `json={"question":..., "thread_id":...}`（无 params）
      - /rag/search 把 "mode" 放在 json body 里
    """
    old_snippet = '''
        self.client.post(
            "/api/v1/agent/mcp_chat",
            headers=self.headers,
            json={"question": q, "thread_id": tid},
            name="/agent/mcp_chat"
        )
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            json={"question": q, "top_k": 3, "mode": "accurate"},
            name="/rag/search"
        )
'''
    problems, checked = _scan_locustfile("(修复前快照)", old_snippet)
    assert checked == 2, f"自证片段应被扫到 2 条请求，实际 {checked} —— 扫描器失效"
    assert len(problems) == 4, (
        f"对修复前的形态本应报 4 条问题（mcp_chat 缺 params/不该有 json；"
        f"/rag/search 缺 params/mode 不该在 body），实际报 {len(problems)} 条: {problems}"
    )


# ==================== ③ query_rewriter 的容错（连带修的可达 500）====================

def test_history_lines_accepts_both_shapes():
    """`query_rewriter._history_lines` 必须同时吃 `list[dict]`（schema 形态）与 `list[str]`（旧形态）。

    不修的话：按 schema 传 dict 的调用方会在 `"|".join([dict, …])` 上 TypeError ⇒ **500**
    （默认模式 `accurate_norerank` 本身就开着改写 ⇒ 这条路径可达）。
    """
    from rag.query_rewriter import _history_lines

    dict_form = [{"role": "user", "content": "什么是 RAG"},
                 {"role": "assistant", "content": "检索增强生成"}]
    str_form = ["用户: 什么是 RAG", "助手: 检索增强生成"]

    assert _history_lines(dict_form) == ["user: 什么是 RAG", "assistant: 检索增强生成"]
    assert _history_lines(str_form) == str_form
    assert _history_lines(None) == []
    assert _history_lines([]) == []

    # 只取最近 5 条
    many = [{"role": "user", "content": f"q{i}"} for i in range(9)]
    assert len(_history_lines(many)) == 5
    assert _history_lines(many)[-1] == "user: q8"


def test_would_have_caught_the_join_typeerror():
    """自证：旧的 `"|".join(history[-5:])` 写法对 dict 列表**确实会 TypeError**。"""
    import pytest
    with pytest.raises(TypeError):
        "|".join([{"role": "user", "content": "x"}][-5:])
