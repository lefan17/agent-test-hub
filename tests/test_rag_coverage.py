"""coverage 端点映射回归：声明表必须与 rag_tool 实际打的端点一致。

背景（审计 item 12）：`_coverage_key` 曾把 10 条 RAG 用例一律记成同一个
`RAG GET /api/rag/search`。报告里于是显示 /api/rag/index、/api/rag/reindex、
POST /api/rag/docs、DELETE /api/rag/docs/{doc_id} 「没有任何用例覆盖」，
而真实打这些端点的正是索引一致性探针用例 RAG-006 —— coverage 不是偏小，
而是把有覆盖的端点报成没覆盖，方向性错误。

修法是把 RAG 键按用例实际会打的端点展开（声明式多端点映射）。声明的代价是
可能与实现脱节，因此下面用**真实调用记录**当唯一真值来源来核对它：
`_run_rag_case` 跑一遍，拦截 rag_tool 发出的每次 HTTP 请求，把 (method, path)
去重成集合，与声明表逐字比对。谁给 rag_tool 加了端点调用而没同步声明表，
这条测试就变红。
"""
from __future__ import annotations

import pytest

from agenttest.brain import MockBrain
from agenttest.graph.nodes import (
    RAG_CONSISTENCY_ENDPOINTS,
    RAG_SEARCH_ENDPOINTS,
    _coverage_keys,
    _run_rag_case,
    _uses_consistency_probe,
)
from agenttest.tools import rag_tool

PROBE_DOC_PLACEHOLDER = "{doc_id}"


def _rag_case(case_id: str):
    cases = MockBrain().make_plan("t", {}, {"base_url": "http://x:1"})
    return next(c for c in cases if c.id == case_id)


def _record_endpoints(monkeypatch) -> list:
    """拦截 rag_tool 的 HTTP 出口，记录 (method, path)。"""
    calls: list = []
    real = rag_tool.api_request

    def spy(method, path, **kwargs):
        calls.append((method.upper(), path))
        return real(method, path, **kwargs)

    monkeypatch.setattr(rag_tool, "api_request", spy)
    return calls


def _observed(calls) -> set:
    """把探针文档 id 归一化掉，得到「端点模板」集合。"""
    return {(method, path.replace(rag_tool.RAG_PROBE_DOC_ID, PROBE_DOC_PLACEHOLDER))
            for method, path in calls}


def test_coverage_declaration_matches_endpoints_actually_called(demo_server_url, monkeypatch):
    """核心：声明表 == rag_tool 真打过的端点集合（两个分支都核）。"""
    normal_calls = _record_endpoints(monkeypatch)
    _run_rag_case(_rag_case("RAG-001"), demo_server_url)
    monkeypatch.undo()

    probe_calls = _record_endpoints(monkeypatch)
    _run_rag_case(_rag_case("RAG-006"), demo_server_url)
    monkeypatch.undo()

    observed_normal = _observed(normal_calls)
    observed_probe = _observed(probe_calls)
    declared_normal = {tuple(key.split(" ", 1)) for key in RAG_SEARCH_ENDPOINTS}
    declared_probe = {tuple(key.split(" ", 1)) for key in RAG_CONSISTENCY_ENDPOINTS}

    assert observed_normal == declared_normal, (
        f"普通检索用例的实际调用 {sorted(observed_normal)} "
        f"与声明 {sorted(declared_normal)} 不一致——声明表跟不上 rag_tool 了")
    assert observed_probe == declared_probe, (
        f"一致性探针的实际调用 {sorted(observed_probe)} "
        f"与声明 {sorted(declared_probe)} 不一致——声明表跟不上 rag_tool 了")


def test_consistency_probe_case_really_calls_all_five_endpoints(demo_server_url):
    """RAG-006 不是「多声明了几个端点」：它真的打了 5 个不同端点。"""
    case = _rag_case("RAG-006")
    assert _uses_consistency_probe(case)
    assert len(set(RAG_CONSISTENCY_ENDPOINTS)) == 5

    detail = _run_rag_case(case, demo_server_url)

    assert detail["ok"] is False, "注入的陈旧索引缺陷下，一致性用例本该失败（这是它的职责）"


def test_coverage_keys_expand_rag_cases_instead_of_collapsing_them():
    """改前的假绿：10 条 RAG 用例共用 1 个 key；改后必须展开且端点名实相符。"""
    cases = MockBrain().make_plan("t", {}, {"base_url": "http://x:1"})
    rag_cases = [c for c in cases if c.kind == "rag"]
    assert len(rag_cases) == 10

    keys = [key for case in rag_cases for key in _coverage_keys(case)]
    unique = set(keys)

    assert unique != {"RAG GET /api/rag/search"}, "10 条 RAG 用例又塌回同一个 key 了"
    assert unique == set(RAG_CONSISTENCY_ENDPOINTS) | set(RAG_SEARCH_ENDPOINTS)

    # 曾经被报成「无人覆盖」的四个端点，现在必须挂着真正打它们的用例
    # （/api/rag/search 两个分支都打，故排除在「探针独有」之外）
    probe_ids = {case.id for case in rag_cases if _uses_consistency_probe(case)}
    assert probe_ids == {"RAG-006"}
    probe_only = set(RAG_CONSISTENCY_ENDPOINTS) - set(RAG_SEARCH_ENDPOINTS)
    assert len(probe_only) == 4
    for key in probe_only:
        covered = {case.id for case in rag_cases if key in _coverage_keys(case)}
        assert covered == probe_ids, f"{key} 应只由一致性探针用例覆盖，实际 {covered}"

    search_covered = {case.id for case in rag_cases if f"GET {rag_tool.SEARCH_PATH}" in _coverage_keys(case)}
    assert search_covered == {c.id for c in rag_cases}, "每条 RAG 用例都要打 /api/rag/search"


def test_api_and_ui_coverage_keys_unchanged():
    """改动只应影响 RAG 分支，API/UI 键的语义不许被顺手改掉。"""
    cases = MockBrain().make_plan("t", {}, {"base_url": "http://x:1"})
    api = next(c for c in cases if c.id == "API-006")
    ui = next(c for c in cases if c.kind == "ui")

    assert _coverage_keys(api) == ["GET /api/todos"]
    assert _coverage_keys(ui) == [f"UI {ui.url}"]


@pytest.mark.parametrize("case_id", ["RAG-007", "RAG-008", "RAG-009", "RAG-010"])
def test_contract_rag_cases_count_as_search_coverage(case_id):
    """契约用例（空 q / 非法 top_k）打的是同一个 search 端点，不另立键。"""
    case = _rag_case(case_id)
    assert _coverage_keys(case) == list(RAG_SEARCH_ENDPOINTS)
