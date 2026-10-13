"""executor 的断言求值层单测：API 双断言、RAG 断言分派、重试退避。

这些用例守住三类「假绿」：
- 未实现的 check.type 必须判失败（旧实现末尾兜底 ok=True，漏写分支会静默通过）；
- API 用例不能只看状态码，`case.checks` 非空时必须校验响应体；
- 重试退避必须是指数而非线性（文档宣称指数退避）。
"""
from __future__ import annotations

import threading
import time
import typing

import pytest

from agenttest.config import Config
from agenttest.graph import nodes
from agenttest.models import Check, TestCase

ALL_CHECK_TYPES = typing.get_args(Check.model_fields["type"].annotation)
RAG_CHECK_TYPES = [t for t in ALL_CHECK_TYPES if t.startswith("rag_")]


def _rag_case(expected_status: int = 200) -> TestCase:
    return TestCase(id="RAG-T", title="t", kind="rag", rag_query="vector database",
                    rag_relevant=["DOC-EN-001"], expected_status=expected_status)


def _api_case(expected_status: int, checks) -> TestCase:
    return TestCase(id="API-T", title="t", kind="api", method="GET", path="/api/health",
                    expected_status=expected_status, checks=checks)


# ---------- RAG 断言分派 ----------

def test_rag_status_check_compares_status_code():
    case = _rag_case(expected_status=200)
    assert nodes._eval_rag_check(Check(type="rag_status"), [], case, 200)["ok"] is True
    assert nodes._eval_rag_check(Check(type="rag_status"), [], case, 500)["ok"] is False
    # threshold 可显式覆盖 expected_status
    strict = Check(type="rag_status", threshold=404.0)
    assert nodes._eval_rag_check(strict, [], _rag_case(200), 404)["ok"] is True


def test_unimplemented_rag_check_type_fails():
    """Literal 之外的 check.type 走不到 Pydantic 校验时也必须判失败，不能静默通过。"""
    unknown = Check.model_construct(type="rag_not_implemented_yet", target="", doc_ids=[],
                                    threshold=0.0)
    verdict = nodes._eval_rag_check(unknown, [], _rag_case(), 200)
    assert verdict["ok"] is False
    assert "未实现" in verdict["detail"]


def test_every_declared_rag_check_type_has_an_implementation():
    """每个已声明的 rag_* 断言都必须有落点：要么在 _eval_rag_check，要么在用例分派层。

    rag_no_ghost_hit 由 _run_rag_case 分派给一致性探针（见 test_rag_sut.py），不在本函数内。
    """
    hits = [{"doc_id": "DOC-EN-001", "score": 1.0, "rank": 1}]
    case = _rag_case()
    for check_type in RAG_CHECK_TYPES:
        if check_type == "rag_no_ghost_hit":
            continue
        verdict = nodes._eval_rag_check(Check(type=check_type), hits, case, 200)
        assert "未实现" not in verdict["detail"], f"{check_type} 没有实现分支，会静默通过"
    dispatched = [c for c in RAG_CHECK_TYPES if c == "rag_no_ghost_hit"]
    assert dispatched, "rag_no_ghost_hit 必须仍由用例分派层处理"


# ---------- API 用例双断言 ----------

def test_api_case_fails_when_body_lacks_expected_text(demo_server_url):
    case = _api_case(200, [Check(type="contains_text", target="THIS-TEXT-IS-NOT-IN-BODY")])
    detail = nodes._run_api_case(case, demo_server_url)
    assert detail["ok"] is False
    assert "THIS-TEXT-IS-NOT-IN-BODY" in detail["error"]


def test_api_case_passes_when_status_and_body_both_match(demo_server_url):
    case = _api_case(200, [Check(type="contains_text", target='"status"')])
    assert nodes._run_api_case(case, demo_server_url)["ok"] is True


def test_api_case_still_fails_on_status_mismatch(demo_server_url):
    case = _api_case(404, [Check(type="contains_text", target='"status"')])
    detail = nodes._run_api_case(case, demo_server_url)
    assert detail["ok"] is False
    assert "404" in detail["error"]


def test_api_case_without_checks_still_asserts_status_only(demo_server_url):
    """既有 15 条 MockBrain API 用例都不带 checks，行为必须与改动前一致。"""
    assert nodes._run_api_case(_api_case(200, []), demo_server_url)["ok"] is True
    assert nodes._run_api_case(_api_case(500, []), demo_server_url)["ok"] is False


# ---------- 重试退避 ----------

def test_retry_backoff_is_exponential(monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(nodes.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(nodes, "_run_api_case",
                        lambda case, base_url: {"ok": False, "status": 503, "actual": "503",
                                                "error": "服务不可用", "evidence": "", "notes": []})
    cfg = Config(max_attempts=3)
    state = {"cases": [_api_case(200, [])],
             "context": {"base_url": "http://127.0.0.1:1", "report_dir": ".", "max_attempts": 3}}
    result = nodes.make_executor(cfg)(state)["results"][0]

    assert result.attempts == 3
    assert result.retried is True
    assert sleeps == [pytest.approx(0.3), pytest.approx(0.6)]  # 指数：0.3×2^(attempt-1)


def test_single_attempt_never_sleeps(monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(nodes.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(nodes, "_run_api_case",
                        lambda case, base_url: {"ok": False, "status": 400, "actual": "400",
                                                "error": "契约不符", "evidence": "", "notes": []})
    cfg = Config(max_attempts=3)
    state = {"cases": [_api_case(200, [])],
             "context": {"base_url": "http://127.0.0.1:1", "report_dir": ".", "max_attempts": 3}}
    result = nodes.make_executor(cfg)(state)["results"][0]

    assert result.attempts == 1 and result.retried is False
    assert sleeps == []  # 4xx 不可重试


# ---------- 并发---------- 
def test_lock_serializes_writes_against_reads():
    """写路径确实走同一把可重入锁：主线程持锁时写线程必须被挡住。"""
    from demo_app.rag import CORRECT_BUGS, RagService

    service = RagService(bugs=CORRECT_BUGS)
    assert isinstance(service._lock, type(threading.RLock()))

    done = threading.Event()
    service._lock.acquire()
    try:
        worker = threading.Thread(target=lambda: (service.add_doc("DOC-LOCK-TMP", "locked write"),
                                                  done.set()), daemon=True)
        worker.start()
        assert done.wait(0.2) is False, "写路径没有走锁，会在检索迭代中改字典"
    finally:
        service._lock.release()
    assert done.wait(2.0) is True
