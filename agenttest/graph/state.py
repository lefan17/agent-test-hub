"""LangGraph 状态定义。"""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, Dict, List, TypedDict

from agenttest.models import Defect, TestCase, TestResult


class AgentState(TypedDict):
    goal: str
    spec: Dict[str, Any]
    cases: List[TestCase]
    results: List[TestResult]
    defects: List[Defect]
    coverage: Dict[str, List[str]]
    trace: Annotated[List[str], add]
    started_at: str
    context: Dict[str, Any]
    report: Dict[str, Any]
