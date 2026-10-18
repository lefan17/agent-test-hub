"""RAG 评测集端到端阈值判定（审计 item A2）。

审计原文的结论是：`recall@5` / `MRR` 有真断言，但断言的是**纯函数**不是**质量**——
`recall_at_k(HITS, ["A", "B"], 5) == 1.0` 是「手写的 hits 列表进、数字出」，
验证函数算得对，不验证检索质量达标。工具层 `rag_evaluate` 的 `ok` 只表示
「每次检索的 HTTP 调用都成功」，一次召回为 0 的检索同样 `ok=True`。

这个文件补上缺的那一层：用带标注的评测集（`RAG_EVAL_SET`，20 条查询）跑端到端检索，
并对 `recall@5 >= 0.8` 这类阈值给出真判定。三条曾经把假绿放进报告的口子都必须判失败：

  1. 空标注 + 阈值 0.0：`labeled_count == 0` 时 recall / MRR 无定义，判失败
     （不能按「无可漏项」记满分）；
  2. 被测量不存在：指标缺键 / None / NaN 时无从与阈值比较，判失败
     （不能让「指标没算出来」和「指标算出来是 0」混为一谈）；
  3. 服务正常但召回为 0：`ok=True` 且 `recall_at_k == 0.0` 时判失败。

第 3 条同时是对照组：它证明这里是「真的在判阈值」，不是「一律判失败」——
正确实现下同一套阈值判定必须通过，且判失败是返回值而不是抛异常。
"""

from __future__ import annotations

import threading

import pytest
from werkzeug.serving import make_server

from agenttest.tools import RAG_EVAL_SET, judge_quality, rag_evaluate
from agenttest.tools.rag_metrics import build_report
from demo_app.rag import CORRECT_BUGS, RAG_BUGS, RagService

MIN_RECALL = 0.8
MIN_MRR = 0.8

# 只含中文的自然语言查询：默认实现下中文分词缺陷使召回为 0，
# 但每次请求都是 200——正是「服务正常、召回为 0」的最小复现。
CHINESE_ONLY = [item for item in RAG_EVAL_SET if item["q"] == "向量数据库" or item["q"] == "中文分词质量"]


@pytest.fixture
def correct_rag_server(monkeypatch):
    """起一个「关掉全部 RAG 缺陷」的被测系统，用于阈值判定的正向对照。

    RagService 在 __init__ 里读模块级 RAG_BUGS，因此在 create_app 之前替换它即可
    得到一个正确实现；不触碰 session 级的 demo_server_url，两个服务互不影响。
    """
    import demo_app.rag as rag_module
    from demo_app.app import create_app

    monkeypatch.setattr(rag_module, "RAG_BUGS", dict(CORRECT_BUGS))
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(5)


def _in_memory_report(bugs, queries=RAG_EVAL_SET, k=5):
    """同一套评测集走内存 RagService，用于核对评测集本身的数字。"""
    service = RagService(bugs=bugs)
    results = [{"q": item["q"], "relevant": item["relevant"], "hits": service.search(item["q"], k)} for item in queries]
    return build_report(results, k)


def test_eval_set_discriminates_defect_from_correct_implementation():
    """评测集本身要有区分度，否则阈值判定没有信息量。"""
    defect = _in_memory_report(RAG_BUGS)
    correct = _in_memory_report(CORRECT_BUGS)

    assert defect["labeled_count"] == len(RAG_EVAL_SET) == 20
    assert defect["recall_at_k"] == pytest.approx(0.70, abs=1e-4)
    assert defect["mrr"] == pytest.approx(0.5917, abs=1e-3)
    assert correct["recall_at_k"] == pytest.approx(1.0)
    assert correct["mrr"] == pytest.approx(1.0)

    assert judge_quality(defect, min_recall=MIN_RECALL, min_mrr=MIN_MRR)["ok"] is False
    assert judge_quality(correct, min_recall=MIN_RECALL, min_mrr=MIN_MRR)["ok"] is True


def test_evaluate_end_to_end_judges_threshold_and_fails_under_defects(demo_server_url):
    """端到端：默认（注入缺陷）实现下 recall@5=0.70，阈值 0.8 必须判失败。"""
    report = rag_evaluate(demo_server_url, RAG_EVAL_SET, top_k=5, min_recall=MIN_RECALL, min_mrr=MIN_MRR)

    assert report["ok"] is True, "HTTP 层应当全部成功——判失败不该是调用失败导致的"
    assert report["quality_ok"] is False
    assert report["quality"]["checked"] == 2
    assert report["recall_at_k"] < MIN_RECALL
    assert any("recall_at_k" in reason for reason in report["quality"]["failures"])


def test_evaluate_end_to_end_passes_on_correct_implementation(correct_rag_server):
    """对照组：同一套评测集与阈值，正确实现必须判通过（证明不是一律判失败）。"""
    report = rag_evaluate(correct_rag_server, RAG_EVAL_SET, top_k=5, min_recall=MIN_RECALL, min_mrr=MIN_MRR)

    assert report["ok"] is True
    assert report["recall_at_k"] == pytest.approx(1.0)
    assert report["quality_ok"] is True
    assert report["quality"]["failures"] == []


def test_healthy_service_with_zero_recall_is_judged_failed(demo_server_url):
    """对照 3：服务正常（HTTP 全 200）但召回为 0，仍必须判失败。

    判定依据是阈值比较的结果，不是抛异常、也不是「因为出错了所以失败」——
    此处没有任何异常，`ok=True`，失败只来自 `recall_at_k=0.0 < 0.8`。
    """
    report = rag_evaluate(demo_server_url, CHINESE_ONLY, top_k=5, min_recall=MIN_RECALL)

    assert report["ok"] is True, "中文查询在缺陷实现下也返回 200"
    assert report["recall_at_k"] == 0.0
    assert report["quality_ok"] is False
    assert report["quality"]["verdicts"] == [
        {"metric": "recall_at_k", "threshold": MIN_RECALL, "value": 0.0, "ok": False}
    ]


def test_unlabeled_eval_set_is_judged_failed(demo_server_url):
    """假绿口子 1：空标注 + 阈值 0.0 曾让三条断言全部通过，现在必须判失败。"""
    queries = [{"q": "vector database", "relevant": []}, {"q": "bm25 ranking", "relevant": []}]

    report = rag_evaluate(demo_server_url, queries, top_k=5, min_recall=0.0)

    assert report["ok"] is True
    assert report["labeled_count"] == 0
    assert report["quality_ok"] is False
    assert report["quality"]["checked"] == 0
    assert any("标注" in reason for reason in report["quality"]["failures"])


@pytest.mark.parametrize("metric", ["recall_at_k", "mrr"])
@pytest.mark.parametrize("value", [None, float("nan"), "0.9"])
def test_missing_measured_quantity_is_judged_failed(metric, value):
    """假绿口子 2：被测量不存在 / 不是有限实数时判失败，不与「值为 0」混淆。"""
    report = {"labeled_count": 20, "recall_at_k": 1.0, "mrr": 1.0}
    if value is None:
        report.pop(metric)
    else:
        report[metric] = value

    verdict = judge_quality(
        report, min_recall=0.0 if metric == "recall_at_k" else None, min_mrr=0.0 if metric == "mrr" else None
    )

    assert verdict["ok"] is False
    assert verdict["verdicts"] == []
    assert any("不存在或不是有限实数" in reason for reason in verdict["failures"])


def test_missing_threshold_is_judged_failed():
    """没给阈值时判定无定义，按失败处理——不能因为「没设阈值」就默认达标。"""
    report = {"labeled_count": 20, "recall_at_k": 1.0, "mrr": 1.0}

    verdict = judge_quality(report)

    assert verdict["ok"] is False
    assert "没有给出任何阈值" in verdict["failures"][0]


def test_threshold_verdict_never_expressed_by_raising(demo_server_url):
    """判定必须写在返回值里：达标与不达标都正常返回，不靠异常表达结论。"""
    failed = rag_evaluate(demo_server_url, CHINESE_ONLY, top_k=5, min_recall=MIN_RECALL)
    passed = rag_evaluate(demo_server_url, CHINESE_ONLY, top_k=5, min_recall=0.0)

    assert failed["quality_ok"] is False and passed["quality_ok"] is True
    assert failed["quality"]["checked"] == passed["quality"]["checked"] == 1


def test_evaluate_without_thresholds_keeps_existing_behavior(demo_server_url):
    """不传阈值时返回体不变：既有调用方读到的键与语义一个都不动。"""
    report = rag_evaluate(demo_server_url, RAG_EVAL_SET, top_k=5)

    assert report["ok"] is True
    assert report["query_count"] == len(RAG_EVAL_SET)
    assert "quality" not in report and "quality_ok" not in report
