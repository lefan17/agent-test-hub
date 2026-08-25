"""接口执行工具：发起 HTTP 请求并返回结构化结果。"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests


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
        resp = requests.request(
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
