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
        return cases

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
            if "400" in r.expected and r.actual_status == 500:
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
