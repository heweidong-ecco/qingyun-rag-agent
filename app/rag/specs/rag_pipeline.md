# `app/rag/rag_pipeline.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **可用（生产）** —— `/rag/search` 的**唯一**检索管线。⚠️ 但里面有**一段死代码**和**一条没有过滤的档位** |
| **对外提供** | `RAGPipeline`（`__init__` · `search_async` · `_rrf_fusion`）· 4 个工厂：`create_fast_pipeline` · `create_accurate_pipeline` · `create_accurate_norerank_pipeline` · `create_full_pipeline` |
| **谁在用** | 🔴 **1 处**：`app/routing/api_v1_rag.py` 的 `PIPELINE_FACTORIES`（`:558` 建实例 → `:561` `search_async(...)`，`:575` **整个返回体就是它的返回值**） |
| **测试** | ⛔ **专属用例零条**（`app/test_rag_pipeline.py` **不存在**）。间接：`app/tests/test_rag_billing_wiring.py`（`:291`/`:317` **直接调 `search_async`**）· `app/tests/test_rag_search.py`（走端点） |

## ✅ 做了什么

**四阶段可开关的检索管线**：

```
① 查询规范化（clean_whitespace + normalize_text，就地构造 DocumentPreprocessor）
② 查询改写 / 扩展（enable_rewrite / enable_expand）
③ 多路检索 + RRF 融合（向量 + BM25）→ 取 top_k × candidate_multiplier
④ Cross-Encoder 重排（enable_rerank）→ 过滤 → 可选生成答案
```

**四档预置**（`PIPELINE_FACTORIES` 的键与它一一对应）：

| 档 | rewrite | expand | bm25 | rerank |
|---|:-:|:-:|:-:|:-:|
| `fast` | ⛔ | ⛔ | ✅ | ⛔ |
| `accurate` | ✅ | ⛔ | ✅ | ✅ |
| `accurate_norerank` | ✅ | ⛔ | ✅ | ⛔ |
| `full` | ✅ | ✅ | ✅ | ✅ |

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| 🔴 **零专属测试** | 四个档位的差别 · 空结果路径 · 过滤行为 —— **都没有用例钉着**（只有"记账接线"和"端点形状"的间接覆盖） |
| 🔴 **`accurate_norerank` 没有任何相关性过滤** | 见 §⚠️ 第 3 条 —— 这一档把不相关文档**直接交给模型** |
| ⚠️ **`total_ms` 在两条出口上存在性不同** | 见 §⚠️ 第 1 条 |
| ⚠️ **记账的 `thread_id` 硬编码 `"default"`** | 见 §⚠️ 第 5 条 |
| ⚠️ **RRF 去重键 = `content`** | 两篇内容完全相同的文档会被**合成一篇**（有意的，但⛔ 不是按 id 去重） |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🔴 `total_ms` 在**空结果**那条出口上**不存在**

```python
if not candidates:
    return {..., "timing": timing, "docs": []}   # 🔴 :156 —— 此时 timing 里【没有 total_ms】
...
timing["total_ms"] = round(...)                  # :165 —— 只有走到这里才有
```

⇒ `timing` 这个 dict **两条出口的键不一样**：
**有结果 ⇒ 4 个键（含 `total_ms`）· 无结果 ⇒ 3 个键**。

🔴 **消费端写 `body["timing"]["total_ms"]` 会在"什么都搜不到"时 `KeyError`** ——
而那正是**最需要看耗时**的场景（"为什么没结果"）。
⚠️ `app/tests/test_rag_search.py:281` 那条断言（`>= {rewrite_ms, search_ms, rerank_ms, total_ms}`）
**只在有结果的路径上跑** ⇒ **钉不住这个**。

🔴 **判据**：`grep -n 'total_ms' app/rag/rag_pipeline.py` ⇒ 只有**一处**赋值，且在 `:156` 那个 `return` **之后**。

### 2. 🔴 `:212` 那个 `elif` 是**死代码** —— 那句"无法回答"**从未执行过**

```python
if not candidates:
    return {...}                      # :156 ← 这里已经 return 了
...
if generate_answer and candidates:    # :176 ← 走到这里时 candidates 必非空
    ...
elif generate_answer and not candidates:   # :212 ← 🔴 【永远为假】
    result["answer"] = "根据现有资料，无法回答。"   # :213 ← 🔴 从未执行
```

⇒ 🔴 **"检索不到就不出答案"这件事，不是由这个分支保证的，是由「`answer` 键根本不存在」保证的。**

⇒ **`generate_answer=true` + 搜不到** ⇒ 返回体里**没有 `answer` 键**（⛔ 不是空串、⛔ 不是那句拒答）。

🔴 **⚠️ 「无法回答」这句话在本仓有【三个】地方，判据是字符串、不是常量**：

| 处 | 字面量 | 活/死 |
|---|---|---|
| `app/rag/rag_pipeline.py:213` | `根据现有资料，无法回答。` | ⚰️ **死** |
| `app/routing/api_v1_rag.py:702` | `REFUSAL_SENTENCE = "根据现有资料，无法回答"` | ✅ **活**（流式拒答的判据） |
| `app/rag/answer_with_citations.py:17` | 「…请直接说"根据现有资料，无法回答"」 | ✅ **活**（**写给模型的 prompt**） |

⇒ ⛔ **别以为改一处就都改了。**

🔴 **判据（可打印）**：`grep -rn '根据现有资料，无法回答' --include='*.py' app/`

### 3. 🔴 `accurate_norerank` ⛔ **不是"少了一层排序"** —— 是**少了挡不相关文档的那一层**

过滤那步的前置条件是 `if self.enable_rerank`（`:150`）：

```python
if self.enable_rerank and candidates:
    candidates = [d for d in candidates if d.get("rerank_score", 0) >= 0]
```

⇒ 🔴 **`enable_rerank=False` ⇒ 这一步【整个不执行】** ⇒ 一个候选都不会被丢
⇒ 那一档把 **RRF 排出来的前 N 篇**（**不问相关度**）**直接交给模型**。

⚠️ 而 `app/eval/bad_cases.md:191` 里「设一个向量相似度最低阈值（比如 0.7）」那条建议
**与本文件的做法不是同一件事** —— RRF 分数 ≈ `1/(k+rank)`（k=60）**很小**，
**不能用 0.7 那种余弦阈值判**（这一点文件里 `:148` 写明了）。
⇒ 重排分是 **Cross-Encoder logits**，`>= 0` 视为可接受 —— **⛔ 别把这两个分数混着用。**

### 4. 🔴 记账在**两条分支的不同文件里** —— 漏一条就是**零记账而测试照常绿**

| 分支 | 记账点 |
|---|---|
| `citations=False` | **本文件** `:205` `record_from_response(self.answer_llm, response, "answer_generation", …)` |
| `citations=True` | **`app/rag/answer_with_citations.py` 内部**（靠 `user_name=user_id` 传进去，`:182`） |

⇒ ⛔ **别以为"记账在 `rag_pipeline`"** —— 它**只管一半**。
⚠️ 这正是  那轮"零记账"的形态：**账没记，而测试全绿。**

⚠️ **还一个坑**：`StrOutputParser` **⛔ 不能接回来**（`:199` 有明文）——
它把 `AIMessage` **剥成 `str`**，`usage_metadata` 随之丢光 ⇒ **记账拿不到数**。
🔒 守卫 ⇒ `app/tests/test_rag_billing_wiring.py`（`:304` 那条钉的是 `generate_answer=False` ⇒ **不该记一笔**）。

### 5. 🔴 `thread_id` 是**必填关键字**（`:66`），⛔ 不给默认值 —— 2026-10-16修的

**改前**：`search_async` **根本没有 `thread_id` 形参**，而
`record_from_response(..., thread_id="default")` 是**硬编码**（`:207`，引用分支由
`answer_with_citations.py` 的形参默认值写死）⇒ **所有调用者的账都挤进 `"default"` 这一个桶**。

🔴 **它不是"归不到具体会话"那么轻 —— 它让那道会话闸**失效**了**（实测复现）：
`/rag/search` 在 `:774` 用**调用者传的那个** `thread_id` 查
`check_session_token_budget(user_name, thread_id)`，而账写进 `(user, "default")`
⇒ **闸查的桶恒空** ⇒ **那条路上的会话 50k 上限永远不会触发**（静默放行）。
📌 **可打印判据**：`get_session_token_usage(user, <调用者的 thread>)` —— 改前 **0**、改后 **36**。

**改后**：`thread_id` 与 `user_id` **两根轴一样，是必填关键字**（`:66`），
由端点传进来，往下贯穿到**链上每一处记账**（改写 / 扩展 / embedding / 答案生成）。
⛔ **别给默认值** —— 给了就等于「**可以忘记传**」（`user_id` 那条的原话，逐字同款）。
🔒 守卫 ⇒ `app/tests/test_embedding_attribution.py::test_every_call_site_passes_a_thread`
（活代码里每个 `get_embedding(` 都得带 `thread_id=`；白名单要写理由）。
📄 决策与备选（`contextvars` 为什么被否）⇒  · 账 ⇒ `docs/待办总表.md`

### 6. 🔴 身份 `user_id` 是**必填关键字**（`:65`），⛔ 不给默认值

⇒ 漏传 = **`TypeError`**，⛔ 不是"静默查全库"（决策 4/5）。
⇒ 它必须**贯穿到两个检索函数**：`search_similar_async(..., user_id=user_id)` 与
`bm25_search_async(..., user_id=user_id)` —— ⛔ **加第三条检索路时别漏传**（漏了就是**静默全库**）。

### 7. ⚠️ RRF 按 **`content` 去重**（`rrf_scores[content]`）—— ⛔ 不是按 id

⇒ **两篇正文完全相同的文档会被合成一篇**（有意的：同一段被多路重复召回了）。
⚠️ 但**代价**是"文档身份"在这个函数里**不存在** —— `doc_info[content]["id"]` 只是**碰巧留下的那一路的 id**。

### 8. ⚠️ 查询规范化**就地构造** `DocumentPreprocessor`

```python
from document_preprocessor import DocumentPreprocessor
preprocessor = DocumentPreprocessor()
```
⚠️ 类属性上那个 `preprocessor` **已删**（`:52` 有注释：**全仓零引用**，只是**在类定义时构造一次**、从没被读过）。
⇒ ⛔ **别为了"省一次构造"把它加回类属性**。

### 9. ⚠️ `strict_mode` **只改一句 system prompt**

```python
"根据上下文回答。找不到则说'无法回答'。"   # vs
"根据上下文回答，可适当补充常识。"
```
⇒ 它**不是检索约束**，是**提示词开关** —— ⛔ 别指望它拦文档。
**真正把不相关文档挡在外面的**是 §⚠️ 第 3 条那步（重排过滤）。

### 10. ⚠️ `__init__` 里就建 LLM 对象

`self.answer_llm = answer_llm or make_llm("fast", "answer")`
⇒ **构造管线**（`PIPELINE_FACTORIES[mode]()`）就会调 `make_llm` —— ⚠️ 且 `make_llm` 在 **import/构造期**
就会判 `LLM_API_KEY` 并抛（见 `app/core/specs/config.md` §⚠️ 第 4 条）。
⇒ 即使 `generate_answer=False`（**根本不用 LLM**），**也要先有一个能构造出来的 LLM 配置**。

## 关联

| 文档 | 说明 |
|---|---|
| `app/routing/specs/api_v1_rag.md` | ⭐ **唯一调用方**（`PIPELINE_FACTORIES` / `SearchMode` / 返回体） |
| `app/rag/specs/query_rewriter.md` | `rewrite_query` / `expand_query`（②那个阶段） |
| `app/rag/specs/reranker.md` | `rerank_async` —— ③④之间那步，**过滤判据的来源** |
| `app/rag/specs/hybrid_search.md` · `app/rag/specs/bm25_index.md` | 两路检索 |
| `app/core/specs/db.md` | `search_similar_async` / `bm25_search_async` 的**落点**（身份隔离在那里生效） |
| `app/rag/specs/embedding_client.md` | 每个子查询一次向量化（③） |
| `app/rag/specs/answer_with_citations.md` | ⚠️ **另一半的记账点**（§⚠️ 第 4 条） |
| `app/billing/specs/token_tracker.md` | `record_from_response` |
| `app/core/specs/llm_factory.md` | `make_llm("fast", "answer")` 的两条轴 |
| 本仓的决策记录（⛔ 不随产品仓提供） | `user_id` 必填贯穿（§⚠️ 第 6 条） |
| 本仓的决策记录（⛔ 不随产品仓提供） | ⭐ §⚠️ 第 4 条 —— "零记账而测试全绿" |
| 本仓的决策记录（⛔ 不随产品仓提供） | 会话键/留痕口径（对比 §⚠️ 第 5 条） |
