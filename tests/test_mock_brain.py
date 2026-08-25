"""MockBrain（规则引擎）的规划与归因逻辑测试。"""
from __future__ import annotations

from agenttest.brain import MockBrain
from agenttest.models import TestResult


def test_plan_covers_api_and_ui():
    brain = MockBrain()
    cases = brain.make_plan("test", {}, {"base_url": "http://x:1"})
    ids = [c.id for c in cases]
    assert len(cases) >= 15
    assert any(c.kind == "ui" for c in cases)
    assert any(c.kind == "api" for c in cases)
    # 两个注入缺陷对应的契约用例必须存在
    assert "API-006" in ids  # invalid status -> 400
    assert "API-008" in ids  # empty title -> 400


def test_analyze_contract_mismatch_500():
    brain = MockBrain()
    r = TestResult(case_id="API-006", title="非法 status", kind="api", ok=False,
                   expected="400", actual="500", actual_status=500,
                   error="期望 400，实际 500", evidence="")
    defects = brain.analyze_failures([r], {})
    assert len(defects) == 1
    assert defects[0].category == "contract"
    assert defects[0].severity == "high"


def test_analyze_empty_title_bug():
    brain = MockBrain()
    r = TestResult(case_id="API-008", title="空 title", kind="api", ok=False,
                   expected="400", actual="200", actual_status=200,
                   error="期望 400，实际 200", evidence="脏数据已写入")
    defects = brain.analyze_failures([r], {})
    assert defects[0].category == "bug"
    assert "校验" in defects[0].suggestion


def test_analyze_flaky():
    brain = MockBrain()
    r = TestResult(case_id="API-015", title="不稳定服务", kind="api", ok=False,
                   expected="200", actual="503", actual_status=503,
                   error="service temporarily unavailable",
                   evidence="service temporarily unavailable", attempts=3)
    defects = brain.analyze_failures([r], {})
    assert defects[0].category == "flaky"


def test_analyze_pass_only_no_defects():
    brain = MockBrain()
    r = TestResult(case_id="API-001", title="健康检查", kind="api", ok=True,
                   expected="200", actual="200")
    assert brain.analyze_failures([r], {}) == []
