"""Agent 可调用的执行工具：接口请求 / UI 检查 / RAG 检索 / 契约读取。"""
from .api_tool import api_request
from .rag_metrics import (LABELED_QUERIES, build_report, check_rank_monotonic, check_tie_by_doc_id,
                          doc_ids_of, find_duplicate_hits, mean_reciprocal_rank,
                          recall_at_k, reciprocal_rank)
from .rag_tool import (rag_add_doc, rag_consistency_probe, rag_delete_doc, rag_evaluate,
                       rag_index, rag_reindex, rag_search, rag_update_doc)
from .spec_tool import fetch_openapi, summarize_spec
from .ui_tool import UiUnavailableError, ui_check

__all__ = [
    "api_request",
    "fetch_openapi",
    "summarize_spec",
    "ui_check",
    "UiUnavailableError",
    "rag_search",
    "rag_index",
    "rag_reindex",
    "rag_add_doc",
    "rag_delete_doc",
    "rag_update_doc",
    "rag_evaluate",
    "rag_consistency_probe",
    "doc_ids_of",
    "recall_at_k",
    "reciprocal_rank",
    "mean_reciprocal_rank",
    "build_report",
    "check_rank_monotonic",
    "check_tie_by_doc_id",
    "find_duplicate_hits",
    "LABELED_QUERIES",
]
