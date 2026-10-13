"""BM25 检索引擎与检索质量指标：纯单元测试，不依赖被测服务与网络。

这些用例是「正确实现应全绿」的对照组：同一套断言在缺陷开关开启时必然失败，
在 CORRECT_BUGS（全部 False）下必须全部通过——这就是「检索质量可回归」的基线。
"""
from __future__ import annotations

import pytest

from agenttest.tools.rag_metrics import (LABELED_QUERIES, build_report, check_rank_monotonic,
                                         check_tie_by_doc_id, doc_ids_of, find_duplicate_hits,
                                         mean_reciprocal_rank, recall_at_k, reciprocal_rank)
from demo_app.rag import (CORRECT_BUGS, DEFAULT_CORPUS, DOC_TIE_A, DOC_TIE_B, RAG_BUGS,
                          RAG_MAX_TOP_K, RagService, tokenize)


# ---------- 分词 ----------

def test_ascii_words_are_lowercased():
    assert tokenize("BM25 Ranking") == ["bm25", "ranking"]


def test_cjk_yields_unigram_and_bigram():
    tokens = tokenize("向量数据库")
    assert tokens[:5] == ["向", "量", "数", "据", "库"]
    assert "向量" in tokens and "据库" in tokens
    assert len(tokens) == 5 + 4  # 5 个 unigram + 4 个相邻 bigram


def test_buggy_cjk_tokenizer_collapses_whole_run():
    # 缺陷注入点：整段连续中文只切一个 token，中文查询与中文文档不可能有 token 交集
    assert tokenize("向量数据库", buggy_cjk=True) == ["向量数据库"]


def test_mixed_text_splits_by_script():
    tokens = tokenize("用 BM25 做检索")
    assert "bm25" in tokens
    assert "检" in tokens and "检索" in tokens


# ---------- BM25 后端（正确实现）----------

@pytest.fixture()
def engine():
    return RagService(bugs=CORRECT_BUGS)


def test_correct_engine_indexes_all_documents(engine):
    assert engine.size() == len(DEFAULT_CORPUS)
    assert engine.index_size() == engine.size()


def test_correct_engine_recalls_chinese_document(engine):
    hits = engine.search("向量数据库", 5)
    assert hits, "正确实现下中文查询必须能召回文档"
    assert hits[0]["doc_id"] == "DOC-CN-002"
    assert hits[0]["rank"] == 1


def test_correct_engine_recalls_english_document(engine):
    hits = engine.search("search quality metrics", 5)
    assert hits[0]["doc_id"] == "DOC-EN-004"


def test_correct_engine_aggregates_duplicate_terms(engine):
    # 「vector database」两个 term 都命中同一文档，必须聚合为一条而非两条
    hits = engine.search("vector database", 5)
    assert doc_ids_of(hits) == ["DOC-EN-001"]


def test_correct_engine_ranks_by_score_desc(engine):
    hits = engine.search("search quality metrics", 5)
    assert check_rank_monotonic(hits)["ok"]


def test_correct_engine_tie_breaks_by_doc_id(engine):
    hits = engine.search("duplicate", 5)
    assert doc_ids_of(hits) == [DOC_TIE_A, DOC_TIE_B]  # 必须升序，与语料插入顺序无关
    assert check_tie_by_doc_id(hits)["ok"]


def test_correct_engine_hits_structure(engine):
    hit = engine.search("bm25", 1)[0]
    assert set(hit) == {"doc_id", "score", "rank", "text"}
    assert isinstance(hit["score"], float)


def test_correct_engine_empty_query_returns_no_hits(engine):
    assert engine.search("", 5) == []


def test_correct_engine_unknown_term_returns_no_hits(engine):
    assert engine.search("zzzz-not-in-corpus", 5) == []


def test_top_k_limits_hits(engine):
    assert len(engine.search("document", 1)) <= 1
    assert len(engine.search("document", RAG_MAX_TOP_K)) <= RAG_MAX_TOP_K


# ---------- 索引一致性 ----------

def test_correct_engine_drops_deleted_document(engine):
    assert engine.remove_doc("DOC-CN-002") is True
    assert engine.index_size() == engine.size()
    assert "DOC-CN-002" not in doc_ids_of(engine.search("向量数据库", 5))


def test_correct_engine_updates_document_text(engine):
    assert engine.update_doc("DOC-CN-002", "全新的内容只有这一个词") is True
    assert engine.search("向量数据库", 5) == [] or \
        doc_ids_of(engine.search("向量数据库", 5))[0] != "DOC-CN-002"


def test_reindex_repairs_index_drift():
    # 缺陷实现：删除文档后索引未同步 —— reindex 应把索引拉回与语料一致
    buggy = RagService()
    buggy.remove_doc("DOC-CN-002")
    assert buggy.index_size() != buggy.size()   # 已漂移
    buggy.reindex()
    assert buggy.index_size() == buggy.size()   # 重建后一致


def test_reindex_increments_version(engine):
    before = engine.version
    assert engine.reindex() == before + 1


# ---------- 缺陷实现（钉住当前行为）----------

def test_buggy_engine_fails_chinese_recall():
    assert RagService().search("向量数据库", 5) == []


def test_buggy_engine_sorts_score_ascending():
    hits = RagService().search("search quality metrics", 5)
    assert check_rank_monotonic(hits)["ok"] is False


def test_buggy_engine_tie_order_is_wrong():
    hits = RagService().search("duplicate", 5)
    assert find_duplicate_hits(hits) == []
    assert check_tie_by_doc_id(hits)["ok"] is False


def test_buggy_engine_emits_duplicate_hits():
    hits = RagService().search("vector database", 5)
    assert find_duplicate_hits(hits) == ["DOC-EN-001"]


def test_buggy_engine_reports_loose_contract():
    service = RagService()
    assert service.validate_search("", "1") == (1, "")       # 空 q 未报错
    assert service.validate_search("x", "0")[1] == ""        # 非法 top_k 未报错
    assert RagService(bugs=CORRECT_BUGS).validate_search("", "1")[1]


def test_buggy_engine_keeps_ghost_hit():
    buggy = RagService()
    buggy.add_doc("DOC-TMP", "temporary probe document")
    buggy.remove_doc("DOC-TMP")
    assert "DOC-TMP" in doc_ids_of(buggy.search("temporary probe document", 5))


# ---------- 可选 embedding 后端（缺 Key 时优雅降级）----------

def test_embedding_backend_degrades_without_key(monkeypatch):
    for name in ("AGENTTEST_EMBEDDING_API_KEY", "AGENTTEST_EMBEDDING_BASE_URL",
                 "AGENTTEST_LLM_API_KEY", "AGENTTEST_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    service = RagService(bugs=CORRECT_BUGS, backend="embedding")
    assert service.backend.name == "bm25"
    assert service.fallback_reason
    assert service.search("向量数据库", 5)  # 降级后仍可检索


# ---------- 检索质量指标（纯函数）----------

HITS = [
    {"doc_id": "A", "score": 3.0, "rank": 1},
    {"doc_id": "B", "score": 2.0, "rank": 2},
    {"doc_id": "C", "score": 1.0, "rank": 3},
]


def test_recall_at_k_full_and_partial():
    assert recall_at_k(HITS, ["A", "B"], 5) == pytest.approx(1.0)
    assert recall_at_k(HITS, ["A", "Z"], 5) == pytest.approx(0.5)
    assert recall_at_k(HITS[:1], ["A", "Z"], 5) == pytest.approx(0.5)


def test_recall_at_k_respects_k_window():
    assert recall_at_k(HITS, ["C"], 2) == pytest.approx(0.0)
    assert recall_at_k(HITS, ["C"], 3) == pytest.approx(1.0)


def test_reciprocal_rank_uses_first_relevant_rank():
    assert reciprocal_rank(HITS, ["A"]) == pytest.approx(1.0)
    assert reciprocal_rank(HITS, ["B"]) == pytest.approx(0.5)
    assert reciprocal_rank(HITS, ["C"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(HITS, ["Z"]) == pytest.approx(0.0)


def test_mean_reciprocal_rank_averages():
    assert mean_reciprocal_rank([1.0, 0.5, 0.0]) == pytest.approx(0.5)
    assert mean_reciprocal_rank([]) == 0.0


def test_build_report_aggregates_per_query():
    report = build_report(
        [{"q": "q1", "relevant": ["A"], "hits": HITS},
         {"q": "q2", "relevant": ["C"], "hits": []}],
        k=3,
    )
    assert report["query_count"] == 2
    assert report["recall_at_k"] == pytest.approx(0.5)
    assert report["mrr"] == pytest.approx((1.0 + 0.0) / 2)
    assert report["per_query"][1]["retrieved"] == []


def test_check_rank_monotonic_detects_ascending_scores():
    bad = [{"doc_id": "A", "score": 1.0, "rank": 1}, {"doc_id": "B", "score": 2.0, "rank": 2}]
    assert check_rank_monotonic(bad)["ok"] is False
    assert "非降序" in check_rank_monotonic(bad)["detail"]


def test_check_rank_monotonic_detects_non_contiguous_rank():
    bad = [{"doc_id": "A", "score": 2.0, "rank": 1}, {"doc_id": "B", "score": 1.0, "rank": 3}]
    assert check_rank_monotonic(bad)["ok"] is False


def test_check_tie_by_doc_id_flags_unstable_order():
    bad = [{"doc_id": "B", "score": 1.0, "rank": 1}, {"doc_id": "A", "score": 1.0, "rank": 2}]
    assert check_tie_by_doc_id(bad)["ok"] is False
    assert check_tie_by_doc_id(list(reversed(bad)))["ok"] is True


def test_find_duplicate_hits():
    dups = find_duplicate_hits([{"doc_id": "A"}, {"doc_id": "A"}, {"doc_id": "B"}])
    assert dups == ["A"]
    assert find_duplicate_hits([{"doc_id": "A"}, {"doc_id": "B"}]) == []


def test_labeled_query_set_reproduces_documented_metrics():
    """文档里写死的 recall@5 / MRR 数值必须能由 LABELED_QUERIES 复现（防止文档与代码漂移）。"""
    def metrics(bugs):
        service = RagService(bugs=bugs)
        return build_report(
            [{"q": item["q"], "relevant": item["relevant"], "hits": service.search(item["q"], 5)}
             for item in LABELED_QUERIES], k=5)

    defect = metrics(RAG_BUGS)
    correct = metrics(CORRECT_BUGS)
    assert defect["query_count"] == 7
    # 缺陷实现：4 条英文查询命中排第 1（RR=1），3 条中文查询召回为 0 → recall=MRR=4/7
    assert defect["recall_at_k"] == pytest.approx(4 / 7)
    assert defect["mrr"] == pytest.approx(4 / 7)
    # 正确实现：7 条查询的标注文档都排在第 1 位
    assert correct["recall_at_k"] == pytest.approx(1.0)
    assert correct["mrr"] == pytest.approx(1.0)
