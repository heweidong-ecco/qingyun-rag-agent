# `app/tools/simple_tools_impl.py`

## ✅ 做了什么

简单工具的**纯 stdlib 内核**（**无 langchain 依赖**）：`calculator_impl` / `date_today_impl`
/ 那三个新工具的实现在这里。

## 🟡 做到哪 / 缺什么

- **5 处产品代码引用 · 3 个测试文件提到**。
- 🔴 **依赖方向单向**：本模块**只 import 标准库**；`simple_tools.py` 引用本模块，
  **本模块绝不反向引用**它。
- ⚠️ **一个【有意】的例外**：多了 `from core.safe_math import calculate`
  —— 它**不破坏不变量**，因为 `safe_math` **自己也只用标准库**。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **返回值形状是【逐字约定】的**：成功 ⇒ `str(结果)`；失败 ⇒ `计算错误: {原因}`
   —— **⛔ 失败也返回字符串，⛔ 不抛异常**。
   ⇒ 改成抛异常会让**上游的 `else` 分支**接管，而那是「**工具不存在**」的语义 ⇒ **两件事混成一件**。
2. 🔴 **三个函数都是【纯函数】** —— 同一组入参结果永远一样（**连失败结果也稳定**）。
   ⇒ 这条是**缓存**成立的前提。⛔ 别引入时间/随机/IO。
3. 🔴 **`start_date` 必填，【故意】⛔ 不默认"今天"**
   —— 一默认，「(今天, 1)」这组入参就会**每天变**，纯函数那条不成立、缓存也会串味。
4. 🔴 **日期区间要【先整串校验、再拆】**：
   直接用 `re.findall` 扫 `a..b` 会**悄悄当成 `a.b`** ⇒ 解析成一个错的日期而**不报错**。
5. ⚠️ `calculator` 曾经是 `eval(expression)`，**换成 `safe_math.calculate`**
   —— ⛔ **别改回去**；有专门的守卫钉着（`app/tests/test_safe_math_wiring.py`）。

## 关联

- `app/tools/specs/simple_tools.md` —— 上层外壳（**docstring 是给 LLM 的**）
- `app/tools/specs/safe_math.md` —— 唯一被允许的跨模块依赖


---

## 📎 原先写在代码 docstring 里的设计说明

> 来源 = `app/tools/simple_tools_impl.py` 的模块 docstring（产品仓口径：模块 docstring 只留「摘要 + 怎么用」，设计论证归 spec）。⚠️ 整段**原样搬来**，⛔ 未改写。

**依赖方向单向**：本模块**只 import 标准库**（`datetime` / `json` / `re` / `statistics`）。
`simple_tools.py` 引用本模块；**本模块绝不反向引用**它。

**例外**：多了一个 `from safe_math import calculate`。
   它**不破坏上面那条不变量** —— `safe_math` 自己也**只用标准库**（`ast` / `math` / `operator`），
   没有任何 langchain 依赖。**这是本模块唯一允许的外部依赖，别再往里加第二个。**

函数名带 `_impl` 后缀是**故意的** —— 提醒读者：**这不是给 LLM 看的工具**。
面向 LLM 的工具描述（docstring）留在 `simple_tools.py` 的 `@tool` 那一层。
