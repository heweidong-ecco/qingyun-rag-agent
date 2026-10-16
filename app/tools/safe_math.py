"""安全数学求值 —— `calculator` 工具的求值实现，**替代 `eval(expression)`**。
"""

import ast
import math
import operator

__all__ = [
    "InvalidExpression",
    "UnsafeExpressionError",
    "DivisionByZero",
    "calculate",
    "evaluate",
    "MAX_EXPRESSION_LENGTH",
    "MAX_POWER_EXPONENT",
    "MAX_RESULT_BITS",
]

#: 表达式字符数上限 —— 正常算式（`3*4-5/6`）远够，别往上调着玩。
MAX_EXPRESSION_LENGTH = 200

#: 指数绝对值上限。与 `MAX_RESULT_BITS` 是**两道并联的闸**，不是冗余：
#: 前者挡 `9**9**9`（指数巨大），后者挡 `10**1000*10**1000*...`（指数不大但结果巨大）。
MAX_POWER_EXPONENT = 1000

#: 结果整数位宽上限（4096 位 ≈ 1233 位十进制）。`2**100` 只有 101 位 ⇒ 正常算式不受影响。
MAX_RESULT_BITS = 4096


class InvalidExpression(ValueError):
    """表达式**不合规**（语法错 / 太长 / 溢出）。是"算不了"，不是"不安全"。"""


class UnsafeExpressionError(InvalidExpression):
    """表达式**不是算术**（出现了名字 / 调用 / 属性访问这些）。

    它是 `InvalidExpression` 的子类 ⇒ `except InvalidExpression` 两种都接得住，
       而测试可以**分开**断言"拒绝的原因是不安全"而不是"只是语法错"。
    """


class DivisionByZero(InvalidExpression):
    """除以零。单列一个类是因为**文案对外**：`calculate("1/0")` 的结果里必须含
    `division by zero` —— `app/tests/test_impl_modules.py` 与给 LLM 看的提示都依赖它。"""


# 放行的二元运算符。用**白名单 dict**（不是 if-elif 链）：没登记的运算符
# 自然落进"不支持"，不会因为漏写一个分支而被放行。
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_BINARY_OP_NAMES = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
}


def _unsupported(node) -> "UnsafeExpressionError":
    """统一出一句**模型能读懂**的拒绝原因。

    文案要具体到节点类型：只说"不支持"的话，模型会一直换写法试到上下文用光。
    """
    kind = type(node).__name__
    return UnsafeExpressionError(
        f"不支持的语法: {kind}（只认数字与 + - * / // % ** 组成的算式）"
    )


def _check_literal(node: "ast.Constant"):
    """只收 int / float。`bool` 得先排掉 —— 它 `isinstance(x, int)` 为真。"""
    value = node.value
    if isinstance(value, bool):
        raise UnsafeExpressionError("不支持的字面量: bool（只收数字）")
    if not isinstance(value, (int, float)):
        raise UnsafeExpressionError(
            f"不支持的字面量: {type(value).__name__}（只收 int / float）"
        )
    return value


def _check_size(value):
    """每一步都查结果的规模 —— 这是"结果巨大"那条路唯一的闸。"""
    if isinstance(value, int):
        bits = value.bit_length()
        if bits > MAX_RESULT_BITS:
            raise InvalidExpression(f"结果过大（{bits} 位 > 上限 {MAX_RESULT_BITS} 位）")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidExpression("结果不是有限数（溢出）")
    else:  # pragma: no cover - 上面的白名单已经把别的类型挡住了
        raise UnsafeExpressionError(f"不支持的运算结果类型: {type(value).__name__}")
    return value


def _eval_pow(node: "ast.BinOp", left, right):
    """`**` 单独一条路 —— **预判必须在算之前**。

    顺序反了等于没防：`9**9**9` 右结合 ⇒ 指数 387420489，
       "先算再检查结果大小"会**卡死在求值里**，根本走不到检查那一步。
    """
    if abs(right) > MAX_POWER_EXPONENT:
        raise InvalidExpression(
            f"指数过大（|{right}| > 上限 {MAX_POWER_EXPONENT}）"
        )
    # 底数再大，乘以指数也不能爆位宽 —— `(10**1000)**1000` 在这里就被挡下，
    # 不用真去算那个 330 万位的大数。
    if isinstance(left, int) and left.bit_length() * abs(right) > MAX_RESULT_BITS:
        raise InvalidExpression(
            f"幂运算结果会过大（底数 {left.bit_length()} 位 × 指数 {abs(right)}）"
        )
    try:
        return operator.pow(left, right)
    except (OverflowError, ValueError) as e:
        raise InvalidExpression(f"幂运算溢出: {e}") from None


def _eval_node(node):
    """递归求值。任何名单外的节点都在这里被拒 —— 这是整个模块的**白名单**所在。"""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        return _check_literal(node)

    if isinstance(node, ast.UnaryOp):
        # 只放行一元正负号。`not` / `~` 不是算术 ⇒ 落进 else。
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return _check_size(+operand)
        if isinstance(node.op, ast.USub):
            return _check_size(-operand)
        raise _unsupported(node)

    if isinstance(node, ast.BinOp):
        op = _BINARY_OPS.get(type(node.op))
        if op is None:
            raise UnsafeExpressionError(
                f"不支持的运算符: {type(node.op).__name__}（只认 + - * / // % **）"
            )
        left = _eval_node(node.left)
        right = _eval_node(node.right)

        if isinstance(node.op, ast.Pow):
            return _check_size(_eval_pow(node, left, right))

        try:
            return _check_size(op(left, right))
        except ZeroDivisionError as e:
            # 原样保留 Python 的措辞（`division by zero`）—— 对外文案，别换。
            raise DivisionByZero(str(e)) from None
        except (OverflowError, ValueError) as e:
            raise InvalidExpression(f"计算溢出: {e}") from None

    raise _unsupported(node)


def evaluate(expression):
    """把算术表达式算成一个数字。名单外的语法一律 `InvalidExpression`。

    与 `calculate` 的分工：这里是**会抛的**版本（测试与内部用），
       `calculate` 是**永不抛**的那层（给工具当兜底）。
    """
    if not isinstance(expression, str):
        raise InvalidExpression(f"表达式必须是字符串，收到 {type(expression).__name__}")
    if not expression.strip():
        raise InvalidExpression("表达式为空")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise InvalidExpression(
            f"表达式过长（{len(expression)} 字符 > 上限 {MAX_EXPRESSION_LENGTH}）"
        )

    try:
        # `mode="eval"` ⇒ 只接受**单个表达式**：`x=1` / `1;2` 这类直接语法错。
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise InvalidExpression(f"表达式语法错误: {e.msg}") from None

    return _eval_node(tree)


def calculate(expression) -> str:
    """给 `calculator` 工具用的那一层：**成功返回结果字符串，失败返回 `计算错误: …`**。

    **永不抛异常** —— 它是给 LLM 当工具用的，炸出去会变成一次 500。
       这个契约是**旧行为原样保留**的（改前那 5 处也都是 `try/except` 包着返回字符串）。
    """
    try:
        return str(evaluate(expression))
    except Exception as e:
        return f"计算错误: {e}"
