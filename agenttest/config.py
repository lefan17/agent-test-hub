"""运行配置：环境分层（dev / staging / prod）+ 环境变量覆盖。

优先级（从高到低）：
  1. 真实环境变量（含 `.env`，由 `_load_dotenv` 注入）
  2. `AGENTTEST_ENV` 选中的配置档案文件 `scripts/env/<profile>.env`
  3. 代码内默认值

默认 `AGENTTEST_ENV=dev`，且 `scripts/env/dev.env` 的取值与代码默认值一致，
因此不设置任何环境变量时的行为与引入分层之前完全相同。

详见 `.env.example`。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

DEFAULT_ENV = "dev"
KNOWN_ENVS = ("dev", "staging", "prod")

PROFILE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "env"

DEFAULT_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_dotenv_text(text: str) -> Dict[str, str]:
    """解析 dotenv 文本为键值对；忽略空行与 `#` 注释。"""
    values: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _load_dotenv(path: Optional[Path] = None) -> None:
    """轻量 .env 加载：已存在的环境变量优先，不覆盖。"""
    path = path or Path(".env")
    if not path.exists():
        return
    for key, value in _parse_dotenv_text(path.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value


def resolve_env_name() -> str:
    """读取 `AGENTTEST_ENV`，校验取值；默认 dev。"""
    name = _env("AGENTTEST_ENV", DEFAULT_ENV).lower() or DEFAULT_ENV
    if name not in KNOWN_ENVS:
        raise ValueError(
            f"未知的 AGENTTEST_ENV={name!r}，可选值：{' / '.join(KNOWN_ENVS)}"
        )
    return name


def load_profile(env_name: str) -> Dict[str, str]:
    """读取配置档案文件；文件缺失时返回空（回落到代码默认值）。"""
    profile_path = PROFILE_DIR / f"{env_name}.env"
    if not profile_path.exists():
        return {}
    return _parse_dotenv_text(profile_path.read_text(encoding="utf-8"))


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
    env: str = DEFAULT_ENV            # 当前配置档案：dev | staging | prod

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
        _load_dotenv()
        env_name = resolve_env_name()
        profile = load_profile(env_name)

        def pick(name: str, default: str = "") -> str:
            """环境变量 > 档案文件 > 代码默认值。"""
            if name in os.environ:
                return os.environ[name].strip()
            if name in profile:
                return profile[name]
            return default

        def pick_int(name: str, default: int) -> int:
            raw = pick(name, "")
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                raise ValueError(f"{name} 需要整数，得到 {raw!r}") from None

        return cls(
            demo_url=pick("AGENTTEST_DEMO_URL", "http://127.0.0.1:5001"),
            llm_api_key=pick("AGENTTEST_LLM_API_KEY"),
            llm_base_url=pick("AGENTTEST_LLM_BASE_URL"),
            llm_model=pick("AGENTTEST_LLM_MODEL", "deepseek-chat"),
            ui_backend=pick("AGENTTEST_UI_BACKEND", "auto"),
            chrome_path=pick("AGENTTEST_CHROME_PATH"),
            report_dir=Path(pick("AGENTTEST_REPORT_DIR", "reports")),
            max_attempts=pick_int("AGENTTEST_MAX_ATTEMPTS", 3),
            max_plan_rounds=pick_int("AGENTTEST_MAX_PLAN_ROUNDS", 2),
            env=env_name,
        )
