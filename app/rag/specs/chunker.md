# `app/rag/chunker.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **可用** —— **六档**（新增 `faq`）· 有测试了<br>⚠️ **但"能选"≠"被选了"**：默认路径仍只走两档，后四档**只有显式传 `doc_type` 才用得上** |
| **对外提供** | `get_text_splitter(doc_type)` · `split_text(text, doc_type)` · `split_text_with_filter(text, doc_type, min_length=20)` · `CHUNK_CONFIGS` · `DEFAULT_SEPARATORS` |
| **谁在用** | 🔴 **生产调用点只有 1 处**：`app/routing/api_v1_rag.py` 的 `upload_document`（文档上传时切块）。⛔ 别处没有 |
| **测试** | ✅ ：`app/tests/test_rag_upload_doc_type.py`（**10 条** —— 在本模块与上传端点之间钉「分档」契约：不传/传了/空白/未知 四种情形 + 档位表本身）<br>⚠️ **它测的是"档位选对了没有"，⛔ 不是"切出来的块好不好"** —— 后者**没有自动尺子** |

## ✅ 做了什么

**按文档类型选分块参数**，底层是 LangChain 的 `RecursiveCharacterTextSplitter`。

| `doc_type` | `chunk_size` | `chunk_overlap` | 谁在用 |
|---|---:|---:|---|
| `default` | 500 | 50 | ⚠️ **无人**（只作兜底） |
| `technical` | **600** | **60** | ✅ **上传端点的默认** —— **非 PDF 都走这条**（: 500/50 → 600/60） |
| `legal` | 800 | 100 | ✅ 上传端点 —— **PDF 走这条** |
| `report` | 800 | 100 | 🟡 **只有显式传 `doc_type` 才用得上**（乙的灌库脚本走这条） |
| `article` | 1000 | 200 | 🟡 同上（暂无计划用它） |
| **`faq`** | **300** | **50** | 🟡 **新增** —— FAQ 的问答对 140–300 字，500 的块会把四五对**合并成一块** |

> 🔴 **动的三个地方 + 为什么**：
> ① `technical` **500/50 → 600/60** —— 调研给手册类是 **600–800 字**；
>    ⚠️ **重叠一起提到 60 是本 Agent 加的一步**（那份文档按**比例**判：500/50 = 10% ⇒ 只改 size 会掉到 8.3%）。
> ② **新增 `faq` = 300/50** —— 原来没有这档。
> ③ 🔴 **`doc_type` 的校验【不在本模块】** —— 见 §⚠️ 第 1 条（在**端点**那一层拦，理由写在那里）。
> ⚠️ **其余三档一个数没动**（有测试钉着：`test_default_and_the_untouched_tiers_did_not_move`）。

`split_text_with_filter` 额外**丢掉短于 `min_length`（默认 20 字符）的块**。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| 🟡 **六档里三档没人用** | `default` / `article`（以及 `report` 的**默认路径**）**全仓零调用**。⚠️ 但 **调用方可以显式传 `doc_type`** ⇒ 它们**不再是"永远用不上"**，只是"没人传"。判据见 §⚠️ 第 1 条 |
| ✅ ~~`"technical"` 与 `"default"` 的配置逐字相同~~ | **已分家**：`technical` = **600/60**、`default` = 500/50 ⇒ 那一档**真的生效了** |
| ✅ ~~`doc_type` 没有校验~~ | **【端点层】拒未知档位**（`api_v1_rag.py` 的 `upload_document` ⇒ 400）。⚠️ **本模块内仍是静默回落**（`)get(..., default)`）—— 见 §⚠️ 第 2 条 |
| 🟡 **测试只覆盖了"分档"** | ✅ 分档契约有 10 条钉着了；⚠️ **分块边界**（超长块、全空文本、单个超长句）**仍一条用例都没有** |
| ⚠️ **没有"每块字符数上限"的保证** | `RecursiveCharacterTextSplitter` 在**找不到分隔符**时会**切出超长块**（见 §⚠️ 第 4 条）—— 而下游要拿块去算 embedding |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🟡 **`doc_type` 现在**是请求参数了 —— 但**只有显式传才用得上**

🔴 **之前**，全仓唯一调用点写的是（**只有两个取值**）：

```python
# app/routing/api_v1_rag.py（旧）
doc_type = "legal" if ext == "pdf" else "technical"
```

✅ **现在**签名叫 `upload_document(file, domain, doc_type="", user_name)`，
**没给才回落到上面那句**：

```python
doc_type = doc_type.strip() or ("legal" if ext == "pdf" else "technical")
if doc_type not in CHUNK_CONFIGS:
    raise AppException(ErrorCode.PARAM_INVALID, ...)   # 🔴 未知档位 ⇒ 400
```

⇒ ⚠️ **访客走的那条路一个字没变**（作者裁的就是这个默认值）；
**`report` / `article` / `faq` 只有"我们的灌库脚本显式传"才用得上** —— ⛔ **别以为上传个文件就会自动分档**。

🔴 **判据（可打印）**：
```bash
grep -rn 'doc_type' app/ | grep -v '^app/rag/chunker.py'
```
⇒ `api_v1_rag.py` 的形参 + 那两句 + `import`；⛔ 别处没有。

### 2. 🔴 **`doc_type` 传错** → `default`，**⛔ 不报错**

```python
config = CHUNK_CONFIGS.get(doc_type, CHUNK_CONFIGS["default"])
```

⚠️ **`.get` 带兜底 ⇒ "传了个拼错的类型" 与 "传了 `default`" 在行为上完全一样**，
**没有任何痕迹**。⇒ 这与本仓对 `mode` 的处理**正好相反**（`api_v1_rag.py` 的 `SearchMode` 是 `Literal`，
非法值 **422**，明文理由写在）。

⛔ **别把这里的 `.get` 当成"容错设计"** —— 那是一种**静默**；真容错要**打日志**或**拒绝**。

### 3. 🔴 **过滤短块的是 `split_text_with_filter`，⛔ 不是 `split_text`**

两个函数**只差一个 `min_length` 过滤**，名字也接近。谁直接调 `split_text` 就**不过滤**。
⇒ 下游拿到空块 / 单字符块是可能的（会让 embedding 白花钱、也让 RRF 的分母变脏）。
⚠️ **目前唯一调用点用的是带过滤的那个**（`api_v1_rag.py:325`）—— 但**这个选择没有任何东西拦着**。

### 4. ⚠️ 分隔符里**混了中英文标点** —— 这是**实质**，⛔ 别"优化"掉

```python
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "，", " ", ""]
```

中文没有空格分词，**句读符号就是天然的块边界**。⛔ 按英文习惯精简成 `["\n\n", "\n", " ", ""]`
会让中文长段**没有可切的地方** ⇒ 落到最后一个 `""` 分隔符 ⇒ **按字符硬切**（切断句子）。
⇒ 这份顺序是**中文分块的实质**，改动它的后果**不会让任何用例变红**（因为**零测试**）。

### 5. 🔴 两个常量**已删**—— 看 git 历史会看到 `DEFAULT_CHUNK_SIZE` / `DEFAULT_CHUNK_OVERLAP`

删的理由写在文件里：**全仓零引用**，且与 `CHUNK_CONFIGS["default"]` 的 500/50 **重复**
—— 「**同一个数两个来源必然漂移**」。

### 6. ⚠️ 块大小是**字符数**，⛔ 不是 token 数

`chunk_size=500` 是**字符**。⇒ 中文 500 字 ≈ 更多 token。
⚠️ **别拿它去估算 embedding / LLM 的成本**（成本按 token 算，在 `token_tracker`）。

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/api_v1_rag.md` | 唯一调用点所在的模块（文档上传链） |
| 预处理模块（`app/rag/document_preprocessor.py`） | ⛔ **它还没有 spec**（221 行）⇒ 本表**不给路径**（给了就是断链）。它在本链的**上游**：切块前先过它 |
| `app/rag/specs/embedding_client.md` | 切出来的块**下一步去哪**（逐块算向量 = 真花钱） |
