"""LLMBrain 的降级与畸形输出回归。

真实 LLM 调用不在测试里发生（CI 不注入任何 Key），但"LLM 挂了会怎样"必须被测到：
审计发现旧实现里 llm.invoke 没有任何保护，Key 配了但网络/鉴权/解析失败会让整条流水线
traceback 退出、连报告都不生成，而文档却声称"不会失败"。

这里用 monkeypatch 制造三类失败，断言的是同一件事：**要么降级并留下痕迹，绝不静默产出一条跑不了的用例，也绝不崩掉流程。**
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agenttest.brain.llm_brain import LLMBrain
from agenttest.config import Config
from agenttest.models import TestResult


def _brain() -> LLMBrain:
    cfg = Config()
    cfg.llm_api_key = "sk-test-not-a-real-key"
    cfg.llm_base_url = "http://127.0.0.1:59999/v1"
    return LLMBrain(cfg)


class _RaisingChat:
    """模拟 LLM 不可达：with_structured_output 之后 invoke 抛异常。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        raise self._exc


class _CannedChat:
    """模拟 LLM 成功返回，但内容是畸形/空的。"""

    def __init__(self, out) -> None:
        self._out = out

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return self._out


def test_llm_unreachable_degrades_plan_instead_of_crashing(monkeypatch):
    brain = _brain()
    monkeypatch.setattr(brain, "_chat", lambda: _RaisingChat(ConnectionError("Connection error.")))

    cases = brain.make_plan("冒烟", {"paths": {}}, {"base_url": "http://127.0.0.1:1"})

    assert cases, "降级后必须仍然给出一份可执行的用例列表"
    assert brain.last_fallback_reason, "降级必须留下原因，否则报告会把规则引擎输出标成真实 LLM"


def test_llm_unreachable_degrades_attribution_instead_of_crashing(monkeypatch):
    brain = _brain()
    monkeypatch.setattr(brain, "_chat", lambda: _RaisingChat(TimeoutError("timed out")))
    failed = [TestResult(case_id="API-001", title="x", ok=False, error="boom")]

    defects = brain.analyze_failures(failed, {"base_url": "http://127.0.0.1:1"})

    assert defects, "归因失败也必须给出兜底结论"
    assert brain.last_fallback_reason


def test_malformed_llm_output_degrades(monkeypatch):
    """with_structured_output 对畸形 JSON 会抛 ValidationError——同样不能穿透。"""
    brain = _brain()
    monkeypatch.setattr(
        brain, "_chat", lambda: _RaisingChat(ValidationError.from_exception_data("_PlanResult", []))
    )

    cases = brain.make_plan("冒烟", {"paths": {}}, {"base_url": "http://127.0.0.1:1"})

    assert cases
    assert brain.last_fallback_reason


def test_empty_llm_plan_degrades(monkeypatch):
    brain = _brain()
    out = type("_Out", (), {"cases": []})()
    monkeypatch.setattr(brain, "_chat", lambda: _CannedChat(out))

    cases = brain.make_plan("冒烟", {"paths": {}}, {"base_url": "http://127.0.0.1:1"})

    assert cases
    assert "空" in brain.last_fallback_reason


def test_successful_call_leaves_no_fallback_mark(monkeypatch):
    """正例：正常返回不得被误标成降级，否则警告会变成噪音。"""
    brain = _brain()
    from agenttest.models import TestCase

    out = type("_Out", (), {"cases": [TestCase(id="API-001", title="t", path="/x")]})()
    monkeypatch.setattr(brain, "_chat", lambda: _CannedChat(out))

    cases = brain.make_plan("冒烟", {"paths": {}}, {"base_url": "http://127.0.0.1:1"})

    assert [c.id for c in cases] == ["API-001"]
    assert brain.last_fallback_reason == ""


def test_llm_requires_api_key():
    with pytest.raises(ValueError):
        LLMBrain(Config())
