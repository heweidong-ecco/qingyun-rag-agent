"""今日 token 按用途拆成 **LLM / Embedding / 其它**（前端整体调整 · 批 4）。

本文件**不连库** —— 拆分数那个是**纯函数**；而"归类全不全"那条走的是**扫源码**。
"""
import os
import re

from billing.token_tracker import (
    EMBEDDING_PURPOSES, LLM_PURPOSES, split_daily_tokens,
)

API_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def test_split_is_a_pure_function_and_keeps_other_separate():
    got = split_daily_tokens({
        "embedding": 300,
        "answer_generation": 1200,
        "query_rewrite": 400,
        "某个还没归类的用途": 7,
    })
    assert got["llm_tokens_today"] == 1600.0
    assert got["embedding_tokens_today"] == 300.0
    assert got["other_tokens_today"] == 7.0, (
        "归不进两表的用途被并进了某一边 —— 那是**编一个数**（本仓：不许印没有数据源的数）"
    )


def test_split_tolerates_empty_and_none():
    for bad in ({}, None):
        got = split_daily_tokens(bad)
        assert got == {"llm_tokens_today": 0.0, "embedding_tokens_today": 0.0, "other_tokens_today": 0.0}


def test_the_two_tables_do_not_overlap():
    """同一张表里**不许**一个 purpose 两边都在 —— 那会让它**被算两次**。"""
    overlap = set(LLM_PURPOSES) & set(EMBEDDING_PURPOSES)
    assert not overlap, f"这些 purpose 同时出现在两张表里：{sorted(overlap)}"


def test_every_purpose_in_our_code_is_classified():
    """**活代码里出现过的每个 `purpose=` 都要归进两张表之一。**

    **为什么非有这条**：漏归一个 ⇒ 它**静默进「其它」** ⇒ 页头上那两个数悄悄变小，
    而**没有任何门会红**（同族： 的类名、`token_tracker` 的别名表）。
    扫的是 **`app/` 下的产品代码**，不含 `tests/`（那里的 purpose 是夹具，故意用 `test` / `alice_only`）。
    判据是**命中的集合**，不是"数条数"（数条数会被新增文件骗过去）。
    """
    pat = re.compile(r"""purpose\s*=\s*["']([A-Za-z_][A-Za-z0-9_.]*)["']""")
    found = set()
    for root, dirs, files in os.walk(API_DIR):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests", "alembic")]
        for f in files:
            if not f.endswith(".py"):
                continue
            text = open(os.path.join(root, f), encoding="utf-8").read()
            found |= set(pat.findall(text))
    assert found, "一个 `purpose=` 都没扫到 —— 扫描规则失效了（⛔ 别直接删了这条）"
    known = set(LLM_PURPOSES) | set(EMBEDDING_PURPOSES)
    unknown = sorted(found - known)
    assert not unknown, (
        f"这些 purpose 在活代码里用着、却没归进任何一张表：{unknown}\n"
        f"⇒ 它们会被算进「其它」，页头上 LLM / Embedding 那两个数会**悄悄变小**\n"
        f"⇒ 请把它们加进 `token_tracker.LLM_PURPOSES`（或 `EMBEDDING_PURPOSES`）"
    )
