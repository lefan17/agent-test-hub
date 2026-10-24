"""条件边与回边（planner → executor →(条件)→ retrier → executor → critic → reporter）。

这组测试针对一个具体的历史缺陷：`build.py` 曾经只有四条 `add_edge` 硬连，
没有任何条件分支与回边，而 `docs/面试QA.md` 却写着「LangGraph 提供条件跳转与
断点/重放」。文档与代码不一致是能被面试官 30 秒揭穿的硬伤。

**这里测的是图本身的行为，不是节点的行为**——节点单测在 test_checks.py 等文件里。
所以这些用例会直接调 `build_graph(...).invoke(state)`，然后从 trace 里数
`[retrier]` 出现了几次、executor 跑了几轮，来判断条件边和回边真的生效了。

为了让图行为测试跑得快且确定，涉及 invoke 的用例把 `_run_api_case` 与
`_run_rag_case` 一起打桩成立即返回（不真的发网络请求，避免对不可达端口的
连接超时把 8 条用例拖成 5 分钟）——测的是「图怎么走」，不是「HTTP 怎么发」。
"""

from __future__ import annotations

import pytest

from agenttest.brain import MockBrain
from agenttest.config import Config
from agenttest.graph import build_graph
from agenttest.graph.build import RETRYABLE_STATUSES, _retry_candidates
from agenttest.models import TestResult


@pytest.fixture
def stub_network(monkeypatch):
    """把 api / rag 两个执行器全部打桩成「立即返回 503」。

    只测图结构，不测网络。没有这个 fixture，RAG 用例会连不可达端口，
    每条都要等连接超时，8 条用例能跑成 5 分钟。
    """
    from agenttest.graph import nodes as nodes_mod

    def boom(*args, **kwargs):
        return {"ok": False, "status": 503, "actual": "503", "error": "stubbed"}

    monkeypatch.setattr(nodes_mod, "_run_api_case", boom)
    monkeypatch.setattr(nodes_mod, "_run_rag_case", boom)
    return boom


def _state(extra=None):
    ctx = {
        "base_url": "http://127.0.0.1:1",
        "report_dir": None,
        "skip_ui": True,
        "max_attempts": 1,
    }
    if extra:
        ctx.update(extra)
    return {
        "goal": "retry edge test",
        "spec": {},
        "cases": [],
        "results": [],
        "defects": [],
        "coverage": {},
        "trace": [],
        "started_at": "test",
        "context": ctx,
        "report": {},
    }


def _mk(case_id: str, ok: bool, status: int, invalid: bool = False) -> TestResult:
    return TestResult(
        case_id=case_id,
        title=case_id,
        ok=ok,
        actual_status=status,
        invalid_case=invalid,
    )


# ---------- 纯函数：哪些失败值得补跑 ----------


def test_retry_candidates_only_takes_5xx_and_no_response():
    """业务 4xx 不补跑：那是确定性行为，重跑只会拖慢流水线。"""
    results = [
        _mk("A", ok=True, status=200),
        _mk("B", ok=False, status=503),  # 值得补跑
        _mk("C", ok=False, status=0),  # 无响应，值得补跑
        _mk("D", ok=False, status=400),  # 业务 4xx，不补跑
        _mk("E", ok=False, status=401),  # 业务 4xx，不补跑
    ]
    assert [r.case_id for r in _retry_candidates(results)] == ["B", "C"]


def test_retry_candidates_skips_invalid_case():
    """用例自己写错的（invalid_case）不补跑：再跑一遍它还是错的。

    这条守的是一个具体的假阳性：把「用例写错」当成「服务端抖动」去重试，
    既白烧一轮，又会把 invalid_case 混进 retrier 的 trace，误导归因。
    """
    results = [
        _mk("B", ok=False, status=503),
        _mk("X", ok=False, status=503, invalid=True),
    ]
    assert [r.case_id for r in _retry_candidates(results)] == ["B"]


def test_retryable_statuses_matches_node_level_retry():
    """图级回边的可重试集必须与节点级 `_retryable` 一致，否则两套口径会分叉。"""
    from agenttest.graph.nodes import _retryable

    assert all(_retryable(s) for s in RETRYABLE_STATUSES)
    assert _retryable(0) and _retryable(500) and _retryable(502)
    assert not _retryable(404) and not _retryable(400)


# ---------- 图结构：条件边与回边存在吗 ----------


def test_graph_has_conditional_edge_and_back_edge():
    """直接检查编译后的图：必须存在条件分支，且 retrier -> executor 这条回边存在。

    这条是「文档与代码一致」的机器化守卫：任何人把条件边改回单条 `add_edge`，
    这个测试就会红。
    """
    cfg = Config(retry_rounds=1)
    compiled = build_graph(MockBrain(), cfg)
    graph = compiled.get_graph()

    assert {"planner", "executor", "retrier", "critic", "reporter"} <= set(graph.nodes)

    edges = {(e.source, e.target) for e in graph.edges}
    assert ("retrier", "executor") in edges, "回边缺失：retrier 必须连回 executor"
    assert ("planner", "executor") in edges
    assert ("critic", "reporter") in edges

    # executor 出边不止一条 → 条件分支的物证（走哪个由 route 函数运行时决定）
    out_of_executor = {t for s, t in edges if s == "executor"}
    assert out_of_executor == {"retrier", "critic"}, (
        f"executor 之后必须是条件分支到 retrier/critic，实际 {out_of_executor}"
    )


# ---------- 图行为：回边真的会被触发、且不会死循环 ----------


def test_retry_rounds_zero_disables_back_edge(tmp_path, stub_network):
    """retry_rounds=0 时条件边永远走 critic：回边存在但不会被触发。"""
    cfg = Config(retry_rounds=0, report_dir=tmp_path / "reports", max_attempts=1)
    compiled = build_graph(MockBrain(), cfg)
    final = compiled.invoke(_state())

    retrier_lines = [t for t in final["trace"] if t.startswith("[retrier]")]
    assert retrier_lines == [], "retry_rounds=0 时不应该补跑"
    assert final["report"], "流程必须走到 reporter"


def test_back_edge_triggers_when_retryable_failure_present(tmp_path, stub_network):
    """有可重试失败时，回边真的把流程拉回 executor 再跑一轮。

    打桩让每轮都产生 503（可重试），于是必然触发回边——测的是图，不是网络。
    """
    cfg = Config(retry_rounds=1, report_dir=tmp_path / "reports", max_attempts=1)
    compiled = build_graph(MockBrain(), cfg)
    final = compiled.invoke(_state())

    retrier_lines = [t for t in final["trace"] if t.startswith("[retrier]")]
    assert len(retrier_lines) == 1, f"应当补跑且只补跑一轮，实际 {retrier_lines}"
    assert "第 1 轮补跑" in retrier_lines[0]

    # 补跑时 executor 的 trace 里应当出现「补跑 N 条」而不是全量
    assert any("补跑" in t and "不重跑全量" in t for t in final["trace"] if t.startswith("[executor]")), (
        "补跑模式必须只跑失败的用例，不能重跑全量"
    )


def test_back_edge_does_not_loop_forever(tmp_path, stub_network):
    """补跑清单必须被消费掉，否则会一直原地打转（这是最容易写出的死循环）。"""
    cfg = Config(retry_rounds=2, report_dir=tmp_path / "reports", max_attempts=1)
    compiled = build_graph(MockBrain(), cfg)
    final = compiled.invoke(_state())

    retrier_lines = [t for t in final["trace"] if t.startswith("[retrier]")]
    assert len(retrier_lines) <= cfg.retry_rounds, f"补跑次数 {len(retrier_lines)} 超过 retry_rounds={cfg.retry_rounds}"
    assert final["report"], "流程必须走到 reporter 产出报告"


def test_retry_recovers_when_second_round_passes(tmp_path, monkeypatch):
    """第一轮 503、第二轮 200：补跑把这条用例救回来，最终不产生缺陷。

    这是回边的**业务价值**证明——不是为了「图里有个环」，而是为了
    让概率性失败（如 /api/flaky）不被误判成缺陷。
    """
    from agenttest.graph import nodes as nodes_mod

    rounds = {"n": 0}

    def fake_api(case, base_url):
        # 只让「登录接口」在第二轮变绿，其余照旧失败：
        # 既验证救援，又保证仍有其它可重试失败，不会因救援而跳过回边逻辑。
        if case.id == "API-002":
            rounds["n"] += 1
            ok = rounds["n"] > 1
            return {
                "ok": ok,
                "status": 200 if ok else 503,
                "actual": "200" if ok else "503",
                "error": "" if ok else "boom",
            }
        return {"ok": False, "status": 503, "actual": "503", "error": "boom"}

    def fake_rag(*args, **kwargs):
        return {"ok": False, "status": 503, "actual": "503", "error": "boom"}

    monkeypatch.setattr(nodes_mod, "_run_api_case", fake_api)
    monkeypatch.setattr(nodes_mod, "_run_rag_case", fake_rag)

    cfg = Config(retry_rounds=1, report_dir=tmp_path / "reports", max_attempts=1)
    compiled = build_graph(MockBrain(), cfg)
    final = compiled.invoke(_state())

    target = [r for r in final["results"] if r.case_id == "API-002"][0]
    assert target.ok, "第二轮已通过，API-002 必须被救回为 PASS（不能被记成缺陷）"
