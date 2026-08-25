"""被测系统（SUT）：AgentTest Demo —— 带缺陷的待办清单服务。

该应用故意注入 3 个缺陷，供 Agent 自动测试发现：
  BUG-API-001  GET /api/todos?status=invalid 返回 500（契约要求 400）
  BUG-API-002  POST /api/todos 空 title 返回 200 并创建脏数据（契约要求 400）
  FLAKY-001    GET /api/flaky 约 10% 概率返回 503（模拟不稳定服务，考验重试机制）

契约以 demo_app/openapi.json 为准，代码实现与契约的偏差就是 Agent 要发现的缺陷。
"""
from __future__ import annotations

import random
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template_string, request

APP_TITLE = "AgentTest Demo — 智能待办清单"
APP_VERSION = "1.0.0"

VALID_STATUS = {"active", "done"}
VALID_PRIORITY = {"low", "medium", "high"}

# 每个 id 的缺陷开关（便于测试精确控制）
BUG_EMPTY_TITLE = True
BUG_INVALID_STATUS_500 = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TodoStore:
    """线程安全的内存存储。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 3
        self._todos: Dict[int, Dict[str, Any]] = {
            1: {"id": 1, "title": "学习 LangGraph 状态图", "priority": "high", "status": "active", "created_at": _now()},
            2: {"id": 2, "title": "写 Agent 测试用例", "priority": "medium", "status": "done", "created_at": _now()},
        }

    def list(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._todos.values())
        if status:
            items = [t for t in items if t["status"] == status]
        return items

    def get(self, todo_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._todos.get(todo_id)

    def create(self, title: str, priority: str = "medium") -> Dict[str, Any]:
        with self._lock:
            todo_id = self._next_id
            self._next_id += 1
            todo = {"id": todo_id, "title": title, "priority": priority, "status": "active", "created_at": _now()}
            self._todos[todo_id] = todo
            return todo

    def update(self, todo_id: int, status: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            todo = self._todos.get(todo_id)
            if todo is None:
                return None
            todo["status"] = status
            return todo

    def delete(self, todo_id: int) -> bool:
        with self._lock:
            return self._todos.pop(todo_id, None) is not None

    def reset(self) -> None:
        with self._lock:
            self._todos.clear()
            self._next_id = 1


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
</head>
<body>
  <header><h1 data-testid="page-title">{{ title }}</h1></header>
  <main>
    <p data-testid="app-intro">这是一个供 Agent 自动测试演示用的待办清单应用。</p>
    <nav>
      <a href="/login" data-testid="nav-login">登录</a>
      <a href="/todos" data-testid="nav-todos">待办清单</a>
    </nav>
  </main>
  <footer data-testid="app-version">version {{ version }}</footer>
</body>
</html>
"""

LOGIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>登录</title></head>
<body>
  <h1 data-testid="login-title">用户登录</h1>
  <form action="/api/login" method="post" data-testid="login-form">
    <label>用户名 <input type="text" name="username" data-testid="input-username" required></label>
    <label>密码 <input type="password" name="password" data-testid="input-password" required></label>
    <button type="submit" data-testid="btn-submit">登录</button>
  </form>
</body>
</html>
"""

TODOS_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>待办清单</title></head>
<body>
  <h1 data-testid="todos-title">待办清单</h1>
  <ul data-testid="todo-list">
    {% for t in todos %}
    <li data-testid="todo-item" data-id="{{ t['id'] }}">{{ t['title'] }} <span class="status">{{ t['status'] }}</span></li>
    {% endfor %}
  </ul>
</body>
</html>
"""


def create_app() -> Flask:
    app = Flask(__name__)
    store = TodoStore()
    app.config["TODO_STORE"] = store

    # ---------- 契约 ----------
    @app.get("/openapi.json")
    def openapi_spec() -> Any:
        import json as _json
        from pathlib import Path as _Path

        spec_path = _Path(__file__).parent / "openapi.json"
        return _json.loads(spec_path.read_text(encoding="utf-8"))

    # ---------- API ----------
    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok", "version": APP_VERSION, "time": _now()})

    @app.post("/api/login")
    def login() -> Any:
        data = request.get_json(silent=True) or request.form.to_dict()
        username = (data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not username or not password:
            return jsonify({"error": "username and password are required"}), 400
        if password != "123456":
            return jsonify({"error": "invalid credentials"}), 401
        return jsonify({"token": "demo-token-123", "user": username}), 200

    @app.get("/api/todos")
    def list_todos() -> Any:
        status = request.args.get("status")
        if status is not None and status not in VALID_STATUS:
            if BUG_INVALID_STATUS_500:  # 注入缺陷 1：契约要求 400，实现返回 500
                return jsonify({"error": "internal error: unexpected status filter"}), 500
            return jsonify({"error": "invalid status"}), 400
        return jsonify({"data": store.list(status), "count": len(store.list(status))})

    @app.post("/api/todos")
    def create_todo() -> Any:
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        priority = (data.get("priority") or "medium").strip().lower()
        if priority not in VALID_PRIORITY:
            return jsonify({"error": "invalid priority"}), 400
        if not title:
            if BUG_EMPTY_TITLE:  # 注入缺陷 2：契约要求 400，实现返回 200 且创建脏数据
                todo = store.create(title or "(empty)", priority)
                return jsonify({"data": todo, "warning": "created with empty title"}), 200
            return jsonify({"error": "title is required"}), 400
        todo = store.create(title, priority)
        return jsonify({"data": todo}), 201

    @app.get("/api/todos/<int:todo_id>")
    def get_todo(todo_id: int) -> Any:
        todo = store.get(todo_id)
        if todo is None:
            return jsonify({"error": "todo not found"}), 404
        return jsonify({"data": todo})

    @app.patch("/api/todos/<int:todo_id>")
    def update_todo(todo_id: int) -> Any:
        data = request.get_json(silent=True) or {}
        status = (data.get("status") or "").strip()
        if status not in VALID_STATUS:
            return jsonify({"error": "invalid status"}), 400
        todo = store.update(todo_id, status)
        if todo is None:
            return jsonify({"error": "todo not found"}), 404
        return jsonify({"data": todo})

    @app.delete("/api/todos/<int:todo_id>")
    def delete_todo(todo_id: int) -> Any:
        if store.delete(todo_id):
            return "", 204
        return jsonify({"error": "todo not found"}), 404

    @app.get("/api/flaky")
    def flaky() -> Any:
        if random.random() < 0.1:  # 注入缺陷 3：不稳定服务
            return jsonify({"error": "service temporarily unavailable"}), 503
        return jsonify({"flaky": True, "ok": True})

    @app.get("/api/stats")
    def stats() -> Any:
        items = store.list()
        return jsonify({
            "total": len(items),
            "active": len([t for t in items if t["status"] == "active"]),
            "done": len([t for t in items if t["status"] == "done"]),
        })

    # ---------- Web 页面 ----------
    @app.get("/")
    def index() -> str:
        return render_template_string(INDEX_HTML, title=APP_TITLE, version=APP_VERSION)

    @app.get("/login")
    def login_page() -> str:
        return render_template_string(LOGIN_HTML)

    @app.get("/todos")
    def todos_page() -> str:
        return render_template_string(TODOS_HTML, todos=store.list())

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
