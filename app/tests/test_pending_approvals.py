"""待接管队列。不碰 DB / Redis / 网络 —— 纯内存注册表。

**为什么需要这张表**：`MemorySaver`（`agent_graph.py`）**没有"列出所有 thread"的 API**
   ⇒ **没法从 checkpoint 反查"谁卡在审批"** ⇒ 只能自己记账。
"""
import agent.pending_approvals as pa


def test_register_then_list():
    pa.clear()                                   # 测试隔离
    pa.register("t1", "admin", [{"name": "web_search", "args": {"q": "x"}}])
    rows = pa.list_pending()
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "t1"
    assert rows[0]["user_name"] == "admin"
    assert "since" in rows[0]


def test_resolve_removes_it():
    """批完必须注销 —— 否则它会**永远留在队列里**，变成假待办。"""
    pa.clear()
    pa.register("t2", "admin", [])
    pa.resolve("t2")
    assert pa.list_pending() == []


def test_resolve_unknown_thread_is_a_noop():
    """重复注销 / 注销不存在的 ⇒ **不许抛异常**（`/agent/approve` 会调到）。"""
    pa.clear()
    pa.resolve("never-registered")               # 不应抛


def test_register_twice_keeps_latest():
    """同一 thread 再次登记 ⇒ 更新，不产生两条。"""
    pa.clear()
    pa.register("t3", "admin", [{"name": "a", "args": {}}])
    pa.register("t3", "admin", [{"name": "b", "args": {}}])
    rows = pa.list_pending()
    assert len(rows) == 1 and rows[0]["tool_calls"][0]["name"] == "b"


# ==================== 以下 3 条【不在计划里】—— 理由见各自 docstring ====================

def test_list_pending_is_ordered_oldest_first(monkeypatch):
    """**"卡得最久的排最前"是写进 docstring 的契约，却没人守**。

    它是**给人看队列用的**：最该先处理的排最前。排错了**不会报错**，
       只会让人**从最不紧急的那条开始处理**。

    实现靠 `time.time` ⇒ 测试里必须**把时钟捏住**，
       否则两条登记在同一毫秒内 ⇒ **顺序变成 dict 插入序**（碰巧对，测试却没在测东西）。
    """
    pa.clear()
    ticks = iter([1000.0, 2000.0])          # 后登记的【更晚】⇒ 应该排在【后面】
    monkeypatch.setattr(pa.time, "time", lambda: next(ticks))

    pa.register("late", "admin", [])        # since = 1000.0 其实是先登的，改个名避免读混
    pa.register("early", "admin", [])       # since = 2000.0

    rows = pa.list_pending()
    # since 小的 = 卡得久的 ⇒ 排最前。第一条 since=1000.0 ⇒ "late" 其实最早
    assert [r["thread_id"] for r in rows] == ["late", "early"]
    assert rows[0]["since"] < rows[1]["since"]


def test_warn_if_backend_mismatch_fires_on_sqlite(monkeypatch, capsys):
    """落盘后端 + 内存队列 = **重启后图还在等审批、队列里却查不到** ⇒ 会话变孤儿。

    这个组合**不会自己报错** —— 要等到有人重启后去队列里找，才发现找不到。
       ⇒ 唯一的防线就是**启动时那条警告**，所以它必须有人守。
    """
    monkeypatch.setenv("AGENT_CHECKPOINT_BACKEND", "sqlite")

    pa.warn_if_backend_mismatch()

    out = capsys.readouterr().out
    assert "pending_approvals" in out
    assert "sqlite" in out


def test_warn_if_backend_mismatch_is_silent_by_default(monkeypatch, capsys):
    """反向：**默认（内存 checkpointer）本来就是一致的** ⇒ 不许报。

    与上一条配成一对：只测"设了会响" ⇒ **可能误报**（比如判据写反、恒响）。
       恒响的警告在日志里 = **等于没有**，还会盖掉真警告。
    """
    monkeypatch.delenv("AGENT_CHECKPOINT_BACKEND", raising=False)

    pa.warn_if_backend_mismatch()

    assert capsys.readouterr().out == ""
