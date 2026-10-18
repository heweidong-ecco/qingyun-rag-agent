# `app/tools/tool_health.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **可用 —— 但它只【记录】健康状态，⛔ 不做降级** |
| **对外提供** | `HEALTHY` / `UNHEALTHY` / `UNKNOWN` · `TEST_ARGS_MAP` · `update_tool_health(tool_name)` · `get_tool_health(tool_name)` · `run_health_check()` · 🔴 `_tool_health`（**私有名，但被外部 import**） |
| **谁在用** | `app/main.py:700/705/719`（**启动自检**）· `app/tools/mcp_server.py:61/71`（⭐ **降级在这里**）· `app/routing/api_v1_agent.py:46`（import 了 `run_health_check` / `get_tool_health` / `UNHEALTHY` / **`_tool_health`**）⇒ 两个端点 `:1518` `GET /agent/tool_health` · `:1525` 刷新 |
| **测试** | 🔴 **专属用例零条** —— `app/test_tool_health.py` **不存在**。只有 `app/tests/test_pending_approvals.py` 等**顺带提过工具名** |

## ✅ 做了什么

**通过 MCP 协议逐个探测工具能不能用**，把结果记在**进程内的一个 dict** 里：

```python
_tool_health: Dict[str, Dict] = {}   # {工具名: {"status": ..., "last_checked": ...}}

async def run_health_check():        # 启动时调一次
    for tool_name in TEST_ARGS_MAP:  # 🔴 注意是 TEST_ARGS_MAP，不是 mcp_server.TOOLS
        await update_tool_health(tool_name)
```

探测方式是**真调一次**：`_check_tool_via_mcp` 走 `agent_graph_advanced.call_mcp_tool`，
按 `TEST_ARGS_MAP` 给的安全参数执行 —— 例如 `calculator` 用 `{"expression": "1+1"}`、
`web_search` 用 `{"query": "test"}`（⇒ **会真发一次外网请求**）。

**降级不在这里** —— `app/tools/mcp_server.py` 的 `list_tools()`（`:63-73`）把 `UNHEALTHY` 的工具**移出工具清单**。
那才是当前实际生效的降级机制。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| 🔴 **`_tool_health` 是模块级进程内存** | **重启即清空** · 每个进程**各有一份** ⇒ 多进程部署下**不共享**。⚠️ 且没有落盘、没有 TTL —— `last_checked` 写了但**全仓没有一处读它** |
| ⚠️ **探测表与注册表要【手动对齐】** | 🔴：`TEST_ARGS_MAP` 有 **7** 项（`calculator` / `date_today` / `date_calc` / `json_extract` / `stats` / `web_search` / `execute_python`），另两项**注释着**。⚠️ 与 `mcp_server.TOOLS` 的 7 项**逐名一致**。<br>🔴 **不在表里 ⇒ 该工具【永远不被探测】（静默）** —— 已由 `app/tests/test_tool_registration_completeness.py` 补成**会红的断言**（改前**零守卫**） |
| ⚠️ **没有任何"健康度随时间衰减"** | 探过就是探过了，`last_checked` 不再被用 ⇒ 一个工具坏掉后**只有等下次手动刷新**才知道 |
| ⚠️ **零专属测试** | 探测失败 / 字符串误判 / 表外工具 —— 都没有用例 |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. ✅ 【已修】注释与代码互相矛盾那条

**改前**，`app/tools/tool_health.py` 的 `TEST_ARGS_MAP` 上方那句注释写着：
> 「`run_health_check` 是**按 `mcp_server.TOOLS` 遍历**的，表里多两项不会被查到。」

🔴 **那句是假的** —— 实际代码是 `for tool_name in TEST_ARGS_MAP`，**遍历的是本表**。
⚠️ 连带它那句**结论**也对不上：按实际代码，**在 `TEST_ARGS_MAP` 里取消注释那两行，是【会】被探测到的**
（真正卡住它们的是 `mcp_server.TOOLS` 里那两行也没开——那边注释写对了）。

✅ **已按作者口径「注释也得是真的」改准**：注释现在写的是
「它遍历的是**本表**（`run_health_check()` 里的 `for tool_name in TEST_ARGS_MAP`）**结论没变、理由变了**」。
⚠️ **改的是【理由】，⛔ 不是结论** —— 「多两项不会被查到」这个**结论依然成立**（它们不在 `mcp_server.TOOLS` 里）。
⚠️ **新注释里不写行号** —— 本仓：行号锚点系统性漂移（这次一加行，原 `:103` 就变成了 `:111`）。

🔴 **判据（可打印）**：
```bash
grep -n 'for tool_name in' app/tools/tool_health.py
grep -n 'mcp_server.TOOLS' app/tools/tool_health.py      # ⇒ 只应命中注释行，⛔ 不应命中代码
```
⇒ **两处口径不一致时，跑得起来的那句才算数。**

### 2. 🔴 **表外的工具返回 `UNKNOWN`**，而"表外"= **永不被探测**

`get_tool_health(name)` 对**不在 dict 里**的名字返回 `UNKNOWN`。而 dict 的键**只来自 `TEST_ARGS_MAP`**。

⇒ **`UNKNOWN` 有两种成因，长得一模一样**：① 还没探过 ② **永远不会被探**。
⚠️ 后者是**永久**状态，但症状与"刚启动还没来得及探"**完全一样**。

⇒ 这与本仓那条立场同源：**「从不命中」与「没人违规」在机器痕迹上完全一样。**

### 3. 🔴 `GET /agent/tool_health` 返回的是**原始 dict**，⛔ 不是 `get_tool_health()` 归一化过的

`app/routing/api_v1_agent.py:1522` 直接 `return {"tools": _tool_health, ...}`。

⇒ **从未被探测的工具在那个 JSON 里是「键不存在」，而不是 `"unknown"`**。
⚠️ **同一件事（不知道）在两个接口上长得不一样**：
内部查 `get_tool_health()` 得字符串 `"unknown"`；接口读到的却是**没有这个键**。
⇒ 消费端如果写 `body["tools"][name]["status"]`，会**`KeyError`**，而不是拿到 `unknown`。

⚠️ 顺带：`_tool_health` 是**下划线开头的私有名**，却被 `api_v1_agent.py:46` 跨模块 import。
⇒ 它是**事实上的公开接口**了 —— 改它的形状会**同时**打穿那个端点。

### 4. 🔴 健康判据是**字符串匹配** —— 工具有可能被误判为不健康

```python
if result and "工具调用失败" not in result and "未找到工具" not in result:
```
⇒ 一个**正常返回**里恰好含这两个短语的调用（比如用户问的正是"工具调用失败是什么意思"）会被记成 `UNHEALTHY`
—— 而 `UNHEALTHY` 会让 `list_tools()` **把它移出 LLM 的工具表**。
⇒ **一次误判的后果不是"多探一次"，是"该工具对模型消失"**（直到下次刷新）。

⚠️ 探测参数是**固定字面量**（`"1+1"` / `"test"`），所以日常不会撞上；
但**判据的形状**是字符串，不是结构化返回值 —— ⛔ 别把它读成"在检查工具真的坏了"。

### 5. ⚠️ `run_health_check()` 会**打外网**（`web_search` 那条）

`TEST_ARGS_MAP["web_search"] = {"query": "test"}` ⇒ 启动自检 + **每次** `POST /agent/tool_health` 刷新
都**真发一次 `cn.bing.com` 请求**（超时 20 秒，见 `app/tools/specs/search_tools.md`）。
⇒ ⚠️ **刷新端点是同步 await 的** ⇒ 网络不通时那次请求会**卡住到超时**。

### 6. ⚠️ `update_tool_health` **没有外部调用者**

全仓只有 `run_health_check` 调它（判据：`grep -rn 'update_tool_health' app/ | grep -v tool_health.py` ⇒ 空）。
⇒ 它是**内部步骤**，⛔ 别当"可以单独探一个工具"的公开入口用。

### 7. ⚠️ 已删的 `FALLBACK_MAP` / `get_fallback_tool()` —— 看 git 历史会看到

**全仓都没有定义** ⇒ 就算接上线，返回的也是一个**不存在的工具名**。
⇒ ⛔ **别把"降级"理解成"换一个备用工具"** —— 本仓的降级是**把不健康的工具移出清单**。

## 关联

| 文档 | 说明 |
|---|---|
| MCP Server 模块（`app/tools/mcp_server.py`） | ⭐ **降级真正发生的地方**（`list_tools()` `:63-73`）。⛔ **它还没有 spec** ⇒ 本表不给路径 |
| `app/tools/specs/search_tools.md` | 探测表里唯一会**打外网**的那个工具 |
| `app/routing/specs/api_v1_agent.md` | 两个健康端点（含 `_tool_health` 直出） |


---

## 📎 从代码 docstring 移出的设计说明

> 来源 = `app/tools/tool_health.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ **内容不改写**；只清理内部编号 / 批次号这类工作日志坐标。

本文件原先自称「工具健康检查**与自动降级**」，并带一份 `FALLBACK_MAP` + `get_fallback_tool`。
   实测那套是**双重死代码**：
     ① **全仓零调用**（`grep -rn 'get_fallback_tool|FALLBACK_MAP' app/` 除定义处 0 次）
     ② **连它引用的名字也不存在** —— `fallback_search`、`chat` 全仓都无定义
        ⇒ **就算接上线，它返回的也是一个不存在的工具名。**
   ⇒ 它不是"预留的能力"，是**一段从来没能工作过的代码**。
   真正的降级是"**把不健康的工具移出清单**"，不是"换一个备用工具"—— 已在上方写明。
