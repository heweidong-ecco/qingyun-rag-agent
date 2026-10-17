# `app/rag/answer_with_citations.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 **后端可用 · 但【默认不启用】—— 且零测试** |
| **对外提供** | `generate_answer_with_citations(contexts, question, llm, *, user_name, thread_id)` —— ⚠️ **`thread_id` 必填**（原先默认 `"default"`，那正是缺陷） |
| **谁在用** | `rag_pipeline.py:182`（仅当 `generate_answer=True` **且** `citations=True`） |

## ✅ 做了什么

- 生成带 **`[来源:n]` 行内引用标记**的 LLM 答案
- 返回 `answer` + **结构化的 `sources`**（`[{"index","id","source","content","content_preview"}]`）
- 支持**无据拒答**（资料不足时输出「根据现有资料，无法回答」）—— `docs/展示与实测.md:50-51` 有实测
- 🔴 **生成后记一笔真账**（`token_tracker.record_from_response`，`:68`），
  身份由**必填** keyword `user_name` 传进来（`:35`）
- 🔵 **（契约 A）：`sources` 每条补 `index` 与 `content`（全文）** ——
  与**流式出口**（`api_v1_rag.py:744`）**逐字同构**：同一个 `i` 同时写进 prompt 的 `[文档{i}]`
  和帧里的 `index`；`content` 是**追加**（老前端用的 `content_preview` 仍在，⛔ 不是替换）。
  ⚠️ **这两份是【手工对齐】的，没有任何东西钉住它们同构** —— 见下方误判表最后一行。

## 🟡 做到哪 / 缺什么

- 🔴 **零测试覆盖**（`docs/说明/测试.md` §六 **#6**）
  ⚠️ **澄清**：`app/tests/test_rag_billing_wiring.py` 里那两条测的是**记账**，
  ⛔ **不是**引用格式 / 无据拒答 ⇒ **上面这条欠账依然成立**。
- 🔵 **引用点不开这件事【在流式那条链上已经解决】** ——
  `GET /chat` → `app/static/web/chat.html` 能点开引用、卡片里是**全文**（手工验过）。
  ⚠️ **但本文件走的是【非流式】链**（`rag_pipeline.py:182` ⇒ `/rag/search`）——
  那条链**没有前端**。⇒ 准确表述是「**流式那半 ✅ · 非流式这半仍是后端有、界面没有**」。
- ⚠️ **它是硬门 B 的【后端】主体** —— 硬门 B 的界面那半 在**流式链**上打通了，
  **非流式链**（= 本文件）仍是 ⬜。
- ⬜ **`index` 与 `content` 那两条字段没有用例** —— `app/tests/test_frontend_contract.py` 打的是
  **流式**出口（`/rag/stream_search` 的 `sources` 帧），**本文件的 `sources` 不在其中**。

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 「引用溯源做完了」 | ⚠️ **要分链说**：**流式**那半（`/rag/stream_search` + 对话页）✅ 已通；**本文件这条非流式链** ❌ 仍没有界面。⚠️ 本文件**不是**对话页消费的那一份 —— 看到这里的 `sources` 被改好，⛔ **别推断"对话页引用的就是它"** |
| 「调用它就会有引用」 | 🔴 **不会** —— **`citations` 默认 `False`**（`app/routing/schemas.py:16`），**`generate_answer` 默认也 `False`**（`:14`）⇒ **两个都不显式打开 ⇒ 连 `answer` 字段都没有，更不会有引用** |
| 「README 说的点击溯源是真的」 | 🔴 **那句已在 更正** —— 原文写「支持点击溯源到原始文档块」，**"点击"不成立** |
| 「`llm \| StrOutputParser()` 是常规写法，无妨」 | 🔴 **在本文件里它是个坑** —— `StrOutputParser` 把 `AIMessage` **剥成 `str`**，`usage_metadata` **随之丢光** ⇒ 记账**拿不到数**、且**静默跳过**（不抛错）。<br>⇒ 本文件**直调 `llm.invoke(messages)`** 取消息本体（`:62`）。⛔ **别"顺手统一成链式写法"** —— 那等于把记账再关掉一次。<br>📌 守卫：`app/tests/test_rag_billing_wiring.py::test_no_llm_usage_is_swallowed_by_string_output_parser`（**全文件扫描**，不依赖任何前置识别） |
| 「`user_name` 可以不传」 | 🔴 **它是必填 keyword**（`:35`）—— 漏传 = `TypeError`。⛔ 不是"静默记成 `unknown`"（明文反例）。 |
| 🔴 **「`thread_id` 有默认值 `"default"`，不传也行」** | ⛔ **它也是必填 keyword、没有默认值了**。<br>改前那个默认值**正是缺陷本身**：调用点不传 ⇒ **所有调用者的账挤进 `"default"` 一个桶**，而会话闸查的是**调用者传的那个** ⇒ **闸恒看到 0**（那条路上的会话上限永远不触发）。<br>📌 判据：`grep -n 'def generate_answer_with_citations' -A 3 app/rag/answer_with_citations.py` ⇒ 形参表里 **`thread_id: str`**，⛔ 不带 `= "default"`。 |
| 🔴 **「流式的 `sources` 帧和这里的 `sources` 是同一段代码产出的」** | ⛔ **不是** —— 是**两段**代码（本文件 `:53` · `api_v1_rag.py:744`），只是**手工写成了同一个形状**。⚠️ **没有任何东西钉住这个同构**：改一边忘另一边 ⇒ 两个出口**悄悄分叉**，而两边各自的用例**都还是绿的**。⇒ 要动其中一个，**必须同时核另一个**（`grep -n '"index"' app/rag/answer_with_citations.py app/routing/api_v1_rag.py`）。 |
| ⚠️ **「前端拿 `content_preview` 显示卡片」** | ⛔ 对话页拿的是 **`content`（全文）** —— 硬门 B 的判定是"点开能展开原文"（`通用/四硬门-定义与验收标准.md`），**摘要过不了**。`content_preview` 是给**老前端/别处**留的，⛔ 别以为它是卡片那条路。 |

> 📌 **这是硬门 B 的典型"看着有、实际缺一半"** ——
>  功能现状表标的是「硬门 B 引用溯源**界面**」，**后端那半才是 ✅**。
> 🔵：界面那半在**流式链**上打通了（对话页），**本文件这条非流式链仍未打通**。

## 关联

 功能现状表 · `docs/契约/接口契约.md` §四（`generate_answer` / `citations` 默认值）·
`docs/展示与实测.md:46-54` · `通用/四硬门-定义与验收标准.md` 硬门 B
