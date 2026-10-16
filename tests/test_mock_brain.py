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


def test_analyze_no_response_is_an_environment_issue_not_a_product_defect():
    """没有拿到任何响应（status=0）必须归 environment，不能算被测缺陷。

    这类失败的原因在被测服务之外（没启动、端口不通、连接被重置）。若漏掉这个分支，
    API 用例会落到「测试失败待人工确认」（test_issue）、RAG 用例会落到
    「检索召回质量缺陷」（relevance）——报告于是把「服务没起来」写成被测质量问题。
    """
    brain = MockBrain()
    broken = [
        TestResult(case_id="API-001", title="健康检查", kind="api", ok=False,
                   expected="200", actual="", actual_status=0,
                   error="ConnectionError: 无法连接 127.0.0.1:59999"),
        TestResult(case_id="RAG-001", title="检索召回", kind="rag", ok=False,
                   expected="200", actual="", actual_status=0, rag_query="向量数据库",
                   error="ConnectionError: 无法连接 127.0.0.1:59999"),
    ]
    defects = brain.analyze_failures(broken, {})
    assert len(defects) == 2
    for defect in defects:
        assert defect.category == "environment", f"{defect.title} 被归成了 {defect.category}"
        assert "环境" in defect.title


def test_analyze_a_real_rag_recall_failure_is_still_relevance():
    """对照组：服务正常响应但召回为 0，仍必须归到 relevance。

    这条与上一条一起钉住判据的方向——加环境分支不能把真缺陷也吞掉。
    """
    brain = MockBrain()
    r = TestResult(case_id="RAG-002", title="中文召回", kind="rag", ok=False,
                   expected="200", actual="200 hits=[] recall@5=0.0000 MRR=0.0000",
                   actual_status=200, rag_query="向量数据库",
                   error="recall@5=0.000000 < 阈值 1.0",
                   evidence="命中缺少 ['DOC-CN-002']（实际 []）",
                   metrics={"recall_at_k": 0.0, "mrr": 0.0})
    defects = brain.analyze_failures([r], {})
    assert len(defects) == 1
    assert defects[0].category == "relevance"
    assert defects[0].severity == "high"
