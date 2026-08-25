"""运行配置：全部可通过环境变量覆盖（详见 .env.example）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    demo_url: str = "http://127.0.0.1:5001"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "deepseek-chat"
    ui_backend: str = "auto"          # auto | chrome | selenium
    chrome_path: str = ""
    report_dir: Path = field(default_factory=lambda: Path("reports"))
    max_attempts: int = 3             # 失败/不稳定用例重试次数
    max_plan_rounds: int = 2          # planner 最多可追加计划的轮数
    workdir: Path = field(default_factory=Path.cwd)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def effective_ui_backend(self) -> str:
        if self.ui_backend != "auto":
            return self.ui_backend
        return "chrome" if self._find_chrome() else "selenium"

    def _find_chrome(self) -> str:
        if self.chrome_path and Path(self.chrome_path).exists():
            return self.chrome_path
        for cand in DEFAULT_CHROME_CANDIDATES:
            if Path(cand).exists():
                return cand
        return ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            demo_url=_env("AGENTTEST_DEMO_URL", "http://127.0.0.1:5001"),
            llm_api_key=_env("AGENTTEST_LLM_API_KEY"),
            llm_base_url=_env("AGENTTEST_LLM_BASE_URL"),
            llm_model=_env("AGENTTEST_LLM_MODEL", "deepseek-chat"),
            ui_backend=_env("AGENTTEST_UI_BACKEND", "auto"),
            chrome_path=_env("AGENTTEST_CHROME_PATH"),
            report_dir=Path(_env("AGENTTEST_REPORT_DIR", "reports")),
        )
