"""RAG 检索工具：把被测系统的检索链路包装成 Agent 可调用的确定性工具。

与 api_tool 同风格：返回结构化 dict，`ok` 只表示「HTTP 调用本身成功」，
业务断言（该命中哪些文档、排序是否单调、指标是否达标）交给用例与断言层，
工具自己不下结论。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from agenttest.tools.api_tool import api_request
from agenttest.tools.rag_metrics import build_report, doc_ids_of

# 索引一致性探针使用的临时文档（纯 ASCII，避免受中文分词缺陷影响而掩盖陈旧索引缺陷）
RAG_PROBE_DOC_ID = "DOC-PROBE-TMP"
RAG_PROBE_TEXT = "probe document for stale index detection"
RAG_PROBE_QUERY = "probe document"

SEARCH_PATH = "/api/rag/search"
INDEX_PATH = "/api/rag/index"
REINDEX_PATH = "/api/rag/reindex"
DOCS_PATH = "/api/rag/docs"


def _call(method: str, base_url: str, path: str, query: Optional[Dict[str, str]] = None,
          body: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
    detail = api_request(method, path, query=query, body=body, base_url=base_url, timeout=timeout)
    return {
        "ok": bool(detail.get("ok")),
        "status": int(detail.get("status") or 0),
        "body": detail.get("body"),
        "time_ms": int(detail.get("time_ms") or 0),
        "error": detail.get("error") or "",
        "path": path,
    }


def rag_search(base_url: str, query: Optional[str] = None, top_k: Optional[Any] = None,
               timeout: float = 10.0) -> Dict[str, Any]:
    """GET /api/rag/search?q=&top_k= —— 一次检索调用。"""
    params: Dict[str, str] = {}
    if query is not None:
        params["q"] = str(query)
    if top_k is not None:
        params["top_k"] = str(top_k)
    detail = _call("GET", base_url, SEARCH_PATH, query=params, timeout=timeout)
    body = detail["body"] if isinstance(detail["body"], dict) else {}
    detail["hits"] = list(body.get("hits") or [])
    detail["index"] = dict(body.get("index") or {})
    detail["query"] = body.get("query", query)
    detail["top_k"] = body.get("top_k")
    return detail


def rag_index(base_url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """GET /api/rag/index —— 语料/索引元信息。"""
    return _call("GET", base_url, INDEX_PATH, timeout=timeout)


def rag_reindex(base_url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """POST /api/rag/reindex —— 依据当前语料重建索引。"""
    return _call("POST", base_url, REINDEX_PATH, timeout=timeout)


def rag_add_doc(base_url: str, doc_id: str, text: str, timeout: float = 10.0) -> Dict[str, Any]:
    """POST /api/rag/docs —— 新增语料文档。"""
    return _call("POST", base_url, DOCS_PATH, body={"doc_id": doc_id, "text": text}, timeout=timeout)


def rag_delete_doc(base_url: str, doc_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    """DELETE /api/rag/docs/{doc_id} —— 删除语料文档。"""
    return _call("DELETE", base_url, f"{DOCS_PATH}/{doc_id}", timeout=timeout)


def rag_update_doc(base_url: str, doc_id: str, text: str, timeout: float = 10.0) -> Dict[str, Any]:
    """PATCH /api/rag/docs/{doc_id} —— 更新语料文档正文。"""
    return _call("PATCH", base_url, f"{DOCS_PATH}/{doc_id}", body={"text": text}, timeout=timeout)


def doc_ids_in_index(index_detail: Dict[str, Any]) -> List[str]:
    body = index_detail.get("body") if isinstance(index_detail.get("body"), dict) else {}
    return [str(doc_id) for doc_id in (body.get("doc_ids") or [])]


def rag_evaluate(base_url: str, queries: Sequence[Dict[str, Any]], top_k: int = 5,
                 timeout: float = 10.0) -> Dict[str, Any]:
    """跑一组 (query, 标注相关文档) 并计算 recall@k / MRR。

    指标本身由 rag_metrics 的纯函数计算，这里只负责取数，方便用例直接断言数值。
    """
    results: List[Dict[str, Any]] = []
    statuses: List[int] = []
    for item in queries:
        query = str(item.get("q", ""))
        relevant = [str(doc_id) for doc_id in (item.get("relevant") or [])]
        detail = rag_search(base_url, query, top_k, timeout=timeout)
        statuses.append(detail["status"])
        results.append({"q": query, "relevant": relevant, "hits": detail["hits"]})
    report = build_report(results, int(top_k))
    ok = bool(statuses) and all(200 <= status < 300 for status in statuses)
    report.update({
        "ok": ok,
        "status": statuses[0] if statuses else 0,
        "statuses": statuses,
        "error": "" if ok else f"检索请求状态异常: {statuses}",
    })
    return report


def rag_consistency_probe(base_url: str, doc_id: str = RAG_PROBE_DOC_ID, text: str = RAG_PROBE_TEXT,
                          query: str = RAG_PROBE_QUERY, top_k: int = 5,
                          timeout: float = 10.0) -> Dict[str, Any]:
    """索引一致性探针：新增临时文档 → 确认可召回 → 删除 → 再检索，看是否仍被召回。

    流程（全程自清理，跑完调用 reindex 让被测系统回到语料/索引一致状态，避免污染其他用例）：
      1. 先删除可能残留的探针文档（幂等）
      2. POST 新增探针文档 → 检索应能召回
      3. DELETE 删除探针文档 → /api/rag/index 的 doc_ids 应不再包含它
      4. 再次检索 → 若仍返回该 doc_id，即为陈旧索引（ghost hit）
      5. POST reindex 清理

    `ok` 仅表示探针流程本身跑通（各步骤都有 HTTP 响应），不做业务断言。
    """
    index_before = rag_index(base_url, timeout=timeout)
    rag_delete_doc(base_url, doc_id, timeout=timeout)          # 幂等清理
    add = rag_add_doc(base_url, doc_id, text, timeout=timeout)
    hits_before = rag_search(base_url, query, top_k, timeout=timeout)
    index_mid = rag_index(base_url, timeout=timeout)
    delete = rag_delete_doc(base_url, doc_id, timeout=timeout)
    index_after = rag_index(base_url, timeout=timeout)
    hits_after = rag_search(base_url, query, top_k, timeout=timeout)
    reindex = rag_reindex(base_url, timeout=timeout)

    corpus_ids_after = doc_ids_in_index(index_after)
    ghost_ids = [doc_id_ for doc_id_ in doc_ids_of(hits_after["hits"]) if doc_id_ not in set(corpus_ids_after)]
    ok = bool(index_after["ok"] and hits_after["ok"] and add["ok"] and delete["ok"])
    return {
        "ok": ok,
        "status": hits_after["status"],
        "probe_doc_id": doc_id,
        "probe_query": query,
        "corpus_doc_ids_before": doc_ids_in_index(index_before),
        "corpus_doc_ids_after_delete": corpus_ids_after,
        "corpus_size_after_delete": (index_after["body"] or {}).get("size") if isinstance(index_after["body"], dict) else None,
        "index_size_after_delete": (index_after["body"] or {}).get("index_size") if isinstance(index_after["body"], dict) else None,
        "index_size_mid": (index_mid["body"] or {}).get("index_size") if isinstance(index_mid["body"], dict) else None,
        "hits_before_delete": hits_before["hits"],
        "hits_after_delete": hits_after["hits"],
        "ghost_doc_ids": ghost_ids,
        "add_status": add["status"],
        "delete_status": delete["status"],
        "reindex_status": reindex["status"],
        "reindex_version": (reindex["body"] or {}).get("version") if isinstance(reindex["body"], dict) else None,
        "error": "" if ok else "索引一致性探针调用失败",
    }
