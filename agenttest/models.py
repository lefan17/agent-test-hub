"""领域模型：测试用例、执行结果、缺陷、报告。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Priority = Literal["P0", "P1", "P2"]
Severity = Literal["critical", "high", "medium", "low"]


class Check(BaseModel):
    """断言项：UI 断言（文本/元素/标题）与 RAG 检索断言（召回/排序/去重/索引一致性/指标阈值）。

    rag_* 断言项的语义：
      rag_hits_include      hits 中必须包含 doc_ids（或 target）里的每个 doc_id
      rag_rank_monotonic    rank 连续 1..n 且 score 单调非增
      rag_tie_by_doc_id     分数并列时按 doc_id 升序（稳定次序）
      rag_no_duplicate      同一 doc_id 不得在 hits 中重复出现
      rag_recall_at_least   recall@top_k >= threshold（相关文档取 doc_ids）
      rag_mrr_at_least      MRR >= threshold
      rag_no_ghost_hit      语料中已删除的文档不得再被召回（陈旧索引）
      rag_status            HTTP 状态码等于用例的 expected_status
    """

    type: Literal[
        "contains_text",
        "has_element",
        "title_contains",
        "rag_hits_include",
        "rag_rank_monotonic",
        "rag_tie_by_doc_id",
        "rag_no_duplicate",
        "rag_recall_at_least",
        "rag_mrr_at_least",
        "rag_no_ghost_hit",
        "rag_status",
    ] = "contains_text"
    target: str = Field(default="", description="文本内容、CSS 选择器或 doc_id")
    doc_ids: List[str] = Field(default_factory=list, description="RAG 断言涉及的 doc_id 列表")
    threshold: float = Field(default=0.0, description="RAG 指标阈值")


class TestCase(BaseModel):
    """一条测试用例（API / UI / RAG 检索链路）。"""

    __test__ = False  # 避免被 pytest 当作测试类收集

    id: str = Field(description="用例唯一编号")
    title: str
    kind: Literal["api", "ui", "rag"] = "api"
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
    # RAG 字段
    rag_query: str = ""
    top_k: int = 5
    rag_relevant: List[str] = Field(
        default_factory=list, description="标注为相关的 doc_id 集合，用于计算 recall@k / MRR"
    )
    source: str = "spec"


class TestResult(BaseModel):
    """单条用例的执行结果。"""

    __test__ = False  # 避免被 pytest 当作测试类收集

    case_id: str
    title: str
    kind: Literal["api", "ui", "rag"] = "api"
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
    metrics: Dict[str, float] = Field(default_factory=dict, description="检索质量指标（recall@k / MRR 等）")


class Defect(BaseModel):
    """Agent 分析失败结果后产出的缺陷结论。"""

    __test__ = False  # 避免被 pytest 当作测试类收集

    id: str
    title: str
    severity: Severity = "medium"
    category: Literal["bug", "contract", "flaky", "environment", "test_issue", "relevance"] = "bug"
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
    # 非空表示这一轮 LLM 调用失败、已降级为 MockBrain；报告据此避免把规则引擎
    # 的输出标成"真实 LLM"。
    brain_fallback_reason: str = ""
    base_url: str = ""
    summary: Dict[str, int] = Field(default_factory=dict)
    cases: List[TestResult] = Field(default_factory=list)
    defects: List[Defect] = Field(default_factory=list)
    coverage: Dict[str, List[str]] = Field(default_factory=dict)
    trace: List[str] = Field(default_factory=list)
    report_files: Dict[str, str] = Field(default_factory=dict)
