"""命令行入口：一键启动被测系统并运行 Agent 自动测试。

用法：
  python -m agenttest run                 # 默认：MockBrain，API + UI 全量
  python -m agenttest run --force-llm     # 强制使用真实 LLM（需配置 Key）
  python -m agenttest run --no-ui         # 只跑 API 用例
  python -m agenttest run --keep-server   # 跑完保留被测系统，方便手动浏览
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from werkzeug.serving import make_server

from agenttest.brain import LLMBrain, MockBrain
from agenttest.config import Config
from agenttest.graph import build_graph
from agenttest.tools.spec_tool import fetch_openapi


class DemoServer:
    """在后台线程中启动被测 Flask 应用。"""

    def __init__(self, url: str) -> None:
        from demo_app.app import create_app

        self.url = url.rstrip("/")
        port = int(url.rsplit(":", 1)[-1])
        self._server = make_server("127.0.0.1", port, create_app(), threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)


def wait_for_health(url: str, timeout: float = 30.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{url}/api/health", timeout=2)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.3)
    return False


def pick_brain(cfg: Config, force_llm: bool):
    if force_llm or cfg.llm_configured:
        try:
            return LLMBrain(cfg)
        except ValueError as exc:
            if force_llm:
                print(f"[runner] 无法启用 LLM：{exc}")
                sys.exit(1)
            print(f"[runner] LLM 未配置（{exc}），降级为 MockBrain")
    return MockBrain()


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="agenttest", description="Agent 自动测试系统")
    p.add_argument("command", nargs="?", choices=["run"], default="run",
                   help="子命令（当前仅 run，可省略）")
    p.add_argument("--goal", default="对被测待办清单系统执行 API + Web UI 自动化测试，发现缺陷并生成报告")
    p.add_argument("--demo-url", default=None, help="被测系统地址（默认 http://127.0.0.1:5001）")
    p.add_argument("--report-dir", default=None, help="报告输出目录（默认 ./reports）")
    p.add_argument("--ui-backend", default=None, choices=["auto", "chrome", "selenium"])
    p.add_argument("--force-llm", action="store_true", help="强制使用真实 LLM")
    p.add_argument("--no-ui", action="store_true", help="跳过 UI 用例")
    p.add_argument("--max-attempts", type=int, default=None, help="失败用例最大尝试次数（默认 3）")
    p.add_argument("--keep-server", action="store_true", help="运行结束后保留被测系统")
    return p.parse_args(argv)


def build_context(cfg: Config, skip_ui: bool) -> Dict[str, Any]:
    return {
        "base_url": cfg.demo_url,
        "report_dir": cfg.report_dir,
        "ui_backend": cfg.effective_ui_backend,
        "chrome_path": cfg.chrome_path,
        "skip_ui": skip_ui,
        "max_attempts": cfg.max_attempts,
    }


def main(argv: Optional[list] = None) -> int:
    # Windows 控制台默认 GBK，强制 UTF-8 输出避免 emoji/中文报错
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = parse_args(argv)
    cfg = Config.from_env()
    if args.demo_url:
        cfg.demo_url = args.demo_url
    if args.report_dir:
        cfg.report_dir = Path(args.report_dir)
    if args.ui_backend:
        cfg.ui_backend = args.ui_backend
    if args.max_attempts:
        cfg.max_attempts = args.max_attempts
    cfg.report_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("🤖 Agent 自动测试系统（LangGraph + LangChain）")
    print("=" * 64)

    server: Optional[DemoServer] = None
    if not wait_for_health(cfg.demo_url, timeout=3):
        print(f"[runner] 被测系统未启动，正在本进程内启动: {cfg.demo_url}")
        server = DemoServer(cfg.demo_url)
        server.start()
        if not wait_for_health(cfg.demo_url):
            print("[runner] 被测系统启动失败")
            return 1
    else:
        print(f"[runner] 复用已运行被测系统: {cfg.demo_url}")

    try:
        spec = fetch_openapi(cfg.demo_url)
        print(f"[runner] 已获取契约: {spec.get('info', {}).get('title', 'OpenAPI')} "
              f"v{spec.get('info', {}).get('version', '?')}，"
              f"{len(spec.get('paths', {}))} 个端点")

        brain = pick_brain(cfg, args.force_llm)
        print(f"[runner] Brain = {brain.name}{'（LLM: ' + cfg.llm_model + '）' if brain.name == 'llm' else ''}")
        print(f"[runner] UI 后端 = {cfg.effective_ui_backend}")

        context = build_context(cfg, args.no_ui)
        graph = build_graph(brain, cfg)
        state = {
            "goal": args.goal,
            "spec": spec,
            "cases": [],
            "results": [],
            "defects": [],
            "coverage": {},
            "trace": [],
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "context": context,
            "report": {},
        }

        print("\n[runner] 运行 LangGraph 流程...\n")
        final = None
        for step in graph.stream(state, stream_mode="updates"):
            for node_name, update in step.items():
                for line in update.get("trace", []):
                    print(f"  {line}")
                if node_name == "reporter":
                    final = update
        print("\n[runner] 流程结束")

        result = {
            "goal": args.goal,
            "brain": brain.name,
            "report_files": (final or {}).get("report", {}),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if final:
            files = final.get("report", {})
            print("\n📄 报告位置：")
            print(f"  HTML: {files.get('html')}")
            print(f"  JSON: {files.get('json')}")
        return 0
    finally:
        if server and not args.keep_server:
            server.stop()
            print("[runner] 被测系统已停止")


if __name__ == "__main__":
    raise SystemExit(main())
