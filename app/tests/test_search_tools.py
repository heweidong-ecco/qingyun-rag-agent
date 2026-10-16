"""`web_search` 的回归用例（N19 · 2026-09-21 重写）。

**本文件守的是那件最容易悄悄回退的事**：
   `web_search` 曾经**调 LLM 的 `enable_search`、把模型输出当搜索结果返回** ——
   实测证明**它根本没在搜索**，还返回过一条**日期差半年的天气**（格式与真答案无异）。

⇒ 所以判据分两层：
   · **结构判据**（`test_search_tools_has_no_llm_client`）：这个文件里**不许有 LLM 客户端** ——
     那是"**结构上不可能再返回模型编的内容**"的保证，不是靠自觉
   · **行为判据**：搜不到时**必须如实报失败**，不能返回任何像答案的东西
"""
import ast
import pathlib

_HERE = pathlib.Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层


# ===========================================================================
# 结构判据：这个模块里【不许有 LLM 客户端】
# ===========================================================================
def test_search_tools_has_no_llm_client():
    """`search_tools.py` **不许 import 任何 LLM 客户端 / LLM 配置**。

    这条是**结构性保证**，不是风格要求：
       只要这个文件里没有能"生成文本"的东西，**它就【不可能】再把模型编的内容当成搜索结果返回**。
       —— 那正是这次重写要根除的病（见模块 docstring 里那三条实测）。

    本仓教训：**「写了注释说别这么干」是软约束**（`Product/CLAUDE.md` §二：
       「有结构才执行，只有文字就漏」）⇒ 所以这条用 `ast` 查**真实 import**，不查文本。
    """
    src = (_HERE / "tools/search_tools.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "openai" not in imported, (
        "search_tools.py 又 import 了 openai —— 一旦这里能生成文本，"
        "就又有可能把【模型编的内容】当成搜索结果返回。"
    )
    assert "config" not in imported, (
        "search_tools.py 又 import 了 config —— 那意味着它又拿 LLM_API_KEY 去调模型了。"
    )
    assert "chat.completions" not in src, (
        "search_tools.py 里出现了 `chat.completions` —— 那是 LLM 调用，不该在这里。"
    )


# ===========================================================================
# 行为判据：解析得到 → 返回真结果；解析不到 → 如实报失败
# ===========================================================================
# 一段**逼真的必应结果页片段**（结构取自实测的 cn.bing.com 结果页）
_BING_HTML = """
<html><body><ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://www.nobelists.org/zh/physics-2026/">2026年诺贝尔物理学奖：获奖者与发现</a></h2>
    <div class="b_caption"><p>2026年诺贝尔物理学奖授予了三位科学家……</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="https://zhuanlan.zhihu.com/p/2058090174844768849">预定2026诺贝尔物理学奖？</a></h2>
    <div class="b_caption"><p>仅仅旋转1.1度，改写整个物理界</p></div>
  </li>
</ol></body></html>
"""

_EMPTY_HTML = "<html><body><div>换个关键词试试</div></body></html>"


def test_parses_real_bing_results(monkeypatch):
    """能解析出标题 / 链接 / 摘要，且**返回里带真链接**。"""
    import tools.search_tools as S

    class _Resp:
        status_code = 200
        text = _BING_HTML

        def raise_for_status(self):
            pass

    monkeypatch.setattr(S.httpx, "get", lambda *a, **k: _Resp())

    out = S.web_search.invoke({"query": "2026 诺贝尔物理学奖"})

    assert out.startswith("搜索「2026 诺贝尔物理学奖」的结果"), out[:80]
    assert "https://www.nobelists.org/zh/physics-2026/" in out
    assert "2026年诺贝尔物理学奖：获奖者与发现" in out
    assert "https://zhuanlan.zhihu.com/p/2058090174844768849" in out


def test_honest_failure_when_nothing_parsed(monkeypatch):
    """**解析不到任何条目时必须如实报失败** —— 这是本次重写的**核心要求**。

    判据有两层，缺一不可：
      ① 以「搜索失败」开头
      ② **明说没有用模型知识替代** —— 且输出里**不许出现任何像答案的内容**
    """
    import tools.search_tools as S

    class _Resp:
        status_code = 200
        text = _EMPTY_HTML

        def raise_for_status(self):
            pass

    monkeypatch.setattr(S.httpx, "get", lambda *a, **k: _Resp())

    out = S.web_search.invoke({"query": "随便什么"})

    assert out.startswith("搜索失败"), f"解析不到却没报失败：{out[:100]!r}"
    assert "没有" in out and "模型知识替代" in out, (
        "失败时必须明说【没有用模型知识替代】—— 否则调用方会以为这是查到的"
    )


def test_honest_failure_when_network_fails(monkeypatch):
    """网络挂了也必须**如实报失败**，不能抛出去、更不能编。"""
    import tools.search_tools as S

    def _boom(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(S.httpx, "get", _boom)

    out = S.web_search.invoke({"query": "x"})

    assert out.startswith("搜索失败")
    assert "ConnectionError" in out, "应当把真实原因报出来（便于排查），但不许变成答案"
    assert "没有" in out and "模型知识替代" in out


def test_parse_returns_empty_for_garbage():
    """解析器对**任意 HTML** 都只返回列表 —— 不抛异常、不返回 None、**不猜**。"""
    import tools.search_tools as S

    for html in ("", "<html>", "not html at all", "<li class='b_algo'></li>"):
        assert S._parse_bing_results(html) == [], f"{html!r} 竟然解析出了东西"


def test_tool_description_tells_the_llm_what_to_do_on_failure():
    """工具描述（**LLM 读的那份**）必须**告诉 LLM 怎么处理失败** —— 否则它会拿自己的知识圆场。

    与 `execute_python` 那条「不要 import」的用例同一个道理：
       **工具的失败语义必须写进给 LLM 的描述里**，否则 LLM 只会看到一段像答案的文本。
    """
    import tools.search_tools as S

    desc = S.web_search.description or ""
    assert "搜索失败" in desc, "描述里没提失败长什么样"
    assert "不要" in desc and ("假装" in desc or "当成搜索结果" in desc), (
        "描述里没告诉 LLM【不许拿自己的知识假装搜索结果】"
    )
