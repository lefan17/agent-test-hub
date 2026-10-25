"""LangGraph 节点实现。

- planner：调用 Brain 生成测试计划
- executor：执行全部用例（API 直接请求 / UI 真实浏览器渲染断言），带失败重试
- critic：调用 Brain 归因失败，产出缺陷与覆盖率
- reporter：落盘 JSON + HTML 报告
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from agenttest.brain import BaseBrain, MockBrain
from agenttest.config import Config
from agenttest.models import Check, Defect, TestCase, TestReport, TestResult
from agenttest.tools import api_request, ui_check
from agenttest.tools import check_rank_monotonic, check_tie_by_doc_id, find_duplicate_hits
from agenttest.tools import rag_consistency_probe, rag_search
from agenttest.tools.rag_metrics import recall_at_k, reciprocal_rank
from agenttest.tools.rag_tool import DOCS_PATH, INDEX_PATH, REINDEX_PATH, SEARCH_PATH
from agenttest.tools.ui_tool import UiUnavailableError


def _retryable(status: int) -> bool:
    return status in (0, 500, 502, 503, 504)


def make_planner(brain: BaseBrain, cfg: Config):
    def planner(state: Dict[str, Any]) -> Dict[str, Any]:
        cases = brain.make_plan(state["goal"], state["spec"], state["context"])
        reason = getattr(brain, "last_fallback_reason", "")
        if reason:
            state["context"]["brain_fallback_reason"] = reason
        if state["context"].get("skip_ui"):
            kept = [c for c in cases if c.kind != "ui"]
            trace = [f"[planner] {brain.name} 生成 {len(cases)} 条测试用例（--no-ui 过滤后 {len(kept)} 条）"]
            if reason:
                trace.insert(0, f"[planner] LLM 规划失败，已降级规则引擎：{reason}")
            return {"cases": kept, "trace": trace}
        trace = [f"[planner] {brain.name} 生成 {len(cases)} 条测试用例"]
        if reason:
            trace.insert(0, f"[planner] LLM 规划失败，已降级规则引擎：{reason}")
        return {"cases": cases, "trace": trace}

    return planner


def make_executor(cfg: Config):
    def executor(state: Dict[str, Any]) -> Dict[str, Any]:
        ctx = state["context"]
        base_url = ctx["base_url"]
        report_dir = ctx["report_dir"]
        # 被测系统在公网时才需要（显式传、不读环境变量，理由见 api_tool）。
        base_proxy = ctx.get("base_url_proxy", "") or ""
        results: List[TestResult] = []
        trace: List[str] = []

        # 条件回边（retrier）会把待补跑的 case_id 放进 context。
        # 非空 = 本轮是补跑，只跑这些用例，其余结果原样保留；
        # 空 = 首轮，跑全量。不区分这两种情况，回边就退化成「全量重跑」，
        # 既有额外耗时又会把已经通过的用例再赌一次。
        retry_ids = set(ctx.get("retry_case_ids") or [])
        if retry_ids:
            cases = [c for c in state["cases"] if c.id in retry_ids]
            trace_prefix = f"[executor] 补跑 {len(cases)} 条可重试用例（不重跑全量）"
            trace.append(trace_prefix)
        else:
            cases = list(state["cases"])

        for case in cases:
            start = time.perf_counter()
            attempts = 0
            last_detail: Dict[str, Any] = {"ok": False, "actual": "无响应", "error": "未执行"}
            retried = False
            while True:
                attempts += 1
                if case.kind == "api":
                    last_detail = _run_api_case(case, base_url, base_proxy)
                elif case.kind == "rag":
                    last_detail = _run_rag_case(case, base_url)
                else:
                    last_detail = _run_ui_case(case, base_url, report_dir, cfg)
                if last_detail["ok"] or attempts >= cfg.max_attempts or not _retryable(_status_of(last_detail)):
                    break
                retried = True
                time.sleep(0.3 * 2 ** (attempts - 1))  # 指数退避：0.3s, 0.6s, 1.2s…
            duration_ms = int((time.perf_counter() - start) * 1000)
            actual = str(last_detail.get("actual", ""))
            ok = bool(last_detail["ok"])
            result = TestResult(
                case_id=case.id,
                title=case.title,
                kind=case.kind,
                ok=ok,
                expected=_expected_of(case),
                actual=actual,
                actual_status=_status_of(last_detail),
                error=last_detail.get("error", ""),
                evidence=last_detail.get("evidence", ""),
                duration_ms=duration_ms,
                attempts=attempts,
                retried=retried,
                notes=last_detail.get("notes", []),
                metrics=last_detail.get("metrics", {}),
                invalid_case=bool(last_detail.get("invalid_case", False)),
            )
            results.append(result)
            trace.append(
                f"[executor] {'PASS' if ok else 'FAIL'} {case.id} {case.title} "
                f"(期望 {result.expected} / 实际 {actual} / {duration_ms}ms"
                + (f" / 重试{attempts - 1}次" if retried else "")
                + ")"
            )

        if retry_ids:
            # 补跑模式：按 case_id 覆盖旧结果，保持原始顺序（报告里用例次序不乱）。
            merged = {r.case_id: r for r in state["results"]}
            for r in results:
                merged[r.case_id] = r
            ordered = [merged[c.id] for c in state["cases"] if c.id in merged]
            # 消费掉补跑清单：不清空的话条件边会认为「还有失败待补跑」，
            # 在 retry_rounds 用完之前原地打转。
            ctx.pop("retry_case_ids", None)
            return {"results": ordered, "trace": trace}

        return {"results": results, "trace": trace}

    return executor


def _status_of(detail: Dict[str, Any]) -> int:
    try:
        return int(detail.get("status", 0))
    except (TypeError, ValueError):
        return 0


def _expected_of(case: TestCase) -> str:
    if case.kind == "api":
        return str(case.expected_status)
    if case.kind == "rag":
        labels = "；".join(_check_label(c) for c in case.checks)
        return f"HTTP {case.expected_status}；{labels}" if labels else f"HTTP {case.expected_status}"
    return " ".join(c.target for c in case.checks)


def _check_label(check: Check) -> str:
    labels = {
        "rag_rank_monotonic": "rank 连续且 score 单调非增",
        "rag_tie_by_doc_id": "并列分数按 doc_id 升序",
        "rag_no_duplicate": "hits 中无重复 doc_id",
        "rag_no_ghost_hit": "已删除文档不再被召回",
        "rag_status": "HTTP 状态码符合契约",
    }
    if check.type == "rag_hits_include":
        return f"命中包含 {','.join(check.doc_ids or [check.target])}"
    if check.type == "rag_recall_at_least":
        return f"recall@k>={check.threshold}"
    if check.type == "rag_mrr_at_least":
        return f"MRR>={check.threshold}"
    return labels.get(check.type, check.type)


def _run_api_case(case: TestCase, base_url: str, proxy: str = "") -> Dict[str, Any]:
    """执行一条 API 用例：状态码 + 响应体关键字段双断言。

    状态码不符即失败；`case.checks` 非空时还要逐条校验响应体关键字段
    （contains_text / title_contains / has_element），避免「状态码对了就算过」的假绿。
    """
    detail = api_request(
        case.method,
        case.path,
        query=case.query,
        body=case.body,
        headers=case.headers,
        base_url=base_url,
        proxy=proxy,
    )
    actual = detail.get("status", 0)
    ok = bool(detail.get("ok")) and actual == case.expected_status
    failures: List[str] = []
    invalid = False
    if not detail.get("ok"):
        failures.append(detail.get("error") or "请求失败")
    elif actual != case.expected_status:
        failures.append(f"期望状态码 {case.expected_status}，实际 {actual}")
    for check in case.checks:
        verdict = _eval_api_check(check, detail.get("body"))
        if not verdict["ok"]:
            failures.append(verdict["detail"])
            # 断言类型不存在 = 用例写错。它不该让整条用例"通过"，但也不能算被测缺陷。
            invalid = invalid or bool(verdict.get("invalid"))
    return {
        "ok": ok and not failures,
        "status": actual,
        "actual": f"{actual} {_body_snippet(detail.get('body'))}",
        "error": "" if (ok and not failures) else "；".join(failures),
        "evidence": f"请求 {case.method} {case.path} -> {actual}，响应体: {_body_snippet(detail.get('body'), 300)}",
        "notes": [f"耗时 {detail.get('time_ms')}ms"],
        "invalid_case": invalid,
    }


def _eval_api_check(check: Check, body: Any) -> Dict[str, Any]:
    """对 API 响应体求值一条 Check（非 RAG 的通用断言类型）。"""
    text = body if isinstance(body, str) else ("" if body is None else json.dumps(body, ensure_ascii=False))
    if check.type == "contains_text":
        found = check.target in text
        return {"ok": found, "detail": f"响应体缺少关键字段 {check.target!r}"}
    if check.type == "title_contains":
        found = check.target in _html_title(text)
        return {"ok": found, "detail": f"页面标题缺少 {check.target!r}（实际 {_html_title(text)!r}）"}
    if check.type == "has_element":
        found = _has_element(text, check.target)
        return {"ok": found, "detail": f"页面缺少元素 {check.target!r}"}
    return {
        "ok": False,
        "invalid": True,
        "detail": f"API 用例不支持的断言类型 {check.type}（该断言只对 RAG/UI 用例有效，"
        f"属用例本身写错，不计为被测缺陷）",
    }


def _html_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _has_element(text: str, selector: str) -> bool:
    try:
        return BeautifulSoup(text, "html.parser").select_one(selector) is not None
    except Exception:  # 选择器非法等：按「未命中」处理，不静默通过
        return False


def _body_snippet(body: Any, limit: int = 200) -> str:
    if body is None:
        return "None"
    text = body if isinstance(body, str) else repr(body)
    return text[:limit]


def _run_rag_case(case: TestCase, base_url: str) -> Dict[str, Any]:
    """执行一条 RAG 检索链路用例。

    两条路径：
    - 含 rag_no_ghost_hit 断言：跑「新增→召回→删除→再检索」一致性探针（自清理）；
    - 其余：普通检索，逐条检查 rank 单调 / 并列稳定 / 去重 / 命中包含 / 指标阈值，
      并把 recall@k 与 MRR 写进 metrics，报告与 pytest 都能直接读。
    """
    if any(check.type == "rag_no_ghost_hit" for check in case.checks):
        return _run_rag_consistency_case(case, base_url)

    detail = rag_search(base_url, case.rag_query, case.top_k)
    hits = detail["hits"]
    index_info = detail["index"]
    status = detail["status"]
    metrics = {
        "recall_at_k": recall_at_k(hits, case.rag_relevant, case.top_k),
        "mrr": reciprocal_rank(hits, case.rag_relevant),
        "hit_count": float(len(hits)),
    }
    expected_label = _expected_of(case)
    failures: List[str] = []
    if detail["status"] != case.expected_status:
        failures.append(f"状态码期望 {case.expected_status}，实际 {detail['status']}")

    if case.expected_status < 400:
        for check in case.checks:
            verdict = _eval_rag_check(check, hits, case, status)
            if not verdict["ok"]:
                failures.append(verdict["detail"])

    hit_ids = [h.get("doc_id") for h in hits]
    ok = not failures
    return {
        "ok": ok,
        "status": status,
        "actual": (
            f"{status} hits={hit_ids[: case.top_k]} "
            f"recall@{case.top_k}={metrics['recall_at_k']:.4f} MRR={metrics['mrr']:.4f}"
        ),
        "error": "" if ok else "；".join(failures),
        "evidence": (
            f"请求 GET /api/rag/search?q={case.rag_query}&top_k={case.top_k} -> {status}；"
            f"hits={[(h.get('doc_id'), h.get('score'), h.get('rank')) for h in hits]}；"
            f"index={index_info}；recall@{case.top_k}={metrics['recall_at_k']:.6f}；"
            f"MRR={metrics['mrr']:.6f}"
        ),
        "notes": [
            f"期望: {expected_label}",
            f"检索质量指标 recall@{case.top_k}={metrics['recall_at_k']:.6f}，MRR={metrics['mrr']:.6f}",
            f"索引 backend={index_info.get('backend')} size={index_info.get('size')} "
            f"version={index_info.get('version')}",
        ],
        "metrics": metrics,
    }


def _eval_rag_check(check: Check, hits: List[Dict[str, Any]], case: TestCase, status: int = 0) -> Dict[str, Any]:
    """求值一条 RAG 断言。

    未实现的 check.type **必须判失败**：旧实现在末尾兜底返回 ok=True，任何漏写分支的
    新断言类型都会静默通过（假绿），这比断言失败更危险。
    """
    if check.type == "rag_hits_include":
        wanted = list(check.doc_ids or ([check.target] if check.target else []))
        if not wanted:
            return {
                "ok": False,
                "detail": "用例没有声明要命中的 doc_id（doc_ids 与 target 都为空），无法判定——声明缺失按失败处理",
            }
        got = {str(h.get("doc_id")) for h in hits}
        missing = [doc_id for doc_id in wanted if doc_id not in got]
        if missing:
            return {"ok": False, "detail": f"命中缺少 {missing}（实际 {sorted(got)}）"}
        return {"ok": True, "detail": f"命中包含 {wanted}（实际 {sorted(got)}）"}
    if check.type == "rag_rank_monotonic":
        return check_rank_monotonic(hits)
    if check.type == "rag_tie_by_doc_id":
        return check_tie_by_doc_id(hits)
    if check.type == "rag_no_duplicate":
        dups = find_duplicate_hits(hits)
        return {"ok": not dups, "detail": f"hits 中重复出现的 doc_id: {dups}"}
    if check.type == "rag_recall_at_least":
        if not case.rag_relevant:
            return {
                "ok": False,
                "detail": "用例没有标注相关文档（rag_relevant 为空），recall 无定义——"
                "标注缺失按失败处理，不按「无可漏项」记满分",
            }
        value = recall_at_k(hits, case.rag_relevant, case.top_k)
        if value + 1e-12 >= check.threshold:
            return {"ok": True, "detail": f"recall@{case.top_k}={value:.6f} >= 阈值 {check.threshold}"}
        return {"ok": False, "detail": f"recall@{case.top_k}={value:.6f} < 阈值 {check.threshold}"}
    if check.type == "rag_mrr_at_least":
        if not case.rag_relevant:
            return {
                "ok": False,
                "detail": "用例没有标注相关文档（rag_relevant 为空），MRR 无定义——"
                "标注缺失按失败处理，不按「无可漏项」记满分",
            }
        value = reciprocal_rank(hits, case.rag_relevant)
        if value + 1e-12 >= check.threshold:
            return {"ok": True, "detail": f"MRR={value:.6f} >= 阈值 {check.threshold}"}
        return {"ok": False, "detail": f"MRR={value:.6f} < 阈值 {check.threshold}"}
    if check.type == "rag_status":
        expected = int(check.threshold) if check.threshold else case.expected_status
        return {"ok": int(status) == expected, "detail": f"状态码期望 {expected}，实际 {status}"}
    return {"ok": False, "detail": f"未实现的断言类型 {check.type}（executor 未支持，按失败处理）"}


def _run_rag_consistency_case(case: TestCase, base_url: str) -> Dict[str, Any]:
    probe = rag_consistency_probe(base_url)
    corpus_ids = set(probe["corpus_doc_ids_after_delete"])
    ghost_hits = [h for h in probe["hits_after_delete"] if str(h.get("doc_id")) not in corpus_ids]
    ghost_ids = [str(h.get("doc_id")) for h in ghost_hits]
    indexed_after = probe.get("index_size_after_delete")
    corpus_after = probe.get("corpus_size_after_delete")
    failures: List[str] = []
    if not probe["ok"]:
        failures.append(probe["error"] or "一致性探针调用失败")
    if ghost_ids:
        failures.append(f"删除后仍被召回（陈旧索引 ghost hit）: {ghost_ids}")
    if indexed_after is not None and corpus_after is not None and indexed_after != corpus_after:
        failures.append(f"index_size={indexed_after} != corpus size={corpus_after}（索引未与语料同步）")
    metrics = {
        "ghost_hit_count": float(len(ghost_ids)),
        "index_size_after_delete": float(indexed_after or 0),
        "corpus_size_after_delete": float(corpus_after or 0),
    }
    ok = not failures
    return {
        "ok": ok,
        "status": probe["status"],
        "actual": (
            f"删除后检索 hits={[h.get('doc_id') for h in probe['hits_after_delete']]}，"
            f"ghost={ghost_ids}，index_size={indexed_after}/corpus_size={corpus_after}"
        ),
        "error": "" if ok else "；".join(failures),
        "evidence": (
            f"一致性探针：新增 {probe['probe_doc_id']}（{probe['add_status']}）→ "
            f"删除前命中 {[h.get('doc_id') for h in probe['hits_before_delete']]} → "
            f"删除后语料 {probe['corpus_doc_ids_after_delete']}，再检索命中 "
            f"{[h.get('doc_id') for h in probe['hits_after_delete']]}；"
            f"reindex -> version {probe['reindex_version']}"
        ),
        "notes": [
            f"ghost hit 数={len(ghost_ids)}",
            f"index_size={indexed_after}，语料 size={corpus_after}",
            f"reindex 后 version={probe['reindex_version']}",
        ],
        "metrics": metrics,
    }


def _run_ui_case(case: TestCase, base_url: str, report_dir, cfg: Config) -> Dict[str, Any]:
    url = case.url if case.url.startswith("http") else f"{base_url}{case.url}"
    try:
        shot_path = report_dir / f"screenshot_{case.id}.png"
        detail = ui_check(
            url,
            case.checks,
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
        return {
            "ok": False,
            "status": 0,
            "actual": "UI 不可用",
            "error": str(exc),
            "evidence": "",
            "notes": ["UI 后端不可用，该用例失败"],
        }


# RAG 用例的覆盖端点声明：一条 RAG 用例可能依次打多个端点，coverage 必须把它们都列出来。
#
# 旧实现把 10 条 RAG 用例一律记成同一个 "RAG GET /api/rag/search"，于是报告里
# /api/rag/index、/api/rag/reindex、POST /api/rag/docs、DELETE /api/rag/docs/{doc_id}
# 全部显示成「没有任何用例覆盖」，而实际打这些端点的正是索引一致性探针用例 RAG-006。
#
# 键与 API/UI 分支同构（method + path）。这些字符串是**声明**，可能与 rag_tool 的
# 实际调用脱节，因此 tests/test_rag_coverage.py 会用真实调用记录去核对：
# 谁给 rag_tool 加了端点调用却没同步这里，那条测试就会变红。
# 已知局限：coverage 记录的是「声明这些用例会打哪些端点」，不是执行期实测；若要让它
# 变成执行事实，需要把端点带进 TestResult 再从 executor 汇总（本次未做）。
RAG_SEARCH_ENDPOINTS: List[str] = [f"GET {SEARCH_PATH}"]
RAG_CONSISTENCY_ENDPOINTS: List[str] = [
    f"GET {INDEX_PATH}",
    f"POST {DOCS_PATH}",
    f"DELETE {DOCS_PATH}/{{doc_id}}",
    f"GET {SEARCH_PATH}",
    f"POST {REINDEX_PATH}",
]


def _uses_consistency_probe(case: TestCase) -> bool:
    """这条用例是否走「新增→召回→删除→再检索」的一致性探针。"""
    return any(check.type == "rag_no_ghost_hit" for check in case.checks)


def _coverage_keys(case: TestCase) -> List[str]:
    """一条用例覆盖的端点键（可能多个，因此返回列表）。

    RAG 分支按用例实际会打的端点展开：一致性探针打 5 个端点，其余检索用例只打
    /api/rag/search。契约用例（q 为空 / top_k 非法等）同样只打 search，不单独成键
    ——报告那一列是「端点 / 页面」，键必须名实相符。
    """
    if case.kind == "api":
        return [f"{case.method.upper()} {case.path}"]
    if case.kind == "rag":
        if _uses_consistency_probe(case):
            return list(RAG_CONSISTENCY_ENDPOINTS)
        return list(RAG_SEARCH_ENDPOINTS)
    return [f"UI {case.url}"]


def make_critic(brain: BaseBrain):
    def critic(state: Dict[str, Any]) -> Dict[str, Any]:
        results = state["results"]
        defects = brain.analyze_failures(results, state["context"])
        reason = getattr(brain, "last_fallback_reason", "")
        if reason:
            state["context"]["brain_fallback_reason"] = reason
        coverage: Dict[str, List[str]] = {}
        for case in state["cases"]:
            for key in _coverage_keys(case):
                coverage.setdefault(key, []).append(case.id)
        trace = [f"[critic] 失败 {sum(1 for r in results if not r.ok)}/{len(results)}，归因出 {len(defects)} 个缺陷"]
        if reason:
            trace.append(f"[critic] LLM 归因失败，已降级规则引擎：{reason}")
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
            brain_fallback_reason=state["context"].get("brain_fallback_reason", ""),
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
