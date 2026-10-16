# `app/core/llm_factory.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟢 **新建（2026-10-02 · `①b`）** —— LLM 客户端的**唯一构造落点**<br>✅ 15 个调用点**已全部改走它**（`app/tests/test_max_tokens_wiring.py` 钉着）<br>⬜ **自动兜底没做**（评估后**故意推迟**，见下）—— ⛔ 别以为它能"兜底" |
| **对外提供** | `make_llm(model_role, token_role, *, temperature=0.0, streaming=False, **extra) -> ChatOpenAI` |
| **谁在用** | `api_v1_rag.py`（2）· `rag_pipeline.py`（1）· `evaluate_with_ragas.py`（1）· `agent_checkpointer.py`（1）· `agent_graph.py`（1）· `agent_graph_advanced.py`（1）· `agent_graph_advanced_learning.py`（5）· `plan_execute.py`（3）—— **合计 15 处** |

## ✅ 做了什么

**一句话**：把"`model` / `api_key` / `base_url` / `max_tokens` 写在哪"从 **15 处**收到 **1 处**。

**改之前**：15 处各自写
```python
ChatOpenAI(model=LLM_MODEL_CHAT, api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
           temperature=0, max_tokens=MAX_TOKENS_AGENT, ...)
```
三个值取自同一组配置常量，却在 15 个地方各抄一遍 ⇒ 换 provider / 改上限要动 15 处，**必然漏**。

**改之后**：
```python
make_llm("chat", "agent")          # 两个轴，见下
```

### 两个轴（⛔ **别合成一个参数**）

| 轴 | 取值 | 决定 | 现网分布 |
|---|---|---|---|
| **模型轴** `model_role` | `"fast"` / `"chat"` | 用 `LLM_MODEL_FAST` 还是 `LLM_MODEL_CHAT` | `fast`：`rag_pipeline` · `agent_graph*` · `agent_checkpointer`；其余 `chat` |
| **长度轴** `token_role` | `"answer"` / `"agent"` | `MAX_TOKENS_ANSWER`(2000) 还是 `MAX_TOKENS_AGENT`(1024) | 答案类 4 处 / 中间步骤 11 处 |

**4 种组合现网都存在** ⇒ 合成一个参数会**悄悄截断某一类**。
分类的**裁定表**在 `app/tests/test_max_tokens_wiring.py` 的 `EXPECTED_ROLES`，⛔ 不是从代码里推出来的规律。

`temperature` / `streaming` ⛔ **不进常量表** —— 它们是**逐点调参**
（`plan_execute` 的 executor 要 0.1、`api_v1_rag` 的流式要 0.3），进表反而把意图藏起来。
其余一次性参数（`timeout` / `max_retries`）走 `**extra` 原样透传给 `ChatOpenAI`。

### ✅ 顺带修掉的一个真隐患

`evaluate_with_ragas.py` 原来是 `os.getenv("LLM_MODEL_CHAT", "deepseek-chat")`，
兜底值和 `config.py:55` **当时的**默认值 `qwen-plus` **不一致** ⇒ **env 一缺失，
脚本和应用会静默用上两个不同的模型**。
现在两边都走 `make_llm()` ⇒ **默认值只剩 `config.py:53-55` 一处**。

🔴 **2026-10-02 追加（同一天 · 任务之外但同源）**：`config.py:53-55` 的默认值
**也从百炼 qwen 改成了 DeepSeek**（⇒ LLM 只用 DeepSeek）。
⚠️ **副作用**：默认值与 `.env` 现在**同值** ⇒  那条
「靠模型名不同来自证 `.env` 不在场」的旁证**失效**（判据已改成看 `.env` 在不在）。

## 🟡 做到哪 / 缺什么

| 项 | 状态 |
|---|---|
| 15 个调用点收口 | ✅ 全做完（`app/tests/test_max_tokens_wiring.py` 硬钉「工厂以外零直连」） |
| 工厂以外禁止裸 `ChatOpenAI(` | ✅ 门禁已加 |
| **自动兜底**（主 provider 用完 ⇒ 自动切备用） | ⬜ **没做 —— 评估后【故意推迟】，不是忘了** |
| **运行时不重启换 provider** | ⬜ 没做（调用点**仍在 import 时**构造） |

### 为什么自动兜底被推迟（2026-10-02 评估）

| 方案 | 代价 / 卡在哪 |
|---|---|
| `主.with_fallbacks([备], exceptions_to_handle=(...))` | 🟡 **能跑**（`bind_tools` 没问题），但 **`model_name` 会静默记错账**（见下 ⚠️）；⚠️ 而且**收益只在额度耗尽那一刻兑现** |
| 自己写 `BaseChatModel` 包装类 | 🟡 可行，要透传 `bind_tools` / `_generate` / `_stream` / `model_name` |
| 换 `httpx` transport 在传输层改写请求 | 🟡 可行且调用点零改动，但 sync/async 两套都要写，属"黑魔法"，排障困难 |

⇒ **落点已收敛到这一处**：将来要做，**只需改本模块 + 处理那 5 个 `bind_tools` 点**，
**15 个调用点一行都不用再动**。

**当前的兜底方式是【手动】的**：额度耗尽 ⇒ **请求直接报错**，直到人工改 `.env` 的
`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_*` 并**重建容器**
（⚠️ `docker compose restart` **不重读 `env_file`**，要用 `docker compose up -d api`）。

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **返回值"可以包一下"** | ⛔ **取值必须是裸 `ChatOpenAI`** —— 5 处 `bind_tools()`、4 处 `model_name`（**成本记账**）依赖它。<br>⚠️ **注意理由不是"包了会炸"** —— `主.with_fallbacks([真的 ChatOpenAI 备用])` 实测 **`bind_tools` 能用**，<br>🔴 真正的缺陷是**静默的**：**`w.model_name` 永远返回【主】模型名** ⇒ **备用模型烧的 token 被记到主模型头上**。<br>📌 `app/tests/test_llm_factory.py::test_wrapping_would_silently_break_cost_attribution` 把这个反证钉死了。 |
| **这里能"兜底"** | ⛔ **不能** —— 返回值没有任何降级/重试逻辑（见上「缺什么」） |
| **有「主/备 provider」的概念** | 🟡 **没有** —— 只认 `LLM_MODEL_*` + `LLM_API_KEY` + `LLM_BASE_URL` 三个值 |
| **换了 model 不用重启** | 🔴 **要重启** —— 15 个调用点**仍在 import 时**构造。本次只收口了"在哪写"，**没**改成运行时可切。<br>⚠️ **2026-10-05再次问过这件事**：真惰性要连 **3 张图的模块级 `llm` + `bind_tools`** 一起下沉（`agent_graph` · `agent_checkpointer` · `agent_graph_advanced_learning`）⇒ **与"运行时可切"是同一件事**，⛔ **不单独立项** |
| 🔴 **缺 `LLM_API_KEY` 时报的是 SDK 那句通用话** | ⛔ **不是了**（2026-10-05 · 批 6）—— 现在抛 `EnvironmentError`，消息里**点名 `LLM_API_KEY`**。<br>⚠️ **但时机【没变】**：仍**在 import 期构造** ⇒ **`import` 仍会中断**，只是报的话能看懂了。<br>⛔ **别把这条读成"LLM 侧也惰性化了"** —— 「import 不许崩」那半**没做**（理由见上一行）。<br>📌 守卫：`app/tests/test_llm_factory.py::test_missing_key_is_reported_by_name_not_by_openai` |
| **`import llm_factory` 会拉 langchain** | ⛔ **不会** —— `langchain_openai` 是在 `make_llm()` **函数内**才 import 的。<br>⚠️ 这条是**故意的**（`api_v1_rag.py` 有"导入期不拉 langchain"的既有约束）；<br>`app/tests/test_llm_factory.py::test_importing_the_module_does_not_pull_langchain` 用**子进程**钉着 |
| **打错角色名会静默跑** | ⛔ **不会** —— 两个轴都在**工厂里**校验，未知取值直接 `ValueError`（`test_unknown_roles_raise`） |
| ⚠️ **门禁只钉"走没走工厂"** | **不止** —— 还钉**两个轴的角色**：全传 `("chat","agent")` 会被红。<br>⇒ 顺手改角色 = **改行为 + 改账单**，那**不属于** （收口）的范围 |
| ⚠️ **`query_rewriter.py` 也该改走它** | ⛔ **不适用** —— 它用的是**裸 `openai.OpenAI(`**（不是 `ChatOpenAI`），<br>上限是 `MAX_TOKENS_REWRITE_VARIANTS` / `_INTENT`，**另有其表**。<br>`embedding_client.py` 走 `OpenAIEmbeddings` —— **没有 `max_tokens` 这个概念**。 |

| 🔴 **env 没设 `LLM_*` 时会拿到 `None`** | ⛔ **不会** —— `_resolve()` 是**先看 env、回落 `config.py:51-55`**。<br>⚠️ **这条是血的**：2026-10-02 收口时写成 `os.getenv("LLM_"+key) or None`，**把 config 的默认值绕过去了** ⇒ CI 无 `.env` ⇒ `model=None` ⇒ `ChatOpenAI` 抛 `ValidationError` ⇒ **`import api_v1_rag` 就崩，一条测试都没跑到**（PR `#67` 红）。<br>📌 **本地全绿发现不了** —— 本机有 `.env`。守卫：`app/tests/test_llm_factory.py::test_falls_back_to_config_when_env_is_absent`。<br>⚠️ ⛔ **别退回自己读 env + 自己写默认值**：那就是新的「同一件事两个落点」。 |

## 关联

| 文档 | 说明 |
|---|---|
| ⭐ 本仓的决策记录（⛔ 不随产品仓提供） | **本模块的形状**：为什么只收口、⛔ 为什么不做自动兜底（含 `with_fallbacks` 的实测） |
| 本仓的决策记录（⛔ 不随产品仓提供） | **缺 key 时报什么** + ⛔ 为什么真惰性**不在本批**（要动 3 张图的模块级 `bind_tools`） |
| `app/billing/specs/token_config.md` | 两个上限常量（`MAX_TOKENS_*`）的**唯一落点** |
| `app/billing/specs/token_tracker.md` | `①b`  的原始计划与【修订后的形态】（形态 = 甲） |
| `app/tests/test_llm_factory.py` | 钉返回值形状（**记账不失真**）+ 两个轴 + 参数校验 + 不拉 langchain |
| `app/tests/test_max_tokens_wiring.py` | 钉**全仓**：工厂以外零 `ChatOpenAI(` · 每个调用点的角色与裁定表一致 |
| `docs/契约/环境变量.md` | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_*` 的口径 |
