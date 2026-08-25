"""api_tool 与真实被测服务的集成测试。"""
from __future__ import annotations

from agenttest.tools import api_request, fetch_openapi


def test_health_ok(demo_server_url):
    r = api_request("GET", "/api/health", base_url=demo_server_url)
    assert r["ok"] is True
    assert r["status"] == 200
    assert r["body"]["status"] == "ok"


def test_contract_served(demo_server_url):
    spec = fetch_openapi(demo_server_url)
    assert spec["info"]["title"] == "AgentTest Demo API"


def test_expected_contract_violations_detected(demo_server_url):
    # 契约要求 400，实现返回 500 —— 工具如实返回实际状态，由断言层判定失败
    r = api_request("GET", "/api/todos", query={"status": "invalid"}, base_url=demo_server_url)
    assert r["ok"] is True
    assert r["status"] == 500


def test_network_error_reported():
    r = api_request("GET", "/api/health", base_url="http://127.0.0.1:1")
    assert r["ok"] is False
    assert r["error"]
