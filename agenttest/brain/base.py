"""Brain 抽象接口。"""
from __future__ import annotations

from typing import Any, Dict, List, Protocol

from agenttest.models import Defect, TestCase, TestResult


class BaseBrain(Protocol):
    name: str

    def make_plan(self, goal: str, spec: Dict[str, Any], context: Dict[str, Any]) -> List[TestCase]:
        """根据目标与契约生成测试计划（用例列表）。"""
        ...

    def analyze_failures(self, results: List[TestResult], context: Dict[str, Any]) -> List[Defect]:
        """分析失败用例，产出缺陷结论。"""
        ...
