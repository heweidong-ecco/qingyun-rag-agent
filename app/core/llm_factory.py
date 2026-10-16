"""`make_llm` —— **LLM 客户端的唯一构造落点**（`①b ` · 形态 =  甲）。

## 为什么要有这个模块

本仓 16 条 LLM 路径**全在 `import` 时**构造客户端，把 `model=` / `api_key=` / `base_url=` 写死
（15 处 `ChatOpenAI(` + `query_rewriter.py` 一处裸 `OpenAI(`）。⇒ 两个后果：

1. **换 provider**（百炼 ⇄ DeepSeek）要在 15 处各改一遍 —— 明明三个值取自同一组配置常量
2. **改 `max_tokens`** 要在 15 处各写一遍常量名

`make_llm` 把这两件事**收到一处**。

## 两个轴（别合成一个参数）

| 轴 | 取值 | 决定 |
|---|---|---|
| **模型轴** `model_role` | `"fast"` / `"chat"` | 用 `LLM_MODEL_FAST` 还是 `LLM_MODEL_CHAT` |
| **长度轴** `token_role` | `"answer"` / `"agent"` | 用 `MAX_TOKENS_ANSWER`(2000) 还是 `MAX_TOKENS_AGENT`(1024) |

**4 种组合现网都存在**（`app/tests/test_max_tokens_wiring.py` 的分类表）
合成一个参数会**悄悄截断某一类**。

`temperature` / `streaming` **不进常量表** —— 它们是**逐点调参**
（`plan_execute` 的 executor 要 0.1、`api_v1_rag` 的流式要 0.3），进表反而把意图藏起来。

## 返回值**必须**是裸 `ChatOpenAI`（别包）

**调用点依赖它的这些属性**，实测：

| 属性 | 谁在用 | 处数 |
|---|---|---|
| **`bind_tools`** | `agent_graph.py:42` · `agent_graph_advanced.py:295` · `agent_checkpointer.py:40` · `agent_graph_advanced_learning.py:75`/`:219` | **5** |
| **`model_name`** | `agent_checkpointer.py:56` · `agent_graph_advanced.py:329`/`:380` · `plan_execute.py:152`（成本记账） | **4** |

**2026-10-02 实测 —— 结论与直觉相反，别照直觉改**：

```
主.with_fallbacks([真的 ChatOpenAI 备用])
  w.bind_tools(tools)  → 能用，且返回的对象**仍然带兜底**
  w.model_name         → 永远返回【主】模型名
```

⇒ **包上去不会炸**（`bind_tools` 没问题），**真正的缺陷是静默的**：
**备用模型烧掉的 token，会被那 4 处记账记到主模型头上** —— 不报错，只是账错。

⇒ 所以这条约束的**唯一目的是「记账不失真」**。
`app/tests/test_llm_factory.py::test_*` 把它钉死了；**要改返回值形状，先看那两条测试。**

## 没做的事：**自动兜底**（的「换下一个」目前是【手动】的）

**现状**：免费额度耗尽 ⇒ **请求直接报错**，直到人工改 `.env` 里的
`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_*` 并**重建容器**
（`docker compose restart` **不重读 `env_file`**，要用 `up -d api`）。

**为什么不做**（**2026-10-02 评估后推迟，不是忘了**）：

| 方案 | 代价 / 卡在哪 |
|---|---|
| `主.with_fallbacks([备], exceptions_to_handle=(...))` | **能跑**（`bind_tools` 没问题），但 **`model_name` 会静默记错账**（见上一节）⇒ 要额外补一个"谁真的答的"的传递；<br>而且**收益只在额度耗尽那一刻兑现** |
| 自己写 `BaseChatModel` 包装类 | 可行，要透传 `bind_tools` / `_generate` / `_stream` / `model_name` |
| 换 `httpx` transport 在传输层改写请求 | 可行且调用点零改动，但 sync/async 两套都要写，属"黑魔法"，排障困难 |

⇒ **落点已经收敛到这一处**：将来要做，**只需改本模块 + 处理那 5 个 `bind_tools` 点**，
**15 个调用点一行都不用再动**。

## 看代码会误判的地方

| 看代码会以为 | 实际 |
|---|---|
| 换了 model 不用重启 | **要重启** —— 调用点**仍在 import 时**构造（本次只收口了"在哪写"，没改成运行时可切） |
| 这里能"兜底" | **不能** —— 返回值是裸 `ChatOpenAI`，没有任何降级/重试逻辑（见上「没做的事」） |
| 有「主/备 provider」的概念 | **没有** —— 只认 `LLM_MODEL_*` + `LLM_API_KEY` + `LLM_BASE_URL` 三个值 |
"""

import os

_MODEL_ROLE_TO_KEY = {"fast": "LLM_MODEL_FAST", "chat": "LLM_MODEL_CHAT"}
_TOKEN_ROLE_TO_CONST = {"answer": "MAX_TOKENS_ANSWER", "agent": "MAX_TOKENS_AGENT"}


def _resolve(attr: str):
    """取一个 `LLM_*` 值 —— **先看 env，回落 `config`**。两个理由都要留：

    * **先看 env** ⇒ **调用时**读得到（测试改得动它，也给将来"运行时可切"留了路）
    * **回落 `config`** ⇒ **默认值只有一处**（`config.py:51-55`）。
      **不许在这里再抄一遍默认值**（抄了就是新的「同一件事两个落点」——
      正是本次收口要消灭的东西）。

    **别退回 `os.getenv("LLM_" + key) or None`** —— 2026-10-02 CI 实测：
      CI 里**没有 `.env`**，也没设 `LLM_MODEL_*` ⇒ 回落成 `None`
      `ChatOpenAI(model=None)` 抛 `ValidationError` ⇒ **`import api_v1_rag` 就崩**，
      连 `conftest.py` 都进不去（整个 job `exit code 4`）。
      守卫测试：`app/tests/test_llm_factory.py::test_falls_back_to_config_when_env_is_absent`
    """
    import core.config as config

    return os.getenv(attr) or getattr(config, attr)


def make_llm(model_role: str, token_role: str, *, temperature: float = 0.0,
             streaming: bool = False, **extra):
    """按【模型轴】+【长度轴】造一个 `ChatOpenAI`。

    Args:
        model_role: `"fast"`（`LLM_MODEL_FAST`）| `"chat"`（`LLM_MODEL_CHAT`）
        token_role: `"answer"`（`MAX_TOKENS_ANSWER` = 2000）| `"agent"`（`MAX_TOKENS_AGENT` = 1024）
        temperature: 逐点调参
        streaming: 流式模式（`api_v1_rag.py` 的 SSE 端点要 True）
        **extra: **逐点调参的其余项**，原样透传给 `ChatOpenAI` ——
            现网用到 `timeout=` / `max_retries=`（`agent_graph_advanced.py:52-53`）。

            **不许**传 `model` / `api_key` / `base_url` / `max_tokens` ——
            那四个是**这一层管的**，传了 Python 会报「got multiple values for keyword argument」
            （这是**故意的**：让"绕过收口"当场失败，而不是静默覆盖）。

    Returns:
        **裸 `ChatOpenAI`**。不许包成别的类型 —— 见模块 docstring 那张「返回值必须」的表。

    Raises:
        EnvironmentError: `LLM_API_KEY` 没配（批 6）。**点名那个变量** ——
            改前是把 `None` 递给 `ChatOpenAI`，报的是 SDK 的通用话，而它提的
            `OPENAI_API_KEY` 本仓根本不用。
    """
    if model_role not in _MODEL_ROLE_TO_KEY:
        raise ValueError(f"未知的 model_role={model_role!r}；只认 {sorted(_MODEL_ROLE_TO_KEY)}")
    if token_role not in _TOKEN_ROLE_TO_CONST:
        raise ValueError(f"未知的 token_role={token_role!r}；只认 {sorted(_TOKEN_ROLE_TO_CONST)}")

    # 批 6：缺 key 时**点名**，不要把 `None` 递给 `ChatOpenAI`。
    #   改前那种写法报的是 SDK 那句通用话（`The api_key client option must be set … OPENAI_API_KEY`）——
    #   它提的 `OPENAI_API_KEY` 本仓**根本不用** ⇒ 照它去设变量，问题不会消失。
    #   **时机没变**（仍在 import 期构造，调用点没动）—— 本处只改「报什么」；
    #   真惰性要连 3 张图的模块级 `llm` + `bind_tools` 一起下沉，见  的取舍。
    api_key = _resolve("LLM_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "LLM_API_KEY 未设置 —— 生成/对话模型走 DeepSeek，缺它客户端建不出来。"
            "检查 .env 或环境变量（见 docs/契约/环境变量.md §4）。"
        )

    import billing.token_config as token_config

    # `langchain_openai` 在这里才 import（不是模块顶层）——
    #    否则任何 `import llm_factory` 都会拉 langchain，
    #    而 `api_v1_rag.py:567` 那条注释记的正是这个教训（导入期白建对象）。
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=_resolve(_MODEL_ROLE_TO_KEY[model_role]),
        api_key=api_key,
        base_url=_resolve("LLM_BASE_URL"),
        temperature=temperature,
        max_tokens=getattr(token_config, _TOKEN_ROLE_TO_CONST[token_role]),
        streaming=streaming,
        **extra,
    )
