"""检索质量指标与检索结果不变量（纯函数，无 IO、无第三方依赖）。

指标定义（写进报告与文档的口径，务必与实现一致）：
  recall@k = |top-k 召回的 doc_id ∩ 标注相关 doc_id| / |标注相关 doc_id|
             —— 单查询计算；多查询取算术平均（宏平均）。
  MRR     = mean(1 / 首个相关文档的 rank)，无相关文档召回时该查询 RR=0。
             —— 衡量「相关文档排得够不够靠前」。

不变量：
  rank 连续 1..n、score 单调非增（check_rank_monotonic）
  分数并列时按 doc_id 升序（check_tie_by_doc_id）
  同一 doc_id 不得重复出现（find_duplicate_hits）

LABELED_QUERIES 是仓库唯一一份标注集（7 条查询，k=5）：
  缺陷实现 recall@5=MRR=0.5714（4/7），正确实现两项均为 1.0000。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

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


def doc_ids_of(hits: Sequence[Dict[str, Any]]) -> List[str]:
    """按顺序取出 hits 中的 doc_id（含重复，用于暴露去重缺陷）。"""
    return [str(hit.get("doc_id")) for hit in hits or []]


def recall_at_k(hits: Sequence[Dict[str, Any]], relevant: Sequence[str], k: int) -> float:
    """recall@k：标注相关文档中被 top-k 召回的比例。无标注时返回 1.0（无可漏项）。"""
    if not relevant:
        return 1.0
    expected = {str(doc_id) for doc_id in relevant}
    retrieved = set(doc_ids_of(hits)[:max(int(k), 0)])
    return len(retrieved & expected) / len(expected)


def reciprocal_rank(hits: Sequence[Dict[str, Any]], relevant: Sequence[str]) -> float:
    """RR：首个相关文档 rank 的倒数；未召回相关文档时为 0.0。"""
    expected = {str(doc_id) for doc_id in relevant}
    if not expected:
        return 0.0
    for hit in hits or []:
        if str(hit.get("doc_id")) in expected:
            return 1.0 / max(int(hit.get("rank") or 1), 1)
    return 0.0


def mean_reciprocal_rank(per_query_rr: Sequence[float]) -> float:
    """MRR：各查询 RR 的算术平均。"""
    values = list(per_query_rr)
    return sum(values) / len(values) if values else 0.0


def build_report(results: Sequence[Dict[str, Any]], k: int) -> Dict[str, Any]:
    """把多次检索结果汇总成质量指标报告。

    results 每项形如 {"q": str, "relevant": [doc_id...], "hits": [hit...]}。
    """
    per_query: List[Dict[str, Any]] = []
    for item in results:
        hits = list(item.get("hits") or [])
        relevant = [str(doc_id) for doc_id in (item.get("relevant") or [])]
        per_query.append({
            "query": item.get("q", ""),
            "relevant": relevant,
            "retrieved": doc_ids_of(hits)[:max(int(k), 0)],
            "recall_at_k": recall_at_k(hits, relevant, k),
            "reciprocal_rank": reciprocal_rank(hits, relevant),
        })
    count = len(per_query) or 1
    return {
        "k": k,
        "query_count": len(per_query),
        "recall_at_k": sum(p["recall_at_k"] for p in per_query) / count,
        "mrr": sum(p["reciprocal_rank"] for p in per_query) / count,
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
