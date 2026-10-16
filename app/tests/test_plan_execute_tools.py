"""§十四 · N15 —— Plan-and-Execute **执行层真调用工具**的回归用例（2026-09-21）。

**本文件守的是那件最容易悄悄回退的事**：
   `execute_single_step` 曾经**只对 `calculator` 真调用**，其余**全部交给 LLM「模拟执行」** ——
   端点 `/agent/plan_execute` 看起来在跑真工具，**实际只有规划是真的**。

   这种回退**跑一下看不出**（模拟出来的结果也是一段合理文本），
   所以判据必须是**结构性的**：见 `test_execute_single_step_does_not_ask_the_llm`。
"""
import datetime

import pytest


def _step(tool: str) -> dict:
    return {"step": 1, "action": "测试步骤", "tool": tool}


# ===========================================================================
# 决定性用例：执行时**不许碰 LLM**
# ===========================================================================
def test_execute_single_step_does_not_ask_the_llm(monkeypatch):
    """**执行**单步时不能调 LLM —— 真调用与「请 LLM 模拟执行」的分水岭。

    判据：把 `executor_llm.invoke` 换成**一调就炸**的替身。
      · 真调用 ⇒ 三个工具**照样出结果**（根本不碰 LLM）
      · 旧实现（模拟）⇒ 立刻炸 ⇒ 本用例红

    这是那条「**跑一下看不出**」的回退的**唯一结构性判据**：
       模拟产出的也是一段像样的文本，光比对输出发现不了。
    """
    import agent.plan_execute as P

    class _BoomLLM:
        """一调就炸的假 LLM。不能直接 `setattr` 真 `executor_llm.invoke` ——
        它是 pydantic 模型，`monkeypatch.setattr` 会报 `object has no field "invoke"`。"""

        def invoke(self, *a, **k):
            raise AssertionError(
                "执行单步时调用了 LLM —— 说明又退回「请 LLM 模拟执行」了！"
                "真执行层必须走 MCP 注册表的 handler。"
            )

    monkeypatch.setattr(P, "executor_llm", _BoomLLM())

    assert P.execute_single_step(_step("calculator"), "3*4-5/6", "").startswith("11.1666")
    assert "今天是" in P.execute_single_step(_step("date_today"), "今天", "")
    assert P.execute_single_step(_step("execute_python"), "print(6*7)", "").strip() == "42"


# ===========================================================================
# 真调用返回的是【真结果】，不是"像样的文本"
# ===========================================================================
def test_calculator_returns_the_exact_arithmetic_result():
    """`calculator` 先前就是真调的 —— 这里把它钉住，防止重写时退化成模拟。"""
    import agent.plan_execute as P

    assert P.execute_single_step(_step("calculator"), "2**10", "") == str(2 ** 10)
    # 一个 LLM「模拟」几乎不会算对的式子
    assert P.execute_single_step(_step("calculator"), "123456789*987654321", "") == str(
        123456789 * 987654321
    )


def test_date_today_returns_the_real_today():
    """`date_today` 是**最能区分真假**的一个：
    LLM 并不知道"今天"是哪天（它的知识有截止日），**模拟必错**；真调用必对。
    """
    import agent.plan_execute as P

    out = P.execute_single_step(_step("date_today"), "今天", "")
    today = datetime.date.today()
    assert f"{today.year}年{today.month}月{today.day}日" in out, (
        f"返回的不是今天（{today}）：{out!r} —— 是不是又退化成让 LLM 模拟了？"
    )


def test_execute_python_actually_runs_the_code():
    """`execute_python` 真跑：钉的是**算出来**的那个数，不是"像样的文本"。
    **不能写 `import math`** —— 沙箱里 `__import__` 不在白名单（见下条用例）。
    """
    import agent.plan_execute as P

    code = "print(sum(i * i for i in range(100)))"
    expected = sum(i * i for i in range(100))
    assert P.execute_single_step(_step("execute_python"), code, "").strip() == str(expected)


def test_sandbox_modules_are_injected_not_importable():
    """**沙箱里【不能 `import`】—— 模块是直接注入全局域的。**

    实测（2026-09-21）：
      · `import math`            → `ImportError: __import__ not found`
      · 不 import，直接用 `math`  → `4.0`

    **为什么专门钉这条**：`execute_python` 的工具描述**曾经**写着
      「允许的模块：math, json, …」—— 那会让 LLM 写出 `import math` 然后**必炸**。
      本用例与 `test_tool_description_warns_against_import` 一起，
      把「描述 ↔ 实现」两边都锁住。
    """
    import agent.plan_execute as P

    assert "ImportError" in P.execute_single_step(
        _step("execute_python"), "import math\nprint(math.sqrt(16))", ""
    )
    assert P.execute_single_step(
        _step("execute_python"), "print(math.sqrt(16))", ""
    ).strip() == "4.0"


def test_tool_description_warns_against_import():
    """工具描述（**LLM 读的那份**）必须明说「**不要 `import`**」，否则 LLM 会照惯例写 import。

    这是 §三·A 那一类「**注释说 A、代码做 B**」的同型 —— 但后果更重：
       它直接导致 **LLM 写出来的代码跑不了**（而以前是"模拟执行"，看不出来）。
    """
    from tools.code_executor import execute_python

    desc = execute_python.description or ""
    assert "不要" in desc and "import" in desc, (
        "`execute_python` 的工具描述没提醒 LLM「不要 import」—— "
        "沙箱里 __import__ 不在白名单，LLM 写 `import math` 会直接炸。\n"
        f"当前描述：\n{desc}"
    )


# ===========================================================================
# 参数名从 MCP 注册表【派生】，不手写
# ===========================================================================
def test_tool_arg_field_is_derived_from_the_registry():
    """字段名必须来自工具自己的 `args_schema` —— 手写映射会与注册表漂移
    （这正是 §三·C1「工具清单手工维护必然漂」的同一个根因）。"""
    import agent.plan_execute as P
    from tools.mcp_server import TOOLS

    for t in TOOLS:
        fn = t["func"]
        expected = list(fn.args_schema.model_fields.keys())
        got = P._tool_arg_field(fn.name)
        if len(expected) == 1:
            assert got == expected[0], f"{fn.name}: 派生出 {got!r}，schema 是 {expected}"
        else:
            assert got is None, f"{fn.name}: 多字段工具应当返回 None（不猜参数名）"

    assert P._tool_arg_field("这个工具不存在") is None


def test_execute_single_step_wraps_the_value_into_the_schema_field(monkeypatch):
    """传给 handler 的必须是 `{schema 里的字段名: 值}` —— 键名错了真工具会报缺参。"""
    import agent.plan_execute as P

    seen = {}

    class _FakeHandler:
        def __call__(self, args):
            seen.update(args)
            return "OK"

    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", _FakeHandler())
    out = P.execute_single_step(_step("calculator"), "1+1", "")

    assert out == "OK"
    assert seen == {"expression": "1+1"}, f"传给 handler 的键不对：{seen}"


# ===========================================================================
# 失败路径：抛异常，不吞成字符串（否则重试机制静默失效）
# ===========================================================================
def test_unknown_tool_raises_instead_of_returning_a_string():
    """必须**抛异常** —— 上游 `execute_step_with_retry` 靠异常触发"重新生成输入再试"。
    吞成错误字符串会让它**看起来在重试、其实没有**。"""
    import agent.plan_execute as P

    with pytest.raises(ValueError, match="未找到工具"):
        P.execute_single_step({"step": 1, "action": "x", "tool": "根本没这个工具"}, "x", "")


def test_tool_exception_propagates(monkeypatch):
    """真工具自己抛错时，也要**透传**给上游的重试逻辑。"""
    import agent.plan_execute as P

    def _explode(args):
        raise RuntimeError("工具内部炸了")

    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", _explode)
    with pytest.raises(RuntimeError, match="工具内部炸了"):
        P.execute_single_step(_step("calculator"), "1+1", "")


# ===========================================================================
# 「降级」不许把失败原因吃掉 —— 否则「炸了」与「真跑出来」长得一样
# ===========================================================================
def test_downgraded_step_keeps_the_real_reason(monkeypatch):
    """工具的**降级分支**必须留下真实失败原因，不能只剩一句编好的兜底文案。

    **判据为什么是"那个原因串在不在"，而不是"输出像不像失败"**：
       兜底文案「工具 X 当前不可用，使用备用策略生成结果。」**读起来像【有意降级】**，
       而它实际是 `except: pass` **吞掉的一个异常** —— **跑一下看不出**。
       只有断言**真实原因**（`工具内部炸了`）在里面，才能把这两者分开。

    **同一个洞的第三面**：`execute_single_step` 的 docstring（`:562`）明文写
       「**失败一律【抛异常】，不吞成错误字符串** —— 上游两条路都是靠异常工作的」，
       **并且点名了就是这个降级分支** —— 而**那个分支自己正是反面**。
       （另两面：`test_unknown_tool_raises_instead_of_returning_a_string`
         `test_tool_exception_propagates`）

    驱动方式（不碰真 LLM）：
      让每一步都"彻底失败" ⇒ 同一工具连败 3 次进 `failed_tools` ⇒ 下一步走降级分支。
    """
    import agent.plan_execute as P

    def _explode(args):
        raise RuntimeError("工具内部炸了")

    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", _explode)
    # 每步都判"彻底失败" ⇒ 把同一工具推到 3 连败
    # （2026-10-05）起要返回 `StepResult` —— 成败判定换成看 `.ok` 字段了。
    #    用 `*a, **k` 是为了**同时兼容**将来多传的位置参数（已经加过一处）。
    monkeypatch.setattr(
        P, "execute_step_with_quality_check",
        lambda *a, **k: P.StepResult(ok=False, text="执行失败（已重试1次）：假装失败",
                                     error="假装失败"))
    # 重规划不许真调 LLM：给一份**同样只用 calculator**的计划，把循环推到降级分支
    # （2026-10-05）：签名原来只有 `ctx` 一个参数 —— 那时它**正好**兜住了
    #    调用点漏传 `user_name`（少传一个 ⇒ 不报错）。修完之后调用点会传两个，
    #    所以这里必须**跟着收下** `user_name`。
    #    这条替身**本身就是那个洞的旁证**：它只接一个参数 ⇒ 发现不了漏传。
    monkeypatch.setattr(P, "plan_task",
                        lambda ctx, user_name="unknown": [_step("calculator") for _ in range(5)])

    out = P.execute_plan_with_replan([_step("calculator") for _ in range(5)], "目标", "u1")

    assert "工具内部炸了" in out, (
        "降级分支把真实失败原因吞了 ⇒ **兜底文案与真结果无法区分**。\n"
        f"实际输出：\n{out}"
    )


def test_plan_execute_has_no_bare_except():
    """结构判据：`plan_execute.py` 里**不许有裸 `except:`**（连 `KeyboardInterrupt` 都吞）。

    与上一条**互补**：上一条管"原因有没有留下"，这一条管"别把整个解释器的中断信号也吃掉"。
       两者**跑真实业务都看不出**，所以都得是**结构性**判据。
    """
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "agent/plan_execute.py").read_text(encoding="utf-8")
    bare = [
        node.lineno
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]
    assert not bare, f"plan_execute.py 存在裸 except（行 {bare}）—— 请写成 except Exception"


# ===========================================================================
# 代码围栏：LLM 很爱给 execute_python 包 ```，包着会直接语法错
# ===========================================================================
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("```python\nprint(1)\n```", "print(1)"),
        ("```\nprint(1)\n```", "print(1)"),
        ("print(1)", "print(1)"),
        ("  print(1)  ", "print(1)"),
    ],
)
def test_strip_code_fence(raw, expected):
    import agent.plan_execute as P

    assert P._strip_code_fence(raw) == expected


# ===========================================================================
# ③-b · 预算与记账 —— `plan_execute` 以前【完全不查预算、不记账】
# ===========================================================================
def _fake_llm(usage, content="ok"):
    class _R:
        pass

    class _L:
        model_name = "fake-model"

        def invoke(self, messages):
            r = _R()
            r.content = content
            r.usage_metadata = usage
            return r

    return _L()


def test_invoke_llm_raises_budget_exceeded(monkeypatch):
    """预算不足时**必须抛 `BudgetExceededError`** —— 端点据此报 QUOTA_EXCEEDED（而不是漏成 500）。

    这条守的是：`/agent/plan_execute` **以前根本不查预算** ⇒ 免费用户的每日配额管不到它。
    """
    import agent.plan_execute as P

    monkeypatch.setattr(P, "check_budget_before_call", lambda *a, **k: (False, "预算已用完"))

    called = []
    llm = _fake_llm({"input_tokens": 1, "output_tokens": 1})
    monkeypatch.setattr(llm, "invoke", lambda m: called.append(1))

    with pytest.raises(P.BudgetExceededError, match="预算已用完"):
        P._invoke_llm(llm, [], purpose="plan_execute.plan", user_name="u1")

    assert called == [], "预算不足时**不该**还去调 LLM（钱已经花出去了）"


def test_invoke_llm_records_real_usage(monkeypatch):
    """正常时按 `usage_metadata` 的【真实 token】记账（照 2 代 `/agent/mcp_chat` 的模式）。"""
    import agent.plan_execute as P

    monkeypatch.setattr(P, "check_budget_before_call", lambda *a, **k: (True, "充足"))
    recorded = {}
    monkeypatch.setattr(P, "record_usage", lambda **kw: recorded.update(kw))

    P._invoke_llm(
        _fake_llm({"input_tokens": 123, "output_tokens": 45}),
        [], purpose="plan_execute.quality_check", user_name="u1",
    )

    assert recorded["prompt_tokens"] == 123
    assert recorded["completion_tokens"] == 45
    assert recorded["purpose"] == "plan_execute.quality_check"
    assert recorded["user_name"] == "u1", "记账必须落到【发起人】头上，否则配额管不到他"
    assert recorded["model"] == "fake-model"


def test_invoke_llm_does_not_fabricate_usage(monkeypatch):
    """没有 `usage_metadata` 时**如实不记** —— 不编一个数字塞进账里。"""
    import agent.plan_execute as P

    monkeypatch.setattr(P, "check_budget_before_call", lambda *a, **k: (True, "充足"))
    recorded = []
    monkeypatch.setattr(P, "record_usage", lambda **kw: recorded.append(kw))

    P._invoke_llm(_fake_llm(None), [], purpose="plan_execute.plan", user_name="u1")

    assert recorded == [], "没有真实用量却记了账 —— 那是在【编数字】"


def test_plan_execute_entry_points_accept_user_name():
    """两个入口必须能收 `user_name` —— 否则记账落不到人头上，配额就是摆设。"""
    import inspect

    import agent.plan_execute as P

    for fn in (P.plan_task, P.execute_plan, P.execute_plan_with_replan):
        assert "user_name" in inspect.signature(fn).parameters, (
            f"{fn.__name__} 没有 user_name 形参 ⇒ 记账会落到 'unknown'，配额管不到任何人"
        )


# ===========================================================================
# ③ 的遗留 · LLM 调用自身的超时 + 整条计划的总时长上限
# ===========================================================================
def test_llm_clients_have_explicit_timeouts():
    """三个 LLM 客户端**必须显式设超时** —— 否则吃 openai SDK 默认的 `read=600s`。

    这条守的是「**③ 只补了工具调用、漏了 LLM 调用**」那半边：
       实测（2026-09-21）三个客户端都是 `timeout=None` ⇒ 吃 SDK 默认
       `connect=5s / read=600s / write=600s`，**且 SDK 还会自己 max_retries=2**
       ⇒ **一次调用最坏等 600 × (1+2) = 30 分钟**；而 `plan_execute` 最坏跑
       「3–7 步 × 每步重试 3 次 × 每步 2 次 LLM 调用」⇒ **理论上能挂几个小时**。

    这里**断言"非 None"而不是断言具体秒数** —— 秒数是可以调的参数，
       而"**必须显式**"才是要守的性质（回到 SDK 默认 = 回到 10 分钟）。
    """
    import agent.plan_execute as P

    for name in ("planner_llm", "executor_llm", "quality_checker_llm"):
        llm = getattr(P, name)
        assert llm.request_timeout is not None, (
            f"{name} 没设超时 ⇒ 会吃 openai SDK 默认的 read=600s（10 分钟）"
        )
        assert llm.max_retries is not None, (
            f"{name} 的 max_retries 是 None ⇒ 吃 SDK 默认 2 —— 最坏时长不可算"
        )


def test_plan_stops_when_total_budget_exceeded(monkeypatch):
    """**整条计划**要有总时长上限 —— 而且停下来时必须【如实说没跑完】。

    判据有两层，缺一不可：
      ① 真的停下来了（不是继续跑）
      ② 输出里**明说"结果不完整"** —— 不能让它看起来像正常结束
    """
    import agent.plan_execute as P

    # **不要用 `0`** —— 那是个**边界**不是"已超"：判据是 `elapsed > budget`，
    #    而第一次检查时 `elapsed ≈ 0`（`time.time()` 微秒精度，两次连续调用甚至可能同值）
    #    ⇒ `0 > 0` 为 False ⇒ 第一步照跑。用 `-1` 才是**确定的"已超"状态**。
    monkeypatch.setattr(P, "PLAN_TOTAL_BUDGET_SECONDS", -1)

    called = []
    monkeypatch.setattr(
        P, "execute_step_with_quality_check",
        lambda *a, **k: called.append(1) or "不该被调用",
    )

    out = P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "calculator", "input": "1+1"},
         {"step": 2, "action": "b", "tool": "calculator", "input": "2+2"}],
        "目标", "u1",
    )

    assert called == [], "总预算已超，却还执行了步骤"
    assert "超过总时长预算" in out, out
    assert "结果不完整" in out, f"停下来时没如实说明结果不完整：{out!r}"
    assert "2 个步骤未执行" in out, f"没报出剩余步骤数：{out!r}"


def test_dynamic_input_receives_the_real_user_name(monkeypatch):
    """`generate_dynamic_input` 必须收到**真实发起人**。

    这条是**端到端实测**才发现的问题（2026-09-21）：
       `token_usage_logs` 里 `plan_execute.dynamic_input` 的 **4 条全记在 `unknown` 头上**，
       而 `plan` / `quality_check` 的记在 `admin` 头上。
       ⇒ 后果：**这部分额度算不到发起人头上 ⇒ 配额管不住他**（正是 ③-b 要解决的问题）。

    **根因值得记住**：那次改动用的是**盲替换** `s.replace(old, new, 1)` ——
       **命中了注释里的同一串**（注释在前 ⇒ 先被替换），**真正的调用点反而没改到**。
       所以这条用例守的是「**调用点真的传了**」，
          不是「函数签名里有这个参数」（签名一直是对的）。

    也**不能**只靠 `token_usage_logs` 来守 —— 那要真连库。这里用替身把判据做成**离线的**。
    """
    import agent.plan_execute as P

    seen = {}

    def _fake_gen(step, context, user_goal, user_name="unknown"):
        seen["user_name"] = user_name
        return "假输入"

    monkeypatch.setattr(P, "generate_dynamic_input", _fake_gen)
    # （2026-10-05）起返回 `StepResult` —— 再返回裸字符串的话，
    #    调用方读 `.ok` 会 `AttributeError`（那就是**假红**，不是这条用例想测的东西）。
    monkeypatch.setattr(P, "execute_step_with_retry",
                        lambda *a, **k: P.StepResult(ok=True, text="假结果"))
    monkeypatch.setattr(P, "check_step_quality", lambda *a, **k: True)

    P.execute_step_with_quality_check(
        {"step": 1, "tool": "calculator", "action": "算一下"}, "", "目标", "someone_real"
    )

    assert seen.get("user_name") == "someone_real", (
        f"`generate_dynamic_input` 收到的是 {seen.get('user_name')!r} —— **不是真实发起人**。\n"
        "⇒ 这部分 token 会记在 `unknown` 头上 ⇒ 配额管不到真正用的人。\n"
        "⚠️ 检查是不是又漏传了（历史事故：盲替换命中了注释，真正的调用点没改到）。"
    )


# ===========================================================================
# ③ 遗留 #4 · 超时必须是【按实测校准】的 —— 既不能误杀，也不能吃光总预算
# ===========================================================================
# 实测基线（2026-09-21 · 28 次真实调用 · 见 `plan_execute.py` 里那张表）
_MEASURED_LLM_MAX_SEC = {"plan": 2.68, "dynamic_input": 1.77, "quality_check": 1.56}
_MIN_HEADROOM = 5  # 至少 5 倍余量


def test_llm_timeouts_are_calibrated_to_measurement():
    """超时**必须是按实测校准的** —— 两个方向都锁。

    · **下界**：每个超时 ≥ 实测 max 的 `_MIN_HEADROOM` 倍
      ⇒ 防止有人把它收得太紧（**LLM 延迟是重尾分布，观测到的 max ≠ P99**，收紧了会误杀正常请求）
    · **上界**：最坏单次（`timeout × (1+retries)`）**必须 < 总预算**
      ⇒ 否则【**一次卡住就能把整个计划的预算吃光**】，总预算形同虚设

    这条是**遗留 #4** 的产物：原来那几个数（60/30/20）是**拍的**，对实测 max 有 13–22 倍余量；
       `plan` 的 60s 尤其糟 —— 60×(1+1)=120 **正好等于总预算** ⇒ **一次卡住就掐掉整个计划**。
    本用例把「**校准过**」这件事变成**可执行的**，而不是只写在注释里（注释会被改，判据不会）。
    """
    import agent.plan_execute as P

    pairs = [
        ("planner_llm", P.PLANNER_LLM_TIMEOUT, _MEASURED_LLM_MAX_SEC["plan"]),
        ("executor_llm", P.EXECUTOR_LLM_TIMEOUT, _MEASURED_LLM_MAX_SEC["dynamic_input"]),
        ("quality_checker_llm", P.QUALITY_LLM_TIMEOUT, _MEASURED_LLM_MAX_SEC["quality_check"]),
    ]

    for name, timeout, measured in pairs:
        assert timeout >= measured * _MIN_HEADROOM, (
            f"{name} 超时 {timeout}s 对实测 max {measured}s 只剩 {timeout / measured:.1f} 倍余量 —— "
            f"至少要 {_MIN_HEADROOM} 倍。⚠️ LLM 延迟重尾，收太紧会误杀正常请求。"
        )

    worst_single = max(t for _, t, _ in pairs) * (1 + P.LLM_MAX_RETRIES)
    assert worst_single < P.PLAN_TOTAL_BUDGET_SECONDS, (
        f"最坏单次 = {worst_single}s ≥ 总预算 {P.PLAN_TOTAL_BUDGET_SECONDS}s —— "
        f"【一次卡住就能吃光整个计划的预算】，总预算形同虚设。"
    )


# ===========================================================================
# 把「FREE 用户对 plan_execute 的【真实】每日上限」钉住
# ===========================================================================
# 实测（2026-09-21）：一次 `plan_execute`（4 步目标）的真实 token 消耗。
# 测法：查 `token_usage_logs` 里 `user_name='admin'` 的**累计值** → 跑一次 → 再查 → 取差值。
#   ⇒ 27916 − 24570 = **3346 tokens**（该次 9 次 LLM 调用：1 规划 + 4 动态输入 + 4 质量检查）
# 会随目标步数浮动（3 步 + 一次真网络搜索那次更贵）⇒ 这里以 4 步的 3346 为基准。
_MEASURED_PLAN_EXECUTE_TOKENS = 3346


def test_free_users_real_daily_limit_on_plan_execute_is_known(monkeypatch):
    """钉住 FREE 用户对 `plan_execute` 的**真实**每日上限（**token 口径**）。

    ## 历史：这条用例原本是「两套口径」的证据—— 别整条删，那是个宝贵事实

    2026-09-21 实测时，本仓有**两套互不知情的配额**：

      | 口径 | 出处 | FREE 限额 | 对 `plan_execute` 的真实约束 |
      |---|---|---|---|
      | **请求次数** | `permission.ROLE_QUOTA`（中间件用） | **100/天** | **形同虚设** |
      | **Token** | `token_config.ROLE_DAILY_TOKEN` | **10_000/天** | **真正在拦的是它** |

    `plan_execute` 一次实测要花 **~3346 tokens** ⇒ **次数配额（100）根本用不完**
    ⇒ 两套口径**差约 35 倍**。这正是  裁「**统一到 token 一套**」的起因。

    2026-10-03：**次数那套已整张删掉** ⇒ 本用例只留 token 口径。
       **「FREE 每天约 3 次」这个数【没有任何人选择过】** ——
          它是「日预算 ÷ 单次成本」算出来的**副作用**。本用例把它变成**已知且被测的**。

    **谁改了 `ROLE_DAILY_TOKEN['free']`、或让 `plan_execute` 变贵/变便宜，这条会红** ——
       提醒去**重新实测单次消耗**并更新记录。
       **这不是"防改动"，是"防不知情"。**

    ## 2026-10-11（两个额度池 ·）—— 它**真的红过一次**

    日额度拆成两个池（各按角色额度）⇒ **总上限翻倍**（作者当日确认）
    ⇒ FREE 用户的**总预算 10_000 → 20_000** ⇒ 按同一份实测单次消耗算：

    | | 改前 | 改后（当天第一版） | **改后（当天第二版）** |
    |---|---|---|---|
    | FREE 总预算 | 10_000 | **20_000**（LLM 10_000 + Embedding 10_000） | **60_000**（LLM **50_000** + Embedding 10_000） |
    | `plan_execute` 实际能跑 | **~3.0 次** | **~6.0 次** | **~17.9 次** |

    **同一天改了两次**：第一版只是把额度**拆成两个池**（各 10 000）；第二版是作者
    **把 LLM 池调到 50 000**（起因：实测一次 `/agent/memory/add` 要 8 112 个 LLM token，
    按 10 000 算访客点一次就打掉八成）。

    **这是【有意】改的**（作者明确要"两份额度"）⇒ 按本条自己的规矩：
    **重新算一遍、更新记录**（本节就是那次更新）。单次消耗那个数**没变**
    （3346 是 2026-09-21 实测的，与额度无关），变的是**分子**。
    判据走**真函数**（`get_user_token_budget` = 两池之和），不是读某一个常量 ——
    否则"拆了两个池、总额仍在拿老数"这种假完成**测不出来**。
    """
    import access.permission as permission
    from billing.token_tracker import get_user_token_budget

    monkeypatch.setattr(permission, "get_user_role", lambda _u: "free")
    budget = get_user_token_budget("free")
    by_tokens = budget / _MEASURED_PLAN_EXECUTE_TOKENS

    # 历史对照（**这条口径已不存在**，留着只为说明「3 次」有多意外）：
    #   旧「请求次数」口径 FREE = 100/天 ⇒ 按次数能跑 100 次，按 token 只能跑 ~3 次。
    _OLD_REQUEST_COUNT_FREE = 100

    assert 15 <= by_tokens <= 22, (
        f"FREE 用户每天实际能跑 **{by_tokens:.1f}** 次 plan_execute —— 与记录（**17.9**）不符。\n"
        f"  （token 预算 {budget} ÷ 实测单次 {_MEASURED_PLAN_EXECUTE_TOKENS}）\n"
        f"⚠️ 若这是【有意】改的，请**重新实测单次消耗**、更新这条用例与；\n"
        f"⚠️ 若是【无意】的（比如改了预算表却没意识到会波及这条最贵的接口），那正好——本条就是为这个而设。"
    )


# ===========================================================================
# S10 · 成败判定不许读【中文文案】—— 必须看结构化字段
# ===========================================================================
def test_工具正常返回里恰好含那句失败文案时不许触发重规划(monkeypatch):
    """**决定性判据**：工具的**正常**返回文本里恰好出现了那句失败文案时，
    不许被判成「这一步彻底失败了」。

    它同时打到 `plan_execute.py` 里的**两处**子串判定：
       · `:557` `if "执行失败" in step_result:` —— 连**质量检查都被跳过**
       · `:362` `if "执行失败（已重试" in step_result:` —— **凭空触发一次重规划**

    为什么必须**驱动真函数**（不 monkeypatch `execute_step_with_quality_check`）：
       把它替换掉，就等于把「被判定的那个值」自己造出来 —— 那样测的是**我的替身**，
       不是这条路径。这里只让**最外面那层**（LLM / 工具）是假的：
       `calculator` 的 handler **正常返回**一段含该文案的文本，于是
       `execute_single_step` → `execute_step_with_retry` → `execute_step_with_quality_check`
       **全是真的**。

    这正是 `execute_single_step` docstring 里那句话的反面现场 ——
       「失败一律**抛异常**，不吞成字符串」，而**成败判定却回去读字符串**。
    """
    import agent.plan_execute as P

    _MARKER = "搜索结果：`执行失败（已重试3次）：boom` 是重试耗尽的标记"
    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", lambda args: _MARKER)
    monkeypatch.setattr(P, "generate_dynamic_input", lambda *a, **k: "1+1")
    monkeypatch.setattr(P, "check_step_quality", lambda *a, **k: True)

    replans = []
    monkeypatch.setattr(P, "plan_task", lambda *a, **k: replans.append(1) or [])

    out = P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "calculator"}], "目标", "u1")

    assert replans == [], (
        "工具**正常返回**的文本里恰好含那句失败文案 ⇒ 被判成「这一步彻底失败」"
        " ⇒ 触发了一次**凭空的重规划**（还把这句正常结果塞进了 replan_context）。\n"
        "⇒ 成败必须看 `StepResult.ok`，⛔ 不能读中文文案（改一个字的措辞就静默失效）。\n"
        f"实际输出：\n{out}"
    )
    assert "步骤1完成" in out, f"正常的一步被当成失败处理了：\n{out}"


def test_真失败仍然会触发重规划(monkeypatch):
    """反向守卫： 之后**失败仍然认得出** —— 防止有人把判定写成**恒 False**。

    **它在改动前后都是绿的**（改前靠文案也认得出）。留着是因为
       「换成字段」这件事最典型的坏形态就是**恒 False**，而那种坏法
       **上面那条用例发现不了**（它对"被判成失败"更敏感，对"失败被判成成功"不敏感）。
    """
    import agent.plan_execute as P

    def _explode(args):
        raise RuntimeError("boom")

    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", _explode)
    monkeypatch.setattr(P, "generate_dynamic_input", lambda *a, **k: "1+1")
    monkeypatch.setattr(P, "check_step_quality", lambda *a, **k: True)

    replans = []
    monkeypatch.setattr(P, "plan_task", lambda *a, **k: replans.append(1) or [])

    P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "calculator"}], "目标", "u1")

    assert replans == [1], "工具真的炸了，却没触发重规划 ⇒ 失败判定失效了（恒 False？）"


def test_重试耗尽返回结构化结果且文案不含异常原文(monkeypatch):
    """`execute_step_with_retry` 重试耗尽时**返回 `StepResult`**，
    且**用户可见的那句话一字不改**。

    两半**都要**，缺一不可：
      · 类型那一半 —— 它是「调用方看字段」的前提（的**因**）
      · 文案那一半 ——  **只换判定依据**，不许顺手改用户看到的话
        （那句话会原样进 `results`、原样返回给调用方，是**用户可见**的）
    """
    import agent.plan_execute as P

    def _explode(args):
        raise RuntimeError("boom")

    monkeypatch.setitem(P._TOOL_HANDLERS, "calculator", _explode)
    monkeypatch.setattr(P, "generate_dynamic_input", lambda *a, **k: "1+1")

    r = P.execute_step_with_retry(
        {"step": 1, "tool": "calculator"}, "1+1", "", "u1", max_retries=1)

    assert isinstance(r, P.StepResult), (
        f"重试耗尽返回的是 `{type(r).__name__}` —— 调用方只能回去读文案（的原始形态）")
    assert r.ok is False
    assert r.error == "boom", f"`error` 该留**异常原文**（不带那句格式化前缀）：{r.error!r}"
    # 2026-10-10（**作者裁「一起收口」**）：**本行就是那次【有意的文案变更】** ——
    #    原先钉的是 `r.text == "执行失败（已重试1次）：boom"`（的「文案一字未改」）。
    #    **现在反过来钉**：**用户看到的那份不许含异常原文** ——
    #    因为 `str(e)` 会把**路径 / 连接串 / SQL 片段**一起带出去
    #    （CodeQL `py/stack-trace-exposure` **同族**；形状照 `api_v1_agent.py` / `token_tracker.py`）。
    #    **本仓「失败要说出来」那条不变**（不许退化成静默 / 白屏）。
    #    本行是**可取反的**：把 `str(e)` 加回去 ⇒ 第一条断言当场红。
    assert "boom" not in r.text, (
        f"用户可见文案里出现了异常原文：{r.text!r}\n：细节只进服务端日志，⛔ 不出接口。")
    assert "执行失败（已重试1次）" in r.text, (
        f"「失败了、重试过几次」这件事必须仍然说出来：{r.text!r}")


# ===========================================================================
# S9 · 重规划那一次 `plan_task` 必须收到【真实发起人】
# ===========================================================================
def test_重规划把真实发起人传下去(monkeypatch):
    """ · 重规划那一次 `plan_task` 必须收到**真实发起人**。

    改前是 `plan_task(replan_context)`（`:383`）—— 走默认 `"unknown"`，后果两条：
       · `check_budget_before_call("unknown")` ⇒ **不受该用户的预算约束**
       · `record_usage(user_name="unknown")` ⇒ **算不到他头上**（`token_usage_logs` 里是 `unknown`）
       最多 **5** 次 ⇒ 最多 5 次「白跑且不记账」的规划调用。

    **替身必须收 `user_name` 并记下来** ——
       上一条同族的用例（`test_dynamic_input_receives_the_real_user_name`）守着 `dynamic_input` 那处漏传；
       而**重规划这处一直没人守**，原因是它的替身写成了 `lambda ctx: [...]`：
       **只接一个参数 ⇒ 只能发现"多传了"，发现不了"漏传"**。
       **这正是这个洞漏到现在的原因**（`app/agent/specs/plan_execute.md` ①）。

    本仓同族前科：那次修的根因是**盲替换命中了注释**，真调用点没改到；
       而写下来的教训（「改完要按行号核」）**是文字**，没能拦住这一处。
       ⇒ 所以除本用例之外，另有一道**从 AST 推出来的**守卫：
          `app/tests/test_plan_task_user_name_wiring.py`。
    """
    import agent.plan_execute as P

    seen = []

    def _fake_plan_task(ctx, user_name="unknown", on_token=None):
        seen.append(user_name)
        return []          # 空计划 ⇒ 循环 break，不用真跑工具

    monkeypatch.setattr(P, "plan_task", _fake_plan_task)
    monkeypatch.setattr(P, "execute_step_with_quality_check",
                        lambda *a, **k: P.StepResult(ok=False, text="炸", error="炸"))

    P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "calculator"}], "目标", "someone_real")

    assert seen == ["someone_real"], (
        f"重规划的 `plan_task` 收到的是 {seen} —— **不是真实发起人**。\n"
        "⇒ 这次调用的 token 既不受他的预算约束、也不算在他头上。\n"
        "⚠️ 检查是不是又漏传了（本仓已犯过两次：`dynamic_input` 那次 + 重规划这次）。"
    )


# ===========================================================================
# S11 · 重规划次数上限必须是【模块级常量】，且循环真的读它
# ===========================================================================
def test_重规划次数上限是可配的模块级常量(monkeypatch):
    """ · `MAX_REPLANS` 是**模块级常量**，且循环**真的按它**停。

    光断言"有个常量叫 `MAX_REPLANS`"**不够** —— 那只验了名字（本仓纪律：
       「拿**动作成功**当**结果正确**」的同族）。这里把常量**改成 2**，
       然后驱动一条「永远失败、永远能重规划」的路径，**数 `plan_task` 被叫了几次**
       ⇒ 证明**循环读的是它**。

    期望 **3** = `MAX_REPLANS + 1`：循环条件是 `replan_count <= MAX_REPLANS`，
       `replan_count` 从 **0** 起算 ⇒ 0/1/2 各进一次循环、每次重规划一次。

    **每一步必须换一个工具名**（别一直用 `"calculator"`）——
       同一个工具连败 **3** 次会进 `failed_tools`，而那条降级分支
       是 `pop(0)` + `continue`，**根本不调 `plan_task`**。
       ⇒ 那样数出来的次数**比 `MAX_REPLANS` 小**，用例会以一个**看不懂的理由**红。
       （同族先例：`test_downgraded_step_keeps_the_real_reason` 就是那条分支的用例。）

    **它钉的是「循环读常量」这个【行为】，不是「常量等于 5」这个【值】** ——
       所以**默认值改了它不该红**（它自己 monkeypatch 成 2）。
    """
    import agent.plan_execute as P

    monkeypatch.setattr(P, "MAX_REPLANS", 2)
    monkeypatch.setattr(P, "execute_step_with_quality_check",
                        lambda *a, **k: P.StepResult(ok=False, text="炸", error="炸"))

    calls = []

    def _fake_plan_task(ctx, user_name="unknown", on_token=None):
        calls.append(user_name)
        # 每次给一份【新】计划；工具名递增 ⇒ 不触发 `failed_tools` 降级 ⇒ 循环不提前 break
        return [{"step": 1, "action": "a", "tool": f"tool{len(calls)}"}]

    monkeypatch.setattr(P, "plan_task", _fake_plan_task)

    P.execute_plan_with_replan(
        [{"step": 1, "action": "a", "tool": "tool0"}], "目标", "u1")

    assert len(calls) == 3, (
        f"`MAX_REPLANS=2` 时应重规划 3 次（0/1/2，循环条件是 `<=`），实际 {len(calls)} 次。\n"
        "⇒ 要么循环没读模块级常量、要么读的地方漏改了。\n"
        "   ⚠️ 若实际是 1 次，先查 `failed_tools` 那条降级分支有没有被踩到。"
    )
