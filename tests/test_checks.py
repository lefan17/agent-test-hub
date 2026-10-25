"""executor 的断言求值层单测：API 双断言、UI 断言、RAG 断言分派、重试退避。

这些用例守住三类「假绿」：
- 未实现的 check.type 必须判失败（RAG/API/UI 三处都不能静默跳过，否则 all([]) 恒真）；
- API 用例不能只看状态码，`case.checks` 非空时必须校验响应体；
- 重试退避必须是指数而非线性（文档宣称指数退避）。
"""

from __future__ import annotations

import sys
import threading
import types
import typing

import pytest

from agenttest.config import Config
from agenttest.graph import nodes
from agenttest.models import Check, TestCase

ALL_CHECK_TYPES = typing.get_args(Check.model_fields["type"].annotation)
RAG_CHECK_TYPES = [t for t in ALL_CHECK_TYPES if t.startswith("rag_")]


def _rag_case(expected_status: int = 200) -> TestCase:
    return TestCase(
        id="RAG-T",
        title="t",
        kind="rag",
        rag_query="vector database",
        rag_relevant=["DOC-EN-001"],
        expected_status=expected_status,
    )


def _api_case(expected_status: int, checks) -> TestCase:
    return TestCase(
        id="API-T",
        title="t",
        kind="api",
        method="GET",
        path="/api/health",
        expected_status=expected_status,
        checks=checks,
    )


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
    unknown = Check.model_construct(type="rag_not_implemented_yet", target="", doc_ids=[], threshold=0.0)
    verdict = nodes._eval_rag_check(unknown, [], _rag_case(), 200)
    assert verdict["ok"] is False
    assert "未实现" in verdict["detail"]


def test_rag_metric_checks_fail_when_case_has_no_labels():
    """没有标注时 recall/MRR 无定义，必须判失败——不能按「无可漏项」记满分。

    `Check.threshold` 默认 0.0，所以 `value(0.0) + 1e-12 >= 0.0` 恒真。若这里不
    显式拦住标注缺失，一条没有 rag_relevant 的用例会带着「recall@5=0.000000 < 阈值
    0.0」这种自相矛盾的 detail 通过。LLMBrain 生成的用例正好可能漏标注：prompt 只说
    「related 标注文档用 rag_relevant」，没有强制要求填。
    """
    unlabeled = TestCase(id="RAG-NOLABEL", title="t", kind="rag", rag_query="vector database", top_k=5, rag_relevant=[])
    hits = [{"doc_id": "DOC-EN-001", "score": 1.0, "rank": 1}]
    for check_type in ("rag_recall_at_least", "rag_mrr_at_least"):
        verdict = nodes._eval_rag_check(Check(type=check_type), hits, unlabeled, 200)
        assert verdict["ok"] is False, f"{check_type} 在无标注时不应通过"
        assert "标注" in verdict["detail"], verdict["detail"]


def test_rag_hits_include_fails_when_nothing_is_declared():
    """doc_ids 与 target 都为空时没有判定依据，必须判失败。

    旧实现 wanted=[] → missing=[] → `not missing` 恒真，detail 会输出
    「命中缺少 []（实际 [...]）」这种看着像失败的文案却 ok=True。
    """
    verdict = nodes._eval_rag_check(Check(type="rag_hits_include"), [], _rag_case(), 200)
    assert verdict["ok"] is False
    assert "无法判定" in verdict["detail"]


def test_rag_detail_text_agrees_with_the_verdict():
    """detail 文案必须与 ok 同向：通过时不能写「缺少」「< 阈值」。"""
    case = _rag_case()
    hits = [{"doc_id": "DOC-EN-001", "score": 1.0, "rank": 1}]

    included = nodes._eval_rag_check(Check(type="rag_hits_include", doc_ids=["DOC-EN-001"]), hits, case, 200)
    assert included["ok"] is True
    assert "命中包含" in included["detail"] and "缺少" not in included["detail"]

    for check_type in ("rag_recall_at_least", "rag_mrr_at_least"):
        passed = nodes._eval_rag_check(Check(type=check_type, threshold=1.0), hits, case, 200)
        assert passed["ok"] is True
        assert ">=" in passed["detail"] and "< 阈值" not in passed["detail"], passed["detail"]

        failed = nodes._eval_rag_check(Check(type=check_type, threshold=1.0), [], case, 200)
        assert failed["ok"] is False
        assert "< 阈值" in failed["detail"], failed["detail"]


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


# ---------- UI 断言：未实现的类型不得静默跳过 ----------

FAKE_DOM = "<html><head><title>AgentTest Demo</title></head><body><h1>AgentTest Demo</h1></body></html>"


def _chrome_backend(monkeypatch):
    """让 ui_check 走 chrome 分支但不必真的启动浏览器（本机 Chrome 不可用）。"""
    from agenttest.tools import ui_tool

    monkeypatch.setattr(ui_tool, "_chrome_dump_dom", lambda chrome_path, url: FAKE_DOM)
    monkeypatch.setattr(ui_tool, "_chrome_screenshot", lambda *a, **kw: None)
    return ui_tool


def test_ui_check_unknown_check_type_fails(monkeypatch):
    """UI 用例挂未识别断言类型时，results 必须有一条 ok=False，整组判定为失败。

    旧实现里未识别的类型不追加任何 result，`all(r["ok"] for r in [])` 恒为 True。
    """
    ui_tool = _chrome_backend(monkeypatch)
    unknown = Check.model_construct(type="rag_no_duplicate", target="", doc_ids=[], threshold=0.0)
    detail = ui_tool.ui_check("http://127.0.0.1:1/", [unknown], backend="chrome", chrome_path=sys.executable)
    assert detail["ok"] is False
    assert len(detail["results"]) == 1
    assert detail["results"][0]["ok"] is False
    assert "未实现" in detail["results"][0]["detail"]


def test_ui_check_known_types_still_pass(monkeypatch):
    """回归：已实现的三种类型行为不变，不因新增 else 分支而误判。"""
    ui_tool = _chrome_backend(monkeypatch)
    checks = [
        Check(type="contains_text", target="AgentTest Demo"),
        Check(type="title_contains", target="AgentTest"),
        Check(type="has_element", target="h1"),
    ]
    detail = ui_tool.ui_check("http://127.0.0.1:1/", checks, backend="chrome", chrome_path=sys.executable)
    assert detail["ok"] is True
    assert [r["ok"] for r in detail["results"]] == [True, True, True]


def test_ui_case_with_unknown_check_type_is_not_ok(monkeypatch, tmp_path):
    """端到端一层：executor 的 _run_ui_case 必须把该用例判失败。"""
    _chrome_backend(monkeypatch)
    case = TestCase(
        id="UI-T",
        title="t",
        kind="ui",
        url="/",
        checks=[Check.model_construct(type="rag_no_duplicate", target="", doc_ids=[], threshold=0.0)],
    )
    detail = nodes._run_ui_case(
        case, "http://127.0.0.1:1", tmp_path, Config(ui_backend="chrome", chrome_path=sys.executable)
    )
    assert detail["ok"] is False
    assert "未实现" in detail["error"]


def test_selenium_backend_unknown_check_type_fails(monkeypatch):
    """selenium 分支（ui_tool.py 的另一个循环）同样不得静默跳过。"""
    from agenttest.tools import ui_tool

    class _El:
        text = "hello"

    class _Driver:
        title = "T"

        def set_page_load_timeout(self, t):
            pass

        def get(self, url):
            pass

        def find_element(self, by, sel):
            return _El()

        def find_elements(self, by, sel):
            return [_El()]

        def save_screenshot(self, p):
            pass

        def quit(self):
            pass

    class _Options:
        def add_argument(self, a):
            pass

    class _By:
        TAG_NAME = "tag"
        CSS_SELECTOR = "css"

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)
        return m

    _mod("selenium")
    _mod("selenium.webdriver", Chrome=lambda options=None: _Driver())
    _mod("selenium.webdriver.chrome")
    _mod("selenium.webdriver.chrome.options", Options=_Options)
    _mod("selenium.webdriver.common")
    _mod("selenium.webdriver.common.by", By=_By)

    unknown = Check.model_construct(type="rag_no_duplicate", target="", doc_ids=[], threshold=0.0)
    detail = ui_tool._selenium_check("http://127.0.0.1:1/", [unknown], None)
    assert detail["ok"] is False
    assert "未实现" in detail["results"][0]["detail"]
    # 回归：已实现类型不受影响
    ok_detail = ui_tool._selenium_check("http://127.0.0.1:1/", [Check(type="contains_text", target="hello")], None)
    assert ok_detail["ok"] is True


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


def test_api_check_unknown_type_fails():
    """_eval_api_check 对未实现类型同样必须判失败（不得返回恒真）。"""
    unknown = Check.model_construct(type="rag_no_duplicate", target="", doc_ids=[], threshold=0.0)
    verdict = nodes._eval_api_check(unknown, {"status": "ok"})
    assert verdict["ok"] is False
    assert "不支持" in verdict["detail"] or "未实现" in verdict["detail"]


def test_api_case_with_unknown_check_type_is_not_ok(demo_server_url):
    """端到端：状态码正确但挂了未实现断言类型的 API 用例不得被判通过。"""
    unknown = Check.model_construct(type="rag_no_duplicate", target="", doc_ids=[], threshold=0.0)
    detail = nodes._run_api_case(_api_case(200, [unknown]), demo_server_url)
    assert detail["ok"] is False


def test_unknown_check_type_is_marked_invalid_not_a_defect(demo_server_url):
    """写错断言类型的用例必须被标成 invalid_case，否则报告会凭空多一个"缺陷"。

    真实故障现场：LLM 给一条健康检查用例挂了 `rag_status` 断言。请求其实返回了 200，
    但断言类型在 API 执行器里不存在 —— 如果只判 ok=False 就送去归因，
    LLM 会照着 error 文本编出一个像样的被测缺陷，而那个缺陷根本不存在。
    """
    unknown = Check.model_construct(type="rag_status", target="", doc_ids=[], threshold=0.0)
    detail = nodes._run_api_case(_api_case(200, [unknown]), demo_server_url)
    assert detail["ok"] is False
    assert detail["invalid_case"] is True, "断言类型不存在属于用例写错，必须与真实失败区分开"


def test_normal_failure_is_not_marked_invalid(demo_server_url):
    """反向验证：普通的状态码不符不能被当成"用例写错"，否则真缺陷会被漏报。"""
    detail = nodes._run_api_case(_api_case(404, []), demo_server_url)
    assert detail["ok"] is False
    assert detail["invalid_case"] is False


# ---------- 重试退避 ----------


def test_retry_backoff_is_exponential(monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(nodes.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        nodes,
        "_run_api_case",
        lambda case, base_url, proxy="": {
            "ok": False,
            "status": 503,
            "actual": "503",
            "error": "服务不可用",
            "evidence": "",
            "notes": [],
        },
    )
    cfg = Config(max_attempts=3)
    state = {
        "cases": [_api_case(200, [])],
        "context": {"base_url": "http://127.0.0.1:1", "report_dir": ".", "max_attempts": 3},
    }
    result = nodes.make_executor(cfg)(state)["results"][0]

    assert result.attempts == 3
    assert result.retried is True
    assert sleeps == [pytest.approx(0.3), pytest.approx(0.6)]  # 指数：0.3×2^(attempt-1)


def test_single_attempt_never_sleeps(monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(nodes.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        nodes,
        "_run_api_case",
        lambda case, base_url, proxy="": {
            "ok": False,
            "status": 400,
            "actual": "400",
            "error": "契约不符",
            "evidence": "",
            "notes": [],
        },
    )
    cfg = Config(max_attempts=3)
    state = {
        "cases": [_api_case(200, [])],
        "context": {"base_url": "http://127.0.0.1:1", "report_dir": ".", "max_attempts": 3},
    }
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
        worker = threading.Thread(
            target=lambda: (service.add_doc("DOC-LOCK-TMP", "locked write"), done.set()), daemon=True
        )
        worker.start()
        assert done.wait(0.2) is False, "写路径没有走锁，会在检索迭代中改字典"
    finally:
        service._lock.release()
    assert done.wait(2.0) is True
