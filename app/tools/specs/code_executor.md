# `app/tools/code_executor.py` + `app/tools/code_executor_impl.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **可用 · 已进容器** —— 改前是「宿主同权限的子进程 + 白名单」 |
| **对外提供** | `code_executor.execute_python`（**`@tool`，给 LLM 的那一层**）<br>内核（`_impl`，**纯 stdlib**）：`execute_python_impl` · `run_in_sandbox_subprocess` · `create_safe_globals` · `ALLOWED_BUILTINS` · `ALLOWED_MODULES` · `MAX_EXEC_TIME` · `MAX_OUTPUT_LENGTH` |
| **谁在用** | `mcp_server.TOOLS`（⇒ 各图按派生表拿到它）· `api_v1_agent.py` 的 `/agent/execute_code` 直接 `.invoke()`<br>⚠️ **`agent_graph` / `agent_checkpointer` 显式排除它**（见下方 🟡） |
| **测试** | `app/tests/test_impl_modules.py` · `app/tests/test_code_executor_remote.py`（**4**）· `app/tests/test_plan_execute_tools.py` · `app/tests/test_tool_registry_single_source.py`（那 2 条） |

**两个文件的分工**（⛔ 别合并）：

| 文件 | 是什么 | 依赖 |
|---|---|---|
| `code_executor.py`（150 行） | **`@tool` 外壳** —— 给 LLM 读的 docstring + 缓存包装 | langchain · `tool_cache` |
| `code_executor_impl.py`（292 行） | **纯 stdlib 内核** —— 白名单 · 子进程 · 超时硬杀 · 远端路径 | ⚠️ **只 import 标准库** |

⚠️ **依赖方向单向**：`code_executor.py` 引用 `_impl`；⛔ **`_impl` 绝不反向引用**。
📌 `_impl` 的这条不变量**有实际用途**：它让沙箱逻辑能**脱离 langchain 单测**。

## ✅ 做了什么

- **AST 白名单求值环境**（`create_safe_globals`）：`ALLOWED_BUILTINS` + `ALLOWED_MODULES`
  ⚠️ 例外类（`ValueError` 等）与 `__build_class__` 是 **/21 作者裁「放开」**的
  —— 依据是「它们**不提供逃逸能力**」，⛔ 不是"为了方便"
- **子进程 + 5 秒硬杀**（`MAX_EXEC_TIME`）：⚠️ **线程超时杀不掉 Python 线程** ⇒ 死循环会把进程拖垮
- **输出截断**（`MAX_OUTPUT_LENGTH = 2000`）
- **意图检测**：把「帮我写一段代码」这类**需求描述**挡在**执行之前**
- 🔴：执行**进容器** —— `EXECUTOR_URL` 有值走远端，没值回落本地子进程

## 🟡 做到哪 / 缺什么

- ✅ ~~**`execute_python` 暂不进 `agent_graph` / `agent_checkpointer`**~~ ⇒ **已放开**（作者同意「甲」）：
   立的两条理由里，① 无隔离**已由 解决**，② 无审批**不是那两张图特有的**
  ⇒ 抠掉它没挡住什么，只制造不一致。**工具表现在各图一致**（判据：`test_execute_python_is_in_every_graph_tool_table`）。
- ✅ **`execute_python` 【不进】`SENSITIVE_TOOLS`** —— 作者已裁「保持现状」（§二·11）。
  依据：**敏感 = 这个工具会把数据发到本机之外**，而它只在**容器内**跑。
  🔴 **但容器那层无论如何不能撤** —— 白名单是**第二层**，⛔ 不是容器的替代品（见下表）。
- ⬜ **`calculator` 的间接提示注入面**（与执行器同族的"LLM 生成输入"问题）—— 记在  §二 8
- ⚠️ **容器方案下 `EXECUTOR_TIMEOUT = MAX_EXEC_TIME + 10`** —— ⛔ 别设成与 `MAX_EXEC_TIME` 相同
  （那会把"执行器还没跑完"误判成"远端超时"，**正常的慢代码变成报错**）

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「它是"安全沙箱"，所以安全」 | 🔴 **容器之前它【不是】** —— 白名单是**进程内**的，一旦被绕过的就是**宿主**。**容器才是那道 OS 级隔离**，白名单是**第二层**，⛔ 不是替代品 |
| 「白名单里没有 `os` / `open` ⇒ 读不了文件」 | ⚠️ 对**沙箱内**成立。但**子进程本身**与宿主**同权限、同文件系统** ⇒ 逃逸 = 拿到宿主。这就是 要解决的 |
| 「`_run_remote` 失败会回落本地」 | 🔴 **⛔ 不会** —— 回落**只由配置**（`EXECUTOR_URL` 空不空）决定。若"失败就回落"，**运维停了执行器 ⇒ 代码又回到宿主跑，而没人会发现**。实测：停掉执行器 ⇒ 返回错误、**没有返回 42** |
| 「`run_in_sandbox_subprocess` 只是个内部工具」 | ⚠️ **它是执行沙箱的【唯一】落点** —— **本地路径**与**执行器服务**都调它。⛔ 别在别处再抄一份超时 / 报错文案 |
| 「`MAX_EXEC_TIME = 5` ⇒ 每次最多跑 5 秒」 | ⚠️ 是**墙钟 5 秒**，⛔ 不是"5 秒的计算量"。容器限 `cpus: 0.5` ⇒ **并发一上来，同一段码会慢好几倍** ⇒ 可能撞 5 秒（§3.6 有实测数字） |
| 「`code_executor.py` 最下方那段 Docker 代码是活的」 | ⛔ **那是方案记录**（作者要求保留），**未生效**。⛔ 别照它去改实现 —— 里面那个 `docker.from_env()` 正是写法（要挂 `docker.sock`） |
| 「没有执行器容器时也能用 `execute_python`」 | 🔴 **⛔ 不能** —— 那种情况下它**会被显式地【不注册】**（⛔ 不是「回落本地子进程」：那是**宿主同权限**， §3.7）。<br>⚠️ **那个开关 已删**（随单容器那套部署下线，且没人再设它）—— **将来要支持那种部署时，照这个口径重做，⛔ 别改成回落** |

## 关联

- **决策** ⇒ （进容器 · 全量）·  / （`eval` → `safe_math`）· （工具清单收口）
- **容器那侧** ⇒ `app/<组>/specs/` 下的 `executor_server` 那份 ·  §3.3（硬化逐条）
- **邻居** ⇒ `app/tools/specs/safe_math.md`（`calculator` 的安全实现，同族的"LLM 输入"问题）
- ⚠️ **`EXECUTOR_URL` 的注入处** = `docker-compose.yml` 的 `api` 服务 —— 改它要动那里，⛔ 不是改代码默认值
