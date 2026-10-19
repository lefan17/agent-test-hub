"""契约读取工具：从被测系统获取 OpenAPI 规范并生成摘要。"""

from __future__ import annotations

from typing import Any, Dict, List

import requests


def fetch_openapi(base_url: str = "http://127.0.0.1:5001", timeout: float = 10.0) -> Dict[str, Any]:
    """GET {base_url}/openapi.json，返回规范字典。

    `trust_env = False` 的理由与 `api_tool._direct_session` 相同：读契约打的是
    本机被测服务，经代理绕一圈拿回来的可能是代理的错误页，而 `raise_for_status()`
    会把它当成「被测服务挂了」。
    """
    with requests.Session() as session:
        session.trust_env = False
        resp = session.get(f"{base_url.rstrip('/')}/openapi.json", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _describe_request_body(req: Dict[str, Any]) -> str:
    """把 requestBody 压缩成 `{prop:type, ...}` 加示例值。

    只写 `requestBody` 三个字是不够的：实测真 LLM 生成的登录用例会把
    password 填成 `"password"` 字面量，拿到 401 后归因出一个**根本不存在的**
    「鉴权链路异常」缺陷。schema 里有什么字段、example 用什么值，是判断
    「接口坏了」还是「我不知道正确的输入」的唯一依据，必须交给它。
    """
    for media in req.get("content", {}).values():
        schema = media.get("schema") or {}
        props = schema.get("properties") or {}
        if not props:
            return ""
        parts = []
        for name, meta in props.items():
            kind = (meta or {}).get("type", "any")
            parts.append(f"{name}:{kind}")
        text = "{" + ", ".join(parts) + "}"
        example = media.get("example") or schema.get("example")
        if isinstance(example, dict) and example:
            pairs = ", ".join(f'"{k}":"{v}"' for k, v in example.items())
            text += " 示例=" + "{" + pairs + "}"
        return text
    return ""


def summarize_spec(spec: Dict[str, Any]) -> str:
    """把 OpenAPI 规范压缩成给 LLM 看的文本摘要。"""
    lines: List[str] = []
    info = spec.get("info", {})
    lines.append(f"API: {info.get('title', '')} v{info.get('version', '')}")
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            summary = op.get("summary", "")
            codes = ", ".join(sorted(op.get("responses", {}).keys()))
            params = []
            for p in op.get("parameters", []):
                params.append(f"{p.get('in')}:{p.get('name')}")
            req = op.get("requestBody")
            body = _describe_request_body(req) if isinstance(req, dict) else ""
            if req:
                params.append(f"requestBody{body}" if body else "requestBody")
            line = f"  {method.upper():6s} {path}  -> {codes}"
            if params:
                line += f"  params=[{', '.join(params)}]"
            if summary:
                line += f"  ({summary})"
            lines.append(line)
    return "\n".join(lines)
