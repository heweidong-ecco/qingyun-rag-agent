"""上传的两条**实测风险**（`①b`  ·  §二·4.2）。

| # | 风险 | 为什么它不是"锦上添花" |
|---|---|---|
| **①** | **大小上限必须卡在 `read()` 之前** | 改前那句 `tmp.write(await file.read())` 是**整份进内存**。本服务容器实测 **8 GiB / `Swap=0`** ⇒ **OOM 直接 kill，没有缓冲**；**一个 200 MB 文件就够了，不需要恶意、不需要并发** |
| **②** | **临时文件必须在 `try/finally` 里清** | 改前那句 `os.unlink(tmp_path)` 在**函数末尾** ⇒ 中途任何一步抛异常（解析失败 / embedding 失败 / 上面那条超限拒绝）都**执行不到它** ⇒ `/tmp` 攒垃圾，服务跑久了**没人会注意到** |

## 🔴 判据不是"代码里有 `try/finally`"

本仓纪律：**拿「动作成功」当「结果正确」**栽过多次。
⇒ 这里**直接看那个临时目录里还剩不剩文件** —— 把 `tempfile.tempdir` **独占**掉，
   这样"还剩什么"就是确定的，⛔ 不会把别的进程的临时文件算进来。
"""
import asyncio
import tempfile

import pytest

from core.exceptions import AppException
from routing import api_v1_rag


class _FakeUpload:
    """最小的 `UploadFile` 替身。

    ⚠️ **签名照真的写**（`read(size=-1)`）：那个真的（`starlette.UploadFile`）**是收 size 的**。
    ⚠️ **必须真按 size 截断、耗尽后回 `b""`** —— 调用方是 `while True` 循环，⛔ 回同一份会**死循环**。
    """

    def __init__(self, filename: str, data: bytes):
        self.filename, self._data, self._pos = filename, data, 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            piece, self._pos = self._data[self._pos:], len(self._data)
            return piece
        piece = self._data[self._pos:self._pos + size]
        self._pos += len(piece)
        return piece


def _isolate_tmpdir(monkeypatch, tmp_path):
    """把这个用例用到的临时目录**独占**掉 ⇒ "还剩不剩文件"才判得准。"""
    d = tmp_path / "tmpspace"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


def test_oversized_upload_is_rejected_and_leaves_nothing(tmp_path, monkeypatch):
    """超限 ⇒ **拒掉**，且**不留临时文件**（那条拒绝是**在循环里**抛的 ⇒ 最容易漏清）。"""
    d = _isolate_tmpdir(monkeypatch, tmp_path)
    big = _FakeUpload("a.md", b"x" * (api_v1_rag.MAX_UPLOAD_BYTES + 1))

    with pytest.raises(AppException) as ei:
        asyncio.run(api_v1_rag.upload_document(big, user_name="alice"))

    assert "过大" in str(ei.value), f"拒绝的理由不对：{ei.value}"
    assert list(d.iterdir()) == [], f"🔴 超限被拒之后临时文件还在：{list(d.iterdir())}"


def test_temp_file_is_cleaned_even_when_parsing_fails(tmp_path, monkeypatch):
    """🔴 **中途抛异常**（这里用解析失败模拟）⇒ **临时文件照样不许残留**。

    这一条才是 `try/finally` 的正身 —— 改前那句 `os.unlink` 在函数末尾，**这种场景下不会执行**。
    """
    d = _isolate_tmpdir(monkeypatch, tmp_path)

    def _boom(_path):
        raise RuntimeError("模拟解析失败")

    monkeypatch.setattr(api_v1_rag, "parse_document", _boom)
    ok = _FakeUpload("a.md", b"# t\n\nbody")

    with pytest.raises(RuntimeError):
        asyncio.run(api_v1_rag.upload_document(ok, user_name="alice"))

    assert list(d.iterdir()) == [], f"🔴 解析失败之后临时文件还在：{list(d.iterdir())}"


def test_too_many_chunks_is_rejected_before_any_embedding(tmp_path, monkeypatch):
    """🔴 **块数超限 ⇒ 当场拒，且【一次 embedding 都没调】**。

    ⚠️ 判据为什么是"`get_embedding` 被调了几次"而不是"回没回 422"：
       这一条**管的是耗时**（§二·4 第 3 条：块数决定**调多少次 embedding**）。
       一个"先跑完循环再发现超了"的实现**照样会回 422**，但它**已经把时间和额度花掉了** ——
       而那正是这条上限要防的事。⇒ **判据必须落在"有没有进循环"上**。
    """
    _isolate_tmpdir(monkeypatch, tmp_path)
    monkeypatch.setattr(api_v1_rag, "parse_document", lambda _p: "正文")
    calls = []
    monkeypatch.setattr(
        api_v1_rag, "split_text_with_filter",
        lambda *a, **k: [f"块{i}" for i in range(api_v1_rag.MAX_INGEST_CHUNKS + 1)],
    )
    monkeypatch.setattr(api_v1_rag, "get_embedding",
                        lambda chunk, user_name=None: calls.append(chunk) or [0.0])

    with pytest.raises(AppException) as ei:
        asyncio.run(api_v1_rag.upload_document(_FakeUpload("a.md", b"# t"), user_name="alice"))

    assert "块" in str(ei.value), f"拒绝的理由不对：{ei.value}"
    assert calls == [], f"🔴 它在拒绝【之前】已经调了 {len(calls)} 次 embedding —— 闸挂在循环后面了"


def test_a_normal_sized_upload_is_not_rejected(tmp_path, monkeypatch):
    """**正控**：正常大小的文件⛔ **不许**被那道上限误伤。

    ⚠️ 没有这一条，一个"见谁都拒"的实现也能让上面那条绿。
    """
    d = _isolate_tmpdir(monkeypatch, tmp_path)
    monkeypatch.setattr(api_v1_rag, "parse_document", lambda _p: "正文")
    monkeypatch.setattr(api_v1_rag, "get_embedding", lambda chunk, user_name=None: [0.0])
    monkeypatch.setattr(api_v1_rag, "insert_document", lambda *a, **k: None)
    monkeypatch.setattr(api_v1_rag, "invalidate_bm25_cache", lambda: None)
    small = _FakeUpload("a.md", b"# t\n\n" + b"x" * 4096)

    out = asyncio.run(api_v1_rag.upload_document(small, user_name="alice"))

    assert out["filename"] == "a.md"
    assert list(d.iterdir()) == [], "正常上传之后也不许留临时文件"
