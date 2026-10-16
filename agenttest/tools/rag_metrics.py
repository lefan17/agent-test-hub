"""检索质量指标与检索结果不变量（纯函数，无 IO、无第三方依赖）。

指标定义（写进报告与文档的口径，务必与实现一致）：
  recall@k = |top-k 召回的 doc_id ∩ 标注相关 doc_id| / |标注相关 doc_id|
             —— 单查询计算；多查询取算术平均（宏平均），无标注的查询不参与宏平均。
  MRR     = mean(1 / 首个相关文档在返回列表里的位次)，无相关文档召回时该查询 RR=0。
             —— 位次由评测方按返回顺序数出来，不用被测方自报的 rank 字段。
             —— 衡量「相关文档排得够不够靠前」。

不变量：
  rank 连续 1..n、score 单调非增（check_rank_monotonic）
  分数并列时按 doc_id 升序（check_tie_by_doc_id）
  同一 doc_id 不得重复出现（find_duplicate_hits）

LABELED_QUERIES 是一份小标注集（7 条查询，k=5）：
  缺陷实现 recall@5=MRR=0.5714（4/7），正确实现两项均为 1.0000。
RAG_EVAL_SET 是用于**阈值判定**的 20 条评测集（见该常量上方说明）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

# 浮点比较容差：端点返回的 score 已 round 到 6 位小数
EPS = 1e-12

# 标注集：默认语料 `demo_app/rag.py` 的 DEFAULT_CORPUS 中各挑一篇「唯一正确文档」。
# 文档里写死的 recall@5 / MRR 数值（缺陷实现 0.5714、正确实现 1.0000）就来自这 7 条查询，
# 放在这里是为了让文档、测试、报告共用同一份标注集——数字永远可复现。
LABELED_QUERIES: List[Dict[str, Any]] = [
    {"q": "vector database", "relevant": ["DOC-EN-001"]},
    {"q": "bm25 ranking algorithm", "relevant": ["DOC-EN-002"]},
    {"q": "index rebuild", "relevant": ["DOC-EN-003"]},
    {"q": "search quality metrics", "relevant": ["DOC-EN-004"]},
    {"q": "向量数据库", "relevant": ["DOC-CN-002"]},
    {"q": "中文分词质量", "relevant": ["DOC-CN-004"]},
    {"q": "索引重建", "relevant": ["DOC-CN-005"]},
]


# 端到端评测集：用于判定「检索质量是否达标」，而不是只把数字算出来。
#
# 与 LABELED_QUERIES 的分工：那份 7 条标注集服务于「文档/测试/报告共用同一份数字」，
# 每条查询都只有一个唯一正确文档、且缺陷实现恰好 4/7；这一份 20 条则要拉开区分度——
# 正确实现 recall@5 = 1.0000、MRR = 1.0000；默认缺陷实现 recall@5 = 0.7000、
# MRR ≈ 0.5917（6 条中文查询召回为 0，中文分词缺陷）。于是 `recall@5 >= 0.8`
# 这类阈值在两边的判定结果相反，阈值本身才是有信息量的。
#
# 刻意覆盖多种形状：单文档标注、双文档标注（并列文档）、中文与英文查询、
# 以及只命中语料里某一篇的多条同义查询——召回缺口不会被个别查询的波动掩盖。
RAG_EVAL_SET: List[Dict[str, Any]] = [
    {"q": "vector database similarity", "relevant": ["DOC-EN-001"]},
    {"q": "semantic search", "relevant": ["DOC-EN-001"]},
    {"q": "semantic similarity search", "relevant": ["DOC-EN-001"]},
    {"q": "bm25 ranking algorithm", "relevant": ["DOC-EN-002"]},
    {"q": "inverse document frequency", "relevant": ["DOC-EN-002"]},
    {"q": "term frequency algorithm", "relevant": ["DOC-EN-002"]},
    {"q": "index rebuild inconsistency", "relevant": ["DOC-EN-003"]},
    {"q": "data and index", "relevant": ["DOC-EN-003"]},
    {"q": "search quality metrics", "relevant": ["DOC-EN-004"]},
    {"q": "mean reciprocal rank", "relevant": ["DOC-EN-004"]},
    {"q": "recall and rank", "relevant": ["DOC-EN-004"]},
    {"q": "quality metrics recall", "relevant": ["DOC-EN-004"]},
    {"q": "duplicate text", "relevant": ["DOC-TIE-001", "DOC-TIE-002"]},
    {"q": "tie break verification", "relevant": ["DOC-TIE-001", "DOC-TIE-002"]},
    {"q": "向量数据库", "relevant": ["DOC-CN-002"]},
    {"q": "相似度计算", "relevant": ["DOC-CN-002"]},
    {"q": "中文分词质量", "relevant": ["DOC-CN-004"]},
    {"q": "检索召回率", "relevant": ["DOC-CN-004"]},
    {"q": "索引重建", "relevant": ["DOC-CN-005"]},
    {"q": "接口层与测试层", "relevant": ["DOC-CN-001"]},
]


def doc_ids_of(hits: Sequence[Dict[str, Any]]) -> List[str]:
    """按顺序取出 hits 中的 doc_id（含重复，用于暴露去重缺陷）。"""
    return [str(hit.get("doc_id")) for hit in hits or []]


def recall_at_k(hits: Sequence[Dict[str, Any]], relevant: Sequence[str], k: int) -> float:
    """recall@k：标注相关文档中被 top-k 召回的比例。

    无标注（relevant 为空）时返回 0.0：没有标注就无从判断是否漏召，
    不能按「无可漏项」记满分——那会把标注缺失静默变成一次满分。
    这类查询在 `build_report` 里被标记为 unlabeled 并排除出宏平均。
    """
    if not relevant:
        return 0.0
    expected = {str(doc_id) for doc_id in relevant}
    retrieved = set(doc_ids_of(hits)[:max(int(k), 0)])
    return len(retrieved & expected) / len(expected)


def reciprocal_rank(hits: Sequence[Dict[str, Any]], relevant: Sequence[str]) -> float:
    """RR：首个相关文档在**返回列表里的位次**的倒数；未召回相关文档时为 0.0。

    位次取 `enumerate` 的下标，不取 hit 自报的 `rank` 字段。原因：`rank` 是被测
    系统自己写进响应体的，用被测方自报的位次去评被测方的排序质量，等于让它自己
    给自己打分——`BUG_RAG_SORT_ASC`（分数升序）与并列顺序缺陷恰好都污染这个字段，
    此时 RR 会显示 1.0，把真实的排序缺陷盖过去。
    位次是评测方观察到的事实，rank 字段只是被测方的一句话。
    """
    expected = {str(doc_id) for doc_id in relevant}
    if not expected:
        return 0.0
    for position, hit in enumerate(hits or [], start=1):
        if str(hit.get("doc_id")) in expected:
            return 1.0 / position
    return 0.0


def mean_reciprocal_rank(per_query_rr: Sequence[float]) -> float:
    """MRR：各查询 RR 的算术平均。"""
    values = list(per_query_rr)
    return sum(values) / len(values) if values else 0.0


def build_report(results: Sequence[Dict[str, Any]], k: int) -> Dict[str, Any]:
    """把多次检索结果汇总成质量指标报告。

    results 每项形如 {"q": str, "relevant": [doc_id...], "hits": [hit...]}。

    宏平均只统计**有标注**的查询（`labeled_count`）：无标注查询的 recall 是无定义的，
    把它算进去（无论记 0 还是记 1）都会污染整体指标，因此只排除、不参与。
    """
    per_query: List[Dict[str, Any]] = []
    for item in results:
        hits = list(item.get("hits") or [])
        relevant = [str(doc_id) for doc_id in (item.get("relevant") or [])]
        per_query.append({
            "query": item.get("q", ""),
            "relevant": relevant,
            "labeled": bool(relevant),
            "retrieved": doc_ids_of(hits)[:max(int(k), 0)],
            "recall_at_k": recall_at_k(hits, relevant, k),
            "reciprocal_rank": reciprocal_rank(hits, relevant),
        })
    labeled = [p for p in per_query if p["labeled"]]
    count = len(labeled) or 1
    return {
        "k": k,
        "query_count": len(per_query),
        "labeled_count": len(labeled),
        "unlabeled_count": len(per_query) - len(labeled),
        "recall_at_k": sum(p["recall_at_k"] for p in labeled) / count,
        "mrr": sum(p["reciprocal_rank"] for p in labeled) / count,
        "per_query": per_query,
    }


def check_rank_monotonic(hits: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """rank 必须为 1..n 连续，score 必须单调非增。"""
    items = list(hits or [])
    ranks = [int(hit.get("rank") or 0) for hit in items]
    if ranks != list(range(1, len(items) + 1)):
        return {"ok": False, "detail": f"rank 不连续: {ranks}"}
    for index in range(1, len(items)):
        prev = float(items[index - 1].get("score") or 0.0)
        cur = float(items[index].get("score") or 0.0)
        if cur > prev + EPS:
            return {"ok": False,
                    "detail": f"score 非降序: rank{index}={prev:.6f} < rank{index + 1}={cur:.6f}"}
    return {"ok": True, "detail": "score 单调非增、rank 连续"}


def check_tie_by_doc_id(hits: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """分数并列时必须按 doc_id 升序（稳定、可预期的次序）。"""
    items = list(hits or [])
    for index in range(1, len(items)):
        prev, cur = items[index - 1], items[index]
        same_score = abs(float(prev.get("score") or 0.0) - float(cur.get("score") or 0.0)) < EPS
        if same_score and str(prev.get("doc_id")) > str(cur.get("doc_id")):
            return {"ok": False,
                    "detail": (f"并列分数未按 doc_id 升序: {prev.get('doc_id')}"
                               f"(rank{prev.get('rank')}) 排在 {cur.get('doc_id')}(rank{cur.get('rank')}) 之前")}
    return {"ok": True, "detail": "并列分数按 doc_id 升序"}


def find_duplicate_hits(hits: Sequence[Dict[str, Any]]) -> List[str]:
    """返回在 hits 中重复出现的 doc_id（去重缺陷证据）。"""
    seen: Dict[str, int] = {}
    for doc_id in doc_ids_of(hits):
        seen[doc_id] = seen.get(doc_id, 0) + 1
    return [doc_id for doc_id, count in seen.items() if count > 1]


def _as_finite_float(value: Any) -> Optional[float]:
    """把值转成可用于大小比较的有限浮点数。

    不是实数的一律返回 None：缺键（None）、布尔、字符串、非数字、NaN、inf。
    「指标没算出来」与「指标算出来是 0」必须能在这一步被区分开。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def judge_quality(report: Dict[str, Any], *,
                  min_recall: Optional[float] = None,
                  min_mrr: Optional[float] = None) -> Dict[str, Any]:
    """对 `build_report` / `rag_evaluate` 的产出做**阈值判定**，返回结论。

    这是「跑通」与「达标」之间的那一层：`rag_evaluate` 的 `ok` 只表示 HTTP 调用成功，
    一次召回为 0 的检索同样 `ok=True`。本函数只回答「质量是否达到给定阈值」。

    三条判失败规则（缺一不可，都是曾经把假绿放进报告的口子）：
      1. 没有给出任何阈值：判定无定义，按失败处理——不能因为「没设阈值」就默认达标；
      2. 评测集没有标注（`labeled_count == 0`）：recall / MRR 全无定义。空标注集配上
         阈值 0.0 曾让三条断言全部通过，这里必须判失败；
      3. 被测量的指标不存在或不是有限实数（缺键、None、NaN）：无从比较，判失败。
         不能让「指标没算出来」和「指标算出来是 0」在任何一环被混为一谈。

    `ok` 由本函数自己的比较结果决定，**不靠抛异常**表达失败：调用方读 `ok` 与
    `failures` 即可，异常只留给真正的编程错误。
    """
    verdicts: List[Dict[str, Any]] = []
    failures: List[str] = []

    requested = [("recall_at_k", min_recall), ("mrr", min_mrr)]
    requested = [(metric, threshold) for metric, threshold in requested if threshold is not None]

    if not requested:
        return {"ok": False, "verdicts": [], "checked": 0,
                "failures": ["没有给出任何阈值（min_recall / min_mrr 全为 None）："
                             "阈值判定无定义，按失败处理"]}

    labeled_count = report.get("labeled_count")
    if not isinstance(labeled_count, int) or labeled_count <= 0:
        return {"ok": False, "verdicts": [], "checked": 0,
                "failures": [f"评测集没有可用标注（labeled_count={labeled_count!r}）："
                             "recall@k 与 MRR 都无定义——标注缺失按失败处理，"
                             "不按「无可漏项」记满分"]}

    for metric, threshold in requested:
        limit = _as_finite_float(threshold)
        if limit is None:
            failures.append(f"阈值 {metric} >= {threshold!r} 不是有限实数，判定无定义")
            continue
        measured = _as_finite_float(report.get(metric))
        if measured is None:
            failures.append(f"被测量 {metric} 不存在或不是有限实数（实际 {report.get(metric)!r}）："
                            f"无从与阈值 {limit} 比较，按失败处理")
            continue
        passed = measured + EPS >= limit
        verdicts.append({"metric": metric, "threshold": limit, "value": measured, "ok": passed})
        if not passed:
            failures.append(f"{metric}={measured:.4f} < 阈值 {limit:.4f}"
                            f"（标注 {labeled_count} 条查询，未达标）")

    return {"ok": bool(verdicts) and not failures, "verdicts": verdicts,
            "checked": len(verdicts), "failures": failures}
