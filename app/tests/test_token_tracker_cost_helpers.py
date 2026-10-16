"""`token_tracker` 的三个公共件（契约 B 的取数来源）。

本文件存在的理由：契约 B 的 `usage` 帧与账本**必须同源** ——
   若帧里的钱与账本里的钱出自**两份公式**，两边迟早对不上，而**没人会收到告警**
   （本仓「闸是装饰」那一族的形状：两边各写一遍 ⇒ 谁也不会红）。
"""
import pytest
from langchain_core.messages import AIMessage

import billing.token_tracker as tt


def test_compute_cost_matches_the_registered_price():
    """`compute_cost` 与 `PRICING` 表**推导式**对齐（不是把某个数字写死）。"""
    model = "deepseek-v4-flash"
    pricing = tt.PRICING.get(model, tt._DEFAULT_PRICING)
    expected = (1200 / 1000) * pricing["prompt"] + (800 / 1000) * pricing["completion"]
    assert tt.compute_cost(model, 1200, 800) == pytest.approx(expected)


def test_compute_cost_falls_back_for_unknown_model():
    """没登记的模型走兜底价 —— 与 `record_cost` / `record_usage` **同一份兜底**。"""
    unk = "__no_such_model__"
    pricing = tt._DEFAULT_PRICING
    assert tt.compute_cost(unk, 1000, 1000) == pytest.approx(
        pricing["prompt"] + pricing["completion"]
    )


class _LLM:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_resolve_model_name_prefers_model_name_then_model():
    """`model_name` 优先于 `model`；两样都没有 ⇒ `"unknown"`（不是 `None`）。"""
    assert tt.resolve_model_name(_LLM(model_name="n1", model="n2")) == "n1"
    assert tt.resolve_model_name(_LLM(model="n2")) == "n2"
    assert tt.resolve_model_name(object()) == "unknown"


def test_usage_summary_is_none_without_usage_metadata():
    """反面守卫：没有 `usage_metadata` ⇒ **None**（不是全 0 的 dict）。

    本仓墓碑：`agent_checkpointer.py` 曾写 `hasattr(response, "usage")`（**恒为假**）——
       那整段记账**从未执行过**，而测试全绿。
       ⇒ 判据必须是 `usage_metadata`，不是 `.usage`。
    这里**故意只给错的那个属性**（`.usage`），它存在且非空 ——
       若实现写成 `.usage`，本用例会**绿**（因为它真读到了）；写成 `usage_metadata` 才返回 None。
    """
    old = AIMessage(content="x")
    setattr(old, "usage", {"input_tokens": 9})       # 只有错的那个属性
    assert tt.usage_summary(_LLM(model_name="m"), old) is None


def test_usage_summary_reads_the_real_attribute():
    """正面：真带 `usage_metadata` ⇒ 四个键齐全，且 `cost_usd` 是**真的**那个金额。

    **`cost_usd` 的期望值从 `PRICING` 现推，不是拿 `tt.compute_cost(...)` 当期望**
       —— 那样等于**拿函数和自己比**，`compute_cost` 返回什么东西它都绿（同义反复）。
       本仓 实测栽过：反证检验（把 `compute_cost` 改成 `return 0.0`）
       只红了 2 条，**本条照样绿** ⇒ 尺子没量到这件事。已改成现推。
    """
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 30, "output_tokens": 9, "total_tokens": 39},
    )
    got = tt.usage_summary(_LLM(model_name="m"), msg)
    pricing = tt.PRICING.get("m", tt._DEFAULT_PRICING)
    expected_cost = (30 / 1000) * pricing["prompt"] + (9 / 1000) * pricing["completion"]
    assert got == {
        "model": "m",
        "prompt_tokens": 30,
        "completion_tokens": 9,
        "cost_usd": expected_cost,
    }
