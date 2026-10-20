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


def _direct_httpx_client(proxy: str = ""):
    """给 `ChatOpenAI` 一个**不读代理环境变量**的 httpx 客户端。

    这不是「顺手关掉代理」，是绕开一个会**在构造阶段就崩**的上游缺陷：

    httpx 0.28.1 的 `get_environment_proxies()` 生成绕过规则时，对
    `NO_PROXY` 里每个条目先判 `is_ipv4_hostname` / `is_ipv6_hostname`。
    `[::1]`（带方括号的 IPv6 —— curl 明确支持的写法）两个都不是，
    于是落进最后那个 `else` 分支，被当成域名拼成 `all://*[::1]`；
    这个 pattern 解析时把 `1]` 当端口，抛 `InvalidURL: Invalid port: ':1]'`。
    而这个字典是在 `httpx.Client(...)` 构造时喂给 `URLPattern` 的，
    所以**连请求都还没发出去**，`ChatOpenAI(...)` 就已经抛异常了。

    实测（httpx 0.28.1）：
    - `NO_PROXY=...,::1,...`            → 正常（`is_ipv6_hostname('::1')` 为真）
    - `NO_PROXY=...,[::1],...`          → `InvalidURL: Invalid port: ':1]'`
    - `URLPattern('all://[::1]')`       → 正常
    - `URLPattern('all://*[::1]')`      → 崩（就是上面那个错误路径）

    为什么不能让用户自己「把 `[::1]` 从 NO_PROXY 里删掉」：那是合法规避写法，
    而且 `NO_PROXY` 常常是公司/工具自动注入的（本机就是 DSH 宿主注入的），
    期望每个使用者手工清理，等于让代码对环境敏感 —— 这在本仓库已经踩过两次
    （`api_tool` / `run_load.py` 的代理污染）。正确做法是让这条链路对环境无感：
    显式给一个 `trust_env=False` 的客户端，本来也不该让一次 LLM 调用被
    `HTTP_PROXY` 劫持去走转发（模型 API 的凭据与流量不该经过不明代理）。

    代价要说清：设了 `trust_env=False` 之后，**确实需要走代理才能出网的机器**会连不上。
    所以给了一个显式逃生口 `AGENTTEST_LLM_HTTP_PROXY`（见 `_direct_httpx_client(proxy)`）：
    需要走代理就显式填地址，不填就是直连。**刻意不自动继承环境变量** ——
    自动继承正是这个缺陷的来源。取舍是：一次 `InvalidURL` 崩溃比
    「本机该走代理没走」更难排查，因为前者长得像代码 bug，后者报的是连接超时。
    """
    import httpx

    if proxy:
        return httpx.Client(proxy=proxy, timeout=60.0)
    return httpx.Client(trust_env=False, timeout=60.0)


def _direct_async_httpx_client(proxy: str = ""):
    """`_direct_httpx_client` 的异步版，理由完全相同。

    单独一个函数而不是复用：`ChatOpenAI` 要的是同步 / 异步**两个不同类**的实例，
    给错类型它会在别的地方报一个更难懂的错。分成两个函数，名字本身就说明该给哪个。
    """
    import httpx

    if proxy:
        return httpx.AsyncClient(proxy=proxy, timeout=60.0)
    return httpx.AsyncClient(trust_env=False, timeout=60.0)


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
6. 输出 12~20 条用例，id 用 API-xxx / UI-xxx / RAG-xxx 编号，优先级 P0/P1/P2；
7. **字段字面量必须短**：body / query 里任何字符串都不许超过 200 字符。要测超长输入时，
   写一个有代表性的短值（例如 "A" * 256 就写 256 个 A），不要原样铺开几千字符——
   那会把你的输出顶到 token 上限，JSON 被截断后整份计划都会作废；
8. **checks 只在真需要额外断言时才写**：状态码已经由 expected_status 校验，绝大多数 API
   用例的 checks 应当留空数组 `[]`。API 用例只认 `contains_text`（配 target，在响应体里找
   子串）；`rag_*` 系列（含 rag_status）是 RAG 用例专用，写进 API 用例会被执行器判为
   「不支持的断言类型」而直接失败——那不是发现了缺陷，是用例写错了。"""


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
    case_ids: List[str] = Field(default_factory=list, description="该缺陷对应的失败用例 id，必须来自本次失败结果")
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
        # 结构化输出的落地方式。默认 function_calling 而不是 LangChain 的默认 json_schema：
        # DeepSeek 对 response_format=json_schema 直接回 400。见 _structured() 的注释。
        self.structured_method = cfg.llm_structured_method
        # LLM 链路的显式代理；空串 = 直连（刻意不读 HTTP_PROXY / NO_PROXY）
        self.http_proxy = cfg.llm_http_proxy
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
            # 两个都要给。`ChatOpenAI.validate_environment` 会**同时**建同步与异步
            # 两个默认客户端，只传 http_client 的话异步那个仍会读 NO_PROXY 并抛
            # `InvalidURL: Invalid port: ':1]'` —— 崩溃点在
            # `_get_default_async_httpx_client` 而不是同步那个，见 _direct_httpx_client 的注释。
            http_client=_direct_httpx_client(self.http_proxy),
            http_async_client=_direct_async_httpx_client(self.http_proxy),
        )

    def _structured(self, schema):
        """把 LLM 绑到给定 schema，显式指定结构化输出的落地方式。

        **必须显式传 method**，这是踩出来的：LangChain 0.3.x 的默认值是
        `json_schema`，它发出的 `response_format` 是 pydantic 类的 JSON Schema；
        而 DeepSeek 的 OpenAI 兼容层不认这个类型，直接回 400
        `This response_format type is unavailable now`。

        三条路的实测结果（deepseek-chat，2026-10-19）：
        - `function_calling` 通 —— 走 tools/tool_choice，DeepSeek 支持；
        - `json_schema`    400 —— 就是上面那个报错；
        - `json_mode`      400 —— 它还额外要求 prompt 里出现 "json" 字样。

        所以默认值是 function_calling。换成别的 provider 时如果它更擅长
        `json_schema`，把它设回 `json_schema` 即可（配置项见 Config）。
        """
        return self._chat().with_structured_output(schema, method=self.structured_method)

    def make_plan(self, goal: str, spec: Dict[str, Any], context: Dict[str, Any]) -> List[TestCase]:
        prompt = SYSTEM_PLAN_PROMPT.format(spec=summarize_spec(spec))
        user = f"目标：{goal}\n被测系统地址：{context.get('base_url')}\n请生成测试计划。"
        try:
            llm = self._structured(_PlanResult)
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
        # 用例自己写错导致的失败，先本地归因成 test_issue 拿走，不能让 LLM 去猜：
        # 它看不到「断言类型与用例类型不匹配」这个事实，只会照着 error 文本编出一个
        # 看起来很像被测缺陷的结论，而 category 一旦写成 bug，报告就多一个不存在的缺陷。
        invalid = [r for r in failed if r.invalid_case]
        failed = [r for r in failed if not r.invalid_case]
        defects: List[Defect] = [
            Defect(
                id=f"DEF-{i:03d}",
                title="用例本身不成立：断言类型与该用例类型不匹配",
                severity="low",
                category="test_issue",
                endpoint=f"{r.kind.upper()} {r.title}",
                evidence=f"期望 {r.expected}；{r.error}",
                suggestion="修正用例的 checks：API 用例只认 contains_text / title_contains / "
                "has_element，rag_* 系列断言只能用在 kind=rag 的用例上",
                related_case_ids=[r.case_id],
            )
            for i, r in enumerate(invalid, start=1)
        ]
        if not failed:
            return defects
        payload = [
            {
                "case_id": r.case_id,
                "title": r.title,
                "expected": r.expected,
                "actual": r.actual,
                "error": r.error,
                "evidence": r.evidence,
                "attempts": r.attempts,
            }
            for r in failed
        ]
        prompt = SYSTEM_ANALYSIS_PROMPT.format(results=json.dumps(payload, ensure_ascii=False, indent=2))
        try:
            llm = self._structured(_AnalysisResult)
            out = llm.invoke([{"role": "system", "content": prompt}])
        except Exception as exc:
            return defects + self._fallback_defects(
                results, context, f"LLM 归因调用失败（{type(exc).__name__}: {exc}）"
            )
        for i, d in enumerate(out.defects or [], start=len(defects) + 1):
            defects.append(
                Defect(
                    id=f"DEF-{i:03d}",
                    title=d.title,
                    severity=d.severity if d.severity in {"critical", "high", "medium", "low"} else "medium",
                    category=d.category
                    if d.category in {"bug", "contract", "flaky", "environment", "test_issue", "relevance"}
                    else "bug",
                    endpoint=d.endpoint,
                    evidence=d.evidence,
                    suggestion=d.suggestion,
                    related_case_ids=_resolve_related_case_ids(
                        list(getattr(d, "case_ids", []) or []), failed, d.endpoint
                    ),
                )
            )
        return defects
