"""接口执行工具：发起 HTTP 请求并返回结构化结果。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests


def _direct_session() -> requests.Session:
    """一个**不吃任何代理环境变量**的会话。

    这个工具打的是被测系统，绝大多数时候是 `127.0.0.1`。默认的 `requests`
    会读 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`，把发往本机的请求交给代理：

        代理在 → 代理回自己的 502，于是「连不上」被报成「服务回了 502」
        代理不在 → `ProxyError`，错误类型与真实故障对不上

    实测（2026-10-19，本机导出 `HTTP_PROXY=http://127.0.0.1:7897`）：
    `tests/test_api_tool.py::test_network_error_reported` 与
    `tests/test_rag_tool.py::test_rag_search_tool_reports_network_error`
    这两条**专门钉"网络错误被如实报告"的用例**在带代理时红、去掉代理即绿。

    这是最讽刺的一类失败：验证「错误被正确报告」的用例，被一个环境变量
    变成了「报告了一个不存在于被测系统的错误」。工具链自己的传输层必须先
    对环境无感，否则所有基于它的判断都只在这个 shell 里成立。
    """
    session = requests.Session()
    session.trust_env = False
    return session


def api_request(
    method: str,
    path: str,
    query: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    base_url: str = "http://127.0.0.1:5001",
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """执行一次接口调用。

    返回: {ok, status, body, time_ms, error}
    ok 仅表示调用本身成功（有 HTTP 响应），业务断言由测试用例负责。
    """
    url = path if path.startswith("http") else f"{base_url.rstrip('/')}{path}"
    start = time.perf_counter()
    try:
        with _direct_session() as session:
            resp = session.request(
                method=method.upper(),
                url=url,
                params=query or None,
                json=body,
                headers=headers or None,
                timeout=timeout,
            )
        elapsed = int((time.perf_counter() - start) * 1000)
        try:
            resp_body = resp.json()
        except Exception:
            resp_body = resp.text[:2000]
        return {
            "ok": True,
            "status": resp.status_code,
            "body": resp_body,
            "time_ms": elapsed,
            "error": None,
        }
    except requests.RequestException as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return {"ok": False, "status": 0, "body": None, "time_ms": elapsed, "error": str(exc)}
