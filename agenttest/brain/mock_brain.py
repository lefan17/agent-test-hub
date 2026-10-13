"""MockBrain：确定性规则引擎，不依赖任何 LLM，离线可跑完整演示。

它模拟了一个"资深测试工程师"的规划与归因逻辑：
- 规划：覆盖 正向/反向/边界/异常/契约 用例（从 OpenAPI 契约推导）；
- 归因：把失败结果映射为缺陷（契约不符、Bug、不稳定服务等）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from agenttest.models import Check, Defect, TestCase, TestResult


class MockBrain:
    name = "mock"

    def make_plan(self, goal: str, spec: Dict[str, Any], context: Dict[str, Any]) -> List[TestCase]:
        base_url = context.get("base_url", "http://127.0.0.1:5001")
        cases: List[TestCase] = [
            TestCase(id="API-001", title="健康检查应返回 200", kind="api", priority="P0",
                     method="GET", path="/api/health", expected_status=200),
            TestCase(id="API-002", title="正确凭据登录应返回 200 和 token", kind="api", priority="P0",
                     method="POST", path="/api/login",
                     body={"username": "admin", "password": "123456"}, expected_status=200),
            TestCase(id="API-003", title="错误密码登录应返回 401", kind="api", priority="P1",
                     method="POST", path="/api/login",
                     body={"username": "admin", "password": "wrong-pass"}, expected_status=401),
            TestCase(id="API-004", title="缺参数登录应返回 400", kind="api", priority="P2",
                     method="POST", path="/api/login", body={}, expected_status=400),
            TestCase(id="API-005", title="查询待办列表应返回 200", kind="api", priority="P0",
                     method="GET", path="/api/todos", expected_status=200),
            TestCase(id="API-006", title="非法 status 过滤应返回 400（契约要求）", kind="api", priority="P0",
                     method="GET", path="/api/todos", query={"status": "invalid"}, expected_status=400),
            TestCase(id="API-007", title="创建合法待办应返回 201", kind="api", priority="P0",
                     method="POST", path="/api/todos",
                     body={"title": "由 Agent 自动测试创建的待办", "priority": "high"}, expected_status=201),
            TestCase(id="API-008", title="空 title 创建应返回 400（契约要求）", kind="api", priority="P0",
                     method="POST", path="/api/todos", body={"title": "", "priority": "medium"}, expected_status=400),
            TestCase(id="API-009", title="非法 priority 应返回 400", kind="api", priority="P2",
                     method="POST", path="/api/todos", body={"title": "x", "priority": "urgent"}, expected_status=400),
            TestCase(id="API-010", title="查询存在的待办应返回 200", kind="api", priority="P1",
                     method="GET", path="/api/todos/1", expected_status=200),
            TestCase(id="API-011", title="查询不存在的待办应返回 404", kind="api", priority="P1",
                     method="GET", path="/api/todos/99999", expected_status=404),
            TestCase(id="API-012", title="更新待办状态应返回 200", kind="api", priority="P1",
                     method="PATCH", path="/api/todos/2", body={"status": "active"}, expected_status=200),
            TestCase(id="API-013", title="非法状态更新应返回 400", kind="api", priority="P2",
                     method="PATCH", path="/api/todos/2", body={"status": "bogus"}, expected_status=400),
            TestCase(id="API-014", title="删除不存在的待办应返回 404", kind="api", priority="P2",
                     method="DELETE", path="/api/todos/99999", expected_status=404),
            TestCase(id="API-015", title="不稳定服务应最终成功（自动重试）", kind="api", priority="P1",
                     method="GET", path="/api/flaky", expected_status=200),
            TestCase(id="UI-001", title="首页应正确渲染标题与导航", kind="ui", priority="P0",
                     url=f"{base_url}/",
                     checks=[Check(type="contains_text", target="AgentTest Demo"),
                             Check(type="has_element", target="[data-testid=page-title]"),
                             Check(type="has_element", target="[data-testid=nav-todos]")]),
            TestCase(id="UI-002", title="登录页应包含登录表单", kind="ui", priority="P1",
                     url=f"{base_url}/login",
                     checks=[Check(type="contains_text", target="用户登录"),
                             Check(type="has_element", target="[data-testid=login-form]"),
                             Check(type="has_element", target="[data-testid=input-username]")]),
            TestCase(id="UI-003", title="待办页应展示种子数据", kind="ui", priority="P1",
                     url=f"{base_url}/todos",
                     checks=[Check(type="has_element", target="[data-testid=todo-item]"),
                             Check(type="contains_text", target="学习 LangGraph 状态图")]),
        ]
        cases.extend(self._rag_cases())
        return cases

    def _rag_cases(self) -> List[TestCase]:
        """RAG 检索链路用例：召回质量 / 排序 / 去重 / 索引一致性 / 契约边界。

        中文用例（RAG-001）专门覆盖「中文分词缺失导致召回为 0」——
        这是检索链路里最容易漏测、也最容易在生产上翻车的一类缺陷。
        """
        return [
            TestCase(id="RAG-001", title="中文查询应召回相关文档且 recall@5 达标", kind="rag", priority="P0",
                     rag_query="向量数据库", top_k=5, rag_relevant=["DOC-CN-002"],
                     checks=[Check(type="rag_hits_include", doc_ids=["DOC-CN-002"]),
                             Check(type="rag_recall_at_least", threshold=1.0),
                             Check(type="rag_mrr_at_least", threshold=1.0)]),
            TestCase(id="RAG-002", title="英文查询检索质量达标（recall@5 与 MRR）", kind="rag", priority="P1",
                     rag_query="search quality metrics", top_k=5,
                     rag_relevant=["DOC-EN-004", "DOC-EN-001"],
                     checks=[Check(type="rag_hits_include", doc_ids=["DOC-EN-004", "DOC-EN-001"]),
                             Check(type="rag_recall_at_least", threshold=1.0),
                             Check(type="rag_mrr_at_least", threshold=1.0)]),
            TestCase(id="RAG-003", title="检索结果应按 score 降序（rank 单调）", kind="rag", priority="P0",
                     rag_query="search quality metrics", top_k=5,
                     checks=[Check(type="rag_rank_monotonic")]),
            TestCase(id="RAG-004", title="分数并列时 rank 应按 doc_id 稳定排序", kind="rag", priority="P1",
                     rag_query="duplicate", top_k=5, rag_relevant=["DOC-TIE-001", "DOC-TIE-002"],
                     checks=[Check(type="rag_hits_include", doc_ids=["DOC-TIE-001", "DOC-TIE-002"]),
                             Check(type="rag_tie_by_doc_id")]),
            TestCase(id="RAG-005", title="单次检索 hits 中不应出现重复 doc_id", kind="rag", priority="P0",
                     rag_query="vector database", top_k=5, rag_relevant=["DOC-EN-001"],
                     checks=[Check(type="rag_hits_include", doc_ids=["DOC-EN-001"]),
                             Check(type="rag_no_duplicate")]),
            TestCase(id="RAG-006", title="文档删除后不应再被召回（索引与语料一致）", kind="rag", priority="P0",
                     checks=[Check(type="rag_no_ghost_hit")]),
            TestCase(id="RAG-007", title="空查询 q 应返回 400（契约要求）", kind="rag", priority="P1",
                     rag_query="", top_k=5, expected_status=400),
            TestCase(id="RAG-008", title="top_k=0 应返回 400（契约要求）", kind="rag", priority="P1",
                     rag_query="vector database", top_k=0, expected_status=400),
            TestCase(id="RAG-009", title="top_k 超过上限应返回 400（契约要求）", kind="rag", priority="P2",
                     rag_query="vector database", top_k=999, expected_status=400),
            TestCase(id="RAG-010", title="超长 q（240 字符）应返回 400（契约要求）", kind="rag", priority="P2",
                     rag_query="契约边界" * 60, top_k=5, expected_status=400),
        ]

    def analyze_failures(self, results: List[TestResult], context: Dict[str, Any]) -> List[Defect]:
        defects: List[Defect] = []
        seen = set()
        for r in results:
            if r.ok:
                continue
            endpoint = f"{r.kind.upper()} {r.title}"
            key = (r.case_id, r.actual)
            if key in seen:
                continue
            seen.add(key)
            if r.kind == "rag":
                defects.append(self._rag_defect(r, endpoint, len(defects) + 1))
            elif "400" in r.expected and r.actual_status == 500:
                defects.append(Defect(
                    id=f"DEF-{len(defects)+1:03d}", title="契约不符：非法参数应返回 400 却返回 500",
                    severity="high", category="contract", endpoint=endpoint,
                    evidence=f"期望 {r.expected}，实际 {r.actual_status}；{r.error}",
                    suggestion="按 OpenAPI 契约对非法参数返回 400，并在网关层拦截未预期异常",
                    related_case_ids=[r.case_id]))
            elif "400" in r.expected and r.actual_status == 200:
                defects.append(Defect(
                    id=f"DEF-{len(defects)+1:03d}", title="缺失参数校验：空值被接受并写入数据",
                    severity="high", category="bug", endpoint=endpoint,
                    evidence=f"期望 {r.expected}，实际 {r.actual_status}（脏数据已写入）",
                    suggestion="服务端对 title 做非空校验，返回 400 且不落库",
                    related_case_ids=[r.case_id]))
            elif r.actual_status == 503 or "temporarily unavailable" in r.evidence.lower():
                defects.append(Defect(
                    id=f"DEF-{len(defects)+1:03d}", title="不稳定服务：接口偶发 503",
                    severity="medium", category="flaky", endpoint=endpoint,
                    evidence=f"重试 {r.attempts} 次后仍失败：{r.error}",
                    suggestion="排查服务稳定性；建议客户端实现指数退避重试",
                    related_case_ids=[r.case_id]))
            else:
                defects.append(Defect(
                    id=f"DEF-{len(defects)+1:03d}", title="测试失败待人工确认",
                    severity="medium", category="test_issue", endpoint=endpoint,
                    evidence=f"期望 {r.expected}，实际 {r.actual}；{r.error}",
                    suggestion="结合完整日志复现并定位",
                    related_case_ids=[r.case_id]))
        return defects

    @staticmethod
    def _rag_defect(r: TestResult, endpoint: str, seq: int) -> Defect:
        """把检索失败归因到具体缺陷面（relevance / bug / contract），而不是笼统的 test_issue。

        判定依据是 executor 写进 error/evidence 的断言失败原文——这保证了
        「归因」与「实际观测到的证据」一致，不是凭空猜测。
        """
        detail = f"{r.error} {r.evidence}"
        metrics = f"（recall@k={r.metrics.get('recall_at_k', 0):.4f}，MRR={r.metrics.get('mrr', 0):.4f}）" \
            if r.metrics else ""
        if "陈旧索引" in detail or "ghost" in detail or "index_size" in detail:
            return Defect(
                id=f"DEF-{seq:03d}", title="索引一致性缺陷：文档删除后仍被检索召回（陈旧索引）",
                severity="high", category="relevance", endpoint=endpoint,
                evidence=f"{r.error}；{r.evidence}",
                suggestion="语料变更（增/删/改）时同步更新倒排索引，并让 /api/rag/index 的 index_size 与 size 一致；"
                           "可加一条「删除后不可召回」的回归用例",
                related_case_ids=[r.case_id])
        if "重复" in detail:
            return Defect(
                id=f"DEF-{seq:03d}", title="检索去重缺陷：同一文档在 hits 中重复出现",
                severity="medium", category="bug", endpoint=endpoint,
                evidence=f"{r.error}；{r.evidence}",
                suggestion="按 doc_id 聚合各 query term 的分数贡献后再排序，保证每个文档只出现一次",
                related_case_ids=[r.case_id])
        if "单调" in detail or "非降序" in detail or "并列" in detail:
            return Defect(
                id=f"DEF-{seq:03d}", title="检索排序缺陷：score 未降序或并列时次序不稳定",
                severity="high", category="relevance", endpoint=endpoint,
                evidence=f"{r.error}；{r.evidence}",
                suggestion="按 score 降序排序，并以 doc_id 升序作为并列时的稳定次序，保证同一查询结果可复现",
                related_case_ids=[r.case_id])
        if "400" in r.expected:
            return Defect(
                id=f"DEF-{seq:03d}", title="检索接口契约缺陷：非法入参未按契约返回 400",
                severity="high", category="contract", endpoint=endpoint,
                evidence=f"期望 {r.expected}，实际状态码 {r.actual_status}；{r.error}",
                suggestion="空 q、超长 q、top_k<=0 或超上限时按 OpenAPI 契约返回 400 并给出错误说明",
                related_case_ids=[r.case_id])
        return Defect(
            id=f"DEF-{seq:03d}", title="检索召回质量缺陷：相关文档未被召回或排名过低",
            severity="high", category="relevance", endpoint=endpoint,
            evidence=f"{r.error}；{r.evidence}{metrics}",
            suggestion="检查分词与索引构建：中文需切分为可检索的 token（unigram + bigram），"
                       "并核对 IDF/长度归一化参数，避免中文查询召回为 0",
            related_case_ids=[r.case_id])
