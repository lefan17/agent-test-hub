"""LLMBrain：LangChain + OpenAI 兼容接口的智能规划与缺陷归因。

通过 with_structured_output 约束输出为 Pydantic 模型，保证下游可直接消费。
使用真实 LLM 时，Agent 的"规划能力"来自大模型对契约的理解；工具执行仍走确定性代码，
既展示 Agent 智能，又保证测试结果可复现。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from agenttest.config import Config
from agenttest.models import Defect, TestCase, TestResult
from agenttest.tools.spec_tool import summarize_spec

SYSTEM_PLAN_PROMPT = """你是一名资深测试工程师兼 Agent 架构师，负责为一个被测系统设计自动化测试计划。
被测系统契约如下（OpenAPI 摘要）：
{spec}

要求：
1. 覆盖 正向、反向、边界、异常、契约符合性 用例；
2. API 用例必须给出 method/path/query/body/expected_status；
3. UI 用例（kind=ui）必须给出 url 和 checks（contains_text / has_element 的 target）；
4. 契约里写明的状态码要与实现核对——如果怀疑实现不符，也要生成对应契约用例来验证；
5. 输出 12~20 条用例，id 用 API-xxx / UI-xxx 编号，优先级 P0/P1/P2。"""

SYSTEM_ANALYSIS_PROMPT = """你是一名资深测试工程师。以下是自动化测试失败用例的执行结果：
{results}

请逐条分析失败原因，并归类为缺陷。每个缺陷输出：
- title: 一句话结论
- severity: critical/high/medium/low
- category: bug（实现错误）| contract（与契约不符）| flaky（不稳定）| environment（环境问题）| test_issue（用例本身问题）
- endpoint: 关联接口或页面
- evidence: 证据摘要（期望 vs 实际）
- suggestion: 修复建议
请合并同类失败，输出 1~5 个缺陷。"""


class _PlanResult(BaseModel):
    cases: List[TestCase] = Field(default_factory=list, description="测试用例列表")


class _DefectDraft(BaseModel):
    title: str
    severity: str = "medium"
    category: str = "bug"
    endpoint: str = ""
    evidence: str = ""
    suggestion: str = ""


class _AnalysisResult(BaseModel):
    summary: str = Field(default="", description="失败分析一句话总结")
    defects: List[_DefectDraft] = Field(default_factory=list, description="缺陷列表")


class LLMBrain:
    name = "llm"

    def __init__(self, cfg: Config) -> None:
        if not cfg.llm_configured:
            raise ValueError("LLMBrain 需要 AGENTTEST_LLM_API_KEY（或使用 MockBrain）")
        self.api_key = cfg.llm_api_key
        self.base_url = cfg.llm_base_url or None
        self.model = cfg.llm_model

    def _chat(self):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=0,
            timeout=60,
        )

    def make_plan(self, goal: str, spec: Dict[str, Any], context: Dict[str, Any]) -> List[TestCase]:
        prompt = SYSTEM_PLAN_PROMPT.format(spec=summarize_spec(spec))
        user = f"目标：{goal}\n被测系统地址：{context.get('base_url')}\n请生成测试计划。"
        llm = self._chat().with_structured_output(_PlanResult)
        out = llm.invoke([{"role": "system", "content": prompt}, {"role": "user", "content": user}])
        cases = []
        for i, c in enumerate(out.cases or [], start=1):
            c.id = (c.id or f"LLM-{i:03d}").strip()
            cases.append(c)
        return cases or self._fallback_plan(context)

    def _fallback_plan(self, context: Dict[str, Any]) -> List[TestCase]:
        """LLM 输出为空时兜底，保证流程不中断。"""
        from agenttest.brain.mock_brain import MockBrain

        return MockBrain().make_plan("fallback", {}, context)

    def analyze_failures(self, results: List[TestResult], context: Dict[str, Any]) -> List[Defect]:
        failed = [r for r in results if not r.ok]
        if not failed:
            return []
        payload = [
            {"case_id": r.case_id, "title": r.title, "expected": r.expected, "actual": r.actual,
             "error": r.error, "evidence": r.evidence, "attempts": r.attempts}
            for r in failed
        ]
        prompt = SYSTEM_ANALYSIS_PROMPT.format(results=json.dumps(payload, ensure_ascii=False, indent=2))
        llm = self._chat().with_structured_output(_AnalysisResult)
        out = llm.invoke([{"role": "system", "content": prompt}])
        defects = []
        for i, d in enumerate(out.defects or [], start=1):
            defects.append(Defect(
                id=f"DEF-{i:03d}",
                title=d.title,
                severity=d.severity if d.severity in {"critical", "high", "medium", "low"} else "medium",
                category=d.category if d.category in {"bug", "contract", "flaky", "environment", "test_issue"} else "bug",
                endpoint=d.endpoint,
                evidence=d.evidence,
                suggestion=d.suggestion,
                related_case_ids=[r.case_id for r in failed],
            ))
        return defects
