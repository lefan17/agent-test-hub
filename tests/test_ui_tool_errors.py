"""`agenttest/tools/ui_tool.py` 的错误分支测试（不依赖真实浏览器）。

## 为什么补这个文件

`tests/test_ui_tool.py` 已经有 4 条用例，但它们全部带
`@pytest.mark.skipif("not chrome_present()")`，而且只走成功路径：
能渲染、能找到元素、能识别一条失败断言。覆盖率停在 78%，缺的全是**出错时
怎么表现**：

  - chrome 返回非零退出码 / 空输出时，报的是不是一个能看懂的错；
  - 后端指定 chrome 但机器上没 Chrome 时，是抛 `UiUnavailableError`
    还是抛一个 `AttributeError`；
  - 断言类型没实现时，是判失败还是被静默跳过；
  - selenium 回退失败时，异常有没有转成 `UiUnavailableError`。

最后一条尤其要紧：**未实现的断言类型若被静默跳过，`all([])` 就是 True** ——
一条根本没执行过的断言会报「通过」。这类假绿比红更难发现，所以它必须有测试。

本文件全部用替身（monkeypatch），所以不装浏览器也能跑，CI 上不再 skip。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agenttest.models import Check
from agenttest.tools import ui_tool
from agenttest.tools.ui_tool import UiUnavailableError, ui_check

# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class _Proc:
    """`subprocess.CompletedProcess` 的最小替身。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


PAGE_HTML = """<!doctype html><html><head><title>待办清单</title></head>
<body><h1 data-testid="page-title">待办清单</h1><p>今日无待办</p></body></html>"""


@pytest.fixture
def fake_chrome(monkeypatch):
    """让 chrome 后端返回一份固定 DOM，不真的启动浏览器。

    同时把 `_find_chrome` 也替掉，这样即使机器上没装 Chrome 也能跑 ——
    测试不该因为「这台机器恰好有浏览器」才过。
    """
    monkeypatch.setattr(
        ui_tool,
        "_chrome_dump_dom",
        lambda chrome_path, url: PAGE_HTML,
    )
    monkeypatch.setattr(
        ui_tool,
        "_chrome_screenshot",
        lambda chrome_path, url, out_path: None,
    )
    monkeypatch.setattr(
        ui_tool.Config,
        "_find_chrome",
        lambda self: r"C:\fake\chrome.exe",
    )
    return monkeypatch


# ---------------------------------------------------------------------------
# 断言判定（chrome 后端）
# ---------------------------------------------------------------------------


class TestChromeBackendChecks:
    def test_contains_text_pass(self, fake_chrome):
        r = ui_check("http://x/", [Check(type="contains_text", target="今日无待办")], backend="chrome")
        assert r["ok"] is True
        assert r["results"][0]["detail"] == "页面文本包含"

    def test_contains_text_fail_reports_reason(self, fake_chrome):
        r = ui_check("http://x/", [Check(type="contains_text", target="不存在的文本")], backend="chrome")
        assert r["ok"] is False
        assert r["results"][0]["detail"] == "页面文本不包含"

    def test_has_element_pass(self, fake_chrome):
        r = ui_check("http://x/", [Check(type="has_element", target="[data-testid=page-title]")], backend="chrome")
        assert r["ok"] is True

    def test_has_element_fail(self, fake_chrome):
        r = ui_check("http://x/", [Check(type="has_element", target="#no-such-id")], backend="chrome")
        assert r["ok"] is False
        assert r["results"][0]["detail"] == "元素不存在"

    def test_title_contains_pass(self, fake_chrome):
        r = ui_check("http://x/", [Check(type="title_contains", target="待办")], backend="chrome")
        assert r["ok"] is True
        assert "待办清单" in r["results"][0]["detail"]

    def test_title_contains_fail(self, fake_chrome):
        r = ui_check("http://x/", [Check(type="title_contains", target="不匹配")], backend="chrome")
        assert r["ok"] is False

    def test_unimplemented_check_type_fails_instead_of_being_skipped(self, fake_chrome):
        """**最重要的一条**：没实现的断言类型必须判失败。

        如果实现是 `else: continue`（跳过），那么一条只包含这种断言的用例
        会因为 `all([]) is True` 而报「通过」—— 用例根本没被验证，
        报告却是绿的。这条测试就是钉死这个行为。
        """
        r = ui_check("http://x/", [Check(type="rag_status", target="200")], backend="chrome")
        assert r["ok"] is False, "未实现的断言类型被判通过了，这是假绿"
        assert r["results"][0]["ok"] is False
        assert "未实现" in r["results"][0]["detail"]

    def test_one_unimplemented_among_passing_ones_still_fails(self, fake_chrome):
        """一条有效断言通过、一条未实现 —— 整体仍必须失败。"""
        r = ui_check(
            "http://x/",
            [
                Check(type="contains_text", target="今日无待办"),
                Check(type="rag_status", target="200"),
            ],
            backend="chrome",
        )
        assert r["ok"] is False

    def test_dom_excerpt_is_returned(self, fake_chrome):
        r = ui_check("http://x/", [Check(type="contains_text", target="待办")], backend="chrome")
        assert r["dom_excerpt"]
        assert len(r["dom_excerpt"]) <= 300

    def test_empty_checks_list_is_vacuously_true(self, fake_chrome):
        """空断言列表返回 True 本身没错，但正因如此「跳过未实现类型」才危险 ——
        这条测试把 `all([])` 的行为明确记下来，避免以后有人误以为空列表该失败。
        """
        r = ui_check("http://x/", [], backend="chrome")
        assert r["ok"] is True
        assert r["results"] == []


# ---------------------------------------------------------------------------
# 后端选择与不可用
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_chrome_backend_without_chrome_raises(self, monkeypatch):
        """指定了 chrome 但机器上没 Chrome —— 必须明确说「没找到」，
        不能悄悄改用 selenium（那会让 CI 上的行为与本机不一致）。"""
        monkeypatch.setattr(ui_tool.Config, "_find_chrome", lambda self: "")
        with pytest.raises(UiUnavailableError) as exc:
            ui_check("http://x/", [], backend="chrome")
        assert "Chrome" in str(exc.value)

    def test_auto_without_chrome_falls_back_to_selenium(self, monkeypatch):
        """auto 且无 Chrome 时回退 selenium；这里让 selenium 也失败，
        断言的只是「确实尝试了 selenium」+ 异常被转成 UiUnavailableError。"""
        monkeypatch.setattr(ui_tool.Config, "_find_chrome", lambda self: "")

        def boom(url, checks, shot):
            raise RuntimeError("no chromedriver")

        monkeypatch.setattr(ui_tool, "_selenium_check", boom)
        with pytest.raises(UiUnavailableError) as exc:
            ui_check("http://x/", [], backend="auto")
        assert "Selenium" in str(exc.value)

    def test_explicit_chrome_path_is_used_without_discovery(self, monkeypatch):
        """给了 chrome_path 就不该再去猜系统路径。"""
        seen: dict = {}
        monkeypatch.setattr(ui_tool, "_chrome_dump_dom", lambda p, u: seen.setdefault("path", p) or PAGE_HTML)
        monkeypatch.setattr(
            ui_tool.Config,
            "_find_chrome",
            lambda self: pytest.fail("不该走到自动探测"),
        )
        ui_check("http://x/", [], backend="chrome", chrome_path=r"D:\custom\chrome.exe")
        assert seen["path"] == r"D:\custom\chrome.exe"


class TestScreenshot:
    def test_screenshot_path_is_returned_when_file_exists(self, fake_chrome, tmp_path):
        shot = tmp_path / "s.png"
        shot.write_bytes(b"PNG")

        def fake_shot(path, url, out_path):
            return str(out_path)

        fake_chrome.setattr(ui_tool, "_chrome_screenshot", fake_shot)
        r = ui_check("http://x/", [Check(type="contains_text", target="待办")], backend="chrome", screenshot_path=shot)
        assert r["screenshot"] == str(shot)

    def test_screenshot_failure_does_not_block_the_check(self, monkeypatch):
        """截图失败不能把整条 UI 用例判失败 —— 截图是附件，不是断言。"""
        monkeypatch.setattr(ui_tool, "_chrome_dump_dom", lambda p, u: PAGE_HTML)
        monkeypatch.setattr(ui_tool, "_chrome_screenshot", lambda p, u, o: None)
        monkeypatch.setattr(ui_tool.Config, "_find_chrome", lambda self: r"C:\fake\chrome.exe")
        r = ui_check("http://x/", [Check(type="contains_text", target="待办")], backend="chrome")
        assert r["ok"] is True
        assert r["screenshot"] is None


# ---------------------------------------------------------------------------
# chrome 调用的失败模式
# ---------------------------------------------------------------------------


class TestChromeDumpDom:
    def test_nonzero_exit_raises_with_stderr_excerpt(self, monkeypatch):
        monkeypatch.setattr(ui_tool, "_run_cmd", lambda cmd, timeout=30: _Proc(returncode=1, stderr="找不到页面"))
        with pytest.raises(UiUnavailableError) as exc:
            ui_tool._chrome_dump_dom(r"C:\chrome.exe", "http://x/")
        assert "dump-dom" in str(exc.value)
        assert "找不到页面" in str(exc.value)

    def test_empty_output_raises(self, monkeypatch):
        """退出码 0 但没有任何输出 —— 也算失败。

        只看退出码的实现在这里会把空字符串当 DOM 传下去，
        然后所有 contains_text 都判失败，报错信息完全指不到真正原因。
        """
        monkeypatch.setattr(ui_tool, "_run_cmd", lambda cmd, timeout=30: _Proc(returncode=0, stdout=""))
        with pytest.raises(UiUnavailableError) as exc:
            ui_tool._chrome_dump_dom(r"C:\chrome.exe", "http://x/")
        assert "未返回内容" in str(exc.value)

    def test_command_includes_headless_flags(self, monkeypatch):
        """`--headless=new` 与 `--dump-dom` 是这套「零驱动」方案的核心，
        少一个就会真的弹出浏览器窗口或只返回空白。"""
        captured: dict = {}

        def fake_run(cmd, timeout=30):
            captured["cmd"] = cmd
            return _Proc(returncode=0, stdout=PAGE_HTML)

        monkeypatch.setattr(ui_tool, "_run_cmd", fake_run)
        ui_tool._chrome_dump_dom(r"C:\chrome.exe", "http://x/page")
        cmd = captured["cmd"]
        assert "--headless=new" in cmd
        assert "--dump-dom" in cmd
        assert cmd[-1] == "http://x/page"


class TestChromeScreenshot:
    def test_returns_none_on_nonzero_exit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ui_tool, "_run_cmd", lambda cmd, timeout=30: _Proc(returncode=1))
        assert ui_tool._chrome_screenshot(r"C:\c.exe", "http://x/", tmp_path / "s.png") is None

    def test_returns_none_when_file_not_written(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ui_tool, "_run_cmd", lambda cmd, timeout=30: _Proc(returncode=0))
        assert ui_tool._chrome_screenshot(r"C:\c.exe", "http://x/", tmp_path / "missing.png") is None

    def test_exception_is_swallowed(self, monkeypatch, tmp_path):
        """截图抛异常（超时、路径非法）不能往外传。"""

        def boom(cmd, timeout=30):
            raise subprocess.TimeoutExpired(cmd="chrome", timeout=30)

        monkeypatch.setattr(ui_tool, "_run_cmd", boom)
        assert ui_tool._chrome_screenshot(r"C:\c.exe", "http://x/", tmp_path / "s.png") is None

    def test_absolute_path_is_passed_to_chrome(self, monkeypatch, tmp_path):
        """Chrome 对相对路径的解析取决于自己的工作目录，必须传绝对路径。"""
        captured: dict = {}
        out = tmp_path / "shot.png"
        out.write_bytes(b"x")

        def fake_run(cmd, timeout=30):
            captured["cmd"] = cmd
            return _Proc(returncode=0)

        monkeypatch.setattr(ui_tool, "_run_cmd", fake_run)
        ui_tool._chrome_screenshot(r"C:\c.exe", "http://x/", out)
        shot_arg = [c for c in captured["cmd"] if c.startswith("--screenshot=")][0]
        path = shot_arg.split("=", 1)[1]
        assert Path(path).is_absolute()


# ---------------------------------------------------------------------------
# selenium 后端（替身驱动，不装 selenium 也能测判定逻辑）
# ---------------------------------------------------------------------------


class _FakeDriver:
    """模拟 selenium driver 的判定行为。"""

    def __init__(self, *, body_text="", elements=None, title="", screenshot_ok=True):
        self._body = body_text
        self._elements = elements or []
        self.title = title
        self._screenshot_ok = screenshot_ok
        self.quit_called = False

    def set_page_load_timeout(self, _t):
        pass

    def get(self, _url):
        pass

    def find_element(self, _by, _sel):
        return type("El", (), {"text": self._body})()

    def find_elements(self, _by, _sel):
        return self._elements

    def save_screenshot(self, path):
        if self._screenshot_ok:
            Path(path).write_bytes(b"PNG")

    def quit(self):
        self.quit_called = True


@pytest.fixture
def fake_selenium(monkeypatch):
    """把 selenium 的 import 与 driver 构造都换成替身。

    用 `sys.modules` 注入假模块，而不是要求真的装了 selenium ——
    selenium 是本仓的可选依赖，测试不该依赖它。
    """
    import sys
    import types

    state: dict = {}

    def install(driver):
        state["driver"] = driver

        class Options:
            def add_argument(self, _a):
                pass

        class _WebDriver:
            @staticmethod
            def Chrome(options=None):
                return driver

        sel = types.ModuleType("selenium")
        sel.webdriver = _WebDriver
        wd = types.ModuleType("selenium.webdriver")
        wd.Chrome = _WebDriver.Chrome
        opts = types.ModuleType("selenium.webdriver.chrome.options")
        opts.Options = Options
        common = types.ModuleType("selenium.webdriver.common.by")
        common.By = type("By", (), {"TAG_NAME": "tag", "CSS_SELECTOR": "css"})
        for name, mod in [
            ("selenium", sel),
            ("selenium.webdriver", wd),
            ("selenium.webdriver.chrome", types.ModuleType("selenium.webdriver.chrome")),
            ("selenium.webdriver.chrome.options", opts),
            ("selenium.webdriver.common", types.ModuleType("selenium.webdriver.common")),
            ("selenium.webdriver.common.by", common),
        ]:
            monkeypatch.setitem(sys.modules, name, mod)
        return state

    return install


class TestSeleniumChecks:
    def test_contains_text_pass(self, fake_selenium, tmp_path):
        driver = _FakeDriver(body_text="今日无待办 别的事")
        fake_selenium(driver)
        r = ui_tool._selenium_check("http://x/", [Check(type="contains_text", target="今日无待办")], None)
        assert r["ok"] is True
        assert driver.quit_called, "driver 没 quit，会泄漏浏览器进程"

    def test_contains_text_fail(self, fake_selenium):
        fake_selenium(_FakeDriver(body_text="别的"))
        r = ui_tool._selenium_check("http://x/", [Check(type="contains_text", target="不存在")], None)
        assert r["ok"] is False

    def test_has_element_pass(self, fake_selenium):
        fake_selenium(_FakeDriver(elements=[object()]))
        r = ui_tool._selenium_check("http://x/", [Check(type="has_element", target="#a")], None)
        assert r["ok"] is True

    def test_has_element_fail(self, fake_selenium):
        fake_selenium(_FakeDriver(elements=[]))
        r = ui_tool._selenium_check("http://x/", [Check(type="has_element", target="#a")], None)
        assert r["ok"] is False

    def test_title_contains_pass(self, fake_selenium):
        fake_selenium(_FakeDriver(title="待办清单页面"))
        r = ui_tool._selenium_check("http://x/", [Check(type="title_contains", target="待办")], None)
        assert r["ok"] is True

    def test_unimplemented_type_fails_in_selenium_too(self, fake_selenium):
        """两个后端对「未实现类型」的态度必须一致：都判失败。

        只在一个后端判失败、另一个跳过，会出现「本地 chrome 绿、CI selenium 红」
        或反过来 —— 而两边跑的是同一条用例。
        """
        fake_selenium(_FakeDriver())
        r = ui_tool._selenium_check("http://x/", [Check(type="rag_mrr_at_least", target="0.5")], None)
        assert r["ok"] is False
        assert "未实现" in r["results"][0]["detail"]

    def test_driver_quit_even_when_a_check_raises(self, fake_selenium):
        """断言过程抛异常时也必须 quit —— 否则浏览器进程会一直挂着。"""
        driver = _FakeDriver()

        def boom(_by, _sel):
            raise RuntimeError("元素查找炸了")

        driver.find_element = boom
        fake_selenium(driver)
        with pytest.raises(RuntimeError):
            ui_tool._selenium_check("http://x/", [Check(type="contains_text", target="x")], None)
        assert driver.quit_called

    def test_screenshot_saved_when_requested(self, fake_selenium, tmp_path):
        shot = tmp_path / "s.png"
        fake_selenium(_FakeDriver(body_text="待办"))
        r = ui_tool._selenium_check("http://x/", [Check(type="contains_text", target="待办")], shot)
        assert r["screenshot"] == str(shot)
        assert shot.exists()

    def test_screenshot_none_when_not_written(self, fake_selenium, tmp_path):
        shot = tmp_path / "s.png"
        fake_selenium(_FakeDriver(body_text="待办", screenshot_ok=False))
        r = ui_tool._selenium_check("http://x/", [Check(type="contains_text", target="待办")], shot)
        assert r["screenshot"] is None
