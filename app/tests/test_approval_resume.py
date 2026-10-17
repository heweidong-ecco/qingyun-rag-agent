"""接管后续跑—— 硬门 D 的最后一段。

**核心判据**（`通用/四硬门 §3-D`）：**接管后会话【上下文连续】** —— 不是重开一轮。
"""
import asyncio

import routing.api_v1_agent as m
import agent.pending_approvals as pa
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from access.session_key import session_key


class _Snap:
    """`get_state` 返回值的替身。

    **真货有 `.next` 和 `.values["messages"]` 两样**（`StateSnapshot`）——
       `/agent/approve` 两样都读：`.next` 判"停没停在审批点"，
       `.values["messages"][-1].tool_calls` 取"卡住的那几个工具调用"。
       ⇒ 假货少一样，用例就会以 `AttributeError` 红掉，而不是以判据红掉（**看着像红，其实没测到东西**）。
    """

    def __init__(self, next_node, messages):
        self.next = next_node
        self.values = {"messages": messages}


# 卡在审批点的那条消息（真货形状：**只有 `tool_calls`、没有文字**）。
_ASKING = AIMessage(content="", tool_calls=[
    {"name": "web_search", "args": {"query": "x"}, "id": "call_1"}])


class _FakeGraph:
    """假图：**记录被怎么调用**，并按剧本返回一个像样的 result。

    `next_after`：放行**之后** `get_state.next` 是什么。
       它默认是 ``（= 走完了）；`test_resume_that_stops_again…` 那类要的是 `("approval",)`。
       改前假图只有一个恒定的 `next` ⇒ **"放行前"和"放行后"长得一模一样**，
       而 `/agent/approve` 修复后**两处都要读**（B5 的"该不该注销"判据）⇒ 必须分开。
    """

    def __init__(self, next_node=("approval",), next_after=()):
        self.calls = []
        self.configs = []
        self._invoked = False
        self._before = _Snap(next_node, [HumanMessage(content="Q1"), _ASKING])
        # 放行后：孤儿 `tool_calls` 被配对上了（这正是修复后的形状）。
        self._after = _Snap(next_after, [
            HumanMessage(content="Q1"), _ASKING,
            ToolMessage(content="【人工接管】裁定", tool_call_id="call_1", name="web_search"),
            AIMessage(content="续跑后的最终答案"),
        ])

    def get_state(self, config):
        return self._after if self._invoked else self._before

    def update_state(self, config, values=None, **kw):
        # 吞掉 `as_node` 等关键字（真货会收）—— 这里只记 `values`，形状由 ⑤ 节的真图用例钉。
        self.calls.append(("update_state", values))

    def invoke(self, arg, config):
        self.calls.append(("invoke", arg))
        self.configs.append(config)
        self._invoked = True
        return {"messages": [AIMessage(content="续跑后的最终答案")]}

    # --- 断言助手 ---
    def updates(self):
        return [v for (k, v) in self.calls if k == "update_state"]

    def invoked_with(self):
        return [a for (k, a) in self.calls if k == "invoke"]


def _approve(monkeypatch, fake, **kw):
    """装上假图，同步跑一遍端点（本仓没有 pytest-asyncio，异步只能手跑）。

    用 `monkeypatch` 而**不是** `m.agent_graph = fake`：
       直接赋模块属性**不会还原** ⇒ 假图会泄漏到**同一次 pytest 会话的其它文件**里
       （别的用例 import 的是同一个模块对象）。那种污染**不报错**，只会让别处莫名变绿/变红。
    """
    monkeypatch.setattr(m, "agent_graph", fake)
    kw.setdefault("thread_id", "t1")
    kw.setdefault("approved", True)
    kw.setdefault("user_name", "admin")
    #    反查属主**、再按属主拼 checkpoint 键、并按「本人或 admin」校验。
    #    ⇒ 这些用例必须先**登记**（改动前 approve 不看队列，所以不用登记）。
    #    键也变了：登记用的是 `session_key(属主, thread_id)`，不是裸 `thread_id`。
    pa.clear()
    pa.register(session_key(kw["user_name"], kw["thread_id"]), kw["user_name"], [],
                raw_thread_id=kw["thread_id"], graph="agent_graph")
    return asyncio.run(m.approve_agent_action(**kw))


# ==================== ① 续跑形状（回归守卫） ====================

def test_resume_invokes_with_none_not_a_new_message(monkeypatch):
    """**续跑必须 `invoke(None, config)`**。

    **这条钉的是【现有】正确行为，不是新功能** —— 改 `edited_answer` 之前它就该绿。
       留着它是因为**改错的方向太容易发生**（顺手改成"喂一条新消息"），而**返回看着没差别**。

    若某天有人改成 `invoke({"messages": [HumanMessage(question)]}, …)`，
       那就是**重开一轮** —— 上下文断了，而**接口返回一切正常**。
    """
    fake = _FakeGraph()
    _approve(monkeypatch, fake)
    assert fake.invoked_with() == [None], (
        f"续跑没有用 None 续跑（= 从 checkpoint 继续）：{fake.calls}"
    )


# ==================== ② 续跑用的是**这条**会话（回归守卫） ====================

def test_resume_uses_the_requested_thread_id(monkeypatch):
    """续跑必须带上**请求里那个** `thread_id`。

    **为什么这半条不比上面弱**：`invoke(None, config)` 只有在 `config` 指向**同一个 thread**
       时才等于"接着上次跑"。若 config 里的 thread_id 被写错 / 写死 / 丢掉，
       图会**从空状态重开**，或**接到别人的会话上** —— 两种都**不会报错**。
       ⇒ `None` 那半条保证"是续跑"，这半条保证"续的是**这一条**"。

    也是**钉现有行为**的守卫（改动前就该绿）。
    """
    fake = _FakeGraph()
    _approve(monkeypatch, fake, thread_id="thread-abc")
    assert fake.configs, "一次都没 invoke"
    got = [c.get("configurable", {}).get("thread_id") for c in fake.configs]
    #    这条**仍然**在守"续的是**这一条**" —— 只是"这一条"现在**带上了人**：
    #       裸 `thread_id` 会让两个用户指向**同一个** checkpoint（丙段修的正是它）。
    assert got == [session_key("admin", "thread-abc")], (
        f"续跑用的不是「属主 + 请求里的 thread_id」拼出来的键：{got}"
    )


# ==================== ③ 改写后提交（B6 的新东西） ====================

def test_edit_note_is_written_into_state(monkeypatch):
    """人工改写后提交：**改的那句要进 state**，否则"改了等于没改"。

    为什么必须走 `update_state` 而不是"直接把答案当返回值吐出去"：
       后者会让**后续节点看不到这个改写**（它不在 messages 里）——
       我们这条链上审批之后还要去 `tools` ⇒ 改写必须进**状态**，不能只在**响应**里。
    """
    fake = _FakeGraph()
    _approve(monkeypatch, fake, edited_answer="人工改过的答案")
    assert any("人工改过的答案" in str(v) for v in fake.updates()), (
        f"edited_answer 没有被写进 state：{fake.updates()}"
    )


def test_edited_answer_is_a_tool_message_not_a_human_one(monkeypatch):
    """写进去的必须是 **`ToolMessage`**（带 `tool_call_id`），不是 `HumanMessage`、也不是 `AIMessage`。

    **口径变更（端到端验收）** —— 本条原名
       `test_edited_answer_is_an_ai_message_not_a_human_one`，钉的是「必须是 `AIMessage`」。
       真机跑下来**那个形状是错的**（`api_v1_agent._tool_rulings` 的 docstring 记了两种错）：
         · 卡住的那条 `AIMessage(tool_calls=[…])` **永远等不到配对的 `ToolMessage`**
           ⇒ 真模型下一轮直接 400（`must be followed by tool messages`）；
         · `update_state` 把新塞的 `AIMessage` 认成 `agent` 的输出 ⇒ 条件边重算
           ⇒ **图当场 END**（实测 0.017s 返回、`answer` 是空串、`tools`/`agent` 一个都没跑）。
       ⇒ 新口径：**回填 `ToolMessage`**（每个 `tool_call_id` 一条）**+ 显式 `as_node="tools"`**。

    原测试要防的那件事仍在防、而且防得更死：
       目标是「别把**人给的结论**伪装成**用户新提的问题**」——
       `ToolMessage` 同样不是 `HumanMessage`。⇒ 这条断言**没被削弱，是变强了**
       （`AIMessage` 那版还漏了上面那两种真机故障）。
    """
    fake = _FakeGraph()
    _approve(monkeypatch, fake, edited_answer="人工改过的答案")
    pushed = [msg for v in fake.updates()
              if isinstance(v, dict) for msg in v.get("messages", [])]
    assert pushed, f"改写没有任何消息进 state：{fake.updates()}"
    assert all(isinstance(msg, ToolMessage) for msg in pushed), (
        f"改写用的不是 ToolMessage：{[type(x).__name__ for x in pushed]}"
    )
    # 单说"是 ToolMessage"还不够 —— **没配 `tool_call_id` 的 ToolMessage 一样是孤儿**。
    assert [msg.tool_call_id for msg in pushed] == ["call_1"], (
        f"ToolMessage 没和卡住的 tool_call_id 配对：{[m.tool_call_id for m in pushed]}"
    )
    assert any("人工改过的答案" in msg.content for msg in pushed), (
        f"人给的结论没进 state：{[m.content for m in pushed]}"
    )


def test_edited_answer_is_ignored_when_rejected(monkeypatch):
    """**拒绝时带 `edited_answer` ⇒ 不许写进 state**（那条路是"别做了"，不是"按我说的做"）。

    这是**反面**用例：只测"批准时会写"会漏掉"拒绝时也写了"——
       而那种错**不报任何错**，只是让模型收到一条自相矛盾的 state。
    """
    fake = _FakeGraph()
    _approve(monkeypatch, fake, approved=False, edited_answer="人工改过的答案")
    assert not any("人工改过的答案" in str(v) for v in fake.updates()), (
        f"拒绝分支把 edited_answer 也写进去了：{fake.updates()}"
    )


# ==================== ④ 没停在审批点时（回归守卫） ====================

def test_not_at_approval_point_does_not_invoke(monkeypatch):
    """图没停在审批点 ⇒ 早退（`status: error`）∧ **一次都不许 invoke**。

    若这里 invoke 了，就是**从任意状态硬接着跑** ⇒ 可能把一条已经跑完的会话再跑一遍。
    """
    fake = _FakeGraph(next_node=())
    out = _approve(monkeypatch, fake, thread_id="never-stopped")
    assert out.get("status") == "error", out
    assert fake.invoked_with() == [], f"没停在审批点却 invoke 了：{fake.calls}"


# ==================== ⑤ 真图 + 假 LLM：三条出口之后 state 【还合法吗】 ====================

def _orphan_tool_calls(messages):
    """返回**带 `tool_calls` 却没有配对 `ToolMessage`** 的那些消息（合法时为空列表）。

    这是**真模型的硬约束**，不是本仓自定的风格：OpenAI 兼容接口会直接 400。
    """
    orphans = []
    for i, msg in enumerate(messages):
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            continue
        want = {c["id"] for c in calls if c.get("id")}
        got = {m.tool_call_id for m in messages[i + 1:] if isinstance(m, ToolMessage)}
        if want - got:
            orphans.append((i, sorted(want - got)))
    return orphans


class _StubTool:
    """替掉真 `web_search`：测试不许联网。"""

    def invoke(self, args):
        return f"（桩）搜索「{args.get('query', '')}」的假结果"


class _ScriptedLLM:
    """按剧本吐 chunk，并记录**每次收到的 messages**（真模型收到什么，这里就记什么）。

    `script` 逐次取用：`"tool"` = 这一轮要求调 `web_search`（敏感 ⇒ 触发审批）；
    `"text"` = 这一轮直接给最终答复。**用完后一直按最后一个元素重复。**
    """

    def __init__(self, script):
        self.script = list(script)
        self.n = 0
        self.seen = []

    def stream(self, messages, config=None, **kw):
        self.seen.append(list(messages))
        kind = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        if kind == "tool":
            yield AIMessageChunk(content="", tool_call_chunks=[
                {"name": "web_search", "args": '{"query":"x"}',
                 "id": f"call_{self.n}", "index": 0}])
        else:
            yield AIMessageChunk(content=f"最终答案#{self.n}")


def _real_graph(monkeypatch, script):
    """装一块**真图**（独立 MemorySaver）+ 假 LLM + 桩工具。"""
    import agent.agent_graph as ag

    llm = _ScriptedLLM(script)
    monkeypatch.setattr(ag, "llm_with_tools", llm)
    #    它会真去连 mem0（embedding 走外网：CI 里 key 是占位值 ⇒ 401；
    #    本机则会**真花钱**且依赖 Redis 缓存碰巧命中 ⇒ 本地绿、CI 红）。
    import agent.memory_store as _ms
    monkeypatch.setattr(_ms, "search_user_memory", lambda *a, **k: [])
    monkeypatch.setitem(ag.TOOLS_BY_NAME, "web_search", _StubTool())
    return ag.build_agent_graph(), llm


def _cfg(thread_id):
    return {"configurable": {"thread_id": session_key("admin", thread_id)}}


def _stop_at_approval(graph, thread_id):
    """把一条会话开到审批点（= `/agent/langgraph_chat` 干的事）。"""
    graph.invoke({"messages": [HumanMessage(content="Q1")]}, _cfg(thread_id))
    pa.clear()
    pa.register(session_key("admin", thread_id), "admin", [],
                raw_thread_id=thread_id, graph="agent_graph")


def _approve_real(monkeypatch, graph, **kw):
    monkeypatch.setattr(m, "agent_graph", graph)
    kw.setdefault("thread_id", "t-real")
    kw.setdefault("approved", True)
    kw.setdefault("user_name", "admin")
    return asyncio.run(m.approve_agent_action(**kw))


def test_edited_approval_leaves_no_orphan_tool_call(monkeypatch):
    """改写后放行 ⇒ state 里**不许留孤儿 `tool_calls`**（否则真模型下一轮直接 400）。

    实测（真服务）：改写走完，state 是
    `[Human(Q1), AIMessage(tool_calls=[web_search]), AIMessage(改写)]`
    —— 中间那条 `tool_calls` **永远等不到配对** ⇒ 同 thread 再问一句 ⇒ 500。
    """
    graph, _ = _real_graph(monkeypatch, ["tool", "text"])
    _stop_at_approval(graph, "edit-1")
    _approve_real(monkeypatch, graph, thread_id="edit-1", edited_answer="人工写死的结论")
    msgs = graph.get_state(_cfg("edit-1")).values["messages"]
    assert _orphan_tool_calls(msgs) == [], (
        f"改写之后 state 里有孤儿 tool_calls：{_orphan_tool_calls(msgs)}；"
        f"messages={[type(x).__name__ for x in msgs]}"
    )


def test_rejected_approval_leaves_no_orphan_tool_call(monkeypatch):
    """拒绝后放行 ⇒ 同样**不许留孤儿 `tool_calls`**。

    实测（真服务）：拒绝走完，state 是
    `[Human(Q1), AIMessage(tool_calls=[web_search]), HumanMessage(内部指令)]`
    —— 同型缺陷；而且返回给调用方的是那句**内部指令原文**。
    """
    graph, _ = _real_graph(monkeypatch, ["tool", "text"])
    _stop_at_approval(graph, "rej-1")
    _approve_real(monkeypatch, graph, thread_id="rej-1", approved=False)
    msgs = graph.get_state(_cfg("rej-1")).values["messages"]
    assert _orphan_tool_calls(msgs) == [], (
        f"拒绝之后 state 里有孤儿 tool_calls：{_orphan_tool_calls(msgs)}"
    )


def test_thread_is_still_usable_after_edit_and_reject(monkeypatch):
    """**证真②**：三条出口之后，同一个 thread **还能接着问**（上下文连续）。

    这是端到端那条判据的**离线版**：真模型会 400，假 LLM 不会 ——
       所以这里直接核「**假 LLM 收到的 messages 没有孤儿**」，
       那正是真模型 400 的充要条件。
    """
    for label, kw in (("edit", {"edited_answer": "人工结论"}), ("reject", {"approved": False})):
        graph, llm = _real_graph(monkeypatch, ["tool", "text"])
        _stop_at_approval(graph, f"usable-{label}")
        _approve_real(monkeypatch, graph, thread_id=f"usable-{label}", **kw)
        graph.invoke({"messages": [HumanMessage(content="Q2")]}, _cfg(f"usable-{label}"))
        recv = llm.seen[-1]
        assert _orphan_tool_calls(recv) == [], (
            f"[{label}] 出口之后同 thread 再问，模型收到孤儿 tool_calls："
            f"{_orphan_tool_calls(recv)} ⇒ 真模型会 400"
        )


def test_edited_approval_actually_wakes_the_model(monkeypatch):
    """改写后必须**真的续跑**（模型被叫醒），不是"把改写当最终答案直接返回"。

    实测（真服务）：`approve` 返回耗时 **0.017s**、`answer` **= 输入原文** ——
    图在 `update_state` 之后**直接 END**，`tools` / `agent` 一个没跑。

    这正是 `app/routing/specs/api_v1_agent.md`  里那句
    「不能『跳过模型直接把这个答案返回』—— 那样后续节点（`tools`→`agent`）看不到它」
    所要求的形态 —— **而改前的实现做的恰恰是它禁止的那件事**。
    """
    graph, llm = _real_graph(monkeypatch, ["tool", "text"])
    _stop_at_approval(graph, "wake-1")
    out = _approve_real(monkeypatch, graph, thread_id="wake-1", edited_answer="人工写死的结论")
    assert len(llm.seen) == 2, f"改写之后模型没被叫醒（只调用了 {len(llm.seen)} 次 LLM）"
    heard = " ".join(str(getattr(x, "content", "")) for x in llm.seen[-1])
    assert "人工写死的结论" in heard, f"模型没收到人工的改写；它收到的是：{heard[:300]}"
    assert out.get("answer") != "人工写死的结论", (
        "把人工改写**原样**当最终答案返回了 ⇒ 图没有真的续跑"
    )


def test_resume_that_stops_again_is_re_registered(monkeypatch):
    """放行后**又**停在审批点 ⇒ 必须**重新入队**，不许留成"谁也放行不了"的孤儿。

    实测（真服务）：`approve` 返回 `answer=""`（末条是只有 tool_calls 的 AIMessage
    ⇒ 图**又**停在审批点），而结尾的 `resolve` **无条件**把队列清了
      · `/agent/pending` 查不到它
      · 再 `/agent/approve` 报「当前没有等待审批的任务」
      · 同 thread 再问 ⇒ 400
    ⇒ **只有"真的走完了"才许 `resolve`**。
    """
    graph, _ = _real_graph(monkeypatch, ["tool", "tool", "text"])
    _stop_at_approval(graph, "again-1")
    out = _approve_real(monkeypatch, graph, thread_id="again-1")
    still = [r for r in pa.list_pending() if r.get("raw_thread_id") == "again-1"]
    assert still, (
        "放行后图仍停在审批点，但队列里已经没有它了 ⇒ 孤儿会话（谁也放行不了）；"
        f"approve 返回：{out}"
    )


def test_resume_that_finishes_clears_the_queue(monkeypatch):
    """反面：**真的走完了** ⇒ 队列必须清掉，不许留假待办。

    与上一条是**一对** —— 只测"该留的留"会放过"该清的没清"（队列里全是陈条）。
    """
    graph, _ = _real_graph(monkeypatch, ["tool", "text"])
    _stop_at_approval(graph, "clean-1")
    _approve_real(monkeypatch, graph, thread_id="clean-1")
    left = [r for r in pa.list_pending() if r.get("raw_thread_id") == "clean-1"]
    assert not left, f"会话已经跑完，队列里还留着它：{left}"


# ==================== ⑥ 审批轮次上限（§六·2） ====================


def test_first_stop_registers_round_one(monkeypatch):
    """首次登记 ⇒ 轮次 = **1**。

    这条钉的是**计数的起点** —— 起点错了，整个上限就**差一档**，而且**不报错**。
    """
    graph, _ = _real_graph(monkeypatch, ["tool", "text"])
    _stop_at_approval(graph, "rounds-1")
    rec = [r for r in pa.list_pending() if r.get("raw_thread_id") == "rounds-1"]
    assert rec, "首次停在审批点后队列里没有它"
    assert rec[0].get("rounds") == 1, f"首次登记的轮次不是 1：{rec}"


def test_each_requeue_bumps_the_round(monkeypatch):
    """每次都停在审批点 ⇒ 轮次必须**逐次 +1**（否则上限永远够不着）。"""
    graph, _ = _real_graph(monkeypatch, ["tool", "tool", "text"])
    _stop_at_approval(graph, "bump-1")                                # rounds = 1
    out1 = _approve_real(monkeypatch, graph, thread_id="bump-1")      # 又停 ⇒ rounds = 2
    assert out1.get("status") == "pending_approval", out1
    rec = [r for r in pa.list_pending() if r.get("raw_thread_id") == "bump-1"]
    assert rec, "重新入队后队列里没有它"
    assert rec[0].get("rounds") == 2, f"重新入队后轮次没有 +1：{rec}"


def test_cap_forces_finish_instead_of_requeueing(monkeypatch):
    """到上限之后再停 ⇒ **不再入队**，改为**注入收尾裁定 + 续跑**，让会话真的走完。

    剧本：模型连续 4 次要求敏感工具，第 5 次才收口。
    ⇒ 人工批 3 次（= 上限）后第 4 次停 = **触顶** ⇒ 强制收尾。
    """
    graph, _ = _real_graph(monkeypatch, ["tool", "tool", "tool", "tool", "text"])
    _stop_at_approval(graph, "cap-1")                                 # rounds = 1
    assert _approve_real(monkeypatch, graph, thread_id="cap-1").get("status") == "pending_approval"
    assert _approve_real(monkeypatch, graph, thread_id="cap-1").get("status") == "pending_approval"
    out = _approve_real(monkeypatch, graph, thread_id="cap-1")        # 触顶 ⇒ 强制收尾
    assert out.get("forced_finish") is True, f"触顶时没有强制收尾：{out}"
    assert out.get("status") == "approved", out
    left = [r for r in pa.list_pending() if r.get("raw_thread_id") == "cap-1"]
    assert not left, f"触顶强制收尾后不该再入队，队列里还有：{left}"
    assert graph.get_state(_cfg("cap-1")).next == (), "强制收尾后图仍没有走完"


def test_cap_gives_up_if_the_model_still_asks(monkeypatch):
    """反面：触顶收尾后模型**还是**要敏感工具 ⇒ 返回 `error`、**不再入队**。

    没有这一条，"封顶"就可能退化成"换个姿势继续无限循环"。
    """
    graph, _ = _real_graph(monkeypatch, ["tool"])                     # 永远要求
    _stop_at_approval(graph, "giveup-1")
    _approve_real(monkeypatch, graph, thread_id="giveup-1")
    _approve_real(monkeypatch, graph, thread_id="giveup-1")
    out = _approve_real(monkeypatch, graph, thread_id="giveup-1")
    assert out.get("status") == "error", f"触顶且模型仍要求工具时应报 error：{out}"
    left = [r for r in pa.list_pending() if r.get("raw_thread_id") == "giveup-1"]
    assert not left, f"放弃之后不许再入队（否则又是无限循环）：{left}"
