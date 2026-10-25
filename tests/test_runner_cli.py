"""`agenttest/runner.py` 的 CLI 级测试。

## 为什么单独有这个文件

`tests/test_runner_e2e.py` 名字叫 e2e，但它**绕过 runner**：直接
`build_graph(brain, cfg)` + `graph.invoke(state)`。于是 `runner.py` 里真正
会被用户执行的那部分 —— `parse_args` / `pick_brain` / `DemoServer` /
`wait_for_health` / `main` 的退出码 —— **一条都没被覆盖，实测 0.0%**。

这个 0% 是仓库级覆盖率门槛看不见的：`runner.py` 122 语句，占全仓 8%，
整体归零总覆盖率只掉 8pp，而门槛是 70，仍然是绿的。所以它活了很久都没人发现。

本文件按「用户怎么用它」来测，而不是按「函数怎么实现」来测：
命令行参数是契约，退出码是契约，`--force-llm` 无 key 时报错而不是静默降级
也是契约。
"""

from __future__ import annotations

import json
import socket
import threading

import pytest
import requests
from werkzeug.serving import make_server

from agenttest import runner
from agenttest.brain import LLMBrain, MockBrain
from agenttest.config import Config

# ---------------------------------------------------------------------------
# 帮手
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """拿一个当前空闲的端口。

    绑到 0 再取 `getsockname()` 是唯一可靠的做法 —— 猜一个固定端口号
    在 CI 上迟早撞车，然后表现为「flaky 测试」，实际是测试写错了。
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def clean_env(monkeypatch, tmp_path_factory):
    """清掉可能从开发机泄漏进来的 AGENTTEST_* 环境变量 **以及 `.env` 文件**。

    不清理的话，本机 `.env` 或 shell 里设的 key 会让
    「无 key 时降级为 MockBrain」这类测试**在别人机器上过、在 CI 上挂**
    （或反过来），而且失败信息会完全误导。

    只 `delenv` 是不够的：`Config.from_env()` 在读完进程环境后还会调
    `_load_dotenv()` 去读仓库根目录的 `.env`，所以开发机上只要有真 key，
    「无 key 路径」就永远走不到。这里把工作目录切到空的临时目录，
    `_load_dotenv()` 的相对路径 `Path(".env")` 就找不到文件了。
    """
    monkeypatch.chdir(tmp_path_factory.mktemp("no-dotenv"))
    for name in (
        "AGENTTEST_LLM_API_KEY",
        "AGENTTEST_LLM_BASE_URL",
        "AGENTTEST_LLM_MODEL",
        "AGENTTEST_DEMO_URL",
        "AGENTTEST_ENV",
        "AGENTTEST_UI_BACKEND",
        "AGENTTEST_MAX_ATTEMPTS",
        "AGENTTEST_REPORT_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture(scope="module")
def live_demo_url():
    """起一个真实 Flask 服务（模块级，避免每条用例都起服务）。"""
    from demo_app.app import create_app

    port = _free_port()
    server = make_server("127.0.0.1", port, create_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    # 等服务真的能应答再交出去，否则第一条用例会偶发拿不到 health
    assert runner.wait_for_health(url, timeout=10)
    yield url
    server.shutdown()
    thread.join(timeout=5)


# ---------------------------------------------------------------------------
# parse_args：命令行参数是契约
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults_match_documented_values(self):
        """默认值就是契约：README 里写了默认端口 5001、默认最多 3 次。"""
        args = runner.parse_args([])
        assert args.command == "run"
        assert args.demo_url is None  # None 表示"用 Config 的默认值"
        assert args.report_dir is None
        assert args.ui_backend is None
        assert args.force_llm is False
        assert args.no_ui is False
        assert args.keep_server is False
        assert args.max_attempts is None
        assert "API" in args.goal and "UI" in args.goal

    def test_command_can_be_omitted_or_explicit(self):
        # 两种写法必须等价，否则 README 里"子命令可省略"就是假的
        assert runner.parse_args([]).command == "run"
        assert runner.parse_args(["run"]).command == "run"

    def test_every_flag_is_parsed(self):
        args = runner.parse_args(
            [
                "--goal",
                "只跑 API",
                "--demo-url",
                "http://127.0.0.1:9999",
                "--report-dir",
                "tmp/out",
                "--ui-backend",
                "chrome",
                "--force-llm",
                "--no-ui",
                "--max-attempts",
                "7",
                "--keep-server",
            ]
        )
        assert args.goal == "只跑 API"
        assert args.demo_url == "http://127.0.0.1:9999"
        assert args.report_dir == "tmp/out"
        assert args.ui_backend == "chrome"
        assert args.force_llm is True
        assert args.no_ui is True
        assert args.max_attempts == 7
        assert args.keep_server is True

    def test_unknown_command_exits_two(self):
        """argparse 对非法 choices 的退出码是 2。

        这条要测，是因为「退出码 2 被吞成 0」会让 CI 里一条打错的命令
        看起来执行成功 —— 那是最难查的一类问题。
        """
        with pytest.raises(SystemExit) as exc:
            runner.parse_args(["bogus-command"])
        assert exc.value.code == 2

    def test_invalid_ui_backend_exits_two(self):
        with pytest.raises(SystemExit) as exc:
            runner.parse_args(["--ui-backend", "firefox"])
        assert exc.value.code == 2

    def test_non_integer_max_attempts_exits_two(self):
        with pytest.raises(SystemExit) as exc:
            runner.parse_args(["--max-attempts", "three"])
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# pick_brain：降级是刻意的设计，所以它必须精确
# ---------------------------------------------------------------------------


class TestPickBrain:
    def test_without_key_falls_back_to_mock(self, clean_env):
        cfg = Config()
        assert cfg.llm_configured is False
        brain = runner.pick_brain(cfg, force_llm=False)
        assert isinstance(brain, MockBrain)
        assert brain.name == "mock"

    def test_force_llm_without_key_exits_one(self, clean_env):
        """`--force-llm` 无 key 时必须**退出 1**，不能静默降级。

        静默降级会让用户以为自己跑的是 LLM 版本，实际是规则引擎 ——
        这类"看起来跑了、其实没跑"的失败要让流水线红，不能让它绿着过去。
        """
        cfg = Config()
        with pytest.raises(SystemExit) as exc:
            runner.pick_brain(cfg, force_llm=True)
        assert exc.value.code == 1

    def test_force_llm_without_key_prints_a_readable_reason(self, clean_env, capsys):
        cfg = Config()
        with pytest.raises(SystemExit):
            runner.pick_brain(cfg, force_llm=True)
        out = capsys.readouterr().out
        assert "无法启用 LLM" in out

    def test_configured_key_gives_llm_brain(self, clean_env):
        # 造一个假 key，不真调网络：构造 LLMBrain 本身不发请求
        clean_env.setenv("AGENTTEST_LLM_API_KEY", "sk-fake-for-test")
        cfg = Config.from_env()
        assert cfg.llm_configured is True
        brain = runner.pick_brain(cfg, force_llm=False)
        assert isinstance(brain, LLMBrain)
        assert brain.name == "llm"

    def test_auto_detects_key_without_force(self, clean_env):
        """配了 key 就该用 LLM，不需要 `--force-llm`。"""
        clean_env.setenv("AGENTTEST_LLM_API_KEY", "sk-fake-for-test")
        cfg = Config.from_env()
        brain = runner.pick_brain(cfg, force_llm=False)
        assert isinstance(brain, LLMBrain)


# ---------------------------------------------------------------------------
# wait_for_health：重试逻辑必须真的重试
# ---------------------------------------------------------------------------


class TestWaitForHealth:
    def test_returns_true_for_live_service(self, live_demo_url):
        assert runner.wait_for_health(live_demo_url, timeout=5) is True

    def test_returns_false_for_dead_port(self):
        port = _free_port()  # 拿到就释放，没有服务在听
        url = f"http://127.0.0.1:{port}"
        assert runner.wait_for_health(url, timeout=1.5) is False

    def test_dead_port_actually_waits_for_the_timeout(self):
        """防「立即返回 False」的假实现。

        如果实现是 `try: return requests.get(...).ok except: return False`，
        它会在毫秒级返回 False，表面行为一模一样但**没有重试**。
        用耗时下限把这种实现区分出来。
        """
        port = _free_port()
        url = f"http://127.0.0.1:{port}"
        import time

        start = time.monotonic()
        assert runner.wait_for_health(url, timeout=1.0) is False
        elapsed = time.monotonic() - start
        assert elapsed >= 0.9, f"只过了 {elapsed:.2f}s，说明没有真的轮询到 timeout"

    def test_retries_until_success(self, monkeypatch):
        """前两次失败、第三次成功 —— 判定必须来自第三次那个 200。"""
        calls = {"n": 0}

        class _Resp:
            status_code = 200

        def fake_get(url, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.RequestException("还在启动")
            return _Resp()

        monkeypatch.setattr(runner, "_direct_get", fake_get)
        monkeypatch.setattr(runner.time, "sleep", lambda _s: None)  # 别真睡

        assert runner.wait_for_health("http://127.0.0.1:1", timeout=5) is True
        assert calls["n"] == 3

    def test_non_200_does_not_count_as_healthy(self, monkeypatch):
        """服务在听但返回 503（启动中）不算健康 —— 只有 200 算。"""

        class _Resp:
            status_code = 503

        monkeypatch.setattr(runner, "_direct_get", lambda url, timeout=None: _Resp())
        monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
        assert runner.wait_for_health("http://127.0.0.1:1", timeout=0.5) is False


# ---------------------------------------------------------------------------
# DemoServer：生命周期必须闭合
# ---------------------------------------------------------------------------


class TestDemoServer:
    def test_start_then_stop_closes_the_port(self):
        port = _free_port()
        url = f"http://127.0.0.1:{port}"
        server = runner.DemoServer(url)

        server.start()
        try:
            assert runner.wait_for_health(url, timeout=10) is True
            # 真的能拿到业务响应，不只是 health。
            # 注意 `/api/todos` 返回的是分页信封 `{"count": n, "data": [...]}`，
            # 不是裸数组 —— 断言裸数组会挂，而那是我第一版写错的地方。
            resp = requests.get(f"{url}/api/todos", timeout=5)
            assert resp.status_code == 200
            payload = resp.json()
            assert isinstance(payload["data"], list)
            assert payload["count"] == len(payload["data"])
        finally:
            server.stop()

        # 停了之后必须连不上 —— 不验证这一条，"stop 其实没生效"不会被发现
        assert runner.wait_for_health(url, timeout=1.0) is False

    def test_url_trailing_slash_is_stripped(self):
        server = runner.DemoServer(f"http://127.0.0.1:{_free_port()}/")
        assert not server.url.endswith("/")

    def test_url_without_port_gets_an_actionable_error(self):
        """不带端口的地址必须报**能照着改**的错。

        这条是回归测试：修之前 `--demo-url http://127.0.0.1/` 抛的是
        `ValueError: invalid literal for int() with base 10: ''`，
        既没说是哪个参数，也没说该写成什么样。
        """
        with pytest.raises(ValueError) as exc:
            runner.DemoServer("http://127.0.0.1/")
        msg = str(exc.value)
        assert "--demo-url" in msg
        assert "http://127.0.0.1:5001" in msg  # 给出正确写法


# ---------------------------------------------------------------------------
# build_context：字段映射是 planner 的输入契约
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_skip_ui_is_carried_into_context(self):
        cfg = Config(demo_url="http://127.0.0.1:5001", ui_backend="selenium")
        ctx = runner.build_context(cfg, skip_ui=True)
        assert ctx["skip_ui"] is True
        assert ctx["base_url"] == "http://127.0.0.1:5001"
        assert ctx["ui_backend"] == "selenium"

    def test_all_planner_keys_are_present(self):
        cfg = Config()
        ctx = runner.build_context(cfg, skip_ui=False)
        assert set(ctx) == {
            "base_url",
            "base_url_proxy",
            "report_dir",
            "ui_backend",
            "chrome_path",
            "skip_ui",
            "max_attempts",
        }


# ---------------------------------------------------------------------------
# main：退出码与产物
# ---------------------------------------------------------------------------


class TestMain:
    def test_full_run_with_no_ui_writes_reports(self, live_demo_url, clean_env, tmp_path):
        """最完整的一条：真跑一遍 LangGraph（MockBrain），检查报告真的落盘。

        这是本文件唯一一条真跑流程的用例 —— 其余都是单元级。
        用 MockBrain 而非 LLM，所以确定性、不花钱、CI 可跑。
        """
        report_dir = tmp_path / "reports"
        code = runner.main(
            [
                "--no-ui",
                "--demo-url",
                live_demo_url,
                "--report-dir",
                str(report_dir),
                "--goal",
                "只跑 API 用例",
            ]
        )
        assert code == 0

        html = report_dir / "report.html"
        js = report_dir / "report.json"
        assert html.is_file() and html.stat().st_size > 0, "HTML 报告没生成"
        assert js.is_file(), "JSON 报告没生成"

        data = json.loads(js.read_text(encoding="utf-8"))
        # 字段名是 `brain_name`（TestReport 里这么定的），不是 `brain` ——
        # runner 打印到 stdout 的那个 result 字典才用 `brain` 键。
        assert data.get("brain_name") == "mock"
        assert data.get("llm_configured") is False
        assert isinstance(data.get("cases"), list)
        assert data.get("summary", {}).get("total") == len(data["cases"])

    def test_reuses_already_running_service(self, live_demo_url, clean_env, tmp_path, capsys):
        """被测系统已经在跑时不该再起一个（否则端口冲突）。"""
        code = runner.main(["--no-ui", "--demo-url", live_demo_url, "--report-dir", str(tmp_path / "r")])
        assert code == 0
        assert "复用已运行被测系统" in capsys.readouterr().out

    def test_force_llm_without_key_returns_nonzero(self, live_demo_url, clean_env, tmp_path):
        """`main` 里 `--force-llm` 无 key 的退出码要穿透出来。"""
        with pytest.raises(SystemExit) as exc:
            runner.main(
                [
                    "--no-ui",
                    "--force-llm",
                    "--demo-url",
                    live_demo_url,
                    "--report-dir",
                    str(tmp_path / "r"),
                ]
            )
        assert exc.value.code == 1

    def test_server_start_failure_returns_one(self, clean_env, tmp_path, monkeypatch):
        """被测系统起不来时返回 1，而不是抛异常或假装成功。

        打桩 `wait_for_health` 让它一直 False（第一次探测失败 → 尝试自启 →
        自启后仍然探测失败），走的正是真实失败路径。
        """
        port = _free_port()
        monkeypatch.setattr(runner, "wait_for_health", lambda url, timeout=None: False)

        class _BrokenServer:
            def __init__(self, url):
                self.url = url

            def start(self):
                pass

            def stop(self):
                pass

        monkeypatch.setattr(runner, "DemoServer", _BrokenServer)

        code = runner.main(
            [
                "--no-ui",
                "--demo-url",
                f"http://127.0.0.1:{port}",
                "--report-dir",
                str(tmp_path / "r"),
            ]
        )
        assert code == 1

    def test_report_dir_is_created_if_missing(self, live_demo_url, clean_env, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()
        runner.main(["--no-ui", "--demo-url", live_demo_url, "--report-dir", str(nested)])
        assert nested.is_dir()

    def test_cli_overrides_take_effect(self, live_demo_url, clean_env, tmp_path, monkeypatch):
        """命令行参数必须真的覆盖 Config，而不是解析完就丢。

        通过观察传给 build_graph 的 cfg 来验证 —— 这是 planner 实际拿到的对象。
        """
        captured: dict = {}
        real_build = runner.build_graph

        def spy_build(brain, cfg):
            captured["cfg"] = cfg
            return real_build(brain, cfg)

        monkeypatch.setattr(runner, "build_graph", spy_build)
        runner.main(
            [
                "--no-ui",
                "--demo-url",
                live_demo_url,
                "--report-dir",
                str(tmp_path / "r"),
                "--max-attempts",
                "5",
                "--ui-backend",
                "chrome",
            ]
        )
        assert captured["cfg"].max_attempts == 5
        assert captured["cfg"].ui_backend == "chrome"
        assert captured["cfg"].demo_url == live_demo_url
