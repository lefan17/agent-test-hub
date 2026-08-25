"""端到端：MockBrain + LangGraph 全流程，必须发现注入缺陷并产出报告。"""
from __future__ import annotations

import json
from pathlib import Path

from agenttest.brain import MockBrain
from agenttest.config import Config
from agenttest.graph import build_graph
from agenttest.tools import fetch_openapi


def test_e2e_mock_brain_finds_bugs(demo_server_url, tmp_path):
    cfg = Config(demo_url=demo_server_url, report_dir=tmp_path)
    spec = fetch_openapi(demo_server_url)
    brain = MockBrain()
    graph = build_graph(brain, cfg)

    state = {
        "goal": "e2e test",
        "spec": spec,
        "cases": [],
        "results": [],
        "defects": [],
        "coverage": {},
        "trace": [],
        "started_at": "test",
        "context": {
            "base_url": demo_server_url,
            "report_dir": tmp_path,
            "ui_backend": cfg.effective_ui_backend,
            "chrome_path": cfg.chrome_path,
            "skip_ui": False,
            "max_attempts": 3,
        },
        "report": {},
    }
    final = graph.invoke(state)

    assert final["report"]["json"].endswith("report.json")
    assert final["report"]["html"].endswith("report.html")

    data = json.loads(Path(final["report"]["json"]).read_text(encoding="utf-8"))
    assert data["summary"]["total"] >= 15
    assert data["summary"]["failed"] >= 2

    categories = {d["category"] for d in data["defects"]}
    assert "contract" in categories   # BUG-API-001
    assert "bug" in categories        # BUG-API-002

    html = Path(final["report"]["html"]).read_text(encoding="utf-8")
    assert "Agent 自动测试报告" in html
