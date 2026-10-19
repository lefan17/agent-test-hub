"""`agenttest/tools/spec_tool.py` 的单元测试。

## 为什么补这个文件

`spec_tool.py` 原来只有 32% 覆盖（30 语句里 17 条没走过），缺的正是
`summarize_spec()` —— 它把 OpenAPI 规范压成喂给 LLM 的文本摘要。
换句话说，**LLM 规划步骤看到的输入长什么样，一行断言都没有**。

这个缺口比 runner.py 更隐蔽：runner 0% 是「一段没人测的代码」，
而 summarize_spec 0% 是「一个没人验证过的信息压缩器」。后者坏了不会报错，
只会让 LLM 收到一份残缺的契约，然后生成漏测的用例 —— 而报告依然是绿的。
"""

from __future__ import annotations

import pytest

from agenttest.tools.spec_tool import fetch_openapi, summarize_spec


class TestSummarizeSpec:
    def test_header_contains_title_and_version(self):
        spec = {"info": {"title": "待办清单 API", "version": "1.4.0"}, "paths": {}}
        out = summarize_spec(spec)
        assert "待办清单 API" in out
        assert "1.4.0" in out

    def test_missing_info_block_does_not_crash(self):
        """契约里没有 info 时不能抛 KeyError —— 上游服务不一定规范。"""
        out = summarize_spec({"paths": {}})
        assert "API:" in out

    def test_each_method_and_path_becomes_one_line(self):
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/todos": {
                    "get": {"responses": {"200": {}, "500": {}}},
                    "post": {"responses": {"201": {}}},
                },
                "/api/health": {"get": {"responses": {"200": {}}}},
            },
        }
        out = summarize_spec(spec)
        assert "GET    /api/todos" in out
        assert "POST   /api/todos" in out
        assert "GET    /api/health" in out

    def test_responses_codes_are_sorted_and_joined(self):
        """状态码排序必须是确定的 —— 否则 LLM 提示词每次都不一样，
        同样的输入跑出不同计划，就没法复现「为什么这次漏了这条用例」。"""
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {"/x": {"get": {"responses": {"500": {}, "200": {}, "404": {}}}}},
        }
        out = summarize_spec(spec)
        assert "-> 200, 404, 500" in out

    def test_parameters_are_rendered_as_in_name(self):
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/todos": {
                    "get": {
                        "responses": {"200": {}},
                        "parameters": [
                            {"in": "query", "name": "status"},
                            {"in": "query", "name": "limit"},
                        ],
                    }
                }
            },
        }
        out = summarize_spec(spec)
        assert "params=[query:status, query:limit]" in out

    def test_request_body_is_mentioned(self):
        """有请求体时摘要要出现 `requestBody` —— 没提到的话 LLM 会
        以为这是个纯 GET 接口，就不会去测非法请求体。"""
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/todos": {
                    "post": {
                        "responses": {"201": {}},
                        "requestBody": {"content": {"application/json": {}}},
                    }
                }
            },
        }
        assert "requestBody" in summarize_spec(spec)

    def test_request_body_example_reaches_the_summary(self):
        """请求体示例值必须出现在摘要里，否则 LLM 只能猜输入。

        真实故障现场：登录接口的凭据只在契约示例里，摘要只写 `requestBody`
        三个字时，真 LLM 把 password 填成 `"password"` 字面量，拿到 401 后
        归因出一个「鉴权链路异常」的缺陷 —— 那个缺陷不存在，是信息缺口造成的幻觉。
        """
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/login": {
                    "post": {
                        "responses": {"200": {}},
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "username": {"type": "string"},
                                            "password": {"type": "string"},
                                        },
                                    },
                                    "example": {"username": "admin", "password": "123456"},
                                }
                            }
                        },
                    }
                }
            },
        }
        text = summarize_spec(spec)
        assert "username:string" in text
        assert "password:string" in text
        assert "123456" in text, "示例值没进摘要，LLM 就不知道正确的输入长什么样"

    def test_request_body_without_schema_falls_back_to_bare_marker(self):
        """没有 schema 时不能崩，也不能凭空编字段 —— 退回纯 `requestBody`。"""
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {"/x": {"post": {"responses": {"201": {}}, "requestBody": {"content": {}}}}},
        }
        text = summarize_spec(spec)
        assert "requestBody" in text
        assert "{" not in text.split("requestBody")[1], "没有 schema 时不该长出字段列表"

    def test_summary_text_is_appended_in_parentheses(self):
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {"/api/health": {"get": {"responses": {"200": {}}, "summary": "健康检查"}}},
        }
        assert "(健康检查)" in summarize_spec(spec)

    def test_non_dict_operation_is_skipped(self):
        """OpenAPI 的 path item 里可以有 `parameters` 这类非 operation 键，
        它们不是 dict 形式的 op，必须跳过而不是崩掉。"""
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {"/x": {"parameters": [{"name": "ignored"}], "get": {"responses": {"200": {}}}}},
        }
        out = summarize_spec(spec)
        assert "GET    /x" in out
        # 那个非 dict 的键不能被当成一个 HTTP 方法打印出来
        assert "PARAMETERS" not in out.upper()

    def test_paths_absent_gives_header_only(self):
        out = summarize_spec({"info": {"title": "t", "version": "1"}})
        assert out.strip().count("\n") == 0, "没有 paths 时不应产出多余行"

    def test_output_is_stable_across_calls(self):
        """同一个 spec 调两次必须逐字符相同。"""
        spec = {
            "info": {"title": "t", "version": "1"},
            "paths": {"/a": {"get": {"responses": {"200": {}}}}, "/b": {"post": {"responses": {"201": {}}}}},
        }
        assert summarize_spec(spec) == summarize_spec(spec)


class TestFetchOpenApi:
    def test_fetches_from_live_service(self, demo_server_url):
        spec = fetch_openapi(demo_server_url)
        assert spec["info"]["title"] == "AgentTest Demo API"
        assert "paths" in spec

    def test_trailing_slash_in_base_url_is_handled(self, demo_server_url):
        """`base_url` 带尾斜杠时不该拼出 `//openapi.json`。

        多数服务器能吃下双斜杠，所以这个 bug 会一直不显形 ——
        直到换一个严格的路由框架，然后表现为「契约拉不到」，很难查。
        """
        a = fetch_openapi(demo_server_url)
        b = fetch_openapi(demo_server_url + "/")
        assert a == b

    def test_unreachable_host_raises(self):
        with pytest.raises(Exception):
            fetch_openapi("http://127.0.0.1:1", timeout=1.0)
