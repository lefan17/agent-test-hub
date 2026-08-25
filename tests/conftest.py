"""pytest 共享夹具：被测系统真实 HTTP 服务（会话级）。"""
from __future__ import annotations

import threading

import pytest
from werkzeug.serving import make_server

from demo_app.app import create_app


@pytest.fixture(scope="session")
def demo_server_url():
    """启动真实 Flask 服务（随机端口），供 requests 级测试与端到端测试使用。"""
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    yield url
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def chrome_available():
    from agenttest.config import Config

    return bool(Config()._find_chrome())
