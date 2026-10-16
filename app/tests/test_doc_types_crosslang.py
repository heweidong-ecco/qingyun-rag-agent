"""**跨语言**：前端那份 `doc_type` 档位表，必须**逐字等于**后端 `CHUNK_CONFIGS` 的键。
"""
import json
import os
import subprocess

from main import static_dir
from rag.chunker import CHUNK_CONFIGS


def _frontend_doc_types() -> list:
    """用 node 读 `kb.js` 导出的那份 —— 不是拿正则从源码里抠字面量
    （抠不到"导出的到底是不是它"）。"""
    js_dir = os.path.join(static_dir, "js")
    proc = subprocess.run(
        ["node", "-e", "console.log(JSON.stringify(require('./kb.js').DOC_TYPES))"],
        cwd=js_dir, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"跑 node 读 kb.js 的 DOC_TYPES 失败：\n{proc.stderr[:400]}"
    return json.loads(proc.stdout)


def test_the_frontend_table_is_not_empty():
    """**防空跑** —— 空数组与空数组比，怎么比都相等（本仓最恨的静默假通过）。"""
    fe = _frontend_doc_types()
    assert fe, "前端那份 `DOC_TYPES` 是空的 ⇒ 下面那条会**恒为真**"
    assert CHUNK_CONFIGS, "后端那份 `CHUNK_CONFIGS` 是空的 ⇒ 同上"


def test_frontend_doc_types_match_backend_chunk_configs():
    """**两份必须逐字相等**（顺序不计 —— 那是展示顺序，两边本来就不同）。"""
    fe = sorted(_frontend_doc_types())
    be = sorted(CHUNK_CONFIGS.keys())
    assert fe == be, (
        f"前端的档位表与后端对不上：\n  前端：{fe}\n  后端：{be}\n"
        f"⇒ 访客选中一个**后端不认**的档位 ⇒ `/rag/upload_document` **当场 400**，"
        f"而且**先花掉 embedding 才报错**（那一页的接口都是写接口）。"
        f"\n⇒ 改哪边都行，但**两边必须一起改**。"
    )
