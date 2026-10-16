"""**LangGraph 那条链补的「长期记忆注入」—— 接线判据**。
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent import agent_graph, memory_store

#: 假模型回的答案 —— 内容不重要，本文件量的是**喂进去的 messages**。
_ANSWER = "好"


class _Recorder:
    """假模型：**记下**每一次 `stream` 收到的 messages，回一条最普通的 `AIMessage`。

    用 `AIMessage` 而不是 chunk：`agent_decide` 用 `+` 聚合，**单块也能聚合**
       （`AIMessageChunk.__add__` 的语义对单块就是它自己）⇒ 这里不必造碎片。
    名字就叫 `llm_with_tools` 那块的位置 —— 本文件是**整个替换**它，不 bind。
    """

    def __init__(self):
        self.seen = []          # 每次 stream 收到的 messages 列表

    def stream(self, messages, config=None):
        self.seen.append(list(messages))
        return iter([AIMessage(content=_ANSWER)])


def _allow(monkeypatch, *, budget_ok=True):
    """把本节点周围那三件外部件打桩：预算门 · 记账 · 假模型。

    前两件必须打 —— 否则会**真连库 / 真花钱**：`check_token_budget` 读 PG，
       `record_from_response` 写账。
    """
    monkeypatch.setattr(agent_graph, "check_token_budget", lambda *a, **k: budget_ok)
    monkeypatch.setattr(agent_graph, "record_from_response", lambda *a, **k: None)
    fake = _Recorder()
    monkeypatch.setattr(agent_graph, "llm_with_tools", fake)
    return fake


def _counter(monkeypatch, memories):
    """把 `search_user_memory` 换成"回固定结果 + 记调用次数"。打的是**它真正的家**
    （`agent.memory_store`）—— `memory_block` 就在那个模块里按模块全局名字找它。"""
    calls = {"n": 0}

    def _fake(user_id, query, top_k=3):
        calls["n"] += 1
        calls["user_id"] = user_id
        calls["query"] = query
        return list(memories)

    monkeypatch.setattr(memory_store, "search_user_memory", _fake)
    return calls


def _state(messages):
    """本图入口要的最小 state（`user_name` / `thread_id` 在图里是 `.get(…)` 读的）。"""
    return {"messages": messages, "user_name": "alice", "thread_id": "t1"}


# ═══════════════════════════ ① 有记忆 ⇒ 注入在最前面 ═══════════════════════════

def test_有记忆时_拼在模型输入的最前面(monkeypatch):
    """主断言：检索到的记忆**必须真的进模型输入**，且**排在最前**（在用户那句话之前）。"""
    calls = _counter(monkeypatch, ["我叫张三，只看 A 类报告"])
    fake = _allow(monkeypatch)

    agent_graph.agent_decide(
        _state([HumanMessage(content="帮我算个东西")]), config={})

    assert calls["n"] == 1, "第一步应当检索一次记忆"
    assert calls["user_id"] == "alice:default", (
        f"检索身份拼错了：{calls['user_id']!r} —— 本图 state 里没有 memory_space ⇒ 应取 default")
    assert calls["query"] == "帮我算个东西", (
        f"检索用的查询词应当是【最后一条用户消息】：{calls['query']!r}")

    sent = fake.seen[0]
    assert isinstance(sent[0], SystemMessage), (
        f"第一条不是 SystemMessage ⇒ 记忆没拼进去（拿到的是 {type(sent[0]).__name__}）")
    assert "张三" in sent[0].content, "记忆正文没进 prompt"
    assert isinstance(sent[1], HumanMessage) and sent[1].content == "帮我算个东西", (
        "用户那句话应当排在记忆【之后】")


# ═══════════════════════════ ② 没有记忆 ⇒ 一个字节都不变 ═══════════════════════════

def test_没有记忆时_交给模型的还是原来那串(monkeypatch):
    """**反证 / 防空跑**：一条记忆都没检索到 ⇒ 交出去的 messages 与 state 里那份**逐字相同**。

    这条一红就说明上面那条**恒为真**（不管有没有记忆都拼 SystemMessage）——
       而那正是"改了行为却看不出来"的形态。
    """
    _counter(monkeypatch, [])
    fake = _allow(monkeypatch)

    origin = [HumanMessage(content="帮我算个东西")]
    agent_graph.agent_decide(_state(origin), config={})

    sent = fake.seen[0]
    assert sent == origin, f"没有记忆时应当原样透传，实得 {sent!r}"
    assert not any(isinstance(m, SystemMessage) for m in sent), "没有记忆却拼了 SystemMessage"


# ═════════════════ ②b 记忆取不到 ⇒ 只降级，不许打挂整条链 ═════════════════

def test_记忆检索失败时_只降级不抛(monkeypatch):
    """**记忆是锦上添花，不许成为承重件** —— 它失败只该少一段上下文。

    这条是拿一次**真实的 CI 全红**换来的：本图补上这个调用之后，
    CI 里 **14 条"驱动真图"的用例全红** —— 根因是 CI 的 DashScope key 是**占位值**
    检索抛 401 ⇒ **异常从图节点里穿出来** ⇒ 整条请求挂。
    **本机看不见这一次**：本机有真 key ＋ Redis 里有 embedding 缓存（同问句早算过）
    ⇒ **本地的绿灯证明不了这条路径**（这一条值得单记：它是"环境差异"型的漏网）。

    **反证**：把 `memory_block` 里那个 `try/except` 去掉 ⇒ 本条当场红。
    """
    def _boom(*a, **k):
        raise RuntimeError("401 Incorrect API key（模拟 CI 的占位 key）")

    monkeypatch.setattr(memory_store, "search_user_memory", _boom)
    fake = _allow(monkeypatch)

    origin = [HumanMessage(content="帮我算个东西")]
    out = agent_graph.agent_decide(_state(origin), config={})   # 不许抛

    assert fake.seen[0] == origin, "检索失败时应当**原样透传**（不注入任何东西）"
    assert out["messages"], "这一步仍要产出模型那条消息（链子照常往下走）"


# ═══════════════════════════ ③ 只注入一次 ═══════════════════════════

def test_第二步以后不再检索_也再不注入(monkeypatch):
    """同一任务里**只检索一次** —— 判据 = state 里已经有 `ToolMessage`（工具回过话了）。

    代价要写清：记忆**不进 `state`** ⇒ 第二步以后模型**看不到它**（这是有意的取舍，
       换来的是"一次任务只花一次 embedding"）。改成"整条任务都带"= 往 state 里写东西
       = 改 checkpoint 内容，属另一件事。
    """
    calls = _counter(monkeypatch, ["我叫张三"])
    fake = _allow(monkeypatch)

    history = [
        HumanMessage(content="帮我算个东西"),
        AIMessage(content="", tool_calls=[{"name": "calculator", "args": {}, "id": "c1"}]),
        ToolMessage(content="42", tool_call_id="c1"),
    ]
    agent_graph.agent_decide(_state(history), config={})

    assert calls["n"] == 0, "已经调过工具了 —— 这一步⛔ 不该再检索记忆"
    assert fake.seen[0] == history, "第二步以后应当原样透传"


# ═══════════════════════════ ④ 排位：必须在预算门之后 ═══════════════════════════

def test_预算被拦时_一次记忆都不检索(monkeypatch):
    """**排位判据**：预算门拦下那一轮 ⇒ **一次 embedding 都不该花**（同族）。

    把注入那一段挪到 `check_token_budget` **之前** ⇒ 本条立刻红
       （被拦下的那轮会白花一笔 embedding，而结果当场被丢掉 —— 本仓栽过一次）。
    """
    calls = _counter(monkeypatch, ["我叫张三"])
    _allow(monkeypatch, budget_ok=False)

    out = agent_graph.agent_decide(
        _state([HumanMessage(content="帮我算个东西")]), config={})

    assert calls["n"] == 0, "预算已拦 ⇒ ⛔ 不许再去检索记忆（那会真花钱）"
    assert out["budget_intercept"], "被拦时应当把原因写进 state（端点据此回 429）"
