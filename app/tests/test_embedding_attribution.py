"""embedding 的**记账归属**。
"""
import pathlib
import re

API_DIR = pathlib.Path(__file__).resolve().parents[1]          # app/


# ═══════════════════════════════════════════════════════════════════
# 一 · 归属：传谁就记谁
# ═══════════════════════════════════════════════════════════════════

class _FakeResp:
    """能撑起 `response.data[0].embedding` 与 `response.usage.prompt_tokens` 的最小替身。"""

    def __init__(self):
        self.data = [type("D", (), {"embedding": [0.0, 0.0]})()]
        self.usage = type("U", (), {"prompt_tokens": 7})()


def _patched(monkeypatch):
    """把 `get_embedding` 的三条外路全换成假的：客户端 / 缓存 / 记账。"""
    import rag.embedding_client as ec

    seen = {}
    monkeypatch.setattr(ec, "_client", type("C", (), {
        "embeddings": type("E", (), {"create": staticmethod(lambda **k: _FakeResp())})(),
    })())
    monkeypatch.setattr(ec, "get_cached_embedding", lambda text, model: None)
    monkeypatch.setattr(ec, "set_cached_embedding", lambda *a: None)
    monkeypatch.setattr(ec, "record_usage", lambda **kw: seen.update(kw))
    return ec, seen


def test_the_user_is_recorded(monkeypatch):
    """**本条是要修的那件事**：传了 `user_name` ⇒ 账记在**那个用户**头上。

    反证（改前的写法）：把 `user_name=user_name` 改回写死的 `"system"` ⇒ **本条必须红**。
    """
    ec, seen = _patched(monkeypatch)

    ec.get_embedding("你好", user_name="alice")

    assert seen.get("user_name") == "alice", (
        f"embedding 的账记到了 {seen.get('user_name')!r} —— ⛔ 不是调用方传的那个用户"
    )
    assert seen.get("purpose") == "embedding"


def test_two_users_are_recorded_separately(monkeypatch):
    """两个不同的人 ⇒ 两次记录的 `user_name` 不同（别两个都落进同一个桶）。"""
    ec, seen = _patched(monkeypatch)
    got = []
    ec.record_usage = lambda **kw: got.append(kw["user_name"])

    ec.get_embedding("a", user_name="alice")
    ec.get_embedding("b", user_name="bob")

    assert got == ["alice", "bob"], f"两次调用的归属是 {got} —— 没有按人分开"


def test_no_user_falls_back_to_system(monkeypatch):
    """「**调用方本来就没有用户**」的那三条路（启动预热 / 维度自检 / `__main__`）
    ⇒ 记 `"system"`，**这是对的答案**，别把它当成漏传。

    **判据是"调用方有没有用户"**，不是"能不能凑一个名字出来" ——
    把启动成本算到某个真人头上，就是**把我们的钱记到访客账上**。
    """
    ec, seen = _patched(monkeypatch)

    ec.get_embedding("维度自检")

    assert seen.get("user_name") == "system"


# ═══════════════════════════════════════════════════════════════════
# 二 · 结构守卫：新加的调用点不许**静默**回到 `system`
# ═══════════════════════════════════════════════════════════════════

#: 允许**不带** `user_name=` 的地方 —— **每一条都要写清理由**，别当成"历史包袱清单"。
_SYSTEM_LEVEL = {
    "rag/embedding_client.py": "函数本体 + `assert_model_dimension`（启动自检）+ `__main__`",
    "core/cache.py": "`warmup_cache()` —— 启动时预热，**不属于任何用户**",
}


def _balanced_calls(src: str, name: str) -> list:
    """把 `name(` 开头、括号配平的那一段抠出来（能跨行）。"""
    out = []
    for m in re.finditer(re.escape(name) + r"\(", src):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(src)):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), src[m.start():j + 1]))
                    break
    return out


def _strip_comments(src: str) -> str:
    """把注释与字符串**按位换成空白**（长度不变）—— 否则**讲这件事的注释本身**会被判成命中
    （本仓栽过这个坑）。

    **必须"按位换空白"，不能"把剩下的 token 拼起来"** —— 那会**丢掉空白**，
    于是一段调用被拼成 `get_embedding(\\ndoc\\n.content…` ⇒ 下面那句
    `re.escape("get_embedding(")` **一处都匹配不到** ⇒ 门**扫到 0 处**、
    而防空跑断言**当场红**（本条就是这么被照出来的）。
    """
    import io
    import tokenize

    lines = src.splitlines(keepends=True)
    chars = list(src)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                base = sum(len(ln) for ln in lines[:row - 1])
                a = base + (scol if row == srow else 0)
                b = base + (ecol if row == erow else len(lines[row - 1].rstrip("\n")))
                for i in range(a, min(b, len(chars))):
                    if chars[i] != "\n":
                        chars[i] = " "
    except (tokenize.TokenError, IndentationError):
        return src          # 解析不了就原样返回 —— 那会让注释被算进去，宁可**多报**不可漏报
    return "".join(chars)


def test_every_call_site_passes_a_user(monkeypatch):
    """**活代码里每一个 `get_embedding(` 都得带 `user_name=`**，除非在白名单里。

    漏了不会报错 —— 它会**静默回到 `"system"`**，而"Embedding 用量恒为 0"这个病
    就**原样长回来**（那正是本条要防的）。
    """
    offenders, checked = [], 0
    for path in sorted(API_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        rel = path.relative_to(API_DIR).as_posix()
        if rel in _SYSTEM_LEVEL:
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for _, call in _balanced_calls(code, "get_embedding"):
            if call.startswith("get_embedding()"):      # 零参调用是 `def` 那一行之外的形态，忽略
                continue
            checked += 1
            if "user_name=" not in call:
                offenders.append(f"{rel} → {call.strip()[:70]}")

    # 防空跑：扫到 0 处调用 ⇒ 下面那句断言**恒真**
    assert checked >= 5, f"只扫到 {checked} 处 `get_embedding(...)` 调用 ⇒ 枚举口径变了，这条没在测东西"
    assert not offenders, (
        "这些调用点**没传** `user_name` ⇒ 它们的账会静默记到 \"system\" 头上：\n  "
        + "\n  ".join(offenders)
        + "\n⇒ 有用户就传（`user_name=<该用户>`）；**确实没有用户**的，"
          "把它加进本文件的 `_SYSTEM_LEVEL` 白名单**并写明理由**。"
    )


def test_the_allowlist_is_small_and_documented():
    """白名单不许变成"历史包袱清单" —— 它**现在只有 2 条**，加人要写理由。"""
    assert len(_SYSTEM_LEVEL) <= 3, f"白名单长到 {len(_SYSTEM_LEVEL)} 条了 —— 该逐条复核"
    for k, why in _SYSTEM_LEVEL.items():
        assert why.strip(), f"{k} 没写理由"


def test_the_guard_catches_a_call_site_that_forgets(monkeypatch):
    """**自证有效性**：造一个"忘了传"的调用 ⇒ 扫描器**必须**判它。

    没有这条，「那道门到底会不会红」就只是我的声称。
    """
    bad = 'x = get_embedding(q)\n'
    calls = _balanced_calls(_strip_comments(bad), "get_embedding")
    assert len(calls) == 1 and "user_name=" not in calls[0][1], (
        "扫描器认不出「没传 user_name」的形态 ⇒ 上面那条门测不出东西"
    )
    good = 'x = get_embedding(q, user_name=u)\n'
    assert "user_name=" in _balanced_calls(_strip_comments(good), "get_embedding")[0][1]


def test_comment_stripping_keeps_the_code_intact():
    """**剥注释不许把代码也剥掉** —— 这是上一条门能不能跑的前提。

    本条是**补的**：第一版 `_strip_comments` 用「把剩下的 token 拼起来」的写法，
    空白全丢了 ⇒ 一段调用被拼成 `get_embedding(` 之外的样子 ⇒ 门**扫到 0 处**、
    防空跑断言当场红。**没有防空跑的话，那条门会静默变成"永远通过"。**
    """
    src = (
        "def f():\n"
        "    # 这里是注释：get_embedding(q) 没传 user_name\n"
        '    """docstring 里也写着 get_embedding(q)"""\n'
        "    return get_embedding(q, user_name=u)\n"
    )
    stripped = _strip_comments(src)
    calls = _balanced_calls(stripped, "get_embedding")
    assert len(calls) == 1, f"剥完注释还剩 {len(calls)} 处调用（应当只剩代码里那 1 处）"
    assert "user_name=" in calls[0][1], "剥注释把真调用也弄坏了"


# ═══════════════════════════════════════════════════════════════════
# 三 · `thread_id` 那一维
# ═══════════════════════════════════════════════════════════════════
# 🔴 **为什么单立一维：`user_name` 对 ⛔ 不等于 `thread_id` 对。**

#: 允许**不带** `thread_id=` 的地方 —— **每一条都要写清理由**，⛔ 别当"历史包袱清单"。
_NO_SESSION_THREAD = {
    "rag/embedding_client.py":
        "函数本体（默认值 `\"system\"` 的理由见它自己的 docstring）+ `assert_model_dimension`"
        "（启动自检）+ `__main__`",
    "core/cache.py":
        "`warmup_cache()` —— 启动时预热热点查询，**没有用户、也没有会话**",
    "agent/memory_store.py":
        "mem0 那条路：它靠 `ContextVar` 传 user（§六），**thread 那一维本轮没做**；"
        "且它是 **agent 族**，⛔ 不在  这一批的范围里",
}


def test_every_call_site_passes_a_thread():
    """**活代码里每一个 `get_embedding(` 都得带 `thread_id=`**，除非在 `_NO_SESSION_THREAD` 里。

    漏了**不会报错** —— 它会静默回 `"system"`，而会话闸查的是调用者那个桶
    ⇒ **闸恒看到 0、恒放行**（实测复现过）。
    ⚠️ 与上面 `user_name` 那条**成对**：那一维保「记到对的人」，这一维保「记到对的会话」。
    """
    offenders, checked = [], 0
    for path in sorted(API_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        rel = path.relative_to(API_DIR).as_posix()
        if rel in _NO_SESSION_THREAD:
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for _, call in _balanced_calls(code, "get_embedding"):
            if call.startswith("get_embedding()"):      # 零参调用是 `def` 那一行之外的形态，忽略
                continue
            checked += 1
            if "thread_id=" not in call:
                offenders.append(f"{rel} → {call.strip()[:70]}")

    assert checked >= 5, f"只扫到 {checked} 处 `get_embedding(...)` 调用 ⇒ 枚举口径变了，这条没在测东西"
    assert not offenders, (
        "这些调用点**没传** `thread_id` ⇒ 它们的账会静默落进 \"system\"，"
        "而会话闸查的是调用者那个桶 ⇒ **闸恒看到 0**：\n  "
        + "\n  ".join(offenders)
        + "\n⇒ **有会话就传**（`thread_id=<该会话>`）；**确实没有会话**的"
          "（入库 / 基准测试那种，用显式标记如 `\"ingest\"` / `\"benchmark\"`），"
          "或**把它加进本文件的 `_NO_SESSION_THREAD` 白名单并写明理由**。"
    )


def test_the_thread_allowlist_is_small_and_documented():
    """白名单不许变成"历史包袱清单" —— 加人要写理由。"""
    assert len(_NO_SESSION_THREAD) <= 3, (
        f"白名单长到 {len(_NO_SESSION_THREAD)} 条了 —— 该逐条复核："
        f"是不是有哪几处其实**该传而没传**"
    )
    for k, why in _NO_SESSION_THREAD.items():
        assert why.strip(), f"{k} 没写理由"


def test_the_thread_guard_catches_a_call_site_that_forgets():
    """**自证有效性**：造一个"忘了传 `thread_id`"的调用 ⇒ 扫描器**必须**判它。

    ⚠️ 没有这条，「那道门到底会不会红」就只是我的声称（本仓立场）。
    """
    bad = "x = get_embedding(q, user_name=u)\n"
    calls = _balanced_calls(_strip_comments(bad), "get_embedding")
    assert len(calls) == 1 and "thread_id=" not in calls[0][1], (
        "扫描器认不出「传了 user_name 但没传 thread_id」这个形态 ⇒ 上面那条门测不出东西"
    )
    good = "x = get_embedding(q, user_name=u, thread_id=t)\n"
    assert "thread_id=" in _balanced_calls(_strip_comments(good), "get_embedding")[0][1]
