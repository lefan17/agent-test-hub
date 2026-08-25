"""领域模型：测试用例、执行结果、缺陷、报告。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Priority = Literal["P0", "P1", "P2"]
Severity = Literal["critical", "high", "medium", "low"]


class Check(BaseModel):
    """UI 断言项。"""

    type: Literal["contains_text", "has_element", "title_contains"] = "contains_text"
    target: str = Field(description="文本内容或 CSS 选择器")


class TestCase(BaseModel):
    """一条测试用例（API 或 UI）。"""
    __test__ = False  # 避免被 pytest 当作测试类收集

    id: str = Field(description="用例唯一编号")
    title: str
    kind: Literal["api", "ui"] = "api"
    priority: Priority = "P1"
    # API 字段
    method: str = "GET"
    path: str = ""
    query: Dict[str, str] = Field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    expected_status: int = 200
    # UI 字段
    url: str = ""
    checks: List[Check] = Field(default_factory=list)
    source: str = "spec"


class TestResult(BaseModel):
    """单条用例的执行结果。"""
    __test__ = False  # 避免被 pytest 当作测试类收集

    case_id: str
    title: str
    kind: Literal["api", "ui"] = "api"
    ok: bool
    expected: str = ""
    actual: str = ""
    actual_status: int = 0
    error: str = ""
    evidence: str = ""
    duration_ms: int = 0
    attempts: int = 1
    retried: bool = False
    notes: List[str] = Field(default_factory=list)


class Defect(BaseModel):
    """Agent 分析失败结果后产出的缺陷结论。"""
    __test__ = False  # 避免被 pytest 当作测试类收集

    id: str
    title: str
    severity: Severity = "medium"
    category: Literal["bug", "contract", "flaky", "environment", "test_issue"] = "bug"
    endpoint: str = ""
    evidence: str = ""
    suggestion: str = ""
    related_case_ids: List[str] = Field(default_factory=list)


class TestReport(BaseModel):
    """一次完整运行的报告。"""
    __test__ = False  # 避免被 pytest 当作测试类收集

    goal: str
    started_at: str = ""
    finished_at: str = ""
    brain_name: str = "mock"
    llm_configured: bool = False
    base_url: str = ""
    summary: Dict[str, int] = Field(default_factory=dict)
    cases: List[TestResult] = Field(default_factory=list)
    defects: List[Defect] = Field(default_factory=list)
    coverage: Dict[str, List[str]] = Field(default_factory=dict)
    trace: List[str] = Field(default_factory=list)
    report_files: Dict[str, str] = Field(default_factory=dict)
