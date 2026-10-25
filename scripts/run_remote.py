"""对**第三方** OpenAPI 服务跑一遍 Agent 流程（不启动本地被测系统）。

## 为什么需要这个脚本

仓库自带的 `python -m agenttest run` 永远对着 `demo_app/`（自己写的待办清单
服务）。那套演示证明的是「Agent 能测我知道的东西」，证明不了「Agent 能对一份
陌生契约工作」—— 而后者才是这个项目对外宣称的能力。

这个脚本把同一套 LangGraph 流程指向一个**我们不控制**的服务（默认 Swagger
Petstore v3），契约从对方 `/openapi.json` 现取，用例由 LLM 现场生成。它和
`run` 的区别只有三点：target 是远端、不启动本地服务、默认跳过 UI（对方没有
Web UI）。

## 为什么它不是 CI 的阻塞步骤

- 对方是公共演示服务，**随时可能慢、可能挂、可能数据被人改乱**；
- 用例由 LLM 生成，同样的契约两次跑出的计划不一样。

把这种"外部 + 非确定"的东西放进阻塞门禁，只会训练自己「红了就重跑」。
所以它固定在本地/手工跑，产物进 `reports/petstore/`，报告单独发布到
gh-pages 的 `/petstore/`，与主报告的 green badge 分开算。

用法：

    python scripts/run_remote.py --base-url https://petstore3.swagger.io/api/v3 \
        --proxy http://127.0.0.1:7897

    # 换一份契约也行（比如自己公司的 staging 契约，或另一个公共 API）
    python scripts/run_remote.py --base-url https://api.example.com \
        --spec-url https://api.example.com/v1/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# `python scripts/xxx.py` 的 sys.path[0] 是 `scripts/`，不是仓库根 ——
# 直接 import 仓库内的包会 ModuleNotFoundError（本仓库在 CI 上踩过一次，
# `python scripts/proxy_probe.py` 就因为这个报过 `No module named 'bench'`）。
# 这里显式把仓库根插到最前面，让脚本既能 `python scripts/x.py` 跑，
# 也能被 `python -m scripts.x` 跑。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agenttest.brain import LLMBrain, MockBrain  # noqa: E402
from agenttest.config import Config  # noqa: E402
from agenttest.graph import build_graph  # noqa: E402
from agenttest.tools.spec_tool import fetch_openapi  # noqa: E402


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文/emoji 会直接抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_remote",
        description="对第三方 OpenAPI 服务跑 Agent 自动测试（不启动本地被测系统）",
    )
    p.add_argument("--base-url", required=True, help="被测服务根地址，例如 https://petstore3.swagger.io/api/v3")
    p.add_argument("--spec-url", default="", help="契约地址（默认取 {base-url}/openapi.json）")
    p.add_argument("--proxy", default="", help="被测服务的显式代理（不读环境变量）")
    p.add_argument("--report-dir", default="reports/petstore", help="报告输出目录")
    p.add_argument("--goal", default="", help="给 Agent 的目标描述")
    p.add_argument("--max-attempts", type=int, default=2, help="失败用例最大尝试次数（默认 2）")
    p.add_argument("--mock", action="store_true", help="强制用 MockBrain（不调 LLM，用于自检链路）")
    return p.parse_args(argv)


def build_context(cfg: Config, base_url: str, proxy: str, report_dir: Path, skip_ui: bool) -> Dict[str, Any]:
    return {
        "base_url": base_url,
        "base_url_proxy": proxy,
        "report_dir": report_dir,
        "ui_backend": cfg.effective_ui_backend,
        "chrome_path": cfg.chrome_path,
        "skip_ui": skip_ui,
        "max_attempts": cfg.max_attempts,
    }


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8()
    args = parse_args(argv)

    base_url = args.base_url.rstrip("/")
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config.from_env()
    cfg.demo_url = base_url
    cfg.demo_proxy = args.proxy
    cfg.report_dir = report_dir
    if args.max_attempts:
        cfg.max_attempts = args.max_attempts

    print("=" * 64)
    print("🌐 对第三方契约跑 Agent 自动测试（不启动本地被测系统）")
    print("=" * 64)
    print(f"[remote] target = {base_url}")
    print(f"[remote] proxy  = {args.proxy or '(直连)'}")

    try:
        spec = fetch_openapi(base_url, spec_url=args.spec_url, proxy=args.proxy)
    except Exception as exc:
        print(f"[remote] 读不到契约：{type(exc).__name__}: {exc}")
        print("[remote] 需要代理的机器请显式传 --proxy；对方服务挂了也会走到这里。")
        return 1

    info = spec.get("info", {}) or {}
    print(
        f"[remote] 契约 = {info.get('title', '(无标题)')} v{info.get('version', '?')}，{len(spec.get('paths', {}))} 个端点"
    )

    if args.mock:
        brain = MockBrain()
        print("[remote] Brain = mock（手工指定；MockBrain 的模板是针对待办清单服务的，")
        print("[remote]         对陌生契约只会产生一堆失败的『用例』，仅用于验证链路通不通）")
    else:
        try:
            brain = LLMBrain(cfg)
        except ValueError as exc:
            print(f"[remote] 无法启用 LLM：{exc}")
            print("[remote] 对陌生契约必须用真实 LLM（MockBrain 的 28 条模板只认待办清单服务的端点）。")
            return 1
        print(f"[remote] Brain = llm（{cfg.llm_model}）")

    goal = args.goal or (
        f"对一个第三方 OpenAPI 服务（{info.get('title', base_url)}）执行接口自动化测试："
        "按契约生成用例、发现契约与实现的偏差并给出缺陷归因"
    )
    skip_ui = True  # 第三方契约默认没有 Web UI；UI 用例在这里只会制造假失败
    context = build_context(cfg, base_url, args.proxy, report_dir, skip_ui)

    state = {
        "goal": goal,
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

    graph = build_graph(brain, cfg)
    print("\n[remote] 运行 LangGraph 流程...\n")
    final: Optional[Dict[str, Any]] = None
    for step in graph.stream(state, stream_mode="updates"):
        for node_name, update in step.items():
            for line in update.get("trace", []):
                print(f"  {line}")
            if node_name == "reporter":
                final = update

    print("\n[remote] 流程结束")
    files = (final or {}).get("report", {})
    print(json.dumps({"target": base_url, "brain": brain.name, "report_files": files}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
