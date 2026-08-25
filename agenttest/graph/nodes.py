"""LangGraph 节点实现。

- planner：调用 Brain 生成测试计划
- executor：执行全部用例（API 直接请求 / UI 真实浏览器渲染断言），带失败重试
- critic：调用 Brain 归因失败，产出缺陷与覆盖率
- reporter：落盘 JSON + HTML 报告
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from agenttest.brain import BaseBrain, MockBrain
from agenttest.config import Config
from agenttest.models import Defect, TestCase, TestReport, TestResult
from agenttest.tools import api_request, ui_check
from agenttest.tools.ui_tool import UiUnavailableError


def _retryable(status: int) -> bool:
    return status in (0, 500, 502, 503, 504)


def make_planner(brain: BaseBrain, cfg: Config):
    def planner(state: Dict[str, Any]) -> Dict[str, Any]:
        cases = brain.make_plan(state["goal"], state["spec"], state["context"])
        if state["context"].get("skip_ui"):
            kept = [c for c in cases if c.kind != "ui"]
            trace = [f"[planner] {brain.name} 生成 {len(cases)} 条测试用例（--no-ui 过滤后 {len(kept)} 条）"]
            return {"cases": kept, "trace": trace}
        trace = [f"[planner] {brain.name} 生成 {len(cases)} 条测试用例"]
        return {"cases": cases, "trace": trace}
    return planner


def make_executor(cfg: Config):
    def executor(state: Dict[str, Any]) -> Dict[str, Any]:
        ctx = state["context"]
        base_url = ctx["base_url"]
        report_dir = ctx["report_dir"]
        results: List[TestResult] = []
        trace: List[str] = []
        for case in state["cases"]:
            start = time.perf_counter()
            attempts = 0
            last_detail: Dict[str, Any] = {"ok": False, "actual": "无响应", "error": "未执行"}
            retried = False
            while True:
                attempts += 1
                if case.kind == "api":
                    last_detail = _run_api_case(case, base_url)
                else:
                    last_detail = _run_ui_case(case, base_url, report_dir, cfg)
                if last_detail["ok"] or attempts >= cfg.max_attempts or not _retryable(_status_of(last_detail)):
                    break
                retried = True
                time.sleep(0.3 * attempts)
            duration_ms = int((time.perf_counter() - start) * 1000)
            actual = str(last_detail.get("actual", ""))
            ok = bool(last_detail["ok"])
            result = TestResult(
                case_id=case.id,
                title=case.title,
                kind=case.kind,
                ok=ok,
                expected=str(case.expected_status) if case.kind == "api" else " ".join(c.target for c in case.checks),
                actual=actual,
                actual_status=_status_of(last_detail),
                error=last_detail.get("error", ""),
                evidence=last_detail.get("evidence", ""),
                duration_ms=duration_ms,
                attempts=attempts,
                retried=retried,
                notes=last_detail.get("notes", []),
            )
            results.append(result)
            trace.append(f"[executor] {'PASS' if ok else 'FAIL'} {case.id} {case.title} "
                         f"(期望 {result.expected} / 实际 {actual} / {duration_ms}ms"
                         + (f" / 重试{attempts-1}次" if retried else "") + ")")
        return {"results": results, "trace": trace}
    return executor


def _status_of(detail: Dict[str, Any]) -> int:
    try:
        return int(detail.get("status", 0))
    except (TypeError, ValueError):
        return 0


def _run_api_case(case: TestCase, base_url: str) -> Dict[str, Any]:
    detail = api_request(case.method, case.path, query=case.query, body=case.body,
                         headers=case.headers, base_url=base_url)
    actual = detail.get("status", 0)
    ok = bool(detail.get("ok")) and actual == case.expected_status
    return {
        "ok": ok,
        "status": actual,
        "actual": f"{actual} {_body_snippet(detail.get('body'))}",
        "error": "" if ok else (detail.get("error") or f"期望 {case.expected_status}，实际 {actual}"),
        "evidence": f"请求 {case.method} {case.path} -> {actual}，响应体: {_body_snippet(detail.get('body'), 300)}",
        "notes": [f"耗时 {detail.get('time_ms')}ms"],
    }


def _body_snippet(body: Any, limit: int = 200) -> str:
    if body is None:
        return "None"
    text = body if isinstance(body, str) else repr(body)
    return text[:limit]


def _run_ui_case(case: TestCase, base_url: str, report_dir, cfg: Config) -> Dict[str, Any]:
    url = case.url if case.url.startswith("http") else f"{base_url}{case.url}"
    try:
        shot_path = report_dir / f"screenshot_{case.id}.png"
        detail = ui_check(
            url, case.checks,
            backend=cfg.effective_ui_backend,
            chrome_path=cfg.chrome_path,
            screenshot_path=shot_path,
            workdir=cfg.workdir,
        )
        failed = [r for r in detail["results"] if not r["ok"]]
        ok = detail["ok"]
        return {
            "ok": ok,
            "status": 200 if ok else 0,
            "actual": ("全部断言通过" if ok else "；".join(f"{r['target']}: {r['detail']}" for r in failed)),
            "error": "" if ok else "UI 断言失败: " + " | ".join(f"{r['target']} -> {r['detail']}" for r in failed),
            "evidence": f"页面 {url}，DOM 摘要: {detail.get('dom_excerpt', '')[:200]}",
            "notes": [f"后端: {cfg.effective_ui_backend}", f"截图: {detail.get('screenshot') or '无'}"],
        }
    except UiUnavailableError as exc:
        return {"ok": False, "status": 0, "actual": "UI 不可用",
                "error": str(exc), "evidence": "", "notes": ["UI 后端不可用，该用例失败"]}


def make_critic(brain: BaseBrain):
    def critic(state: Dict[str, Any]) -> Dict[str, Any]:
        results = state["results"]
        defects = brain.analyze_failures(results, state["context"])
        coverage: Dict[str, List[str]] = {}
        for case in state["cases"]:
            ep = f"{case.method.upper()} {case.path}" if case.kind == "api" else f"UI {case.url}"
            coverage.setdefault(ep, []).append(case.id)
        trace = [f"[critic] 失败 {sum(1 for r in results if not r.ok)}/{len(results)}，"
                 f"归因出 {len(defects)} 个缺陷"]
        for d in defects:
            trace.append(f"[critic]   {d.id} [{d.severity}/{d.category}] {d.title} @ {d.endpoint}")
        return {"defects": defects, "coverage": coverage, "trace": trace}
    return critic


def make_reporter(brain: BaseBrain, cfg: Config):
    def reporter(state: Dict[str, Any]) -> Dict[str, Any]:
        from agenttest.report.html_report import render_html_report

        report = TestReport(
            goal=state["goal"],
            started_at=state["started_at"],
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            brain_name=brain.name,
            llm_configured=cfg.llm_configured,
            base_url=state["context"]["base_url"],
            cases=state["results"],
            defects=state["defects"],
            coverage=state["coverage"],
            trace=list(state["trace"]),
        )
        total = len(report.cases)
        passed = sum(1 for c in report.cases if c.ok)
        report.summary = {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "defects": len(report.defects),
        }
        report_dir = cfg.report_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        json_path = report_dir / "report.json"
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        html_path = report_dir / "report.html"
        html_path.write_text(render_html_report(report, report_dir), encoding="utf-8")
        files = {"json": str(json_path), "html": str(html_path)}
        trace = [f"[reporter] 报告已生成: {html_path.name}（JSON: {json_path.name}）"]
        return {"report": files, "trace": trace}
    return reporter
