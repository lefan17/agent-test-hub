"""LLM 链路的传输层：不让环境变量决定一次调用能不能发出去。

背景是实测出来的一个**崩溃**（不是降级）：`NO_PROXY` 里同时出现 `::1` 与 `[::1]`
时，httpx 0.28.1 会生成 `all://*[::1]` 这个 pattern，解析时把 `1]` 当端口，
抛 `InvalidURL: Invalid port: ':1]'`。这个解析发生在 `httpx.Client(...)` **构造**
阶段，所以 `ChatOpenAI(...)` 连实例都造不出来 —— 不是请求失败，是代码在本地就崩。

本仓库此前已在 `api_tool` / `run_load.py` 踩过两次「传输层吃代理环境变量」的坑，
这是第三次，所以留一条钉子而不是只改代码。
"""

from __future__ import annotations

import httpx
import pytest

from agenttest.brain import llm_brain
from agenttest.config import Config


# 真实的坏值形状：本机 DSH 宿主注入的 NO_PROXY 就长这样。
_BRACKETED_LOOPBACK = "localhost,127.0.0.1,::1,[::1]"


def _client_handlers(client) -> list:
    """取 httpx 客户端实际挂上的 handlers。

    `OpenerDirector`-风格直接取 `client._mounts` 会触发 mypy `attr-defined`
    （httpx 没把私有属性写进 typeshed），所以走 getattr。
    """
    return list(getattr(client, "_mounts", {}) or [])


def test_bracketed_ipv6_in_no_proxy_reproduces_upstream_crash(monkeypatch):
    """先证明缺陷是真的：不传自定义客户端时，构造 ChatOpenAI 就直接崩。

    这条是**反向验证**——它保证下面几条不是永远为真的空断言。如果哪天 httpx 修了
    这个解析，这条会红，提醒我们「可以把这个规避措施删掉了」。
    """
    monkeypatch.setenv("NO_PROXY", _BRACKETED_LOOPBACK)
    monkeypatch.setenv("no_proxy", _BRACKETED_LOOPBACK)

    from langchain_openai import ChatOpenAI

    with pytest.raises(Exception) as excinfo:
        ChatOpenAI(
            model="deepseek-chat",
            api_key="sk-test-not-a-real-key",
            base_url="https://api.deepseek.com/v1",
            temperature=0,
            timeout=5,
        )

    msg = str(excinfo.value)
    # 不同 httpx 版本的措辞可能不同，钉住最关键的片段即可：
    # 要么是端口解析失败，要么是 URL 非法。
    assert "Invalid port" in msg or "InvalidURL" in type(excinfo.value).__name__, (
        f"预期 httpx 因 [::1] 抛 InvalidURL，实际是 {type(excinfo.value).__name__}: {msg}"
    )


def test_brain_passes_its_own_http_client(monkeypatch):
    """LLMBrain 必须显式给同步 + 异步两个客户端，缺一个就还会崩。

    实测发现只给 `http_client` 不够：`ChatOpenAI.validate_environment` 会同时建
    同步与异步两个默认客户端，崩点在 `_get_default_async_httpx_client` 那条路上。
    """
    monkeypatch.setenv("NO_PROXY", _BRACKETED_LOOPBACK)
    monkeypatch.setenv("no_proxy", _BRACKETED_LOOPBACK)

    cfg = Config()
    cfg.llm_api_key = "sk-test-not-a-real-key"
    cfg.llm_base_url = "https://api.deepseek.com/v1"

    chat = llm_brain.LLMBrain(cfg)._chat()

    assert chat.http_client is not None
    assert chat.http_async_client is not None


def test_direct_clients_do_not_read_proxy_env(monkeypatch):
    """两个客户端都不该把代理环境变量变成 mount 规则。

    `NO_PROXY` 里那条带方括号的 `[::1]` 一旦被读进来，httpx 就会在构造时抛
    `InvalidURL`。所以「成功构造出来」本身就是断言：它证明这条链路对环境无感。
    """
    monkeypatch.setenv("NO_PROXY", _BRACKETED_LOOPBACK)
    monkeypatch.setenv("no_proxy", _BRACKETED_LOOPBACK)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

    sync_client = llm_brain._direct_httpx_client()
    async_client = llm_brain._direct_async_httpx_client()

    assert isinstance(sync_client, httpx.Client)
    assert isinstance(async_client, httpx.AsyncClient)
    # trust_env=False → 没有任何环境代理 mount 规则被注册
    assert _client_handlers(sync_client) == []
    assert _client_handlers(async_client) == []


def test_explicit_proxy_is_a_real_escape_hatch(monkeypatch):
    """需要走代理的机器要有显式入口，且这个入口不能顺手把环境变量带进来。

    断言「有一个 proxy mount」而不是「没有」——这是上一条的对偶：
    两条一起才能说明这个参数真的在起作用，而不是被忽略。
    """
    monkeypatch.setenv("NO_PROXY", _BRACKETED_LOOPBACK)
    monkeypatch.setenv("no_proxy", _BRACKETED_LOOPBACK)

    client = llm_brain._direct_httpx_client("http://127.0.0.1:7897")

    assert isinstance(client, httpx.Client)
    assert _client_handlers(client), "传了 proxy 却没生成任何 mount 规则，说明这个参数被忽略了"


def test_brain_reads_proxy_from_config_not_environment(monkeypatch):
    """代理地址来自配置项，不是环境变量。

    这条钉住的是「谁能改变行为」：改 `AGENTTEST_LLM_HTTP_PROXY` 有效，
    改 `HTTP_PROXY` 无效。否则将来有人图省事把 env 读回来，缺陷会静默复发。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:1")  # 一个必然不可达的假代理
    monkeypatch.setenv("NO_PROXY", _BRACKETED_LOOPBACK)
    monkeypatch.setenv("no_proxy", _BRACKETED_LOOPBACK)
    monkeypatch.setenv("AGENTTEST_LLM_HTTP_PROXY", "")

    cfg = Config.from_env()
    assert cfg.llm_http_proxy == ""

    cfg.llm_api_key = "sk-test-not-a-real-key"
    cfg.llm_base_url = "https://api.deepseek.com/v1"
    chat = llm_brain.LLMBrain(cfg)._chat()

    # 没读 HTTP_PROXY：不会有指向那个假代理的 mount
    assert _client_handlers(chat.http_client) == []
