"""UI 自动化工具：真实浏览器渲染验证（默认 Chrome 无头模式，无需驱动；可选 Selenium）。

设计说明：
  - chrome 后端：调用系统 Chrome/Edge 的 --headless=new --dump-dom 渲染页面并输出 DOM，
    再用 BeautifulSoup 做断言，同时可截图。零驱动、零下载，离线可用。
  - selenium 后端：完整交互式自动化（填表/点击），需要 chromedriver 可用。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from agenttest.config import Config
from agenttest.models import Check


class UiUnavailableError(RuntimeError):
    """UI 后端不可用（未找到浏览器或驱动）。"""


def _run_cmd(cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    # Windows 下 Chrome 可能输出 GBK 编码文本，统一按 utf-8 容错解码
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def _chrome_dump_dom(chrome_path: str, url: str) -> str:
    """用 Chrome 无头模式渲染并返回页面 DOM。"""
    cmd = [
        chrome_path,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--dump-dom",
        url,
    ]
    proc = _run_cmd(cmd)
    if proc.returncode != 0:
        raise UiUnavailableError(f"chrome dump-dom 失败: {proc.stderr[:500]}")
    if not proc.stdout:
        raise UiUnavailableError("chrome dump-dom 未返回内容")
    return proc.stdout


def _chrome_screenshot(chrome_path: str, url: str, out_path: Path) -> Optional[str]:
    """Chrome 无头截图；失败不阻断测试。"""
    try:
        abs_path = str(out_path.resolve())  # Chrome 对相对路径解析异常，必须用绝对路径
        proc = _run_cmd(
            [chrome_path, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--window-size=1280,800", f"--screenshot={abs_path}", url]
        )
        if proc.returncode == 0 and out_path.exists():
            return str(out_path)
    except Exception:
        pass
    return None


def _selenium_check(url: str, checks: List[Check], screenshot_path: Optional[Path]) -> Dict[str, Any]:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    driver = webdriver.Chrome(options=options)
    try:
        driver.set_page_load_timeout(20)
        driver.get(url)
        results = []
        for c in checks:
            if c.type == "contains_text":
                ok = c.target in driver.find_element(By.TAG_NAME, "body").text
                results.append({"type": c.type, "target": c.target, "ok": ok, "detail": "页面文本包含" if ok else "页面文本不包含"})
            elif c.type == "has_element":
                ok = len(driver.find_elements(By.CSS_SELECTOR, c.target)) > 0
                results.append({"type": c.type, "target": c.target, "ok": ok, "detail": "元素存在" if ok else "元素不存在"})
            elif c.type == "title_contains":
                ok = c.target in driver.title
                results.append({"type": c.type, "target": c.target, "ok": ok, "detail": f"标题: {driver.title}"})
        if screenshot_path:
            driver.save_screenshot(str(screenshot_path))
        return {"ok": all(r["ok"] for r in results), "results": results, "screenshot": str(screenshot_path) if screenshot_path and screenshot_path.exists() else None}
    finally:
        driver.quit()


def ui_check(
    url: str,
    checks: List[Check],
    backend: str = "auto",
    chrome_path: str = "",
    screenshot_path: Optional[Path] = None,
    workdir: Optional[Path] = None,
) -> Dict[str, Any]:
    """对 URL 执行一组 UI 断言。

    返回: {ok, results: [{type,target,ok,detail}], screenshot, dom_excerpt}
    """
    workdir = workdir or Path.cwd()
    if backend == "auto" or backend == "chrome":
        found_chrome = chrome_path or Config(chrome_path=chrome_path)._find_chrome()
        if found_chrome:
            dom = _chrome_dump_dom(found_chrome, url)
            soup = BeautifulSoup(dom, "html.parser")
            body_text = soup.get_text(" ", strip=True)
            results = []
            for c in checks:
                if c.type == "contains_text":
                    ok = c.target in body_text
                    results.append({"type": c.type, "target": c.target, "ok": ok,
                                    "detail": "页面文本包含" if ok else "页面文本不包含"})
                elif c.type == "has_element":
                    ok = bool(soup.select_one(c.target))
                    results.append({"type": c.type, "target": c.target, "ok": ok,
                                    "detail": "元素存在" if ok else "元素不存在"})
                elif c.type == "title_contains":
                    title = soup.title.string if soup.title else ""
                    ok = c.target in (title or "")
                    results.append({"type": c.type, "target": c.target, "ok": ok, "detail": f"标题: {title}"})
            shot = None
            if screenshot_path:
                shot = _chrome_screenshot(found_chrome, url, screenshot_path)
            return {
                "ok": all(r["ok"] for r in results),
                "results": results,
                "screenshot": shot,
                "dom_excerpt": body_text[:300],
            }
        if backend == "chrome":
            raise UiUnavailableError("未找到 Chrome/Edge，且后端指定为 chrome")

    # selenium 后端（auto 且无 Chrome 时回退）
    try:
        shot_path = screenshot_path or (workdir / "tmp_shot.png")
        return _selenium_check(url, checks, shot_path)
    except Exception as exc:
        raise UiUnavailableError(f"Selenium 不可用: {exc}")
