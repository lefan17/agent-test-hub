"""契约读取工具：从被测系统获取 OpenAPI 规范并生成摘要。"""
from __future__ import annotations

from typing import Any, Dict, List

import requests


def fetch_openapi(base_url: str = "http://127.0.0.1:5001", timeout: float = 10.0) -> Dict[str, Any]:
    """GET {base_url}/openapi.json，返回规范字典。"""
    resp = requests.get(f"{base_url.rstrip('/')}/openapi.json", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


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
            if req:
                params.append("requestBody")
            line = f"  {method.upper():6s} {path}  -> {codes}"
            if params:
                line += f"  params=[{', '.join(params)}]"
            if summary:
                line += f"  ({summary})"
            lines.append(line)
    return "\n".join(lines)
