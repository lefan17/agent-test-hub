"""报告渲染单元测试。"""
from __future__ import annotations

from agenttest.models import Defect, TestReport, TestResult
from agenttest.report import render_html_report


def test_html_report_contains_summary_and_defects():
    report = TestReport(
        goal="演示目标",
        started_at="2025-01-01 00:00:00",
        finished_at="2025-01-01 00:00:10",
        brain_name="mock",
        llm_configured=False,
        base_url="http://127.0.0.1:5001",
        summary={"total": 2, "passed": 1, "failed": 1, "defects": 1},
        cases=[
            TestResult(case_id="API-001", title="健康检查", kind="api", ok=True,
                       expected="200", actual="200", duration_ms=5),
            TestResult(case_id="API-006", title="非法 status", kind="api", ok=False,
                       expected="400", actual="500", error="期望 400，实际 500", duration_ms=8),
        ],
        defects=[Defect(id="DEF-001", title="契约不符", severity="high", category="contract",
                        endpoint="GET /api/todos", evidence="期望 400 实际 500", suggestion="返回 400")],
        coverage={"GET /api/todos": ["API-006"]},
        trace=["[planner] mock 生成 2 条测试用例"],
    )
    html = render_html_report(report)
    assert "Agent 自动测试报告" in html
    assert "DEF-001" in html
    assert "API-006" in html
    assert "契约不符" in html
