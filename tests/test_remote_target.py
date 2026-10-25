"""第三方契约目标链路的单元测试。

## 这个文件在钉什么

「对陌生契约跑一遍」这件事的风险不在 Agent 的规划能力，而在**目标链路**：
被测系统从「本机自己起的 demo」换成「公网上的陌生服务」，中间有三处会静默出错：

1. `api_request` / `fetch_openapi` 一直是 `trust_env=False`（为了不被代理劫持），
   于是公网目标必然连不上 —— 修法是**显式**传代理，而不是打开环境变量继承；
2. `runner` 的健康检查打的是 `/api/health`，远端服务没有这个端点，
   原逻辑会判定「服务没起来」，然后**原地起一个本地 demo 继续测** ——
   流程全绿，被测对象却是自己，这是最糟的一种假执行；
3. 用例里的路径参数（`/pet/{petId}`）如果不被替换成具体值，
   执行器按字面量发请求只会 404，而 404 会被归因成「契约不符」——
   一个自己造成的假缺陷。

所以这里的断言全部围绕「别把失败伪装成成功 / 别把自伤算成被测缺陷」。
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from agenttest.config import Config
from agenttest.runner import build_context, is_local_target
from agenttest.tools.api_tool import api_request
from agenttest.tools.spec_tool import fetch_openapi

SPEC = {
    "openapi": "3.0.2",
    "info": {"title": "Swagger Petstore - OpenAPI 3.0", "version": "1.0.27"},
    "paths": {
        "/pet/{petId}": {"get": {"responses": {"200": {}, "404": {}}}},
        "/pet": {"post": {"responses": {"200": {}, "405": {}}}},
    },
}


class _FakeResponse:
    def __init__(self, payload: Any = None, status: int = 200) -> None:
        self._payload = payload if payload is not None else SPEC
        self.status_code = status
        self.content = b"{}"

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """记录 `proxies` 与最后一次 `get` 的 URL，避免真的出网。"""

    last_url = ""
    last_proxies: Dict[str, str] = {}

    def __init__(self) -> None:
        self.trust_env = True
        self.proxies: Dict[str, str] = {}

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(self, url: str, timeout: float = 0) -> _FakeResponse:
        _FakeSession.last_url = url
        _FakeSession.last_proxies = dict(self.proxies)
        return _FakeResponse()

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        _FakeSession.last_url = url
        _FakeSession.last_proxies = dict(self.proxies)
        return _FakeResponse({"ok": True})


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch) -> type:
    import agenttest.tools.api_tool as api_tool
    import agenttest.tools.spec_tool as spec_tool

    monkeypatch.setattr(api_tool.requests, "Session", _FakeSession)
    monkeypatch.setattr(spec_tool.requests, "Session", _FakeSession)
    _FakeSession.last_url = ""
    _FakeSession.last_proxies = {}
    return _FakeSession


class TestRemoteTargetDetection:
    @pytest.mark.parametrize("url", ["http://127.0.0.1:5001", "http://localhost:8080", "http://0.0.0.0:9000"])
    def test_local_targets_are_recognised(self, url: str):
        assert is_local_target(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://petstore3.swagger.io/api/v3",
            "https://api.example.com",
            "http://192.168.1.20:8080",
        ],
    )
    def test_remote_targets_are_not_mistaken_for_local(self, url: str):
        """写成「是不是 http:// 开头」也能歪打正着，但内网 IP 会被判成本机，
        于是流程会去起一个本地服务并把真正的远端目标丢掉。所以按 hostname 判。"""
        assert is_local_target(url) is False

    def test_bare_host_without_scheme_is_parsed(self):
        assert is_local_target("localhost:5001") is True


class TestProxyIsExplicit:
    def test_api_request_without_proxy_keeps_trust_env_off(self, fake_session):
        api_request("GET", "/pet/1", base_url="https://petstore3.swagger.io/api/v3")
        assert fake_session.last_proxies == {}

    def test_api_request_passes_the_proxy_it_was_given(self, fake_session):
        api_request(
            "GET",
            "/pet/1",
            base_url="https://petstore3.swagger.io/api/v3",
            proxy="http://127.0.0.1:7897",
        )
        assert fake_session.last_proxies == {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}

    def test_fetch_openapi_defaults_to_the_conventional_path(self, fake_session):
        fetch_openapi("https://petstore3.swagger.io/api/v3")
        assert fake_session.last_url == "https://petstore3.swagger.io/api/v3/openapi.json"

    def test_fetch_openapi_honours_an_explicit_spec_url(self, fake_session):
        fetch_openapi(
            "https://api.example.com",
            spec_url="https://api.example.com/v1/contract.json",
        )
        assert fake_session.last_url == "https://api.example.com/v1/contract.json"

    def test_fetch_openapi_returns_the_parsed_spec(self, fake_session):
        spec = fetch_openapi("https://petstore3.swagger.io/api/v3")
        assert spec["info"]["title"].startswith("Swagger Petstore")


class TestContextCarriesTheProxy:
    def test_build_context_exposes_the_demo_proxy(self):
        cfg = Config()
        cfg.demo_url = "https://petstore3.swagger.io/api/v3"
        cfg.demo_proxy = "http://127.0.0.1:7897"
        ctx = build_context(cfg, skip_ui=True)
        assert ctx["base_url"] == "https://petstore3.swagger.io/api/v3"
        assert ctx["base_url_proxy"] == "http://127.0.0.1:7897"
        assert ctx["skip_ui"] is True

    def test_context_without_proxy_is_empty_string_not_none(self):
        """`ctx.get("base_url_proxy", "")` 在 context 里存 None 时会返回 None，
        再传给 requests 的 proxies 就是 `{'http': None}` —— 静默变成"用默认值"。
        这条钉住它是空串，别的不行。"""
        ctx = build_context(Config(), skip_ui=False)
        assert ctx["base_url_proxy"] == ""
