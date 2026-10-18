"""rag_tool 工具层 + 被测系统 RAG 端点：真实 HTTP 集成测试。

与 test_api_tool.py 同一约定：
- 工具层用例**钉住工具如实返回的当前行为**（`ok` 只表示调用成功，业务断言不在工具里）；
- 契约/正确性用例**断言期望行为**；已知被注入缺陷破坏的用例用 `strict=True` 的 xfail 标记，
  与 test_demo_app.py 的做法一致——缺陷一旦被修复，xfail 会转为 XPASS 从而让测试失败，
  逼迫我们同步更新基准。
"""

from __future__ import annotations

import pytest

from agenttest.tools import (
    doc_ids_of,
    rag_add_doc,
    rag_consistency_probe,
    rag_delete_doc,
    rag_evaluate,
    rag_index,
    rag_reindex,
    rag_search,
    rag_update_doc,
)
from agenttest.tools.rag_tool import RAG_PROBE_DOC_ID, RAG_PROBE_QUERY, RAG_PROBE_TEXT
from demo_app.app import create_app
from demo_app.rag import RAG_MAX_QUERY_LEN, RAG_MAX_TOP_K


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def rag_url(demo_server_url):
    """会话级被测服务上跑完探针后强制重建索引，保证各用例互不污染。"""
    yield demo_server_url
    rag_reindex(demo_server_url)


# ---------- 接口契约 ----------


def test_search_response_contract(client):
    body = client.get("/api/rag/search?q=vector%20database&top_k=5").get_json()
    assert set(body) == {"query", "top_k", "hits", "index"}
    assert body["query"] == "vector database"
    assert body["top_k"] == 5
    assert set(body["index"]) == {"backend", "size", "version"}
    assert body["index"]["backend"] == "bm25"
    hit = body["hits"][0]
    assert set(hit) == {"doc_id", "score", "rank", "text"}
    assert isinstance(hit["score"], float) and isinstance(hit["rank"], int)


def test_index_contract(client):
    body = client.get("/api/rag/index").get_json()
    assert body["backend"] == "bm25"
    assert body["size"] == len(body["doc_ids"])
    assert "version" in body
    assert any(doc_id.startswith("DOC-CN-") for doc_id in body["doc_ids"])  # 语料必须含中文文档


def test_reindex_returns_new_version(client):
    before = client.get("/api/rag/index").get_json()["version"]
    body = client.post("/api/rag/reindex").get_json()
    assert body["reindexed"] is True
    assert body["version"] == before + 1
    assert client.get("/api/rag/index").get_json()["version"] == before + 1


@pytest.mark.parametrize("query", ["", "   "])
@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_LOOSE_CONTRACT：空 q 应返回 400，实际 200", strict=True)
def test_empty_query_must_be_400(query):
    r = create_app().test_client().get("/api/rag/search", query_string={"q": query, "top_k": 5})
    assert r.status_code == 400


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_LOOSE_CONTRACT：top_k=0 应返回 400，实际 200", strict=True)
def test_top_k_zero_must_be_400(client):
    assert client.get("/api/rag/search?q=a&top_k=0").status_code == 400


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_LOOSE_CONTRACT：负 top_k 应返回 400，实际按默认值继续", strict=True)
def test_top_k_negative_must_be_400(client):
    assert client.get("/api/rag/search?q=a&top_k=-3").status_code == 400


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_LOOSE_CONTRACT：超上限 top_k 应返回 400，实际被截断", strict=True)
def test_top_k_over_limit_must_be_400(client):
    assert client.get(f"/api/rag/search?q=a&top_k={RAG_MAX_TOP_K + 1}").status_code == 400


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_LOOSE_CONTRACT：非数字 top_k 应返回 400，实际按默认值继续", strict=True)
def test_top_k_not_a_number_must_be_400(client):
    assert client.get("/api/rag/search?q=a&top_k=abc").status_code == 400


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_LOOSE_CONTRACT：超长 q 应返回 400，实际 200", strict=True)
def test_overlong_query_must_be_400(client):
    long_query = "长" * (RAG_MAX_QUERY_LEN + 40)
    r = client.get("/api/rag/search", query_string={"q": long_query, "top_k": 5})
    assert r.status_code == 400


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_LOOSE_CONTRACT：缺 q 应返回 400，实际 200", strict=True)
def test_missing_q_must_be_400(client):
    assert client.get("/api/rag/search?top_k=5").status_code == 400


# ---------- 钉住当前缺陷行为（与 test_api_tool.py 同一约定：工具如实返回实际状态）----------


def test_empty_query_currently_returns_200(client):
    r = client.get("/api/rag/search", query_string={"q": "", "top_k": 5})
    assert r.status_code == 200  # 实测当前行为，契约要求 400
    assert r.get_json()["hits"] == []


def test_top_k_zero_currently_returns_200(client):
    r = client.get("/api/rag/search?q=vector%20database&top_k=0")
    assert r.status_code == 200  # 实测当前行为，契约要求 400
    assert r.get_json()["top_k"] == 5  # 被静默回退为默认值


# ---------- 工具层：只如实返回，不下断言 ----------


def test_rag_search_tool_reports_200_and_hits(rag_url):
    detail = rag_search(rag_url, "search quality metrics", 5)
    assert detail["ok"] is True
    assert detail["status"] == 200
    assert detail["hits"]
    assert detail["index"]["backend"] == "bm25"
    assert detail["path"] == "/api/rag/search"


def test_rag_search_tool_pins_current_buggy_contract(rag_url):
    # 钉住当前行为：空 q 实现返回 200（契约要求 400），工具如实透传状态码与 body
    detail = rag_search(rag_url, "", 5)
    assert detail["ok"] is True  # 调用本身成功
    assert detail["status"] == 200  # 但契约不符 —— 由用例层判定失败
    assert detail["hits"] == []


def test_rag_search_tool_reports_network_error():
    detail = rag_search("http://127.0.0.1:1", "q", 5)
    assert detail["ok"] is False
    assert detail["status"] == 0
    assert detail["error"]


def test_rag_index_tool_lists_all_documents(rag_url):
    detail = rag_index(rag_url)
    assert detail["ok"] is True
    ids = doc_ids_of_index(detail)
    assert "DOC-CN-002" in ids
    assert len(ids) == detail["body"]["size"]


def doc_ids_of_index(detail):
    return [str(doc_id) for doc_id in detail["body"]["doc_ids"]]


def test_rag_reindex_tool_returns_version(rag_url):
    detail = rag_reindex(rag_url)
    assert detail["ok"] is True
    assert detail["body"]["version"] >= 1


def test_rag_add_and_delete_doc_tools(rag_url):
    add = rag_add_doc(rag_url, "DOC-TOOL-TMP", "temporary document for tool test")
    assert add["status"] == 201
    assert "DOC-TOOL-TMP" in doc_ids_of_index(rag_index(rag_url))
    delete = rag_delete_doc(rag_url, "DOC-TOOL-TMP")
    assert delete["status"] == 200
    assert "DOC-TOOL-TMP" not in doc_ids_of_index(rag_index(rag_url))
    assert rag_delete_doc(rag_url, "DOC-TOOL-TMP")["status"] == 404


def test_rag_update_doc_tool(rag_url):
    rag_add_doc(rag_url, "DOC-UPD-TMP", "before update content")
    assert rag_update_doc(rag_url, "DOC-UPD-TMP", "after update content")["status"] == 200
    assert rag_update_doc(rag_url, "DOC-MISSING-TMP", "x")["status"] == 404


# ---------- 检索质量指标（打到真实端点）----------


def test_rag_evaluate_reports_metrics(rag_url):
    report = rag_evaluate(
        rag_url,
        [
            {"q": "search quality metrics", "relevant": ["DOC-EN-004"]},
            {"q": "bm25 ranking", "relevant": ["DOC-CN-003"]},
        ],
        top_k=5,
    )
    assert report["ok"] is True
    assert report["query_count"] == 2
    # 两条查询都命中了相关文档，因此 recall@5 为 1.0
    assert report["recall_at_k"] == pytest.approx(1.0)
    # 第二条查询的相关文档被排到 rank 3（错误排序），RR=1/3 → MRR=(1.0+1/3)/2
    assert report["per_query"][1]["retrieved"][-1] == "DOC-CN-003"
    assert report["mrr"] == pytest.approx((1.0 + 1 / 3) / 2)


def test_rag_evaluate_chinese_recall_is_zero_under_defect(rag_url):
    # 钉住当前行为：中文分词缺失 → 中文查询 recall@k 恒为 0（本仓库最核心的检索缺陷）
    report = rag_evaluate(rag_url, [{"q": "向量数据库", "relevant": ["DOC-CN-002"]}], top_k=5)
    assert report["ok"] is True
    assert report["recall_at_k"] == pytest.approx(0.0)
    assert report["mrr"] == pytest.approx(0.0)
    assert report["per_query"][0]["retrieved"] == []


# ---------- 索引一致性探针 ----------


def test_consistency_probe_self_cleans(rag_url):
    probe = rag_consistency_probe(rag_url)
    assert probe["ok"] is True
    assert probe["add_status"] == 201 and probe["delete_status"] == 200
    # 探针新增后确实进了语料（index_mid 快照），删除后确实退出（index_after 快照）。
    # 不用 corpus_doc_ids_before：它取自幂等清理之前，上一次跑干净时本来就不含探针文档。
    assert RAG_PROBE_DOC_ID in probe["corpus_doc_ids_mid"]
    assert RAG_PROBE_DOC_ID not in probe["corpus_doc_ids_after_delete"]
    assert probe["reindex_status"] == 200


def test_consistency_probe_detects_ghost_hit(rag_url):
    # 钉住当前行为：删除语料后倒排索引未同步 → 幽灵召回
    probe = rag_consistency_probe(rag_url)
    assert probe["probe_doc_id"] in doc_ids_of(probe["hits_before_delete"])
    assert RAG_PROBE_DOC_ID in probe["ghost_doc_ids"]
    assert probe["index_size_after_delete"] == probe["corpus_size_after_delete"] + 1


def test_consistency_probe_query_works_on_correct_engine_only(rag_url):
    # 反向验证：探针查询本身是能召回的（说明 ghost hit 确实来自陈旧索引，而非查询无意义）
    probe = rag_consistency_probe(rag_url)
    assert probe["probe_query"] == RAG_PROBE_QUERY
    assert RAG_PROBE_TEXT.startswith("probe document")


def test_probe_document_is_not_left_behind(rag_url):
    rag_consistency_probe(rag_url)
    assert RAG_PROBE_DOC_ID not in doc_ids_of_index(rag_index(rag_url))
