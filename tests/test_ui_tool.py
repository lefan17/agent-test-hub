"""UI 工具测试（真实浏览器渲染，无 Chrome 时自动跳过）。"""
from __future__ import annotations

import pytest

from agenttest.config import Config
from agenttest.models import Check
from agenttest.tools import ui_check


def chrome_present() -> bool:
    return bool(Config()._find_chrome())


@pytest.mark.skipif("not chrome_present()")
def test_homepage_contains_title(demo_server_url):
    r = ui_check(f"{demo_server_url}/", [Check(type="contains_text", target="AgentTest Demo")])
    assert r["ok"] is True
    assert r["results"][0]["ok"] is True


@pytest.mark.skipif("not chrome_present()")
def test_homepage_has_element(demo_server_url):
    r = ui_check(f"{demo_server_url}/", [Check(type="has_element", target="[data-testid=page-title]")])
    assert r["ok"] is True


@pytest.mark.skipif("not chrome_present()")
def test_login_form_present(demo_server_url):
    r = ui_check(f"{demo_server_url}/login", [Check(type="has_element", target="[data-testid=login-form]")])
    assert r["ok"] is True


@pytest.mark.skipif("not chrome_present()")
def test_failing_check_detected(demo_server_url):
    r = ui_check(f"{demo_server_url}/", [Check(type="contains_text", target="不存在的文本XYZ")])
    assert r["ok"] is False
