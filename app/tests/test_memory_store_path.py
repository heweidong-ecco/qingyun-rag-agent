"""Qdrant 的落盘路径必须**绝对**（⛔ 不跟着 CWD 跑）—— `①b`。
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
