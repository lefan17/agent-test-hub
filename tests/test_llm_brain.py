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


# ---------------------------------------------------------------------------
# related_case_ids：缺陷与失败用例的关联不许退化成「每个缺陷挂全部失败用例」
#
# 旧实现在 llm_brain.py 里写死 `related_case_ids=[r.case_id for r in failed]`，
# 于是「检索排序缺陷」也挂着与它无关的契约用例，报告里的「关联用例」一栏
# 对每个缺陷都相同 —— 有字段、无信息。下面四条钉住修好后的口径。
# ---------------------------------------------------------------------------

def _analysis(defects):
    """构造 LLM 的结构化返回（用真实 schema，顺带验证 case_ids 字段确实存在）。"""
    from agenttest.brain.llm_brain import _AnalysisResult, _DefectDraft

    return _AnalysisResult(defects=[_DefectDraft(**d) for d in defects])


def _failed_results():
    return [
        TestResult(case_id="API-006", title="非法 status 返回 500", kind="api", ok=False,
                   actual_status=500, error="期望 400，实际 500"),
        TestResult(case_id="RAG-003", title="检索排序应降序", kind="rag", ok=False,
                   actual_status=200, error="score 非降序", rag_query="search quality metrics"),
        TestResult(case_id="RAG-007", title="空 q 应返回 400", kind="rag", ok=False,
                   actual_status=200, error="期望 400，实际 200"),
        TestResult(case_id="API-001", title="健康检查", kind="api", ok=True, actual_status=200),
    ]


def test_related_case_ids_follow_what_the_llm_declared(monkeypatch):
    """正例：LLM 自报的 id 真实存在 → 按自报写，不同缺陷挂不同用例。"""
    brain = _brain()
    monkeypatch.setattr(brain, "_chat", lambda: _CannedChat(_analysis([
        {"title": "契约不符", "category": "contract", "endpoint": "GET /api/todos",
         "case_ids": ["API-006"]},
        {"title": "检索排序缺陷", "category": "relevance", "endpoint": "RAG 检索结果",
         "case_ids": ["RAG-003"]},
    ])))

    defects = brain.analyze_failures(_failed_results(), {})

    assert [d.related_case_ids for d in defects] == [["API-006"], ["RAG-003"]]
    assert len({tuple(d.related_case_ids) for d in defects}) == 2, "两个缺陷挂的用例不该完全相同"


def test_related_case_ids_drop_hallucinated_ids(monkeypatch):
    """反例：LLM 编出不存在的 case_id → 被白名单滤掉，不写进报告。"""
    brain = _brain()
    monkeypatch.setattr(brain, "_chat", lambda: _CannedChat(_analysis([
        {"title": "契约不符", "category": "contract", "endpoint": "GET /api/todos",
         "case_ids": ["API-006", "API-999", "不可能存在的用例"]},
    ])))

    defects = brain.analyze_failures(_failed_results(), {})

    assert defects[0].related_case_ids == ["API-006"]


def test_related_case_ids_fall_back_to_same_kind_only(monkeypatch):
    """兜底：自报为空/全被滤掉 → 只挂同 kind 的失败用例，绝不跨 kind 也不挂全部。"""
    brain = _brain()
    monkeypatch.setattr(brain, "_chat", lambda: _CannedChat(_analysis([
        {"title": "检索排序缺陷", "category": "relevance", "endpoint": "RAG 检索结果",
         "case_ids": []},
        {"title": "契约不符", "category": "contract", "endpoint": "GET /api/todos",
         "case_ids": ["幻觉-001"]},
    ])))

    defects = brain.analyze_failures(_failed_results(), {})

    assert defects[0].related_case_ids == ["RAG-003", "RAG-007"], "RAG 缺陷只该挂 RAG 失败用例"
    assert defects[1].related_case_ids == ["API-006"], "API 缺陷只该挂 API 失败用例"
    for defect in defects:
        assert "API-001" not in defect.related_case_ids, "通过的用例不是失败用例"
        assert len(defect.related_case_ids) < 3, "兜底不许退回「挂全部失败用例」"


def test_mock_brain_related_case_ids_unchanged():
    """对照组：MockBrain 本来就是一缺陷一用例，改造不得波及它。"""
    from agenttest.brain import MockBrain

    failed = [r for r in _failed_results() if not r.ok]
    defects = MockBrain().analyze_failures(failed, {})

    assert [d.related_case_ids for d in defects] == [["API-006"], ["RAG-003"], ["RAG-007"]]
