"""`make_llm` —— LLM 客户端唯一构造落点（`①b ` · 形态  甲）。

模块为什么存在、两个轴为什么要分开 ⇒ `app/core/llm_factory.py` 的模块 docstring。

本文件**不发网络请求** —— 只钉「造出来的对象**是什么、带什么属性、参数对不对**」。
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ENV = {
    "LLM_API_KEY": "sk-fake-for-test",
    "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "LLM_MODEL_FAST": "qwen-turbo",
    "LLM_MODEL_CHAT": "qwen-plus",
}


@pytest.fixture
def factory(monkeypatch):
    """把环境变量摆好再拿模块 —— `make_llm` 是**调用时**读 env 的，所以能改得动。"""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(importlib.import_module("core.llm_factory"))


# ==================== env 缺失时必须回落到 config ====================

def test_falls_back_to_config_when_env_is_absent(monkeypatch):
    """env 里**没设** `LLM_*` 时，`make_llm` 必须回落到 **`config` 的口径**（不是 `None`）。

    **为什么有这一条 —— 线上 CI 实测，不是我推的边界**：

    CI 里**没有 `.env`**（日志原话 `.env 存在吗    = False`），也没设 `LLM_MODEL_*`。
    当时的实现是 `os.getenv("LLM_" + key) or None` ⇒ **直给 `model=None`**
    ⇒ `ChatOpenAI(model=None)` 抛 `ValidationError` ⇒ `import api_v1_rag` 就崩，
    **一条测试都没跑到**（挂在 `conftest.py` 的 `from main import app`），整个 job `exit code 4`。

    **本地为什么没暴露**：本机有 `.env`，那三个值都在。
    ⇒ **本地全绿 [不能] 证明 CI 绿** —— 这条就是那个差集的守卫。
    """
    import core.config as config

    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    m = importlib.reload(importlib.import_module("core.llm_factory"))

    llm = m.make_llm("fast", "agent")

    assert llm.model_name == config.LLM_MODEL_FAST, (
        "env 缺失时要回落到 `config.LLM_MODEL_FAST` —— "
        "回落成 None 会让 ChatOpenAI 构造直接抛（CI 就是这么挂的）"
    )
    assert llm.openai_api_base == config.LLM_BASE_URL, "env 缺失时要回落到 `config.LLM_BASE_URL`"


# ==================== 最关键的一条：返回值形状 ====================

def test_returns_bare_chatopenai_with_the_attrs_call_sites_use(factory):
    """**返回值必须是裸 `ChatOpenAI`，且带 `bind_tools` / `model_name`。**

    **为什么钉这条**（实测，**理由不是"会炸"，别记错**）：

    | 属性 | 谁在用 | 处数 |
    |---|---|---|
    | **`bind_tools`** | `llm.bind_tools(tools)` | **5** |
    | **`model_name`** | `getattr(llm, "model_name", …)` —— **成本记账** | **4** |

    曾打算用 `主.with_fallbacks([备])` 做自动兜底。实测：`bind_tools` **能用**，
    但 **`w.model_name` 永远返回【主】模型名** ⇒ **备用模型烧的 token 记到主模型头上**（静默错账）。
    ⇒ **这条钉的是「记账不失真」，不是「不炸」。** 见 `app/core/llm_factory.py` 的同名小节。
    """
    from langchain_openai import ChatOpenAI

    llm = factory.make_llm("chat", "answer")

    assert isinstance(llm, ChatOpenAI), (
        f"make_llm 必须返回裸 ChatOpenAI，实际是 {type(llm).__name__} —— "
        "包装过的对象 `model_name` 取的是主模型，会让 4 处成本记账静默记错"
    )
    for attr in ("bind_tools", "model_name"):
        assert hasattr(llm, attr), (
            f"返回对象缺 `{attr}` ⇒ 调用点会炸（bind_tools 5 处 / model_name 4 处记账）"
        )


def test_wrapping_would_silently_break_cost_attribution(factory):
    """**反证**：证明上一条**真的**在防一个存在的缺陷 —— 不是空过。

    拿**两个真的 `ChatOpenAI`** 组一个 `with_fallbacks`，证明：

    * `bind_tools` 其实**能用**（所以"会炸"的说法是错的，别写进文档）
    * 但 `model_name` **只会报告主模型** ⇒ 备用接管时，账记错
    """
    primary = factory.make_llm("chat", "answer")
    backup = factory.make_llm("chat", "answer")
    wrapped = primary.with_fallbacks([backup])

    assert hasattr(wrapped, "bind_tools"), "前提变了：with_fallbacks 现在连 bind_tools 都没了？"
    assert wrapped.bind_tools([]) is not None, "前提变了：bind_tools 现在会抛？"

    assert not isinstance(wrapped, type(primary)), "前提变了：包装后仍被认成 ChatOpenAI？"
    assert wrapped.model_name == primary.model_name, (
        "前提变了：包装后 model_name 不再返回主模型名？"
        " —— 若真变了，本约束可以放宽，但**必须同时改 `app/core/llm_factory.py` 的说明**"
    )


# ==================== 两个轴各取各的 ====================

@pytest.mark.parametrize(
    "model_role,token_role,want_model,want_const",
    [
        ("chat", "answer", "qwen-plus", "MAX_TOKENS_ANSWER"),
        ("chat", "agent", "qwen-plus", "MAX_TOKENS_AGENT"),
        ("fast", "agent", "qwen-turbo", "MAX_TOKENS_AGENT"),
        ("fast", "answer", "qwen-turbo", "MAX_TOKENS_ANSWER"),
    ],
)
def test_two_axes_are_independent(factory, model_role, token_role, want_model, want_const):
    """**模型轴**（fast/chat）与**长度轴**（answer/agent）互相独立 —— 4 种组合都得成立。

    这 4 种组合**现网都真实存在** ⇒ 合成一个参数会**悄悄截断某一类**。
    """
    import billing.token_config as token_config

    llm = factory.make_llm(model_role, token_role)

    assert llm.model_name == want_model
    assert llm.max_tokens == getattr(token_config, want_const)


def test_temperature_and_streaming_reach_the_client(factory):
    """`temperature` / `streaming` 是**逐点调参**，必须原样传到客户端（别被吞掉）。"""
    llm = factory.make_llm("chat", "answer", temperature=0.3, streaming=True)

    assert llm.temperature == 0.3
    assert llm.streaming is True


# ==================== 参数校验 ====================

@pytest.mark.parametrize(
    "model_role,token_role",
    [("chatt", "answer"), ("chat", "answers"), ("", ""), ("fast", "MAX_TOKENS_ANSWER")],
)
def test_unknown_roles_raise(factory, model_role, token_role):
    """**打错字不许静默通过** —— 静默会退化成「用了错的角色」，而额度不报错。"""
    with pytest.raises(ValueError):
        factory.make_llm(model_role, token_role)


# ==================== 不拉 langchain ====================

def test_importing_the_module_does_not_pull_langchain(monkeypatch):
    """`import llm_factory` **本身**不许拉 langchain（`api_v1_rag.py:567` 的教训）。

    判据：在**全新的子进程**里只 import 本模块，看 `sys.modules` 里有没有 `langchain_openai`。
    """
    import subprocess

    code = (
        "import sys; sys.path.insert(0, 'app'); import core.llm_factory;"
        "print('langchain_openai' in sys.modules or 'langchain_core' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[2]), env={**os.environ},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", (
        "import llm_factory 就把 langchain 拉进来了 ⇒ 与 api_v1_rag.py:567 记的教训冲突"
    )


# ==================== 缺 key 时要点名====================

def test_missing_key_is_reported_by_name_not_by_openai():
    """缺 `LLM_API_KEY` 时 `make_llm` 必须**点名那个变量**，不是 OpenAI SDK 那句通用话。

    **时机没变**（仍在 import 期构造）—— 只改「**报什么**」。为什么不做真惰性
    （那要动 3 张图的模块级 `llm` + `bind_tools`）。

    判据：在**没有 `.env`、也没设该变量**的子进程里调一次，看报的错里有没有 `LLM_API_KEY`。
    """
    import subprocess

    code = (
        # 逐字复现「CI / 新鲜检出没有 .env」（`ci-local.sh` rsync 掉 .env；`ci.yml` 塞 dummy 绕开的就是这事）
        "import dotenv; dotenv.load_dotenv = lambda *a, **k: None;"
        "import sys; sys.path.insert(0, 'app');"
        # 这里必须是**换行**不是 `;` —— `try:` 是复合语句，跟在 `;` 后面是 SyntaxError
        "import core.llm_factory\n"
        "try:\n"
        "    core.llm_factory.make_llm('chat', 'agent')\n"
        "    print('NO-ERROR')\n"
        "except Exception as e:\n"
        "    print(type(e).__name__, '|', str(e))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
    )
    assert out.returncode == 0, out.stderr[-800:]
    got = out.stdout.strip()
    assert "NO-ERROR" not in got, "缺 key 居然没报错"
    assert "LLM_API_KEY" in got, f"报的错没点名缺哪个变量：{got}"
    assert "api_key client option" not in got, (
        f"还是那句 OpenAI SDK 的通用话（它提的 `OPENAI_API_KEY` 本项目根本不用）：{got}"
    )
