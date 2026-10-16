# `app/tools/simple_tools.py`

## ✅ 做了什么

**面向 LLM 的工具外壳**（`@tool` 装饰层）：`calculator` · `date_today` · `date_calc`
· `json_extract` · `stats`（**五个**）。

## 🟡 做到哪 / 缺什么

- **10 处产品代码引用 · 4 个测试文件提到**。
- **逻辑不在本文件** —— 已搬到 `app/tools/simple_tools_impl.py`（纯 stdlib）。
- 🔵 **从【两个】变【五个】**（新增 `date_calc` / `json_extract` / `stats`）。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **本文件是【壳】，逻辑在 `simple_tools_impl.py`** ——
   改行为要改那边；本层只有 `@tool` 装饰 + **docstring（= 给 LLM 的工具描述）**。
   ⚠️ 反过来：**⛔ 别把 langchain 依赖塞进 `_impl`** —— 那个文件的不变量就是「只用标准库」。
2. 🔴 **`import simple_tools` 会拉 langchain**；**只想用逻辑就 import `simple_tools_impl`**。
   ⇒ 写单测时 import 错的那个 ⇒ **测试平白多一个重依赖**。
3. 🔴 **缓存包在 `@tool` 这一层**：
   `cached_tool` 的 **`name=` 必须显式给** —— 它缺省用 `func.__name__`，
   而外壳函数名与工具名**不一定一致** ⇒ **缓存键会错**（不报错，只是命中率假高/假低）。
   ⛔ **`tool_cache` 绝不许 import 进 `_impl`**（那会破坏第 1 条的不变量）。
4. ⚠️ **docstring 是【给 LLM 读的】** ⇒ 改它 = 改模型看到的行为。
   ⛔ 别把它当普通注释删。

## 关联

- `app/tools/specs/simple_tools_impl.md` —— **逻辑在那边**（本文件是壳）
- `app/tools/specs/mcp_server.md` · `app/tools/specs/tool_cache.md`
- `app/tests/test_tool_registry_single_source.py` · `test_safe_math_wiring.py` —— 两层不变量都有守卫
