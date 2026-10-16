"""接线守卫：四条执行路径共用的那四个工具，**真的带缓存**。

判据不是"文件里有 `cached_tool` 几个字" —— 那是**动作**，不是**结果**。
   这里是真调一次（调的是 `mcp_server.TOOLS` 里那个 `@tool` 对象，**和 Agent 用的是同一个**），
   看**缓存有没有被写进去**。
"""
import types

import pytest

import tools.tool_cache as T

_BING_HTML = """
<html><body><ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/a">标题甲</a></h2>
    <div class="b_caption"><p>摘要甲</p></div>
  </li>
</ol></body></html>
"""


class _CountingRedis:
    """只实现 `tool_cache` 用到的四个方法，并**记下写次数**。

    `nx=True` 的那次（抢锁）**不算写** —— 它不计入 `set_calls`，
       否则"TTL=0 直通"那条用例量到的就不是"缓存写没写"了。
    """

    def __init__(self):
        self.store = {}
        self.set_calls = 0

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx:
            if k in self.store:
                return False
            self.store[k] = v
            return True
        self.set_calls += 1
        self.store[k] = v
        return True

    def delete(self, k):
        self.store.pop(k, None)


@pytest.fixture
def fake(monkeypatch):
    f = _CountingRedis()
    monkeypatch.setattr(T, "redis_client", f)
    return f


def _tool(name):
    """取 `mcp_server.TOOLS` 里那个 `@tool` 对象 —— **和 Agent 拿到的是同一个**。"""
    from tools.mcp_server import TOOLS

    return {t["func"].name: t["func"] for t in TOOLS}[name]


def test_calculator_is_cached(fake):
    """`calculator` 调完该把结果写进缓存；第二次走缓存。"""
    calc = _tool("calculator")

    assert calc.invoke({"expression": "1+1"}) == "2"
    assert fake.set_calls >= 1, "calculator 调完该把结果写进缓存"
    assert calc.invoke({"expression": "1+1"}) == "2"


def test_date_today_is_not_cached(fake):
    """`date_today` TTL=0 ⇒ 一个字节都不该写进去（跨天返昨天的病根）。"""
    _tool("date_today").invoke({"query": ""})

    assert fake.set_calls == 0, "date_today ⛔ 不该被缓存"


def test_execute_python_is_not_cached(fake):
    """`execute_python` TTL=0 ⇒ 不缓存（同一段码可能依赖外部状态）。"""
    out = _tool("execute_python").invoke({"code": "print(1)"})

    assert "1" in out
    assert fake.set_calls == 0, "execute_python 的结果⛔ 不该被缓存"


def test_web_search_success_is_cached(fake, monkeypatch):
    """**反向对照** —— 成功的搜索**必须**进缓存。

    没有这条，一个把 `should_cache` 写成恒 `False` 的实现也能让下面那条绿 ——
       那正是本仓反复栽的「**尺子量不到它该量的事**」。
    """
    import tools.search_tools as S

    class _Resp:
        text = _BING_HTML

        def raise_for_status(self):
            pass

    monkeypatch.setattr(S, "httpx", types.SimpleNamespace(get=lambda *a, **k: _Resp()))

    out = _tool("web_search").invoke({"query": "甲"})

    assert out.startswith("搜索「甲」的结果"), out[:80]
    assert "https://example.com/a" in out
    assert fake.set_calls >= 1, "成功的搜索结果该进缓存"


def test_web_search_failure_is_not_cached(fake, monkeypatch):
    """「搜不到」是**返回值**、不是异常 ⇒ 必须靠 `should_cache` 挡掉。

    不挡的后果：一次网络抖动被缓存 **300 秒** ⇒ 那 5 分钟里**谁都搜不到东西**。
    """
    import tools.search_tools as S

    def _boom(*a, **k):
        raise ConnectionError("网络不通")

    monkeypatch.setattr(S, "httpx", types.SimpleNamespace(get=_boom))

    out = _tool("web_search").invoke({"query": "x"})

    assert out.startswith("搜索失败"), out[:80]
    assert fake.set_calls == 0, "失败结果⛔ 不许进缓存"
