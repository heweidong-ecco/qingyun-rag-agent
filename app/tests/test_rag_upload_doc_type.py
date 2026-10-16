"""`/rag/upload_document` 的【分档】契约 ——  /。

## 为什么要有它

`app/routing/api_v1_rag.py` 的上传端点原文只有一句**按扩展名猜**：

    doc_type = "legal" if ext == "pdf" else "technical"

⇒ **除 PDF 外的一切**（`.md` / `.txt` / `.docx`）**全吃 `technical`**
⇒ `report` / `article` 两档**从来用不上**；
而 FAQ 的问答对只有 **140–300 字**，会被**合并进 500 的块**
检索命中的是「**四五对问答的混合体**」。

**2026-10-08 作者已裁**：「上传接口加一个**可选** `doc_type` 形参，
**默认 = 现有推断 ⇒ 访客行为一字不变**；我们的灌库脚本可显式给。」

## 本文件钉四件

| # | 钉什么 | 反面（写歪了会怎样） |
|---|---|---|
| ① | **不传** ⇒ **仍是**扩展名推断（`.pdf`→`legal`，其余→`technical`） | 悄悄变成 `default` ⇒ **访客的块全变了，且没人会发现** |
| ② | **传了** ⇒ 用它 | 传了不生效 ⇒ 乙的四类语料**全挤进同一档** |
| ③ | 传**空白**（`""` / `"  "`）⇒ 按"没传"处理 | 空白当成一个真档名 ⇒ 静默回落 `default` |
| ④ | 传**未知档位** ⇒ **响亮拒绝** | `get_text_splitter` 是 `.get(doc_type, default)` ⇒ **静默回落**，写错档名**不报错** |

④ 是**有意加的**（里记了它是本 Agent 补的，不是作者原话）——
理由：本仓立场「**响亮 > 静默**」（那族的同一个病）。
"""
import asyncio
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


class _FakeUpload:
    """够 `upload_document` 用的最小 `UploadFile` 替身（只要 `.filename` 与 `.read()`）。"""

    def __init__(self, filename: str, data: bytes = b"# \xe6\xa0\x87\xe9\xa2\x98\n\n\xe6\xad\xa3\xe6\x96\x87"):
        self.filename = filename
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        """⚠️ **签名必须与真的那个一致** —— `starlette.UploadFile.read(size=-1)`。

        🔴 2026-10-14：它原先写的是 `async def read(self)` —— **不收参数**。
           而 `upload_document` 改成分块读（`await file.read(64*1024)`）之后
           —— 那是 `①b`  要的「**大小上限必须卡在 `read()` 之前**」——
           **当场 `TypeError: read() takes 1 positional argument but 2 were given`**。
           ⇒ 那是**夹具陈旧**（假装了一个不存在的 API），⛔ 不是被测代码错了。

        ⚠️ **必须真按 `size` 截断、且耗尽之后回 `b""`** ——
           调用方是 `while True` 循环（读空串才退出），⛔ 每回同一份数据会**死循环**。
        """
        if size is None or size < 0:
            piece, self._pos = self._data[self._pos:], len(self._data)
            return piece
        piece = self._data[self._pos:self._pos + size]
        self._pos += len(piece)
        return piece


@pytest.fixture
def spy(monkeypatch):
    """把端点里那四处**外部依赖**全换掉，只留【分档】这一段真逻辑被观测。

    **不 mock `split_text_with_filter` 本身要判的东西** —— 它是**被观测者**：
    本夹具只是把它换成一个**记录调用参数**的探针，参数照样是真端点算出来的。
    """
    import routing.api_v1_rag as mod

    seen = {}

    def fake_split(text, doc_type="default", min_length=20):
        seen["doc_type"] = doc_type
        return ["块"]

    monkeypatch.setattr(mod, "parse_document", lambda p: "**正文**" * 40)
    monkeypatch.setattr(mod, "get_embedding", lambda c, **k: [0.0, 0.0, 0.0])
    monkeypatch.setattr(mod, "insert_document", lambda *a, **k: None)
    monkeypatch.setattr(mod, "split_text_with_filter", fake_split)
    return seen


def _call(filename, **kw):
    import routing.api_v1_rag as mod

    return asyncio.run(
        mod.upload_document(file=_FakeUpload(filename), user_name="probe", **kw)
    )


# --------------------------------------------------------------------------- #
# ① 不传 ⇒ 扩展名推断（**访客走的就是这条路**）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("filename, expected", [
    ("手册.md", "technical"),
    ("说明书.docx", "technical"),   # 别用 .txt —— 它【不在】白名单里（端点会 400），
                                    #    那是另一条规矩，不该混进本条来测。
    ("合同.pdf", "legal"),
])
def test_no_doc_type_keeps_the_extension_guess(spy, filename, expected):
    """**改动的第一红线**：不传 `doc_type` 的调用方（= 访客）**行为一字不变**。"""
    _call(filename)
    assert spy["doc_type"] == expected, (
        f"{filename} 不传 doc_type 时分到了 {spy['doc_type']!r}，应为 {expected!r}。\n"
        "  ⚠️ 症状：访客上传的块尺寸全变了，而**没有任何报错**。"
    )


# --------------------------------------------------------------------------- #
# ② 传了 ⇒ 用它
# --------------------------------------------------------------------------- #

def test_explicit_doc_type_wins(spy):
    """灌库脚本显式给档位时必须生效 —— 否则乙的四类语料照样全挤一档。"""
    _call("产品.md", doc_type="report")
    assert spy["doc_type"] == "report"


def test_faq_doc_type_is_usable(spy):
    """FAQ 那一条是本次裁定的**起因**（缺口的正面）。"""
    _call("faq.md", doc_type="faq")
    assert spy["doc_type"] == "faq"


# --------------------------------------------------------------------------- #
# ③ 空白 ⇒ 当"没传"
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_doc_type_falls_back_to_the_guess(spy, blank):
    """空白串**不是**一个档名 —— 当成"没传"，不是静默回落 `default`。"""
    _call("手册.md", doc_type=blank)
    assert spy["doc_type"] == "technical"


# --------------------------------------------------------------------------- #
# ④ 未知档位 ⇒ 响亮拒绝
# --------------------------------------------------------------------------- #

def test_unknown_doc_type_is_rejected_loudly(spy):
    """写错档名必须**当场报错**。

    `chunker.get_text_splitter` 用的是 `CHUNK_CONFIGS.get(doc_type, CHUNK_CONFIGS["default"])`
    ⇒ **未知档位静默回落 `default`** ⇒ 一个拼错的 `"techincal"` 会让整批语料按 500/50 切，
    **不报任何错**。⇒ 端点这一层是**唯二**能拦住它的地方（另一处是灌库脚本自己）。

    本行为是**本 Agent 补的**，作者原话只说了"加可选形参"。
    """
    from core.exceptions import AppException

    with pytest.raises(AppException) as ei:
        _call("手册.md", doc_type="techincal")   # ← 故意拼错
    assert "techincal" in str(ei.value), "报错里要【带上那个错档名】，否则人不知道错在哪"


# --------------------------------------------------------------------------- #
# 档位表本身（定的两个数）
# --------------------------------------------------------------------------- #

def test_chunk_configs_are_the_ruled_values():
    """`technical` 500→600（来源建议 600–800）· 新增 `faq` = 300/50。

    重叠一起从 50 提到 60 **是【本 Agent 加的一步】** —— 那份调研文档自己按
    **比例**判（原文：「500/50 = 10% · 800/100 = 12.5% · 1000/200 = 20% 」），
    只改 size 不改 overlap 会把它压到 **8.3%**，**低于它自己的口径**。
    """
    from rag.chunker import CHUNK_CONFIGS

    assert CHUNK_CONFIGS["technical"] == {"chunk_size": 600, "chunk_overlap": 60}, (
        f"technical 档现为 {CHUNK_CONFIGS['technical']} —— 应为 600/60"
    )
    assert CHUNK_CONFIGS.get("faq") == {"chunk_size": 300, "chunk_overlap": 50}, (
        "`faq` 档缺失或数值不对 —— 没有它，上传接口那个 `doc_type` 形参对 FAQ 就是空的。\n"
        f"  现状: {CHUNK_CONFIGS}"
    )


def test_default_and_the_untouched_tiers_did_not_move():
    """只该动 `technical` + 新增 `faq` —— 其余三档**一个数都不许变**。

    （本仓前科：一次"顺手调优"改到了没打算改的那一档，而**所有用例照样绿**。）
    """
    from rag.chunker import CHUNK_CONFIGS

    assert CHUNK_CONFIGS["default"] == {"chunk_size": 500, "chunk_overlap": 50}
    assert CHUNK_CONFIGS["legal"] == {"chunk_size": 800, "chunk_overlap": 100}
    assert CHUNK_CONFIGS["report"] == {"chunk_size": 800, "chunk_overlap": 100}
    assert CHUNK_CONFIGS["article"] == {"chunk_size": 1000, "chunk_overlap": 200}
