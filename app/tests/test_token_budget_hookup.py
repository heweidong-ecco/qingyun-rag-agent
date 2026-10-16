"""`record_from_response` 的**离线**单元判据。
"""
from langchain_core.messages import AIMessage

import billing.token_tracker as token_tracker


class _FakeLLM:
    """最小假模型：`record_from_response` 只从它身上读 `model_name`。"""

    model_name = "fake-model"


def _capture(monkeypatch):
    """把真记账换掉，只留「谁被用什么参数调了」。"""
    calls = []
    monkeypatch.setattr(token_tracker, "record_usage", lambda **kw: calls.append(kw))
    return calls


def test_records_when_usage_metadata_present(monkeypatch):
    """正常路径：带 `usage_metadata` 的响应 ⇒ 记一条，且用户/会话/模型都对。"""
    calls = _capture(monkeypatch)
    msg = AIMessage(
        content="hi",
        usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    )

    assert token_tracker.record_from_response(
        _FakeLLM(), msg, "answer_generation",
        user_name="isolation_a", thread_id="A-thread-001",
    ) is True

    assert len(calls) == 1
    kw = calls[0]
    assert (kw["prompt_tokens"], kw["completion_tokens"]) == (11, 7)
    assert kw["purpose"] == "answer_generation"
    assert kw["user_name"] == "isolation_a"
    assert kw["thread_id"] == "A-thread-001"
    # 不许写死模型名 —— 本仓因它按【错的单价】记过账
    assert kw["model"] == "fake-model"


def test_does_not_record_on_the_old_wrong_attribute(monkeypatch):
    """这条钉的就是 `agent_checkpointer.py:83` 那个 bug 本身。

    一个**只**带 `.usage` 的对象（旧判据认、真 langchain 对象**从来不带**）⇒ **必须不记账**。
    反向控制：若把实现改回 `hasattr(response, "usage")`，本用例**变红**
    （它会替那个错误形状记账）—— 这正是我们要它红的原因。
    """
    calls = _capture(monkeypatch)

    class _HasDotUsageOnly:
        usage = type("_U", (), {"prompt_tokens": 5, "completion_tokens": 5})()

    assert token_tracker.record_from_response(
        _FakeLLM(), _HasDotUsageOnly(), "agent_decision",
    ) is False
    assert calls == []


def test_real_aimessage_without_usage_is_skipped_not_raised(monkeypatch):
    """没有用量信息 ⇒ **静默跳过**，不抛异常。

    取向与 `get_daily_token_usage` 的 fail-open 同族：模型没回 usage 不该让整个请求 500。
    但**代价要写明白**：这一笔会**漏记**。
    """
    calls = _capture(monkeypatch)

    assert token_tracker.record_from_response(
        _FakeLLM(), AIMessage(content="no usage"), "answer_generation",
    ) is False
    assert calls == []


def test_model_falls_back_when_object_has_no_name(monkeypatch):
    """对象上既没有 `model_name` 也没有 `model` ⇒ 落到 `"unknown"`，不是崩。"""
    calls = _capture(monkeypatch)
    # `total_tokens` 在新版 langchain 里是 `usage_metadata` 的【必填】项 ——
    #    漏了会挂在 pydantic 校验上，测试就**红错了地方**（挂在校验而不是挂在行为上）。
    msg = AIMessage(content="x", usage_metadata={
        "input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

    assert token_tracker.record_from_response(
        object(), msg, "agent_decision",
    ) is True
    assert calls[0]["model"] == "unknown"


def test_multimodal_usage_counts_only_text_tokens(monkeypatch):
    """钉住「取的是 `input_tokens`/`output_tokens` 两个键，不是 `total_tokens`」。

    `usage_metadata` 里可能还挂着输入模态细分（`input_token_details`）等键 ——
    实现只要那两个数字，多出来的键**不许**被算进去。
    """
    calls = _capture(monkeypatch)
    msg = AIMessage(content="x", usage_metadata={
        "input_tokens": 3, "output_tokens": 4, "total_tokens": 7,
        "input_token_details": {"image": 999},
    })

    token_tracker.record_from_response(_FakeLLM(), msg, "answer_generation")
    kw = calls[0]
    assert (kw["prompt_tokens"], kw["completion_tokens"]) == (3, 4)
