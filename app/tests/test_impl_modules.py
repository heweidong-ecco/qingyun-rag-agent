"""`*_impl.py` 两个「纯 stdlib 内核」的单元测试（§三·D2 · 2026-09-20）

背景：`code_executor_impl.py` / `simple_tools_impl.py` 的 docstring 都声称
「**可脱离 langchain 单测**」—— 而这个**能力是真的**（实测两个模块只 import 标准库：
一个是 `datetime`，另一个是 `io` + `contextlib`），**只是从来没人用过它**
（全仓唯一引用者是各自的 `@tool` 外壳，没有任何测试在跑）。

所以这组用例的意义**不是"发现了 bug"**，而是两件正事：

1. **兑现那句声明** —— 否则它就是一句**没被验证过的话**（本仓最防的那类）。
2. **锁住那次重构的收益** —— 「⑥ 切开点 3」把沙箱逻辑抽成纯 stdlib，目的就是让它可测；
   但**没有测试，下次改动静悄悄把 langchain 依赖塞回去，没人会发现**。
   ⇒ 下面 `test_impl_modules_do_not_import_langchain` 就是那条**结构性锁**。

这组用例**刻意跑得飞快**（不碰 Redis / DB / LLM），因为那两个模块本来就只依赖标准库 ——
   如果哪天它开始需要 fixture，说明**声明已经被破坏**了。
"""
import importlib
import re

# ===========================================================================
# 结构性锁：两个模块必须【只依赖标准库】
# ===========================================================================
def test_impl_modules_do_not_import_langchain():
    """`*_impl.py` 一旦 import 了 langchain，那条「可脱离 langchain 单测」的声明就**失效了**。

    这不是形式检查：`simple_tools.py` 与 `code_executor.py` 的**模块层**都有
       `from langchain_core.tools import tool` ⇒ 它们**没法**在只有标准库的环境里导入。
       当初抽出 `_impl` 就是为了解决这个 —— **所以"不反向依赖"是那笔重构的全部意义所在**。
    """
    for name in ("tools.simple_tools_impl", "tools.code_executor_impl"):
        mod = importlib.import_module(name)
        tree = __import__("ast").parse(open(mod.__file__, encoding="utf-8").read())
        imported = set()
        for node in __import__("ast").walk(tree):
            if isinstance(node, __import__("ast").Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, __import__("ast").ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        banned = {m for m in imported if m.startswith("langchain") or m.startswith("langgraph")}
        assert not banned, f"{name} 不应依赖 langchain 系（那会让「可脱离 langchain 单测」失效）：{banned}"

        # 反向依赖也要禁：_impl 不该 import 自己的 @tool 外壳
        shell = "simple_tools" if name.startswith("simple") else "code_executor"
        assert shell not in imported, f"{name} 不该反向 import {shell}（依赖方向必须是单向的）"


# ===========================================================================
# simple_tools_impl
# ===========================================================================
def test_calculator_impl_evaluates_and_reports_errors():
    from tools.simple_tools_impl import calculator_impl

    assert calculator_impl("3*4") == "12"
    assert calculator_impl("7/2") == "3.5"

    # 出错时**返回字符串**而不是抛异常 —— 这是给 LLM 当工具用的，不能让它炸
    out = calculator_impl("1/0")
    assert out.startswith("计算错误:"), f"除零应当返回'计算错误:…'，实际：{out!r}"
    assert "division by zero" in out


def test_date_today_impl_returns_a_readable_date():
    from tools.simple_tools_impl import date_today_impl

    out = date_today_impl()
    assert out.startswith("今天是"), out
    assert re.search(r"星期[一二三四五六日]", out), out


# ===========================================================================
# （2026-10-08）：date_calc / json_extract / stats 三个纯函数内核
# ===========================================================================
def test_date_calc_impl_adds_and_subtracts_days():
    from tools.simple_tools_impl import date_calc_impl

    assert date_calc_impl("2026-10-08", 7) == "2026-10-15（星期四）"
    assert date_calc_impl("2026-10-08", -8) == "2026-09-30（星期三）"
    assert date_calc_impl("2026-10-08", 0) == "2026-10-08（星期四）"
    # 跨月 / 跨年 / 闰年 —— 走的是 `datetime`，不是手算
    assert date_calc_impl("2026-01-31", 1) == "2026-02-01（星期日）"
    assert date_calc_impl("2024-02-28", 1) == "2024-02-29（星期四）"


def test_date_calc_impl_rejects_bad_input_without_raising():
    from tools.simple_tools_impl import date_calc_impl

    # 失败必须是【返回值】，不是异常 —— 与 `calculator_impl` 同款约定
    assert date_calc_impl("2026/10/08", 1).startswith("日期计算错误:")
    assert date_calc_impl("", 1).startswith("日期计算错误:")
    assert date_calc_impl("2026-10-08", "七天").startswith("日期计算错误:")


def test_json_extract_impl_walks_dot_and_bracket_paths():
    from tools.simple_tools_impl import json_extract_impl

    doc = '{"user": {"name": "阿伟", "tags": ["a", "b"]}, "n": 3}'
    assert json_extract_impl(doc, "user.name") == "阿伟"
    assert json_extract_impl(doc, "user.tags[1]") == "b"
    assert json_extract_impl(doc, "n") == "3"


def test_json_extract_impl_reports_errors_as_strings():
    from tools.simple_tools_impl import json_extract_impl

    assert json_extract_impl("这不是 json", "a").startswith("JSON 提取错误:")
    assert json_extract_impl('{"a": 1}', "b").startswith("JSON 提取错误:")
    assert json_extract_impl('{"a": [1]}', "a[5]").startswith("JSON 提取错误:")
    # 坏路径必须【说出来】，不许被悄悄当成 `a.b`
    assert json_extract_impl('{"a": {"b": 1}}', "a..b").startswith("JSON 提取错误:")
    assert json_extract_impl('{"a": 1}', "a[x]").startswith("JSON 提取错误:")


def test_stats_impl_reports_basic_descriptives():
    from tools.simple_tools_impl import stats_impl

    out = stats_impl("1,2,3,4")
    assert "个数=4" in out and "均值=2.5" in out
    assert "最小=1" in out and "最大=4" in out and "中位数=2.5" in out
    # 空格 / 中文逗号 / 换行都认（LLM 三种写法都会出现）
    assert "个数=3" in stats_impl("1 2 3")
    assert "个数=3" in stats_impl("1，2，3")
    assert "个数=3" in stats_impl("1\n2\n3")


def test_stats_impl_rejects_bad_input_without_raising():
    from tools.simple_tools_impl import stats_impl

    assert stats_impl("1,abc,3").startswith("统计错误:")
    assert stats_impl("   ").startswith("统计错误:")


# ===========================================================================
# code_executor_impl —— 沙箱
# ===========================================================================
# 沙箱里**不该有**的东西（`ALLOWED_BUILTINS` 里确实没有它们）
_DANGEROUS_BUILTINS = (
    "open", "eval", "exec", "compile", "__import__",
    "input", "globals", "locals", "getattr", "setattr", "vars",
)


def test_sandbox_globals_exclude_dangerous_builtins():
    """`create_safe_globals` 给出的 `__builtins__` 里，**不能有**能逃逸沙箱的那些名字。

    这条是这组用例里**唯一带安全含义**的 —— 它把"沙箱到底挡了什么"变成**可执行的事实**，
       而不是 docstring 里一句话。
    """
    from tools.code_executor_impl import create_safe_globals

    g = create_safe_globals()
    assert isinstance(g.get("__builtins__"), dict), "`__builtins__` 应当是被裁剪过的 dict"

    present = sorted(n for n in _DANGEROUS_BUILTINS if n in g["__builtins__"])
    assert not present, f"沙箱的 __builtins__ 里不该出现这些：{present}"


def test_sandbox_actually_blocks_open_at_runtime():
    """光看白名单不够 —— **真跑一段**试图读文件的代码，必须失败。

    用 `/etc/hosts`（任何机器上都有、且非敏感）当探针：**只看"会不会被挡"**，不读内容。
    """
    from tools.code_executor_impl import execute_python_impl

    out = execute_python_impl("print(open('/etc/hosts').read())")
    assert "NameError" in out, f"沙箱应当让 `open` 不存在（NameError），实际返回：{out!r}"


def test_execute_python_impl_runs_code_and_captures_stdout():
    from tools.code_executor_impl import execute_python_impl

    assert execute_python_impl("print(1+1)").strip() == "2"
    assert execute_python_impl("print('a'); print('b')").strip() == "a\nb"


def test_execute_python_impl_handles_no_output_and_errors():
    from tools.code_executor_impl import execute_python_impl

    assert execute_python_impl("x = 1").strip() == "代码执行成功，但无输出内容。"

    # 用 `1/0`（**由解释器自己抛** ZeroDivisionError）来测错误路径。
    #    **不能**用 `raise ValueError('boom')` —— 沙箱的 `ALLOWED_BUILTINS` 里
    #    **没有任何异常类** ⇒ 那样写会先 `NameError: name 'ValueError' is not defined`，
    #    测到的就不是"用户代码抛错"而是"沙箱白名单太窄"（那是另一回事，见下一条用例）。
    out = execute_python_impl("1/0")
    assert out.startswith("代码执行出错:"), out
    assert "ZeroDivisionError" in out, out


def test_sandbox_whitelist_allows_exception_classes():
    """沙箱白名单里**有**异常类 —— 这是 **N17 放开之后**的行为（2026-09-20 作者裁）。

    **这条用例的前身是反过来的**：它当时断言"白名单里**没有**异常类"，
       并注明「哪天放开了，这条会红、提醒你去更新记录」。
       ⇒ **它确实红了**，于是改成现在这样 —— **用例按预期完成了它的使命**。

    但它**不是简单地删掉断言**：它同时把"**哪些加了、哪些刻意没加**"变成可执行的事实 ——
       尤其是那几个**退出机制**（`BaseException` / `SystemExit` / `KeyboardInterrupt`）
       **必须仍然不在白名单里**（否则被执行的代码能**吃掉执行器的中断信号**）。
    """
    from tools.code_executor_impl import create_safe_globals

    builtins_ = create_safe_globals()["__builtins__"]

    # 应当【有】：常见的异常处理写法要靠它们（`try/except`、`raise`）
    for exc in ("Exception", "ValueError", "TypeError", "KeyError",
                "IndexError", "ZeroDivisionError", "AssertionError"):
        assert exc in builtins_, (
            f"沙箱里应当有 {exc}（N17 已裁「放开」）—— 否则 LLM 写的 try/except 跑不了"
        )

    # 应当【没有】：它们是【退出机制】，不是普通异常
    for exc in ("BaseException", "SystemExit", "KeyboardInterrupt", "GeneratorExit"):
        assert exc not in builtins_, (
            f"沙箱里**不该**有 {exc} —— 它是退出机制，放开等于允许被执行代码"
            "捕获/吃掉执行器的中断信号。要捕获，用 Exception 就够了。"
        )


def test_sandbox_allows_class_definition():
    """沙箱**能定义类** —— 这是 **N18 放开 `__build_class__` 之后**的行为（2026-09-21 作者裁）。

    **为什么放开它不增加能力**（本用例把这条论证变成可执行的事实）：
       白名单里**本来就有 `type`** ⇒ 「动态建类」**早就可达**了。
       加 `__build_class__` 只是让 `class` **这种写法**也成立。

    本用例同时锁死**边界没被放宽**：导入 / 文件 / 求值那几件仍然进不来。
    """
    from tools.code_executor_impl import create_safe_globals, execute_python_impl

    builtins_ = create_safe_globals()["__builtins__"]
    assert "__build_class__" in builtins_, (
        "沙箱里应当有 __build_class__（N18 已裁「放开」）—— 否则 LLM 写的 `class` 跑不了"
    )

    # ① `class` 语法现在真的成立（此前 NameError: __build_class__ not found）
    out = execute_python_impl("class Mine(Exception): pass\nprint('ok', Mine.__name__)")
    assert out == "ok Mine\n", out

    # ② 它**没有**新开能力：`type()` 这条路早就能做到同样的事（放开前就能跑）
    out = execute_python_impl('M = type("M", (Exception,), {})\nprint("ok", M.__name__)')
    assert out == "ok M\n", out

    # ③ **边界没被放宽** —— 导入 / 文件 / 求值仍然进不来
    for name in ("__import__", "open", "eval", "exec", "compile", "globals", "locals"):
        assert name not in builtins_, (
            f"沙箱里**不该**有 {name} —— 它是逃逸通道，放开会让 __build_class__ 变成真突破口"
        )

    # ④ 真·逃逸尝试必须失败（不是「没列出来」，是「跑不了」）
    for code in ('__import__("os").system("echo pwned")',
                 'open("/etc/passwd").read()',
                 'eval("1+1")'):
        out = execute_python_impl(code)
        assert out.startswith("代码执行出错:"), f"逃逸尝试竟然没被拦: {code!r} → {out!r}"


def test_execute_python_impl_truncates_long_output():
    """输出超长必须**截断并写明** —— 否则会把 LLM 的上下文冲掉。"""
    from tools.code_executor_impl import execute_python_impl, MAX_OUTPUT_LENGTH

    out = execute_python_impl(f"print('x' * {MAX_OUTPUT_LENGTH * 2})")
    assert out.endswith("... (输出过长，已截断)"), out[-80:]
    assert len(out) < MAX_OUTPUT_LENGTH * 2, "截断后不该还是原长度"


def test_execute_python_impl_rejects_natural_language_requests():
    """它只执行**已写好的代码**，不接受"需求描述" —— 那条拒绝逻辑必须真的生效。

    这是"工具描述"与"实现"一致性的用例：`code_executor.py` 的 docstring 对 LLM 说
       「只能执行已编写好的代码，不能生成代码」，这里验证**实现真的这么做了**。
    """
    from tools.code_executor_impl import execute_python_impl

    out = execute_python_impl("帮我写一段计算斐波那契的代码")
    assert "不是可执行的 Python 代码" in out, out


def test_sandbox_enforces_max_exec_time(monkeypatch):
    """**`MAX_EXEC_TIME` 真的生效** —— 死循环会被【硬杀】。

    这条用例守的是一件「**以前是假的**」的事（2026-09-21 · §十四 · ③-a）：
       此前 `MAX_EXEC_TIME` **只被定义、从未被使用** —— 实测死循环
       （`while True: i += 1`）**永不返回**；而工具描述却对 LLM **承诺**「最长执行时间：5秒」。
       以前执行层是「LLM 模拟执行」所以不痛；**N15 让执行层真跑代码之后，这就是个洞。**

    **为什么必须换子进程**：另外三条路都不成立 ——
       · `signal.alarm` 在 **worker 线程**里装不上（MCP 用 `asyncio.to_thread` 调工具）
       · 线程 `join(timeout)` **杀不掉 Python 线程** ⇒ 假超时（比没有更糟：给了错误的安全感）
       · `PyThreadState_SetAsyncExc` 对**紧循环不可靠**

    把超时压到 1 秒，免得这条用例本身拖慢整个套件。
    """
    import time

    import tools.code_executor_impl as CE

    monkeypatch.setattr(CE, "MAX_EXEC_TIME", 1)

    t0 = time.time()
    out = CE.execute_python_impl("i = 0\nwhile True:\n    i += 1")
    dt = time.time() - t0

    assert "超过最长执行时间" in out, f"死循环竟然返回了别的东西：{out!r}"
    assert dt < 10, f"用了 {dt:.1f}s —— 超时没生效（子进程没被杀掉）"
