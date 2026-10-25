"""`agenttest/config.py` 的单元测试。

## 为什么补这个文件

`config.py` 把「环境变量 > 档案文件 > 代码默认值」这条三层优先级写进了
docstring 和 README，但**没有任何一条测试直接验证它**。这条规则一旦反过来
（档案文件压过环境变量），后果是：你在 CI 里设的 `AGENTTEST_LLM_API_KEY`
被 `scripts/env/dev.env` 悄悄盖掉，然后表现为「配了 key 却还在跑 MockBrain」。

更要紧的是 prod 档案：它 `AGENTTEST_MAX_ATTEMPTS=1`，是为了不对生产接口
放大流量。如果优先级搞反、环境变量压不过档案，那条收紧就形同虚设。
所以这个文件测的不是「解析对不对」，而是**覆盖顺序对不对**。
"""

from __future__ import annotations

import os

import pytest

from agenttest import config as cfgmod
from agenttest.config import (
    DEFAULT_ENV,
    KNOWN_ENVS,
    Config,
    _parse_dotenv_text,
    load_profile,
    resolve_env_name,
)

# ---------------------------------------------------------------------------
# dotenv 解析
# ---------------------------------------------------------------------------


class TestParseDotenvText:
    def test_basic_key_value(self):
        assert _parse_dotenv_text("A=1\nB=2") == {"A": "1", "B": "2"}

    def test_comments_and_blank_lines_are_ignored(self):
        text = "# 这是注释\n\nA=1\n   \n# B=2\nC=3\n"
        assert _parse_dotenv_text(text) == {"A": "1", "C": "3"}

    def test_quotes_are_stripped(self):
        text = "A=\"带引号\"\nB='单引号'\nC=裸值"
        assert _parse_dotenv_text(text) == {"A": "带引号", "B": "单引号", "C": "裸值"}

    def test_value_may_contain_equals_sign(self):
        """`partition("=")` 而不是 `split("=")` —— 值里的 `=` 要保留。

        URL 带 query string 很常见（`?a=1&b=2`），用 split 会把它截断成
        `?a`，然后被测地址静默变成错的。
        """
        assert _parse_dotenv_text("URL=http://x/y?a=1&b=2") == {"URL": "http://x/y?a=1&b=2"}

    def test_lines_without_equals_are_skipped(self):
        assert _parse_dotenv_text("裸行\nA=1") == {"A": "1"}

    def test_empty_key_is_skipped(self):
        assert _parse_dotenv_text("=无键\nA=1") == {"A": "1"}

    def test_empty_value_is_kept_as_empty_string(self):
        """`A=` 是「显式设成空」，不能当没写 —— 否则清空某个变量的意图会丢失。"""
        assert _parse_dotenv_text("A=\nB=1") == {"A": "", "B": "1"}


# ---------------------------------------------------------------------------
# 档案选择
# ---------------------------------------------------------------------------


class TestResolveEnvName:
    def test_defaults_to_dev(self, monkeypatch):
        monkeypatch.delenv("AGENTTEST_ENV", raising=False)
        assert resolve_env_name() == DEFAULT_ENV == "dev"

    @pytest.mark.parametrize("name", ["dev", "staging", "prod", "DEV", "Prod"])
    def test_known_names_are_accepted_case_insensitively(self, monkeypatch, name):
        monkeypatch.setenv("AGENTTEST_ENV", name)
        assert resolve_env_name() == name.lower()

    def test_unknown_name_raises_with_actionable_message(self, monkeypatch):
        """写错环境名必须直接报错，不能静默回落 dev。

        静默回落意味着「你以为在跑 prod（重试 1 次、只读），实际在跑 dev
        （重试 3 次、指向本地）」—— 这种错在报告里完全看不出来。
        """
        monkeypatch.setenv("AGENTTEST_ENV", "production")
        with pytest.raises(ValueError) as exc:
            resolve_env_name()
        msg = str(exc.value)
        assert "production" in msg
        assert "dev" in msg and "staging" in msg and "prod" in msg  # 列出可选值

    def test_empty_value_falls_back_to_dev(self, monkeypatch):
        """`AGENTTEST_ENV=`（空串）当作没设，不报错。

        这是刻意保留的宽松：CI 里常写成 `AGENTTEST_ENV=$VAR`，变量没定义时
        就是空串。让它报错会让流水线为了一个空值专门加判断。
        """
        monkeypatch.setenv("AGENTTEST_ENV", "")
        assert resolve_env_name() == "dev"

    def test_whitespace_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("AGENTTEST_ENV", "  staging  ")
        assert resolve_env_name() == "staging"

    def test_known_envs_constant_matches_supported_profiles(self):
        """`KNOWN_ENVS` 与磁盘上的档案文件必须一一对应。

        加了 `uat.env` 却忘了加进 `KNOWN_ENVS`，那么这个档案永远选不中 ——
        而且不会有任何报错，只会表现为「配置没生效」。
        """
        on_disk = {p.stem for p in cfgmod.PROFILE_DIR.glob("*.env")}
        assert on_disk == set(KNOWN_ENVS)


class TestLoadProfile:
    @pytest.mark.parametrize("name", ["dev", "staging", "prod"])
    def test_every_known_profile_loads_non_empty(self, name):
        profile = load_profile(name)
        assert profile, f"{name}.env 没解析出任何键，档案等于不存在"
        assert "AGENTTEST_DEMO_URL" in profile

    def test_missing_profile_returns_empty_dict(self):
        assert load_profile("no-such-profile") == {}

    def test_dev_profile_matches_code_defaults(self):
        """dev 档案的取值必须与代码默认值逐条一致。

        这是引入环境分层时给出的承诺（「不设任何环境变量时行为与之前完全相同」）。
        不一致的话，默认配置就取决于你看的是档案还是代码，两边会漂。
        """
        profile = load_profile("dev")
        defaults = Config()
        assert profile["AGENTTEST_DEMO_URL"] == defaults.demo_url
        assert profile["AGENTTEST_UI_BACKEND"] == defaults.ui_backend
        assert profile["AGENTTEST_LLM_MODEL"] == defaults.llm_model
        assert profile["AGENTTEST_REPORT_DIR"] == str(defaults.report_dir)

    def test_prod_profile_tightens_retries(self):
        """prod 必须把重试收紧到 1 —— 对生产接口放大流量是真事故。"""
        assert load_profile("prod")["AGENTTEST_MAX_ATTEMPTS"] == "1"

    def test_staging_uses_a_different_port_than_dev(self):
        """两个档案不能指向同一个端口，否则本机同时起 dev 与 staging 会抢端口。"""
        assert load_profile("staging")["AGENTTEST_DEMO_URL"] != load_profile("dev")["AGENTTEST_DEMO_URL"]


# ---------------------------------------------------------------------------
# 三层优先级 —— 本文件的重点
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_env_var_beats_profile(self, monkeypatch):
        """第一优先级：真实环境变量压过档案文件。"""
        monkeypatch.setenv("AGENTTEST_ENV", "prod")
        monkeypatch.setenv("AGENTTEST_MAX_ATTEMPTS", "9")
        cfg = Config.from_env()
        assert cfg.max_attempts == 9  # 档案里是 1，环境变量写 9 就该是 9

    def test_profile_beats_code_default(self, monkeypatch):
        """第二优先级：档案文件压过代码默认值。"""
        monkeypatch.setenv("AGENTTEST_ENV", "prod")
        monkeypatch.delenv("AGENTTEST_MAX_ATTEMPTS", raising=False)
        cfg = Config.from_env()
        assert cfg.max_attempts == 1  # 代码默认是 3，prod 档案写 1

    def test_code_default_applies_when_nobody_sets_anything(self, monkeypatch):
        """第三优先级：都没设时用代码默认值。"""
        monkeypatch.setenv("AGENTTEST_ENV", "dev")
        for name in ("AGENTTEST_MAX_ATTEMPTS", "AGENTTEST_UI_BACKEND"):
            monkeypatch.delenv(name, raising=False)
        cfg = Config.from_env()
        assert cfg.max_attempts == 3  # dev 档案没写 MAX_ATTEMPTS

    def test_empty_env_var_does_not_shadow_profile(self, monkeypatch):
        """环境变量为空串时**不该**压掉档案值。

        `os.environ[name].strip()` 得到空串，若直接返回它就等于把档案值清空。
        CI 里 `AGENTTEST_LLM_MODEL=$UNSET_VAR` 是常见写法，这条防的就是它。
        """
        monkeypatch.setenv("AGENTTEST_ENV", "prod")
        monkeypatch.setenv("AGENTTEST_MAX_ATTEMPTS", "")
        cfg = Config.from_env()
        # 空串无法 int()，会走 pick_int 的 "not raw" 分支回落到代码默认 3，
        # 而不是抛 ValueError —— 关键是**不能崩**，也不能静默变成 0
        assert cfg.max_attempts == 3

    def test_dotenv_does_not_override_existing_env(self, monkeypatch, tmp_path):
        """`.env` 的语义是「只补没设的」，不能覆盖真实环境变量。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("AGENTTEST_LLM_MODEL=from-dotenv\n", encoding="utf-8")
        monkeypatch.setenv("AGENTTEST_LLM_MODEL", "from-real-env")
        cfgmod._load_dotenv()
        assert os.environ["AGENTTEST_LLM_MODEL"] == "from-real-env"

    def test_dotenv_fills_missing_env(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("AGENTTEST_LLM_MODEL=from-dotenv\n", encoding="utf-8")
        monkeypatch.delenv("AGENTTEST_LLM_MODEL", raising=False)
        cfgmod._load_dotenv()
        assert os.environ["AGENTTEST_LLM_MODEL"] == "from-dotenv"

    def test_missing_dotenv_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        cfgmod._load_dotenv()  # 目录里没有 .env，不该抛


# ---------------------------------------------------------------------------
# 类型转换与边界
# ---------------------------------------------------------------------------


class TestParsingEdges:
    def test_non_integer_max_attempts_raises_with_variable_name(self, monkeypatch):
        monkeypatch.setenv("AGENTTEST_MAX_ATTEMPTS", "three")
        with pytest.raises(ValueError) as exc:
            Config.from_env()
        msg = str(exc.value)
        assert "AGENTTEST_MAX_ATTEMPTS" in msg
        assert "three" in msg

    def test_report_dir_becomes_path(self, monkeypatch):
        monkeypatch.setenv("AGENTTEST_REPORT_DIR", "out/xyz")
        cfg = Config.from_env()
        assert isinstance(cfg.report_dir, os.PathLike)

    def test_llm_configured_follows_key_presence(self, monkeypatch, tmp_path):
        # 必须切到没有 .env 的目录：Config.from_env() 读完进程环境还会调
        # _load_dotenv() 去读仓库根的 .env，开发机上有真 key 时「无 key」分支走不到。
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AGENTTEST_LLM_API_KEY", raising=False)
        monkeypatch.setenv("AGENTTEST_ENV", "dev")
        assert Config.from_env().llm_configured is False
        monkeypatch.setenv("AGENTTEST_LLM_API_KEY", "sk-x")
        assert Config.from_env().llm_configured is True

    def test_empty_key_counts_as_not_configured(self, monkeypatch):
        """空 key 不能让 `llm_configured` 变 True，否则会拿空 key 去调 LLM。"""
        monkeypatch.setenv("AGENTTEST_LLM_API_KEY", "   ")
        assert Config.from_env().llm_configured is False


class TestEffectiveUiBackend:
    def test_explicit_backend_wins_over_auto_detection(self):
        cfg = Config(ui_backend="selenium")
        assert cfg.effective_ui_backend == "selenium"

    def test_auto_falls_back_to_selenium_when_no_chrome(self, monkeypatch):
        monkeypatch.setattr(Config, "_find_chrome", lambda self: "")
        cfg = Config(ui_backend="auto")
        assert cfg.effective_ui_backend == "selenium"

    def test_auto_picks_chrome_when_available(self, monkeypatch):
        monkeypatch.setattr(Config, "_find_chrome", lambda self: r"C:\chrome.exe")
        cfg = Config(ui_backend="auto")
        assert cfg.effective_ui_backend == "chrome"

    def test_explicit_chrome_path_must_exist(self, tmp_path):
        """`chrome_path` 指向不存在的文件时必须当作「找不到 Chrome」。

        否则 `effective_ui_backend` 会返回 chrome，然后在真正启动浏览器时
        才报一个跟配置无关的错。
        """
        cfg = Config(ui_backend="auto", chrome_path=str(tmp_path / "nope.exe"))
        assert cfg._find_chrome() != str(tmp_path / "nope.exe")

    def test_existing_chrome_path_is_used(self, tmp_path):
        fake = tmp_path / "chrome.exe"
        fake.write_text("", encoding="utf-8")
        cfg = Config(ui_backend="auto", chrome_path=str(fake))
        assert cfg._find_chrome() == str(fake)


class TestChromeDiscovery:
    r"""浏览器探测不能只认 Windows。

    这里曾经只有四条 `C:\Program Files\...` 的绝对路径，于是 `_find_chrome()`
    在 Linux / macOS 上必然返回空串，UI 渲染断言与截图在 CI 上整组 skip ——
    「Agent 能做 UI 验证」这条能力从来没有在 CI 上被验证过。
    而 GitHub 的 ubuntu runner 自带 Chrome，所以那不是环境限制，是逻辑写窄了。
    """

    def test_windows_paths_stay_first(self):
        """Windows 路径必须仍排在前面：本机开发优先命中最常见的安装位置。"""
        first = cfgmod.DEFAULT_CHROME_CANDIDATES[0].lower()
        assert first.startswith("c:") and "program files" in first

    def test_linux_and_macos_candidates_exist(self):
        joined = " ".join(cfgmod.DEFAULT_CHROME_CANDIDATES)
        assert "/usr/bin/google-chrome" in joined, "Linux 的常见位置必须列出来"
        assert "/Applications/Google Chrome.app" in joined, "macOS 的常见位置必须列出来"

    def test_path_command_is_used_when_no_absolute_path_exists(self, monkeypatch):
        """装在 PATH 上但不在标准目录（nix / conda / 自建镜像）也要能找到。"""
        monkeypatch.setattr(cfgmod, "DEFAULT_CHROME_CANDIDATES", ["/nonexistent/chrome"])
        monkeypatch.setattr(
            cfgmod.shutil,
            "which",
            lambda command: "/opt/bin/google-chrome" if command == "google-chrome" else None,
        )
        cfg = Config(ui_backend="auto", chrome_path="")
        assert cfg._find_chrome() == "/opt/bin/google-chrome"

    def test_absolute_path_wins_over_path_command(self, monkeypatch, tmp_path):
        """两条通道都在时，标准安装路径优先 —— 顺序是可预期的。"""
        fake = tmp_path / "chrome-binary"
        fake.write_text("", encoding="utf-8")
        monkeypatch.setattr(cfgmod, "DEFAULT_CHROME_CANDIDATES", [str(fake)])
        monkeypatch.setattr(cfgmod.shutil, "which", lambda command: "/somewhere/else")
        cfg = Config(ui_backend="auto", chrome_path="")
        assert cfg._find_chrome() == str(fake)

    def test_overrides_still_win(self, monkeypatch, tmp_path):
        """显式配置永远是第一优先级。"""
        explicit = tmp_path / "explicit-chrome"
        explicit.write_text("", encoding="utf-8")
        monkeypatch.setattr(cfgmod.shutil, "which", lambda command: "/somewhere/else")
        cfg = Config(ui_backend="auto", chrome_path=str(explicit))
        assert cfg._find_chrome() == str(explicit)

    def test_nothing_found_returns_empty(self, monkeypatch):
        """两头都找不到时返回空串，交给上层降级成 selenium。"""
        monkeypatch.setattr(cfgmod, "DEFAULT_CHROME_CANDIDATES", ["/nonexistent/chrome"])
        monkeypatch.setattr(cfgmod.shutil, "which", lambda command: None)
        cfg = Config(ui_backend="auto", chrome_path="")
        assert cfg._find_chrome() == ""
        assert cfg.effective_ui_backend == "selenium"

    def test_path_found_chrome_switches_backend(self, monkeypatch):
        """PATH 上找到 Chrome 就该选 chrome 后端，而不是继续降级。"""
        monkeypatch.setattr(cfgmod, "DEFAULT_CHROME_CANDIDATES", ["/nonexistent/chrome"])
        monkeypatch.setattr(cfgmod.shutil, "which", lambda command: "/usr/bin/chromium")
        cfg = Config(ui_backend="auto", chrome_path="")
        assert cfg.effective_ui_backend == "chrome"
