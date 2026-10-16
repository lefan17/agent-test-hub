"""LLMBrain：LangChain + OpenAI 兼容接口的智能规划与缺陷归因。

通过 with_structured_output 约束输出为 Pydantic 模型，保证下游可直接消费。
使用真实 LLM 时，Agent 的"规划能力"来自大模型对契约的理解；工具执行仍走确定性代码，
既展示 Agent 智能，又保证测试结果可复现。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from agenttest.config import Config
from agenttest.models import Defect, TestCase, TestResult
from agenttest.tools.spec_tool import summarize_spec

logger = logging.getLogger(__name__)

SYSTEM_PLAN_PROMPT = """你是一名资深测试工程师兼 Agent 架构师，负责为一个被测系统设计自动化测试计划。
被测系统契约如下（OpenAPI 摘要）：
{spec}

要求：
1. 覆盖 正向、反向、边界、异常、契约符合性 用例；
2. API 用例必须给出 method/path/query/body/expected_status；
3. UI 用例（kind=ui）必须给出 url 和 checks（contains_text / has_element 的 target）；
4. RAG 用例（kind=rag）必须给出 rag_query 与 top_k，checks 用 rag_hits_include（配 doc_ids）
   / rag_rank_monotonic / rag_tie_by_doc_id / rag_no_duplicate / rag_recall_at_least（配 threshold）
   / rag_mrr_at_least / rag_no_ghost_hit；related 标注文档用 rag_relevant；
5. 契约里写明的状态码要与实现核对——如果怀疑实现不符，也要生成对应契约用例来验证；
6. 输出 12~20 条用例，id 用 API-xxx / UI-xxx / RAG-xxx 编号，优先级 P0/P1/P2。"""

SYSTEM_ANALYSIS_PROMPT = """你是一名资深测试工程师。以下是自动化测试失败用例的执行结果：
{results}

请逐条分析失败原因，并归类为缺陷。每个缺陷输出：
- title: 一句话结论
- severity: critical/high/medium/low
- category: bug（实现错误）| contract（与契约不符）| flaky（不稳定）| environment（环境问题）| test_issue（用例本身问题）| relevance（检索相关性/召回质量缺陷）
- endpoint: 关联接口或页面
- case_ids: 该缺陷对应的失败用例 id 列表，**只能从上面 results 里出现过的 case_id 中挑**，
  一条缺陷只列真正由它解释的那些用例；编造 id 或把全部失败用例都列上都会被下游丢弃
- evidence: 证据摘要（期望 vs 实际）
- suggestion: 修复建议
请合并同类失败，输出 1~5 个缺陷。"""

# 用例 id 前缀 → TestCase.kind。仓库里编号规则就是「前缀即类型」，
# MockBrain 生成的 endpoint 同样是 "<KIND> <title>"，因此两处都能据此还原 kind。
_CASE_KINDS = {"API": "api", "UI": "ui", "RAG": "rag"}


class _PlanResult(BaseModel):
    cases: List[TestCase] = Field(default_factory=list, description="测试用例列表")


class _DefectDraft(BaseModel):
    title: str
    severity: str = "medium"
    category: str = "bug"
    endpoint: str = ""
    case_ids: List[str] = Field(default_factory=list,
                               description="该缺陷对应的失败用例 id，必须来自本次失败结果")
    evidence: str = ""
    suggestion: str = ""


class _AnalysisResult(BaseModel):
    summary: str = Field(default="", description="失败分析一句话总结")
    defects: List[_DefectDraft] = Field(default_factory=list, description="缺陷列表")


def _kind_of_case_id(case_id: str) -> str:
    """按用例 id 前缀还原本条用例的 kind（API-xxx / UI-xxx / RAG-xxx）。"""
    return _CASE_KINDS.get(str(case_id).split("-", 1)[0].upper(), "")


_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _kind_of_endpoint(endpoint: str) -> str:
    """从 endpoint 推断缺陷指向的类型。

    MockBrain 产出形如 `RAG 检索召回`；LLM 自报的 endpoint 是自由文本，常见形状是
    `GET /api/todos` 或 `/api/todos`——带 HTTP 方法或写成路由的端点只可能是 API，
    UI 页面与 RAG 检索都不会长这样。
    """
    text = str(endpoint or "").strip()
    head = text.split(" ", 1)[0].upper()
    if head in _CASE_KINDS:
        return _CASE_KINDS[head]
    if text.startswith("/") or head in _HTTP_METHODS:
        return "api"
    return ""


def _resolve_related_case_ids(declared: List[str], failed: List[TestResult], endpoint: str) -> List[str]:
    """把 LLM 自报的 case_ids 变成可信的关联用例列表。

    旧实现给每个缺陷挂上本次**全部**失败用例，于是「检索排序缺陷」也关联着与它
    毫无关系的契约用例——关联退化成噪声：报告里每个缺陷那一栏都一模一样。

    三步：
      1. 白名单：只保留本次真失败过的 case_id（LLM 编造的 id 一律丢弃）；
      2. 自报有效就用自报的（保留 LLM 的语义判断）；
      3. 自报为空或全被滤掉时，**不退回全部失败用例**（那正是被修的缺陷），
         而是收窄到该缺陷同 kind 的失败用例：先看 endpoint（`RAG 检索` 这类前缀，
         或 `GET /api/todos` 这类只可能是 API 的形状），再看它自报 id 的前缀
         （编造的 id 也保留了类型前缀），最后看失败用例是否只有一种 kind。
         kind 无从判断时留空——空列表是「关联用例未知」，错挂是「关联用例是错的」。
    """
    whitelist = {r.case_id for r in failed}
    kept: List[str] = []
    for case_id in declared or []:
        cid = str(case_id).strip()
        if cid in whitelist and cid not in kept:
            kept.append(cid)
    if kept:
        return kept

    kind = _kind_of_endpoint(endpoint)
    if not kind:
        for case_id in declared or []:
            kind = _kind_of_case_id(case_id)
            if kind:
                break
    if not kind:
        kinds = {r.kind for r in failed}
        kind = kinds.pop() if len(kinds) == 1 else ""
    if not kind:
        logger.warning("[llm_brain] 缺陷 %r 未能确定所属类型，关联用例留空而不是挂上全部失败用例", endpoint)
        return []
    return [r.case_id for r in failed if r.kind == kind]


class LLMBrain:
    name = "llm"

    def __init__(self, cfg: Config) -> None:
        if not cfg.llm_configured:
            raise ValueError("LLMBrain 需要 AGENTTEST_LLM_API_KEY（或使用 MockBrain）")
        self.api_key = cfg.llm_api_key
        self.base_url = cfg.llm_base_url or None
        self.model = cfg.llm_model
        # 最近一次降级到 MockBrain 的原因；空串表示这一轮真的走了 LLM。
        # 报告层靠它避免把"降级后的规则引擎输出"标成"真实 LLM"。
        self.last_fallback_reason = ""

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
        try:
            llm = self._chat().with_structured_output(_PlanResult)
            out = llm.invoke([{"role": "system", "content": prompt}, {"role": "user", "content": user}])
        except Exception as exc:
            return self._fallback_plan(context, f"LLM 调用失败（{type(exc).__name__}: {exc}）")
        cases = []
        for i, c in enumerate(out.cases or [], start=1):
            c.id = (c.id or f"LLM-{i:03d}").strip()
            cases.append(c)
        if not cases:
            return self._fallback_plan(context, "LLM 返回了空的用例列表")
        return cases

    def _fallback_plan(self, context: Dict[str, Any], reason: str) -> List[TestCase]:
        """LLM 调用失败或输出为空时兜底，保证流程不中断。

        兜底本身是设计的一部分，但"降级"必须显式可见——静默降级会让报告里的
        `brain_name=llm` 变成谎言。所以这里把 reason 写进日志，并由调用方
        （planner 节点）把它带进 trace 与报告。
        """
        from agenttest.brain.mock_brain import MockBrain

        logger.warning("[llm_brain] 降级为 MockBrain：%s", reason)
        self.last_fallback_reason = reason
        return MockBrain().make_plan("fallback", {}, context)

    def _fallback_defects(self, results: List[TestResult], context: Dict[str, Any], reason: str) -> List[Defect]:
        """LLM 归因失败时兜底，保证流水线仍能出报告。

        与 _fallback_plan 同理：降级必须可见，否则报告会声称归因来自 LLM。
        """
        from agenttest.brain.mock_brain import MockBrain

        logger.warning("[llm_brain] 归因降级为 MockBrain：%s", reason)
        self.last_fallback_reason = reason
        return MockBrain().analyze_failures(results, context)

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
        try:
            llm = self._chat().with_structured_output(_AnalysisResult)
            out = llm.invoke([{"role": "system", "content": prompt}])
        except Exception as exc:
            return self._fallback_defects(results, context, f"LLM 归因调用失败（{type(exc).__name__}: {exc}）")
        defects = []
        for i, d in enumerate(out.defects or [], start=1):
            defects.append(Defect(
                id=f"DEF-{i:03d}",
                title=d.title,
                severity=d.severity if d.severity in {"critical", "high", "medium", "low"} else "medium",
                category=d.category if d.category in {"bug", "contract", "flaky", "environment", "test_issue", "relevance"} else "bug",
                endpoint=d.endpoint,
                evidence=d.evidence,
                suggestion=d.suggestion,
                related_case_ids=_resolve_related_case_ids(
                    list(getattr(d, "case_ids", []) or []), failed, d.endpoint),
            ))
        return defects
