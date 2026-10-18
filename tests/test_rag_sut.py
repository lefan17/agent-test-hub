"""被测系统 demo_app 的 RAG 端点：语料增删改、索引一致性、四类注入缺陷的 HTTP 级复现。

约定与 test_demo_app.py 一致：
- 「正确性」用例直接断言期望行为；当前实现不满足的，用 `strict=True` 的 xfail 标为缺陷基准；
- 「缺陷复现」用例钉住当前实际行为，并在注释里说明它违反了哪条契约。
"""

from __future__ import annotations

import threading
import time

import pytest

from agenttest.tools import rag_add_doc, rag_reindex, rag_search
from demo_app.app import create_app
from demo_app.rag import CORRECT_BUGS, RAG_BUGS, RagService


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def hits_of(client, query, top_k=5):
    body = client.get("/api/rag/search", query_string={"q": query, "top_k": top_k}).get_json()
    return body["hits"]


# ---------- 语料与索引元信息 ----------


def test_index_exposes_backend_size_and_version(client):
    body = client.get("/api/rag/index").get_json()
    assert body["backend"] == "bm25"
    assert body["size"] == len(body["doc_ids"]) == 11
    assert body["version"] >= 1
    assert body["requested_backend"] == "bm25"
    assert body["fallback"] == ""


def test_index_size_field_matches_doc_ids(client):
    body = client.get("/api/rag/index").get_json()
    assert body["index_size"] == body["size"]


def test_corpus_contains_chinese_documents(client):
    ids = client.get("/api/rag/index").get_json()["doc_ids"]
    assert len([i for i in ids if i.startswith("DOC-CN-")]) == 5


# ---------- 语料 CRUD ----------


def test_add_document_is_searchable_and_bumps_version(client):
    before = client.get("/api/rag/index").get_json()
    r = client.post("/api/rag/docs", json={"doc_id": "DOC-NEW-001", "text": "newly added document about retrieval"})
    assert r.status_code == 201
    assert r.get_json()["version"] == before["version"] + 1
    assert "DOC-NEW-001" in client.get("/api/rag/index").get_json()["doc_ids"]
    assert "DOC-NEW-001" in [h["doc_id"] for h in hits_of(client, "newly added document")]


def test_add_document_rejects_empty_fields(client):
    assert client.post("/api/rag/docs", json={"doc_id": "DOC-X", "text": "  "}).status_code == 400
    assert client.post("/api/rag/docs", json={"doc_id": "", "text": "x"}).status_code == 400


def test_delete_document_removes_it_from_corpus(client):
    client.post("/api/rag/docs", json={"doc_id": "DOC-DEL-001", "text": "document to be deleted soon"})
    r = client.delete("/api/rag/docs/DOC-DEL-001")
    assert r.status_code == 200
    assert r.get_json()["deleted"] == "DOC-DEL-001"
    assert "DOC-DEL-001" not in client.get("/api/rag/index").get_json()["doc_ids"]


def test_delete_missing_document_is_404(client):
    assert client.delete("/api/rag/docs/DOC-NOT-EXIST").status_code == 404


def test_update_document_replaces_text(client):
    client.post("/api/rag/docs", json={"doc_id": "DOC-UPD-001", "text": "original content alpha"})
    assert client.patch("/api/rag/docs/DOC-UPD-001", json={"text": "rewritten content omega"}).status_code == 200
    body = client.get("/api/rag/search?q=rewritten%20content%20omega").get_json()
    assert "DOC-UPD-001" in [h["doc_id"] for h in body["hits"]]


def test_update_missing_document_is_404(client):
    assert client.patch("/api/rag/docs/DOC-NOT-EXIST", json={"text": "x"}).status_code == 404


def test_add_document_overwrite_clears_stale_postings():
    """覆盖写入同一 doc_id 必须先清旧倒排项，否则旧词仍能召回（第 7 个真实缺陷）。

    这条缺陷独立于 6 个注入开关：把开关全关（CORRECT_BUGS）后它依然存在。
    默认开关的 STALE_INDEX 注入点下旧词残留是**预期缺陷**，故此处用 CORRECT_BUGS 定位真缺陷。
    """
    service = RagService(bugs=CORRECT_BUGS)
    service.add_doc("DOC-OVER-001", "vector database legacy")
    assert "DOC-OVER-001" in [h["doc_id"] for h in service.search("vector", 5)]

    service.add_doc("DOC-OVER-001", "zzz unrelated brand new content only")

    for stale_query in ("vector", "database", "legacy"):
        assert "DOC-OVER-001" not in [h["doc_id"] for h in service.search(stale_query, 5)], (
            f"覆盖写入后旧词 {stale_query!r} 仍在召回旧内容"
        )
    assert "DOC-OVER-001" in [h["doc_id"] for h in service.search("zzz unrelated", 5)]
    assert service.index_size() == service.size()


def test_add_document_overwrite_does_not_inflate_score():
    """tf 累加会让重复覆盖同一文档的分数虚高——覆盖后分数必须与首次写入完全一致。"""
    service = RagService(bugs=CORRECT_BUGS)
    text = "retrieval quality benchmark document"
    service.add_doc("DOC-SCORE-001", text)
    fresh = next(h["score"] for h in service.search("retrieval quality benchmark", 5) if h["doc_id"] == "DOC-SCORE-001")
    for _ in range(3):
        service.add_doc("DOC-SCORE-001", text)
    repeated = next(
        h["score"] for h in service.search("retrieval quality benchmark", 5) if h["doc_id"] == "DOC-SCORE-001"
    )
    assert repeated == fresh, "覆盖写入累加了 tf，分数被虚高"


def test_add_document_overwrite_is_upsert_with_flag(client):
    """POST /api/rag/docs 为 upsert 语义（openapi.json 只声明 201/400，没有 409）。"""
    first = client.post("/api/rag/docs", json={"doc_id": "DOC-UPSERT-001", "text": "alpha content"})
    assert first.status_code == 201
    assert first.get_json()["upserted"] is False

    second = client.post("/api/rag/docs", json={"doc_id": "DOC-UPSERT-001", "text": "omega content"})
    assert second.status_code == 201
    assert second.get_json()["upserted"] is True
    assert second.get_json()["version"] == first.get_json()["version"] + 1
    # 覆盖写入不得新增语料条目（doc_id 仍是同一个）
    doc_ids = client.get("/api/rag/index").get_json()["doc_ids"]
    assert doc_ids.count("DOC-UPSERT-001") == 1


def test_openapi_contract_matches_upsert_semantics(client):
    """契约里不得再出现 409（实现从不返回它）。"""
    spec = client.get("/openapi.json").get_json()
    responses = spec["paths"]["/api/rag/docs"]["post"]["responses"]
    assert set(responses) == {"201", "400"}


def test_concurrent_search_and_write_is_stable():
    """并发读写共享同一份倒排索引：不得抛 dictionary changed size during iteration。

    自建一个多线程服务（与 agenttest/runner.py 的 threaded=True 一致），
    避免并发写入的临时文档污染会话级 demo_server_url。
    """
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    base_url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        errors: list = []
        deadline = time.time() + 1.5

        def reader():
            while time.time() < deadline:
                try:
                    rag_search(base_url, "vector database", 5)
                except Exception as exc:  # pragma: no cover - 修复前这里会命中
                    errors.append(f"{type(exc).__name__}: {exc}")

        def writer():
            index = 0
            while time.time() < deadline:
                try:
                    rag_add_doc(base_url, f"DOC-RACE-{index % 20:03d}", "vector database concurrent write probe")
                except Exception as exc:  # pragma: no cover
                    errors.append(f"{type(exc).__name__}: {exc}")
                index += 1

        threads = [threading.Thread(target=reader) for _ in range(3)] + [
            threading.Thread(target=writer) for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert rag_reindex(base_url)["status"] == 200
    finally:
        server.shutdown()


# ---------- reindex ----------


def test_reindex_bumps_version_and_keeps_corpus(client):
    before = client.get("/api/rag/index").get_json()
    body = client.post("/api/rag/reindex").get_json()
    assert body["version"] == before["version"] + 1
    assert body["size"] == before["size"]
    after = client.get("/api/rag/index").get_json()
    assert after["doc_ids"] == before["doc_ids"]


def test_reindex_repairs_index_drift_after_delete(client):
    client.post("/api/rag/docs", json={"doc_id": "DOC-DRIFT-001", "text": "drift probe document alpha"})
    client.delete("/api/rag/docs/DOC-DRIFT-001")
    # 缺陷期：索引未随删除同步，index_size 大于语料 size
    assert client.get("/api/rag/index").get_json()["index_size"] != client.get("/api/rag/index").get_json()["size"]
    client.post("/api/rag/reindex")
    idx = client.get("/api/rag/index").get_json()
    assert idx["index_size"] == idx["size"]  # reindex 修复漂移
    assert "DOC-DRIFT-001" not in [h["doc_id"] for h in hits_of(client, "drift probe document")]


# ---------- 缺陷 1：中文分词缺失导致召回为 0 ----------


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_CJK_TOKENIZER：中文分词缺失，中文查询召回为 0", strict=True)
def test_chinese_query_should_recall_document(client):
    assert "DOC-CN-002" in [h["doc_id"] for h in hits_of(client, "向量数据库")]


def test_chinese_query_recall_is_currently_zero(client):
    # 钉住当前行为：本应命中的中文文档召回为 0 —— 检索链路最典型的缺陷
    assert hits_of(client, "向量数据库") == []
    assert hits_of(client, "中文分词") == []


def test_ascii_query_still_recalls_under_defect(client):
    # 对照：同一缺陷下英文查询正常，说明问题出在中文分词而非整条链路崩掉
    assert "DOC-EN-001" in [h["doc_id"] for h in hits_of(client, "vector database")]


# ---------- 缺陷 2：排序不稳定 / 分数非降序 ----------


@pytest.mark.xfail(
    reason="注入缺陷 BUG_RAG_SORT_ASC + BUG_RAG_NO_TIEBREAK：score 未按降序、并列未按 doc_id", strict=True
)
def test_scores_should_be_descending(client):
    scores = [h["score"] for h in hits_of(client, "search quality metrics")]
    assert scores == sorted(scores, reverse=True)


def test_scores_are_currently_ascending(client):
    # 钉住当前行为：分数升序返回（实测 1.153394, 1.329336, 1.529001, 1.529001）
    scores = [h["score"] for h in hits_of(client, "search quality metrics")]
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_NO_TIEBREAK：并列分数未按 doc_id 稳定排序", strict=True)
def test_tie_should_break_by_doc_id(client):
    ids = [h["doc_id"] for h in hits_of(client, "duplicate")]
    assert ids == ["DOC-TIE-001", "DOC-TIE-002"]


def test_tie_order_is_currently_unstable(client):
    # 钉住当前行为：DOC-TIE-002 排在 DOC-TIE-001 之前，与 doc_id 升序相反
    assert [h["doc_id"] for h in hits_of(client, "duplicate")] == ["DOC-TIE-002", "DOC-TIE-001"]


def test_buggy_tie_order_breaks_reproducibility(client):
    # 排序缺陷同时破坏「多次查询结果一致」：分数相同却无稳定 tie-break，
    # 这里是确定性复现（非随机），用于说明缺陷的可观测后果
    first = [h["doc_id"] for h in hits_of(client, "duplicate")]
    second = [h["doc_id"] for h in hits_of(client, "duplicate")]
    assert first == second  # 缺陷实现下恰好稳定，但次序错误
    assert first != sorted(first)  # 稳定 ≠ 正确


# ---------- 缺陷 3：重复命中（去重缺失）----------


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_DUPLICATE_HITS：多 term 命中同一文档未聚合", strict=True)
def test_hits_should_not_contain_duplicates(client):
    ids = [h["doc_id"] for h in hits_of(client, "vector database")]
    assert len(ids) == len(set(ids))


def test_duplicate_hits_currently_reproduce(client):
    # 钉住当前行为：两个 term 各产生一条命中，同一文档出现两次
    ids = [h["doc_id"] for h in hits_of(client, "vector database")]
    assert ids == ["DOC-EN-001", "DOC-EN-001"]
    assert [h["rank"] for h in hits_of(client, "vector database")] == [1, 2]


# ---------- 缺陷 4：陈旧索引（删除后仍被召回）----------


def test_deleted_document_is_currently_still_recalled(client):
    # 钉住当前行为：语料已删除，索引未同步 → ghost hit
    client.post("/api/rag/docs", json={"doc_id": "DOC-GHOST-001", "text": "ghost probe content zeta"})
    assert "DOC-GHOST-001" in [h["doc_id"] for h in hits_of(client, "ghost probe content zeta")]
    assert client.delete("/api/rag/docs/DOC-GHOST-001").status_code == 200
    assert "DOC-GHOST-001" not in client.get("/api/rag/index").get_json()["doc_ids"]
    ghost_hits = [h for h in hits_of(client, "ghost probe content zeta") if h["doc_id"] == "DOC-GHOST-001"]
    assert ghost_hits, "当前实现存在陈旧索引缺陷：删除后仍被召回"


@pytest.mark.xfail(reason="注入缺陷 BUG_RAG_STALE_INDEX：删除文档后倒排索引未同步", strict=True)
def test_deleted_document_should_not_be_recalled(client):
    client.post("/api/rag/docs", json={"doc_id": "DOC-GHOST-002", "text": "ghost probe content omega"})
    client.delete("/api/rag/docs/DOC-GHOST-002")
    assert "DOC-GHOST-002" not in [h["doc_id"] for h in hits_of(client, "ghost probe content omega")]


# ---------- 对照组：关闭全部缺陷开关后，同一批断言应全绿 ----------


def test_correct_implementation_passes_all_rag_expectations():
    """正确实现的对照：把 RAG_BUGS 全部关掉，前面所有 xfail 期望都应成立。"""
    service = RagService(bugs=CORRECT_BUGS)
    ids = lambda q: [h["doc_id"] for h in service.search(q, 5)]

    assert "DOC-CN-002" in ids("向量数据库")  # 中文召回
    assert ids("duplicate") == ["DOC-TIE-001", "DOC-TIE-002"]  # 并列稳定
    assert len(ids("vector database")) == len(set(ids("vector database")))  # 去重
    scores = [h["score"] for h in service.search("search quality metrics", 5)]
    assert scores == sorted(scores, reverse=True)  # 降序
    assert service.validate_search("", "5")[1]  # 空 q 报错
    assert service.validate_search("q", "0")[1]  # 非法 top_k 报错

    service.add_doc("DOC-GHOST-003", "ghost probe content sigma")
    service.remove_doc("DOC-GHOST-003")
    assert "DOC-GHOST-003" not in ids("ghost probe content sigma")  # 无 ghost hit
    assert service.index_size() == service.size()


def test_all_bug_switches_default_on():
    # 默认开启全部缺陷以复现问题；这是「缺陷注入」可复现性的前提
    assert set(RAG_BUGS) == {
        "cjk_tokenizer",
        "sort_asc",
        "no_tiebreak",
        "duplicate_hits",
        "stale_index",
        "loose_contract",
    }
    assert all(RAG_BUGS.values())
