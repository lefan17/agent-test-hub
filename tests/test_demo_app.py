"""被测系统（SUT）自身的功能测试 —— 同时记录其已知缺陷，作为 Agent 应发现缺陷的基准。"""
from __future__ import annotations

import pytest

from demo_app.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_login_ok(client):
    r = client.post("/api/login", json={"username": "admin", "password": "123456"})
    assert r.status_code == 200
    assert r.get_json()["token"]


def test_login_wrong_password(client):
    r = client.post("/api/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_login_missing_fields(client):
    r = client.post("/api/login", json={})
    assert r.status_code == 400


def test_list_todos(client):
    r = client.get("/api/todos")
    assert r.status_code == 200
    assert r.get_json()["count"] >= 2


@pytest.mark.xfail(reason="注入缺陷 BUG-API-001：非法 status 应返回 400，实际 500", strict=True)
def test_invalid_status_should_be_400(client):
    r = client.get("/api/todos?status=invalid")
    assert r.status_code == 400


@pytest.mark.xfail(reason="注入缺陷 BUG-API-002：空 title 应返回 400，实际 200 并写入脏数据", strict=True)
def test_empty_title_should_be_400(client):
    r = client.post("/api/todos", json={"title": "", "priority": "medium"})
    assert r.status_code == 400


def test_create_todo(client):
    r = client.post("/api/todos", json={"title": "买牛奶", "priority": "low"})
    assert r.status_code == 201
    assert r.get_json()["data"]["title"] == "买牛奶"


def test_get_missing_todo_404(client):
    r = client.get("/api/todos/99999")
    assert r.status_code == 404


def test_patch_invalid_status_400(client):
    r = client.patch("/api/todos/1", json={"status": "bogus"})
    assert r.status_code == 400


def test_delete_missing_404(client):
    r = client.delete("/api/todos/99999")
    assert r.status_code == 404


def test_openapi_contract_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.get_json()
    assert spec["openapi"].startswith("3.")
    assert "/api/todos" in spec["paths"]


def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AgentTest Demo" in r.get_data(as_text=True)


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert 'data-testid="login-form"' in r.get_data(as_text=True)


def test_todos_page_renders(client):
    r = client.get("/todos")
    assert 'data-testid="todo-item"' in r.get_data(as_text=True)
