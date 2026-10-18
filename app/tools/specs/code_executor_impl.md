# `app/tools/code_executor_impl.py`

## ✅ 做了什么

**代码执行器的纯 stdlib 内核**：**沙箱白名单**（`create_safe_globals`）+ 执行逻辑
（`execute_python_impl`）。`app/tools/code_executor.py` 只是给它套一层 `@tool` 外壳。

## 🟡 做到哪 / 缺什么

- **6 处产品代码引用 · 3 个测试文件提到**。
- 🔴 **有两条执行路径**，由环境变量切换：
 | 条件 | 走哪 |
 |---|---|
 | `EXECUTOR_URL` **有值** | **远端执行器容器**（`app/tools/executor_server.py`） |
 | 无值（**默认**） | **回落本地子进程** ⇒ 本机开发 / 离线 CI **不受影响** |
- ⚠️ 容器里由 `docker-compose.yml` 的 `api` 服务注入 `EXECUTOR_URL=http://executor:8000`。
- 🔴 **依赖方向单向**：本模块**只 import 标准库**；`code_executor.py` 引用本模块，⛔ 不反向。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **"执行"跑在【子进程】里，不是 `exec()` 在本进程** ——
   `_SANDBOX_CHILD` 是一段**脚本字符串**，父进程用 `sys.executable -c` 起它。
   ⚠️ 所以 `here = os.path.dirname(os.path.abspath(__file__))` 是**传给子进程当 `sys.path`** 的
   ⇒ ⛔ **本文件搬家时必须一起核这一行**（搬进 `app/tools/` 时它是**同目录**，
   所以恰好没坏 —— **那是巧合，不是设计**）。
2. 🔴 **白名单里那两项是【作者裁"放开"】的，⛔ 别当成漏洞删掉**：
   - **异常类**：⛔ 不加的话 `try/except` 直接不可用，
     而**异常处理是最常见写法之一、且从代码上看不出来**。
   - **`__build_class__`**：让 `class` 语法成立。
     ⚠️ **放开它【不增加任何新能力】**（实测过）—— 它只是语法入口。
3. ⚠️ **远端路径要给执行器【自己那 5 秒】留余量** ——
   执行器容器内部还有一次子进程往返 ⇒ 两边的超时**不是同一个数**，⛔ 别对齐成一样。
4. ⚠️ **删过两个 import（`io` / `contextlib`）** ——
   它们**只在子进程那段字符串里用**（子进程自己 import）⇒ 父进程里已无引用。
   ⚠️ 注释里写明「**不是清理历史遗留，是本次改动自己造成的**」—— 这类要**如实标**，⛔ 别伪装成顺手清。

## 关联

- `app/tools/specs/code_executor.md` —— 上层 `@tool` 外壳
- `app/tools/specs/executor_server.md` —— 远端那条路径的服务端
- `docker-compose.yml` 的 `executor` 服务 —— 硬化配置（`read_only` / `cap_drop: ALL` / `no-new-privileges`）
- * —— 「执行器进独立容器」的裁定


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/tools/code_executor_impl.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ **内容不改写**；只清理内部编号 / 批次号这类工作日志坐标。

**依赖方向单向**：本模块**只 import 标准库**（`sys` 未用故未列）。
`code_executor.py` 引用本模块；**本模块绝不反向引用** `code_executor.py`。

`code_executor.py` 只是给 `execute_python_impl` 套一层 `@tool` 外壳（供 Agent 调用）。
