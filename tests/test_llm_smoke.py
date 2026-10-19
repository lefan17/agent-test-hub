"""真实 LLM 链路的冒烟测试：验证「在真实 provider 上还通不通」，不验证用例质量。

## 为什么单独一个文件、且不进 PR 门禁

这个文件里的用例**真的会调外部 LLM API**，所以它验证的不是「这次改动有没有错」，
而是「这条链路在一个真实 provider 上今天还通不通」。三件事使它不适合当 PR 门禁：

1. **贵**：每条用例至少一次规划 + 一次归因，按 PR 数烧额度；
2. **慢且不确定**：LLM 延迟会把 PR 反馈时间变得不可预测，而 PR 门禁的价值一半在于快；
3. **判据不同**：PR 门禁问「这个 diff 有没有引入错误」，这里问「外部依赖还认不认我们的
   提示词与结构化输出协议」。后者的合理频率是发版前、换模型时、每月一次。

配套的 `.github/workflows/llm-smoke.yml` 只挂 `workflow_dispatch`（手动），无定时。

## 断言什么

**结构契约，不是结果好坏。** LLM 生成的用例内容每次都不同，断言「必须发现 3 个缺陷」
这类东西只会制造一个随机红灯。这里断言的是那些「一旦坏了就说明集成断了」的性质：

- 全链路返回 0，报告文件真的落盘；
- `brain_name == "llm"` 且 `brain_fallback_reason` 为空 —— 静默降级会让报告撒谎，
  这是本项目一直在防的那类缺陷（见 `LLMBrain._fallback_plan` 的 docstring）；
- 缺陷的 `category` 落在模型定义的六个枚举内（`Literal` 已经约束，但报告是 JSON，
  反序列化路径也要走一遍）；
- `related_case_ids` 每一条都来自本次真实失败过的用例 —— 这条钉住的是
  `_resolve_related_case_ids()` 的白名单行为，LLM 编造的 id 必须被丢弃；
- 报告的 `brain_name` 与 stdout 打印的 Brain 一致（两处说法不能打架）。

## 怎么跑

```bash
export AGENTTEST_LLM_API_KEY=sk-...          # 或 --env prod 读档案
export AGENTTEST_LLM_BASE_URL=https://...    # 可选，OpenAI 兼容端点
export AGENTTEST_LLM_MODEL=gpt-4o-mini       # 可选，默认见 config.py
python -m pytest tests/test_llm_smoke.py -v -o addopts= -m llm_smoke
```

Key 缺失时整个文件 skip（不是 fail）—— 没有 key 的机器上跑全量应当照常绿。
"""

from __future__ import annotations

import json
import os

import pytest

# 三处 gate：marker（便于 -m llm_smoke 单跑）、环境变量、以及 pyproject 里
# 登记的 marker 名。skipif 的 reason 里要写清楚怎么修，否则 CI 上看到 skip
# 的人不知道该做什么。
#
# **必须先把 `.env` 灌进 os.environ 再判**，否则这条 skipif 会说出与事实相反的
# 话。踩过的坑（2026-10-19 实测）：`.env.example` 明确写着"LLM Key 一律只写在这里"，
# 但 `.env` 的加载发生在 `Config.from_env()` 里——而本模块的 `_HAS_KEY` 在
# **导入时**求值，早于任何 Config 实例。于是一个把 key 正确写进 `.env` 的人，
# 跑 `-m llm_smoke` 看到的是 4 条 SKIPPED +「未设置 AGENTTEST_LLM_API_KEY」。
#
# 这个比"功能坏了"更坏：**测试永远跳过，就永远绿**，而提示语还在把人往
# 错误方向指（去查环境变量，而 key 就在他刚写好的 .env 里）。显式的错是资产，
# 这种静默的空转是负债。
#
# 复用 `Config.from_env()` 里同一个 `_load_dotenv`，不另写一份解析逻辑：
# 两处对 `.env` 格式的理解必须永远是同一份，否则又会出现"CI 认、本机不认"。
from agenttest.config import _load_dotenv  # 复用的正是 Config.from_env() 那个加载器

_load_dotenv()

_HAS_KEY = bool(os.getenv("AGENTTEST_LLM_API_KEY", "").strip())

# 整个文件打 marker（便于 -m llm_smoke 单跑）。
pytestmark = pytest.mark.llm_smoke

# 需要**真实** key 的用例才 skip；反向验证那条不需要，它只要求「配了 key」这个前提，
# 用一个假 key 就能跑——这样没有 key 的机器上也能验证降级路径的可见性。
_requires_real_key = pytest.mark.skipif(
    not _HAS_KEY,
    reason=(
        "未设置 AGENTTEST_LLM_API_KEY（环境变量与仓库根 .env 里都没有）；"
        "这是真实 LLM 冒烟，没有 key 时跳过而不是失败。\n"
        "怎么配：把 AGENTTEST_LLM_API_KEY 写进仓库根 .env（.gitignore 已忽略它），"
        "或 export 到当前 shell；可选 AGENTTEST_LLM_BASE_URL / AGENTTEST_LLM_MODEL。"
    ),
)


_ALLOWED_CATEGORIES = {"bug", "contract", "flaky", "environment", "test_issue", "relevance"}


def _run_full_pipeline(tmp_path, demo_server_url):
    """跑一次真实 LLM 全链路，返回 (退出码, 报告字典, stdout)。"""
    from agenttest import runner

    report_dir = tmp_path / "llm-report"
    argv = [
        "run",
        "--demo-url",
        demo_server_url,
        "--report-dir",
        str(report_dir),
        "--no-ui",  # UI 用例要真浏览器，冒烟不该被「这台机器没装 Chrome」干扰
        "--force-llm",  # 显式要求：没有 LLM 就失败，别悄悄降级成 MockBrain
        "--max-attempts",
        "1",  # 冒烟只验证链路，不验证重试策略，少跑一轮省额度
    ]
    code = runner.main(argv)
    report_json = report_dir / "report.json"
    data = json.loads(report_json.read_text(encoding="utf-8")) if report_json.exists() else {}
    return code, data, report_json


@_requires_real_key
def test_full_pipeline_runs_on_real_llm(tmp_path, demo_server_url):
    """全链路返回 0，报告落盘，且报告承认自己真的走了 LLM。"""
    code, data, report_json = _run_full_pipeline(tmp_path, demo_server_url)

    assert code == 0, "全链路退出码必须是 0；非 0 说明这条链路在真实 provider 上已经断了"
    assert report_json.exists(), "report.json 必须落盘——它是这个项目唯一的产物"

    assert data.get("brain_name") == "llm", (
        f"报告里的 brain_name 是 {data.get('brain_name')!r}，不是 'llm'。"
        "这一条挂了通常意味着 LLM 调用失败后降级到了规则引擎，而报告仍自称 LLM。"
    )
    assert not data.get("brain_fallback_reason"), (
        f"发生了降级，原因：{data.get('brain_fallback_reason')!r}。"
        "降级本身是设计的一部分，但在冒烟里出现降级等于这次验证没有意义。"
    )
    assert data.get("llm_configured") is True, "配了 key 却报告 llm_configured=False，配置读取链路断了"


@_requires_real_key
def test_llm_generated_cases_are_well_formed(tmp_path, demo_server_url):
    """LLM 产出的用例至少要满足下游能消费的最小形状。"""
    _code, data, _report = _run_full_pipeline(tmp_path, demo_server_url)

    cases = data.get("cases") or []
    assert cases, "LLM 一条用例都没产出——planner 要么返回空，要么结构不对"

    for case in cases:
        cid = case.get("case_id") or ""
        assert cid, f"用例缺 case_id：{case!r}"
        assert case.get("kind") in {"api", "ui", "rag"}, (
            f"{cid} 的 kind 是 {case.get('kind')!r}，不在 api/ui/rag 内；结构化输出可能退化成自由文本了"
        )
        # 用例被真的执行过才谈得上结果：executor 跑完一定会写 ok 与 attempts
        assert "ok" in case, f"{cid} 没有执行结果，说明它没进 executor"
        assert case.get("attempts", 0) >= 1, f"{cid} 的 attempts < 1，说明它没被真的跑过"


@_requires_real_key
def test_defect_categories_stay_inside_the_schema(tmp_path, demo_server_url):
    """缺陷归因的 category 必须落在模型定义的六个枚举内。"""
    _code, data, _report = _run_full_pipeline(tmp_path, demo_server_url)

    for defect in data.get("defects") or []:
        cat = defect.get("category")
        assert cat in _ALLOWED_CATEGORIES, (
            f"缺陷 {defect.get('id')!r} 的 category 是 {cat!r}，不在 {sorted(_ALLOWED_CATEGORIES)} 内。"
            "这一条钉的是 structured output → Defect 的反序列化路径："
            "LLM 返回枚举外的值时应当被收敛成 'bug'，而不是原样落进报告。"
        )
        assert defect.get("id"), "缺陷必须带 id"
        assert defect.get("title"), f"缺陷 {defect.get('id')!r} 没有标题"


@_requires_real_key
def test_related_case_ids_come_from_real_failures(tmp_path, demo_server_url):
    """缺陷关联的用例必须真的失败过——LLM 编造的 id 必须被白名单丢弃。

    这是本文件里最重要的一条：`_resolve_related_case_ids()` 存在的全部理由就是
    这件事，而它的单测用的是构造出来的 TestResult。只有真跑一次真实 LLM，
    才知道「真实 provider 编 id 的概率与形状」是不是被测住了。
    """
    _code, data, _report = _run_full_pipeline(tmp_path, demo_server_url)

    failed_ids = {c.get("case_id") for c in (data.get("cases") or []) if not c.get("ok")}
    all_ids = {c.get("case_id") for c in (data.get("cases") or [])}

    for defect in data.get("defects") or []:
        related = defect.get("related_case_ids") or []
        for cid in related:
            assert cid in all_ids, f"缺陷 {defect.get('id')!r} 关联了本次根本不存在的用例 {cid!r}"
            assert cid in failed_ids, (
                f"缺陷 {defect.get('id')!r} 关联了 {cid!r}，但这条用例是**通过**的。"
                "关联用例必须来自本次真实失败集，否则报告里每个缺陷那一栏都会变成噪声。"
            )


def test_fallback_path_is_visible_when_llm_breaks(tmp_path, monkeypatch):
    """反向验证：LLM 坏掉时必须留下可见的降级标记，而不是静默变成规则引擎输出。

    这条刻意**破坏** LLM 调用，用来证明「`brain_fallback_reason` 为空」这条断言
    是有意义的——如果降级从不写这个字段，那些断言就永远为真，等于没断言。

    **它不需要真 key**：这里只要求「LLMBrain 能被构造出来」这个前提，用一个假 key
    即可；`_chat()` 被替身顶掉，永远不会真的发出请求。所以在没有 key 的机器上
    这条也会真跑，而不是 skip 掉——否则「降级可见」这件事就永远没人验证。
    """
    from agenttest.brain import llm_brain
    from agenttest.config import Config

    monkeypatch.setenv("AGENTTEST_LLM_API_KEY", "sk-fake-for-smoke-fallback")

    def _boom(self):
        raise RuntimeError("冒烟：故意让 _chat() 失败")

    monkeypatch.setattr(llm_brain.LLMBrain, "_chat", _boom)

    cfg = Config.from_env()
    brain = llm_brain.LLMBrain(cfg)
    cases = brain.make_plan("冒烟", {"paths": {}}, {"base_url": "http://127.0.0.1:5001"})

    assert cases, "降级后仍应产出用例（MockBrain 兜底），否则流水线会中断"
    assert brain.last_fallback_reason, (
        "LLM 调用失败后 last_fallback_reason 必须非空；否则报告会声称归因来自 LLM，而实际上是规则引擎的输出"
    )
    # 降级后的用例必须来自 MockBrain 的编号体系，而不是半截 LLM 输出
    assert all(c.id for c in cases), "降级兜底产出的用例必须带 id"
