"""
代码执行器：安全沙箱（**面向 LLM 的工具外壳**）。
让 Agent 能编写并执行 Python 代码，处理精确计算和自动化任务。
更多高级沙箱方案：本代码最下方有一段 **Docker 容器隔离的方案记录**。
   而本行仍在声称它在。**现已加回**（作者明确要求），并标明**未生效**。落地点。
更多的方案：RestrictedPython nsjail / Firejail 云函数服务 Pyodide
等等方案有局限 和安全性，详细对比在"#第68天：代码执行器——安全沙箱执行Python代码"
详细查看，且有"代码执行器"更多具体用法拓展，高级沙箱的混合布置方案和思路。

（纯 stdlib，可脱离 langchain 单测）。本文件只剩一层 `@tool` 外壳。
为避免老调用方失效，下列常量**在此处重新导出**（`ALLOWED_BUILTINS` / `ALLOWED_MODULES` /
`MAX_EXEC_TIME` / `MAX_OUTPUT_LENGTH` / `create_safe_globals`）——
但 `import code_executor` 仍会拉 **langchain**；**只想用沙箱逻辑就 import `code_executor_impl`**。
"""
from langchain_core.tools import tool

from tools.tool_cache import cached_tool
from tools.code_executor_impl import (
    ALLOWED_BUILTINS,
    ALLOWED_MODULES,
    MAX_EXEC_TIME,
    MAX_OUTPUT_LENGTH,
    create_safe_globals,
    execute_python_impl,
)

__all__ = [
    "execute_python",
    "create_safe_globals",
    "ALLOWED_BUILTINS",
    "ALLOWED_MODULES",
    "MAX_EXEC_TIME",
    "MAX_OUTPUT_LENGTH",
]

# **仍然包**：这样「哪些工具带缓存」只有**一个地方**回答（`TTL_BY_TOOL`），
#    不是"有的工具没包、有的是 TTL=0"两种形状混着 —— 后者下一个人要读两处才知道。
# **`tool_cache` 绝不许 import 进 `code_executor_impl`** ——
#    那个文件的不变量是「**只 import 标准库**」（写在它文件头）。
_cached_execute = cached_tool(name="execute_python")(execute_python_impl)


@tool
def execute_python(code: str) -> str:
    """
    执行一段已编写好的 Python 代码，并返回执行结果。

    **重要：此工具只能执行代码，不能生成或编写代码。**

    **重要：不要写 `import`！**
    下列模块**已经直接可用**（已注入到执行环境里），**直接调用即可**：
    `math` · `json` · `datetime` · `collections` · `itertools` · `functools` · `re` · `statistics` · `random`
    例：写 `print(math.sqrt(16))` ／ **不要**写 `import math` （会报 `ImportError: __import__ not found`）

    **安全限制：**
    - 禁止文件操作、网络访问、系统命令
    - 最长执行时间：5秒
    - 最大输出长度：2000字符

    **适用场景：**
    - 执行简单的数学计算
    - 处理 JSON 数据
    - 日期时间格式化
    - 正则表达式匹配和替换
    - 简单的列表/字典操作

    **不适用场景（会失败）：**
    - 需要导入大型库（如 numpy, pandas）
    - 需要网络请求
    - 需要读写文件
    - 生成新的代码

    输入必须是一段完整的、可立即执行的 Python 代码字符串。
    """
    # 上面这段 docstring 是**给 LLM 读的工具描述**，故留在本层；
    #    真正的逻辑在 code_executor_impl.execute_python_impl（纯 stdlib）。
    return _cached_execute(code)


# ============================================================================
# 以下是**方案记录**，**不是正在生效的实现**。
'''
以下只是最基础的 简单的Docker 容器隔离 代码，不够完整，只是基础的代码。
更详细完整的代码和方案在“第68天：⭐️代码执行器 安全沙箱执行Python代码”“将代码执行器升级为 Docker 容器隔离 执行方案和代码。”

Docker 容器隔离

原理：为每次代码执行启动一个全新的、最小化的 Docker 容器（如 python:3.10-slim），将代码注入容器，执行后立即销毁。容器天然提供进程、文件系统、网络和内存的隔离。
优点：

近乎完美的隔离性，即使代码有恶意也几乎无法影响宿主机。
可自由配置容器环境（预装库、资源限制等）。
生态成熟，工具链丰富。
缺点：

启动延迟高：容器启动需要1-3秒，不适合高频实时调用。
资源消耗较大（每个容器占用内存、CPU）。
需要管理 Docker 环境和镜像。
推荐实现：

python
import docker
import tempfile
import os

client = docker.from_env()

def run_code_in_docker(code: str) -> str:
    # 将代码写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        # 启动容器执行代码
        container = client.containers.run(
            image='python:3.10-slim',
            command=f'python /code/{os.path.basename(tmp_path)}',
            volumes={os.path.dirname(tmp_path): {'bind': '/code', 'mode': 'ro'}},
            network_disabled=True,      # 禁用网络
            mem_limit='128m',           # 内存限制
            cpu_period=100000,
            cpu_quota=50000,            # 限制CPU使用
            remove=True,                # 执行后自动删除
            timeout=10,                 # 超时10秒
            stderr=True,
        )
        return container.decode('utf-8')
    finally:
        os.unlink(tmp_path)
'''
