"""MCP 服务端**不许往 stdout 写** —— 那是 stdio 传输通道。
"""
import ast
import subprocess
import sys
import time
from pathlib import Path

API = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层
SERVER = API / "tools/mcp_server.py"


def _bare_prints(path: Path) -> list:
    """找出**没带 `file=`** 的 `print(...)` 调用。

    **必须走 AST，不能 grep**：注释里也写着 `print(`（就是 `mcp_server.py` 里那段说明），
       grep 会把**说明文字**当成违规 —— 本仓为这个栽过
       （见 `app/tests/test_tool_registry_single_source.py` 的表头）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Name) and f.id == "print"):
            continue
        if not any(k.arg == "file" for k in node.keywords):
            out.append(node.lineno)
    return out


def test_no_bare_print_in_mcp_server():
    """`mcp_server.py` 里**一个裸 `print` 都不许有**。

    反证（实测做过）：把任意一处改回 `print(...)` ⇒ **本用例红并点名行号**。
    """
    bad = _bare_prints(SERVER)

    assert bad == [], (
        f"🔴 `app/tools/mcp_server.py` 这些行往 **stdout** 写（那是 MCP 的传输通道）：{bad}\n"
        f"   ⇒ 改成 `print(..., file=sys.stderr)` 或 logging。"
    )


def test_the_guard_itself_can_go_red(tmp_path):
    """**反向对照**：本守卫必须真的抓得住 —— 不是一个"永远绿"的空壳。

    没有这一条，一个"AST 解析出错 ⇒ 返回空列表"的实现也能让上面那条绿。
    """
    fake = tmp_path / "fake_server.py"
    fake.write_text("print('pollutes stdout')\n", encoding="utf-8")

    assert _bare_prints(fake) == [1], "🔴 守卫抓不住裸 print —— 它是个空壳"


def test_real_server_writes_nothing_to_stdout():
    """**行为面**：真起一个服务端子进程，**它的 stdout 必须是空的**。

    上半条（AST）管的是"源码里有没有"，这条管的是"**真跑起来到底写没写**"。
       两者是两件事 —— 例如 `sys.stdout.write(...)` / 某个库替我们写，AST 那条看不。

    起子进程 **3 秒**（够它 import 完并打到"启动中"那行），然后收工。
    """
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(API),
    )
    try:
        time.sleep(3)
    finally:
        proc.terminate()
        out, err = proc.communicate(timeout=15)

    assert out == b"", (
        f"🔴 服务端往 **stdout** 写了东西 —— 那是传输通道：{out[:200]!r}"
    )
    # 反向对照：那句话**应该**还在，只是走 stderr —— 别让"删掉那行日志"也能过
    assert b"MCP Server" in err, (
        f"🔴 启动日志不见了（它应该走 stderr）—— 别用'删日志'来让上一条绿。stderr={err[:200]!r}"
    )
