# `app/rag/document_preprocessor.py`

## ✅ 做了什么

**文档预处理管道**：`DocumentPreprocessor` —— 对解析后的原文做清洗 / 去重 / 标准化。
规则**外置**在 `app/rag/cleanup_rules.json`（**可配置、可扩展**，按 `domain` 分组）。

## 🟡 做到哪 / 缺什么

- **6 处产品代码引用 · 1 个测试文件提到**。
- 🔴 **它在【上传链路】上是活的**：`app/routing/api_v1_rag.py` 里带 `domain` 参数真调用它
  （`:284` 默认域 / `:320` 带域），另在 `app/main.py` 启动期实例化。
- ⚠️ **`dedupe` 那一族方法当前【无任何调用方】**（核实）——
  ⇒ 看到它别以为在上传链路上。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **`cleanup_rules.json` 的路径锚在【本文件旁边】**（`os.path.dirname(__file__)`）。
   ⇒ **搬进 `app/rag/` 时，是【数据文件跟着模块走】才没改代码**。
   ⛔ **再挪动本文件、却把 json 留在原地 ⇒ 清洗规则【静默变成空】**（不报错，只是没洗）。
   📌 判据：`python -c "from rag.document_preprocessor import DocumentPreprocessor as D; print(D().process('联系我们 support@x.com'))"` ⇒ 邮件应被去掉。
2. 🔴 **那条 markdown 图片规则【不是死代码】** —— 它在 **`legal` / `medical` 域正在生效**。
   ⚠️ 但在 `default` 域，**URL 规则排在图片规则之前** ⇒ 同一条规则在不同域**行为不同**。
   ⇒ ⛔ **别只看 `default` 域就判它没用**；也别**顺手**把 `default` 的顺序调过来
   —— 那会**改变文本预处理输出** ⇒ 已入库语料的清洗结果与新规则**不一致**。
3. ⚠️ **把某个阈值 `0.9` → `0.85`**（对齐 `docs/说明/语料要求.md`）——
   🔴 **这次改动【当前零影响】**：那个方法**没有任何调用方**。
   ⇒ 看到注释里的数字变化，先核「**这个方法有人调吗**」，⛔ 别当成"线上行为变了"。
4. 🔴 **删过一份【空壳定义】**（同名方法定义了两次 ⇒ 后一个覆盖前一个）。
   ⚠️ 与 `app/core/db.py` 的 `get_db()` 那件**同型** ⇒ 本文件有一条守卫专门钉"同名方法不能定义两次"
   （`app/tests/test_audit_fixes.py`）。

## 关联

- `app/rag/cleanup_rules.json` · `app/rag/cleanup_rules_notes.md` —— 规则与维护说明
- `docs/说明/语料要求.md` —— 阈值那一条的出处
- `app/rag/specs/document_parser.md` —— 上游（先解析、再清洗）
- `app/routing/specs/api_v1_rag.md` —— 上传链路（带 `domain` 调它）
