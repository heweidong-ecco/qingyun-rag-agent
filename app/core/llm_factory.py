"""`make_llm` —— **LLM 客户端的唯一构造落点**（`①b ` · 形态 =  甲）。
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

    **别退回 `os.getenv("LLM_" + key) or None`** —— CI 实测：
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
