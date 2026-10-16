"""
代码执行器的**纯 stdlib 内核**（无 langchain 依赖）。

从 `code_executor.py` 切出（重构计划 ⑥ 切开点 3）。目的：让**沙箱白名单与执行逻辑**
可在**只有标准库**的环境里被导入和测试 —— 此前它们住在 `code_executor.py` 里，
而那个文件在**模块层** `from langchain_core.tools import tool`，
于是想单测沙箱就必须先把 langchain 装齐。

**依赖方向单向**：本模块**只 import 标准库**（`sys` 未用故未列）。
`code_executor.py` 引用本模块；**本模块绝不反向引用** `code_executor.py`。

`code_executor.py` 只是给 `execute_python_impl` 套一层 `@tool` 外壳（供 Agent 调用）。
"""
# 2026-09-21（③-a）：`io` / `contextlib` 的 import **删掉了** ——
#    执行搬进子进程后，它们只在 `_SANDBOX_CHILD` 那段**脚本字符串**里用（子进程自己 import），
#    父进程里已无引用。**不是清理历史遗留**，是本次改动**自己造成的**未使用导入。
import json
import os
import subprocess
import sys
import urllib.request

# 2026-10-08：**执行器容器的地址**。
#    有值 ⇒ `execute_python_impl` 走**远端**（容器里跑）；没值 ⇒ **回落**本地子进程。
#    回落的目的是「本机开发 / 离线 CI 不许被这个功能拖成"必须有容器"」，
#       **不是**"远端挂了就自动回落" —— 那件事见 `_run_remote` 里的说明。
#    容器里由 `docker-compose.yml` 的 `api` 服务注入（`EXECUTOR_URL=http://executor:8000`）。
EXECUTOR_URL = os.getenv("EXECUTOR_URL", "").strip()

# 安全沙箱配置
ALLOWED_BUILTINS = [
    # 基础内置函数
    "abs", "all", "any", "bool", "bytes", "chr", "dict", "dir",
    "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "hash", "hex", "int", "isinstance", "issubclass", "iter", "len",
    "list", "map", "max", "min", "next", "object", "oct", "ord",
    "pow", "print", "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "str", "sum", "tuple", "type", "zip",
    # 常用数学模块
    "math",

    # 2026-09-20 加（§三·N17 · 作者裁「放开」）：**异常类**。
    #
    #    此前白名单里**一个异常类都没有** ⇒ 被执行的代码**不能**写
    #      `try: … except ValueError: …`，也不能 `raise ValueError(…)`
    #    —— 两者都会先撞 `NameError: name 'ValueError' is not defined`。
    #    而**异常处理是 Python 最常见的写法之一**，且这条限制**从代码上看不出来**
    #       （"一长串白名单里没有异常类"是**沉默的**）。
    #
    #    放开的安全性：**异常类本身不提供逃逸能力** —— 它们只是**类型对象**，
    #    没有文件/进程/导入的能力。放开它们不会让沙箱多出一条逃逸路径。
    #
    #    **刻意【不】放开的**：`BaseException` / `SystemExit` / `KeyboardInterrupt`
    #       / `GeneratorExit` —— 这几个是**退出机制**，让被执行的代码能捕获它们，
    #       等于允许它**吃掉执行器的中断信号**。要捕获，用 `Exception` 就够了。
    "Exception",
    "ArithmeticError", "AssertionError", "AttributeError",
    "IndexError", "KeyError", "NameError", "NotImplementedError",
    "OSError", "RuntimeError", "StopIteration", "TypeError",
    "ValueError", "ZeroDivisionError",

    # 2026-09-21 加（§十四·N18 · 作者裁「放开」）：`__build_class__` —— **让 `class` 语法成立**。
    #
    #    此前被执行的代码**不能定义类**：
    #      `class Mine(Exception): pass` ⇒ `NameError: __build_class__ not found`
    #
    #    **为什么放开它【不增加任何新能力】**（实测，2026-09-21）：
    #       白名单里**本来就有 `type`** ⇒ 「动态建类」这个能力**早就可达**了：
    #         `Mine = type("Mine", (Exception,), {})`        → 可用
    #         `M    = type("M", , {"f": lambda self: 42})` → 可用
    #       ⇒ 加 `__build_class__` **只是让 `class` 这种写法也成立**，
    #         **不是新开一条能力，更不是新开一条逃逸路径**。
    #
    #    与 N17（放开异常类）是**同一条逻辑**：放开的是**语言构造**，不是**逃逸通道**。
    #    实测：`__build_class__` 本身只是类创建的**原语**，白名单里仍**没有**
    #    `__import__` / `open` / `eval` / `exec` ⇒ 逃逸面未变。
    #
    #    **仍然【不】放开的**：见上面 N17 那段列的四个**退出机制**。
    "__build_class__",
]

ALLOWED_MODULES = [
    "math",
    "json",         # JSON 数据处理
    "datetime",     # 日期时间
    "collections",  # 数据结构
    "itertools",    # 迭代工具
    "functools",    # 函数工具
    "re",           # 正则表达式
    "statistics",   # 统计
    "random",       # 随机
]

# 最大执行时间和输出长度限制
MAX_EXEC_TIME = 5  # 最长执行5秒
MAX_OUTPUT_LENGTH = 2000  # 最大输出字符数

# 远端调用要给**执行器自己**那 5 秒留出余量（它内部还有一次子进程往返）。
# 2026-10-08—— 别把两者设成一样：那会让"执行器还没跑完"
#    被本地判成"远端超时"，于是**正常的慢代码**变成报错。
EXECUTOR_TIMEOUT = MAX_EXEC_TIME + 10

# 2026-09-21（§十四 · ③-a）：**真·超时 —— 把执行放进【子进程】，超时【硬杀】。**

# **此前 `MAX_EXEC_TIME` 只被【定义】、从未被使用** —— 实测：
#      `execute_python_impl("i=0\nwhile True:\n    i+=1")` **永不返回**（4 秒后仍在跑）。
#    而工具描述却对 LLM **承诺**「最长执行时间：5秒」。
#    以前是「LLM 模拟执行」所以不痛；**N15 让执行层真跑代码之后，这就是个洞。**

# **为什么必须换机制**（另外三条路都核过，都不成立）：
#   · `signal.alarm` —— **只在【主线程】有效**，而 MCP 服务端用
#     `asyncio.to_thread(handler, args)` 调工具 ⇒ 执行发生在 **worker 线程**，alarm 装不上
#   · 线程 + `join(timeout)` —— **杀不掉 Python 线程** ⇒ 线程照跑，
#     只是"你以为超时了"（**假超时**，比没有更糟：它给了一个错误的安全感）
#   · `PyThreadState_SetAsyncExc` —— 对紧循环**不可靠**，拿它当安全承诺的地基不诚实
#   · **子进程 + 超时硬杀** —— **唯一能真正打断死循环的**

# **收益不止超时**：子进程是**全新解释器**（更强隔离），白名单从**同一个模块**重建
#   （⇒ 单一事实源，不会与主进程漂），且它崩了（段错误 / OOM）**也带不走主进程**。
# **代价**：每次调用多一次进程启动（~100ms 量级）—— 对"执行代码"这类工具可以接受。

# 子进程脚本：**从 `code_executor_impl` 重建白名单**（不手抄一份 —— 抄了必漂）。
_SANDBOX_CHILD = r'''
import sys, io, contextlib, json
sys.path.insert(0, sys.argv[1])          # 由父进程传入本模块所在目录
from code_executor_impl import create_safe_globals, MAX_OUTPUT_LENGTH
code = sys.stdin.read()
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        exec(code, create_safe_globals())
    out = buf.getvalue()
    if len(out) > MAX_OUTPUT_LENGTH:
        out = out[:MAX_OUTPUT_LENGTH] + "\n... (输出过长，已截断)"
    if not out.strip():
        out = "代码执行成功，但无输出内容。"
    payload = {"ok": True, "out": out}
except BaseException as e:                # BaseException：连 SystemExit 也要抓住，否则子进程静默退出
    payload = {"ok": False, "out": "代码执行出错: %s: %s" % (type(e).__name__, e)}
sys.stdout.write(json.dumps(payload))
'''


def create_safe_globals() -> dict:
    """创建一个安全沙箱的执行环境"""
    safe_globals = {"__builtins__": {}}

    # 2026-09-21 加（§十四·N18）：`__name__` —— **`class` 语句要用它填 `__module__`**。
    #
    #    只加 `__build_class__`（见 ALLOWED_BUILTINS）**还不够** —— 实测报的是
    #      `NameError: name '__name__' is not defined`
    #    （`__build_class__` 的调用点会去全局域取 `__name__`）。
    #    ⇒ **N18 实际是两件事**：白名单要放 `__build_class__`，执行全局域要给 `__name__`。
    #
    #    安全性：`__name__` 只是个**字符串**，不提供任何逃逸能力。
    #       它必须是**执行全局域**（`safe_globals`）的键，**不是** builtins 的键 —— 放错位置无效。
    safe_globals["__name__"] = "__sandbox__"

    # 只注入允许的内置函数
    import builtins
    for func_name in ALLOWED_BUILTINS:
        if hasattr(builtins, func_name):
            safe_globals["__builtins__"][func_name] = getattr(builtins, func_name)

    # 注入允许的模块
    for module_name in ALLOWED_MODULES:
        try:
            module = __import__(module_name)
            safe_globals[module_name] = module
        except ImportError:
            pass

    return safe_globals


def execute_python_impl(code: str) -> str:
    """
    执行一段已编写好的 Python 代码，并返回执行结果（**纯逻辑，无 langchain 依赖**）。

    行为与切分前完全一致 —— 只搬位置，不改逻辑：
    先做代码意图检测（拒绝"需求描述"而非可执行代码），再在 `create_safe_globals`
    给出的受限环境里 `exec`，捕获 stdout，超长截断。

    对外说明的完整版（安全限制 / 适用场景）写在 `code_executor.execute_python`
    的 docstring 里 —— 那是给 **LLM 读的工具描述**，必须留在 `@tool` 那一层。
    """
    # 新增：代码意图检测
    # 如果代码看起来像是一个需求描述而非可执行代码，直接拒绝
    non_code_patterns = [
        "帮我", "请写", "生成", "写一段", "写一个", "创建",
        "help me", "generate", "write", "create",
    ]
    first_line = code.strip().split('\n')[0].lower()
    for pattern in non_code_patterns:
        if pattern in first_line:
            return (
                f"错误：传入的不是可执行的 Python 代码。\n"
                f"看起来你传入的是一个需求描述（包含'{pattern}'）。\n"
                f"execute_python 只能执行已编写好的代码，不能生成代码。\n"
                f"请先生成代码文本，再将代码作为参数传入。"
            )

    # 2026-10-08：**先看有没有执行器** —— 有则走**容器**，没有则**本地子进程**。
    #    这一句必须在**意图检测之后**：那个检测是**产品策略**（"这个工具只执行、不生成"），
    #       不许因为"走远端了"就跳过它。
    #       守卫：`test_code_executor_remote.py::test_intent_check_still_applies_before_any_remote_call`
    if EXECUTOR_URL:
        ok, out = _run_remote(code)
    else:
        # 2026-09-21（③-a）：执行搬进**子进程**，超时**硬杀**（详见上面 `_SANDBOX_CHILD` 的注释）。
        ok, out = run_in_sandbox_subprocess(code)
    return out


def _run_remote(code: str) -> tuple:
    """把 `code` 发给**执行器容器**跑。返回 `(ok, out)`。

    **远端失败时【如实报错】，绝不静默回落本地。**

    为什么这条这么硬：若"远端挂了就悄悄回落"，那么
    **运维把执行器停了 / 地址配错了 ⇒ 一切照常工作** ——
    代码**又回到宿主同权限的进程里跑**，而**没有任何人会知道**。
    那等于这道隔离**是装饰性的**。
    （本仓原话：**「『从不命中』与『没人违规』在机器痕迹上完全一样。」**）

    守卫：`test_code_executor_remote.py::test_remote_failure_is_reported_and_does_not_silently_fall_back`
    （判据是**结果里没有本地跑出来的 `42`** —— 不是"函数返回了一个字符串"）。
    """
    url = EXECUTOR_URL.rstrip("/") + "/execute"
    body = json.dumps({"code": code}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=EXECUTOR_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:                       # noqa: BLE001 —— 见 docstring：**不回落**
        return False, (
            f"代码执行出错: 无法访问**执行器容器**（{type(e).__name__}: {e}）。\n"
            f"⛔ 本次【没有】执行任何代码，也【没有】回落到本机进程 —— "
            f"悄悄回落会让这层隔离形同虚设，且没人会发现。"
        )

    return bool(payload.get("ok")), payload.get("out", "")


def run_in_sandbox_subprocess(code: str) -> tuple:
    """把 `code` 丢进一个**新的**子进程里跑。返回 `(ok, out)`。

    **每次调用起一个【新】子进程** —— **不是**常驻解释器。
       常驻解释器会让 `exec` 的 `globals` **跨调用活着** ⇒ 访客之间互相污染，
       且下一次的行为**取决于别人跑过什么**。那是**安全缺陷**，不是性能取舍。

    **本函数是「执行沙箱」的【唯一】落点**：
       `execute_python_impl`（**本地**路径）与 `executor_server`（**远端容器**路径）
       **都调它**，**不许各自抄一份超时 / 报错文案** ——
        刚把"同一个东西抄 5 份"收口掉，这里不能再长第二份。

    `ok` 的含义：**代码自己抛异常 / 超时 / 子进程没正常返回** ⇒ `False`；
    正常跑完（哪怕没有输出）⇒ `True`。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _SANDBOX_CHILD, here],
            input=code,
            capture_output=True,
            text=True,
            timeout=MAX_EXEC_TIME,
        )
    except subprocess.TimeoutExpired:
        # `subprocess.run` 在超时时会**先杀子进程再抛异常** ⇒ 这里返回即代表"已经停下来了"
        return False, (
            f"代码执行出错: 超过最长执行时间（{MAX_EXEC_TIME} 秒）—— 已【强制终止】。\n"
            f"提示：检查是否有死循环；或把计算量拆小、分多次执行。"
        )
    except Exception as e:
        return False, f"代码执行出错: {type(e).__name__}: {str(e)}"

    # 子进程没能给出可解析的结果（崩溃 / 被 OOM 杀 / 段错误）—— 如实报，不假装成功
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        return False, (
            f"代码执行出错: 沙箱子进程没有正常返回（returncode={proc.returncode}）。"
            f"\nstderr 前 300 字：{(proc.stderr or '').strip()[:300]}"
        )

    return bool(payload["ok"]), payload["out"]
