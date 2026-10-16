"""Qdrant 的落盘路径必须**绝对**（⛔ 不跟着 CWD 跑）—— `①b`。

## 为什么要有这一条

改前写的是 **`"./.mem0/qdrant"`（相对路径）**，而它在**模块导入时**就被
`Memory.from_config(...)` 解析掉了 ⇒ **落点取决于当时从哪启动**。

🔴 **本机实测已经发生过**（2026-10-14 量出来的，⛔ 不是推的）：
   · `app/.mem0/qdrant/collection/mem0`（2026-08-17）
   · `./.mem0/qdrant/collection/mem0`（2026-09-17）
   —— **两份独立的记忆，都还在**。⇒ 「从 `app/` 启动」与「从仓库根启动」写出了两套记忆，
   而**没有任何门会报**。

⇒ 本文件把默认值钉成**绝对路径**，并且钉在**这个包自己的位置**上。

⚠️ 本文件会 `import agent.memory_store`，那会**就地打开 Qdrant**（mem0 的单实例锁）。
   ⛔ **所以它只能在同进程里跑** —— 别把它放进任何子进程（`app/tests/CLAUDE.md` 第 4 条）。
"""
import os

from agent import memory_store as ms


def test_default_path_is_absolute():
    """🔴 默认落点**必须绝对** —— 相对路径会跟着 CWD 跑（本机已经裂成两份过）。"""
    assert os.path.isabs(ms._MEM0_ROOT), f"默认落点是相对路径：{ms._MEM0_ROOT!r}"
    assert os.path.isabs(ms.mem0_data_dir())


def test_the_root_lands_under_the_app_directory():
    """它锚在**这个包自己的位置**上（`app/.mem0`）—— ⛔ 不是仓库根、⛔ 不是 CWD。

    ⚠️ **为什么锚在这儿**：`app/.mem0` 正是**本仓一直以来的那个落点**
       （从 `app/` 启动时相对路径解析到的就是它）⇒ 这次改动**不搬动已有数据**。
    """
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(ms.__file__)))
    assert ms._MEM0_ROOT == os.path.join(app_dir, ".mem0")


def test_env_overrides_and_is_read_every_time(monkeypatch, tmp_path):
    """`MEM0_DATA_DIR` 能覆盖它，**且每次现读**（缓存会让 `monkeypatch.setenv` 静默无效）。"""
    monkeypatch.setenv(ms.MEM0_DATA_DIR_ENV, str(tmp_path / "m"))
    assert ms.mem0_data_dir() == str(tmp_path / "m")

    monkeypatch.delenv(ms.MEM0_DATA_DIR_ENV)
    assert ms.mem0_data_dir() == ms._MEM0_ROOT, "拿掉 env 之后没回到默认值"
