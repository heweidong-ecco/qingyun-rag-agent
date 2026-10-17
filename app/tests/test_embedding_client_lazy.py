"""`app/rag/embedding_client.py` —— **客户端惰性构造**。

判据两条（改前都红）：
1. 缺 `DASHSCOPE_API_KEY` 时 **`import embedding_client` 不许炸**
2. 走到调用时，报的错必须**点名那个变量**，不是 OpenAI SDK 那句
   `The api_key client option must be set …`（它连 `OPENAI_API_KEY` 都提，本项目根本不用那个名字）
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = str(Path(__file__).resolve().parents[2])

# 在子进程里**掐掉 `.env` 加载** —— 逐字复现「CI / 新鲜检出没有 `.env`」那条路
# （就是 rsync 掉 `.env` 再跑；`ci.yml` 靠塞 dummy key 绕开的就是这件事）。
# 补丁必须在 `import config` **之前**打：`config.py` 是 `from dotenv import load_dotenv`，
#    `from X import Y` 在 import 期查 `X.Y` ⇒ 先改 `dotenv.load_dotenv` 才拦得住。
NO_DOTENV = "import dotenv; dotenv.load_dotenv = lambda *a, **k: None;"


def _run_without_key(code: str):
    """在没有 `DASHSCOPE_API_KEY`、也读不到 `.env` 的子进程里跑 `code`。"""
    env = {k: v for k, v in os.environ.items() if k != "DASHSCOPE_API_KEY"}
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env,
    )


# ==================== 判据 1：import 不许炸 ====================

def test_import_succeeds_without_the_key():
    """缺 key 时 `import embedding_client` 必须成功 —— 否则**整条 import 链**跟着崩。"""
    r = _run_without_key(
        NO_DOTENV
        + "import sys; sys.path.insert(0, 'app');"
        + "import rag.embedding_client;"
        + "print('IMPORT-OK')"
    )
    assert r.returncode == 0, (
        "缺 key 时 import 就崩 ⇒ `rag_pipeline` / `api_v1` / `hybrid_search` 全都起不来。\n"
        + r.stderr[-800:]
    )
    assert "IMPORT-OK" in r.stdout


# ==================== 判据 2：报错要点名 ====================

def test_missing_key_is_reported_by_name_not_by_openai():
    """缺 key 走到调用时，报的错要含 `DASHSCOPE_API_KEY`，且不是 OpenAI SDK 那句通用话。"""
    r = _run_without_key(
        NO_DOTENV
        + "import sys; sys.path.insert(0, 'app');"
        # 这里必须是**换行**不是 `;` —— `try:` 是复合语句，跟在 `;` 后面是 SyntaxError
        + "import rag.embedding_client\n"
        + "try:\n"
        + "    rag.embedding_client.get_embedding('x')\n"
        + "    print('NO-ERROR')\n"
        + "except Exception as e:\n"
        + "    print(type(e).__name__, '|', str(e))\n"
    )
    assert r.returncode == 0, r.stderr[-800:]
    out = r.stdout.strip()
    assert "NO-ERROR" not in out, "缺 key 居然没报错"
    assert "DASHSCOPE_API_KEY" in out, f"报的错没点名缺哪个变量：{out}"
    assert "api_key client option" not in out, (
        f"还是那句 OpenAI SDK 的通用话（它提的 `OPENAI_API_KEY` 本项目根本不用）：{out}"
    )


# ==================== 正向控制：有 key 时客户端照建、且是惰性单例 ====================

def test_client_is_built_once_and_carries_the_configured_key(monkeypatch):
    """有 key 时客户端建得出来、拿的就是配置里那把，且**不重复建**。

    这条是**正向控制** —— 没有它，上面两条「不许炸」可以由「干脆永远不建客户端」满足。

    用**环境变量**这个杠杆（不是改 `config` 属性）：`_get_client` 与
    `llm_factory._resolve` 同为「env 优先、回落 config」，而 `config` 在 import 时就
    `load_dotenv` 把 `.env` 灌进了 `os.environ` ⇒ 改 config 是**够不着**的。
    """
    import rag.embedding_client as embedding_client

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-sentinel-for-test")
    monkeypatch.setattr(embedding_client, "_client", None)

    first = embedding_client._get_client()
    assert first.api_key == "sk-sentinel-for-test", "客户端没拿到配置里那把 key"
    assert embedding_client._get_client() is first, "惰性单例被拆了：每次调用都在重建客户端"
