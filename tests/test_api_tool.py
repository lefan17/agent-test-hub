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


class _FakeProxy:
    """一个**会回 502 的**假代理。

    为什么不能用"指向死端口的代理"来测：那种情况下 requests 抛 `ProxyError`，
    新旧实现都会得到 `ok is False`，于是测试两边都绿，等于什么都没钉住
    （我第一版就写成了这样，跑在修复前的代码上照样通过）。

    真实的故障形态是**代理活着并回了响应**：代理自己的 502 被 `api_request`
    当成「被测服务回了 502」→ `ok is True` → 「连不上」这个事实被静默改写。
    所以假代理必须真的监听、真的回一个状态码。
    """

    def __init__(self):
        import http.server
        import threading

        class _H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"proxy says upstream down")

            do_POST = do_GET

            def log_message(self, *_a):
                pass

        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
        self.url = f"http://127.0.0.1:{self._srv.server_port}"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *_exc):
        self._srv.shutdown()
        self._srv.server_close()


def test_proxy_env_does_not_hijack_loopback_requests(monkeypatch):
    """本机开着 HTTP(S)_PROXY 时，打 127.0.0.1 的请求也不许交给代理。

    这条是 2026-10-19 那个真实故障的回归钉子。当时导出了
    `HTTP_PROXY=http://127.0.0.1:7897`，于是 `test_network_error_reported`
    （连 127.0.0.1:1，必然拒绝连接）红了：请求被交给代理，代理回自己的 502，
    工具如实报告「服务回了 502」，而用例期望的是「连不上」。

    判据：被测地址 `127.0.0.1:2` 必然拒绝连接。直连 → `ok is False`；
    被代理接管 → 拿到代理的 502 → `ok is True` 且 `status == 502`。
    只有前者是对的。
    """
    with _FakeProxy() as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.url)
        monkeypatch.setenv("HTTPS_PROXY", proxy.url)
        monkeypatch.setenv("ALL_PROXY", proxy.url)

        r = api_request("GET", "/api/health", base_url="http://127.0.0.1:2", timeout=2.0)

    assert r["ok"] is False, f"请求被代理接管了（拿到代理的响应当成被测服务的）：{r}"
    assert r["status"] == 0


def test_proxy_env_does_not_break_openapi_fetch(monkeypatch, demo_server_url):
    """同上，但针对 `fetch_openapi`：读本机契约不能经代理。"""
    with _FakeProxy() as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.url)
        monkeypatch.setenv("HTTPS_PROXY", proxy.url)

        spec = fetch_openapi(demo_server_url)
    assert spec["info"]["title"] == "AgentTest Demo API"
