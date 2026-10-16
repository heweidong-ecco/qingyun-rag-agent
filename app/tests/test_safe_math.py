"""`app/tools/safe_math.py` 的离线测试（不联网、不花钱 ⇒ 进 CI）。
"""
import time

import pytest

from tools.safe_math import InvalidExpression, UnsafeExpressionError, calculate, evaluate


# ===========================================================================
# ① 正常算术：不许回归
# ===========================================================================
@pytest.mark.parametrize(
    "expr,expected",
    [
        ("6*7", "42"),                 # `test_agent_repairs.py` 的 MCP 用例钉的就是这条
        ("3*4-5/6", str(3 * 4 - 5 / 6)),  # docstring 里的那个例子
        ("7/2", "3.5"),
        ("7//2", "3"),
        ("7%3", "1"),
        ("2**10", "1024"),
        ("-3+1", "-2"),
        ("(1+2)*3", "9"),
        ("+5", "5"),
        ("2.5*2", "5.0"),
        ("10-2-3", "5"),               # 结合性：左结合，不是 11
        ("2**3**2", "512"),            # 结合性：`**` 右结合 = 2**(3**2)
    ],
)
def test_arithmetic_still_works(expr, expected):
    """改前改后，**算术结果必须一模一样**。"""
    assert calculate(expr) == expected


def test_result_is_always_a_string():
    """给 LLM 当工具用 ⇒ 返回值恒为 `str`（旧行为如此，别改）。"""
    assert isinstance(calculate("1+1"), str)
    assert isinstance(calculate("bad("), str)


# ===========================================================================
# ② 只认算术 —— 非算术一律拒绝
# ===========================================================================
ESCAPES = [
    # (表达式, 人话说明)
    ("__import__('os').system('touch /tmp/safe_math_pwned')", "RCE：直接执行系统命令"),
    ("__import__('os').popen('cat .env').read()", "RCE：读走 .env 里的 key"),
    ("().__class__.__bases__[0].__subclasses__()", "经典 eval 逃逸链（子类遍历）"),
    ("open('/etc/passwd').read()", "读文件"),
    ("eval('1+1')", "嵌套 eval"),
    ("exec('x=1')", "exec"),
    ("compile('1', '<s>', 'eval')", "compile"),
    ("globals()", "拿 globals"),
    ("print(1)", "任意函数调用"),
    ("'a'*3", "字符串常量（旧实现能算出 'aaa'）"),
    ("b'ab'", "bytes 常量"),
    ("[1,2,3]", "列表"),
    ("(1,2)", "元组"),
    ("{'a':1}", "字典"),
    ("{1,2}", "集合"),
    ("[x for x in [1]]", "推导式"),
    ("1 if True else 2", "条件表达式"),
    ("x", "变量名（旧实现会 NameError）"),
    ("f'{1}'", "f-string"),
    ("lambda: 1", "lambda"),
    ("(1).__class__", "属性访问"),
    ("[1][0]", "下标"),
    ("1 < 2", "比较"),
    ("(a := 1)", "海象赋值"),
    ("1j", "复数（只收 int / float）"),
    ("...", "Ellipsis"),
]


@pytest.mark.parametrize("expr,why", ESCAPES, ids=[e[0][:40] for e in ESCAPES])
def test_non_arithmetic_is_rejected(expr, why):
    """一律拒绝。断言**同时**钉住 `evaluate` 抛异常与 `calculate` 不改契约。"""
    with pytest.raises(UnsafeExpressionError):
        evaluate(expr)

    out = calculate(expr)
    assert out.startswith("计算错误:"), f"{why} —— 应当被拒绝，实际返回：{out!r}"


def test_rejection_surfaces_a_reason_the_llm_can_read():
    """拒绝原因要**说得出是什么**，否则模型只会瞎试到上下文用光。"""
    out = calculate("__import__('os')")
    assert out.startswith("计算错误:")
    assert "不支持" in out, f"原因要能读，实际：{out!r}"


def test_dangerous_expression_is_not_executed():
    """**本文件最要紧的一条**：拒绝必须发生在**执行之前**。

    "返回了错误字符串"**不足以**证明没执行 —— 必须验副作用（文件没被创建）。
    """
    import os

    marker = "/tmp/safe_math_pwned"
    if os.path.exists(marker):
        os.remove(marker)

    out = calculate(f"__import__('os').system('touch {marker}')")

    assert out.startswith("计算错误:")
    assert not os.path.exists(marker), "🔴 表达式被**执行**了 —— 拒绝发生在执行之后"


def test_bool_is_not_accepted_as_a_number():
    """细节：Python 里 `isinstance(True, int)` 是真的 ⇒ 不特判就会算出 `2`。"""
    assert calculate("True").startswith("计算错误:")
    assert calculate("True+True").startswith("计算错误:")


# ===========================================================================
# ③ DoS 面：不许把 CPU 占满
# ===========================================================================
@pytest.mark.parametrize(
    "expr,why",
    [
        ("9**9**9", "右结合 ⇒ 指数 387420489，朴素求值会卡死"),
        ("2**100000000", "超大指数"),
        ("10**1000*10**1000*10**1000*10**1000*10**1000", "结果位数爆掉"),
        ("9" * 500, "超长表达式（先撞长度上限）"),
    ],
)
def test_dos_shapes_are_rejected_fast(expr, why):
    """判据是**两条**：被拒 **且** 很快返回 —— 只断言"被拒"的话，卡死那条测不出来。"""
    t0 = time.monotonic()
    out = calculate(expr)
    elapsed = time.monotonic() - t0

    assert out.startswith("计算错误:"), f"{why} 应当被拒，实际：{out!r}"
    assert elapsed < 1.0, f"{why} 慢了 {elapsed:.2f}s ⇒ 上限没起作用"


def test_the_right_gate_fires():
    """钉住**是哪道闸**拦下的 —— 这条是 `9**9**9` 那条的**替身**。

    为什么需要替身：`9**9**9` 那条**做不了变异自证** —— 把「指数预判」那道闸拆掉之后，
       求值会真的去算 9**387420489，**机器直接卡死**（这正是那道闸存在的理由）。
       ⇒ 改钉"报错文案里写明是哪道闸"，就能在**不把它跑起来**的前提下证明闸还在。
    """
    # 闸一：指数预判（`9**9**9` 右结合 ⇒ 指数 387420489）
    out = calculate("9**9**9")
    assert "指数过大" in out, f"应当是【指数预判】那道闸拦的，实际：{out!r}"

    # 闸二：结果位宽（指数都合规，是乘法链把结果堆大的）
    out = calculate("10**1000*10**1000*10**1000")
    assert "结果过大" in out, f"应当是【结果位宽】那道闸拦的，实际：{out!r}"

    # 闸三：表达式长度（在解析之前就拦，成本最低）
    out = calculate("1+" * 300 + "1")
    assert "表达式过长" in out, f"应当是【长度】那道闸拦的，实际：{out!r}"


def test_power_exponent_is_bounded_even_when_it_is_computed():
    """指数是**算出来的**也要拦 —— 否则 `9**(9*9*9*9*9*9*9)` 绕过去了。

    判据是**指数的大小**，不是"它是不是字面量"：`9*(9*9*9*9*9*9*9)` 里那个指数
       字面上确实是个算式，但它算出来是 4782969 ⇒ 一样得拒。
    """
    assert calculate("9**(9*9*9*9*9*9*9)").startswith("计算错误:")


def test_reasonable_powers_still_work():
    """别把上限收得过紧 —— 正常算式照算。"""
    assert calculate("2**100") == str(2 ** 100)


def test_expression_length_is_capped():
    assert calculate("1+" * 300 + "1").startswith("计算错误:")


# ===========================================================================
# ④ 契约：错误路径的形状不许变
# ===========================================================================
def test_division_by_zero_message_is_unchanged():
    """钉住既有契约 —— `app/tests/test_impl_modules.py` 断言了这两条。

    改这条就是改对外行为：除零的文案是给 LLM 看的，别顺手换掉。
    """
    out = calculate("1/0")
    assert out.startswith("计算错误:"), out
    assert "division by zero" in out, out


def test_syntax_error_is_returned_not_raised():
    assert calculate("3*").startswith("计算错误:")
    assert calculate("").startswith("计算错误:")


def test_non_string_input_is_returned_not_raised():
    """工具参数走 JSON ⇒ `null` / 数字都可能进来（不能让它 500）。"""
    for bad in (None, 5, ["1+1"]):
        assert calculate(bad).startswith("计算错误:"), f"{bad!r} 应当返回错误字符串"


def test_calculate_never_raises():
    """`calculate` 是给工具用的**最后一道兜底**：任何输入都不许抛出去。"""
    for expr in ESCAPES + [("", ""), ("(((", ""), (None, "")]:
        try:
            calculate(expr[0])
        except Exception as e:  # pragma: no cover - 只有回归时才走到
            pytest.fail(f"calculate({expr[0]!r}) 抛了 {type(e).__name__}: {e}")


def test_invalid_expression_is_a_distinct_exception_type():
    """语法错 ≠ 不安全：分成两类，报错文案才说得清（`InvalidExpression` 是父类）。"""
    assert issubclass(UnsafeExpressionError, InvalidExpression)

    with pytest.raises(InvalidExpression):
        evaluate("3*")
    with pytest.raises(InvalidExpression):
        evaluate("__import__('os')")
