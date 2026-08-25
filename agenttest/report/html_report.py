"""HTML 报告渲染（Jinja2，自包含单文件，可直接浏览器打开）。"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, BaseLoader, select_autoescape

from agenttest.models import TestReport

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Agent 自动测试报告 - {{ report.goal }}</title>
<style>
  :root { --ok:#16a34a; --fail:#dc2626; --warn:#d97706; --muted:#6b7280; --line:#e5e7eb; }
  * { box-sizing: border-box; }
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 0; background:#f8fafc; color:#0f172a; }
  header { background: linear-gradient(135deg,#0f172a,#1e3a8a); color:#fff; padding: 28px 40px; }
  header h1 { margin: 0 0 8px; font-size: 22px; }
  header .meta { opacity: .85; font-size: 13px; line-height: 1.8; }
  main { padding: 24px 40px 60px; max-width: 1200px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 14px; margin: 20px 0; }
  .card { background:#fff; border:1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
  .card .num { font-size: 30px; font-weight: 700; }
  .card .label { color: var(--muted); font-size: 13px; margin-top: 4px; }
  .ok { color: var(--ok); } .fail { color: var(--fail); } .warn { color: var(--warn); }
  h2 { margin: 34px 0 12px; font-size: 18px; border-left: 4px solid #1e3a8a; padding-left: 10px; }
  .defect { background:#fff; border:1px solid var(--line); border-left:4px solid var(--fail); border-radius:10px; padding: 14px 16px; margin-bottom: 12px; }
  .defect.flaky { border-left-color: var(--warn); }
  .defect h3 { margin: 0 0 6px; font-size: 15px; }
  .defect .tag { display:inline-block; font-size:12px; padding:2px 8px; border-radius:999px; margin-right:8px; background:#fee2e2; color:#991b1b; }
  .defect.flaky .tag { background:#fef3c7; color:#92400e; }
  .defect p { margin: 4px 0; font-size: 13px; color:#334155; }
  table { width:100%; border-collapse: collapse; background:#fff; border:1px solid var(--line); border-radius: 10px; overflow: hidden; }
  th, td { padding: 9px 12px; font-size: 13px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
  th { background:#f1f5f9; font-weight: 600; }
  tr:last-child td { border-bottom: none; }
  .badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600; }
  .badge.pass { background:#dcfce7; color:#166534; } .badge.fail { background:#fee2e2; color:#991b1b; }
  .badge.api { background:#e0f2fe; color:#075985; } .badge.ui { background:#ede9fe; color:#5b21b6; }
  .trace { background:#0f172a; color:#d1d5db; border-radius: 10px; padding: 16px 18px; font-family: Consolas, monospace; font-size: 12px; line-height: 1.9; overflow-x: auto; }
  .trace .t-pass { color:#4ade80; } .trace .t-fail { color:#f87171; }
  img.shot { max-width: 420px; border: 1px solid var(--line); border-radius: 8px; margin-top: 8px; }
  .muted { color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>🤖 Agent 自动测试报告</h1>
  <div class="meta">
    目标：{{ report.goal }}<br>
    Brain：{{ report.brain_name }}{% if report.llm_configured %}（真实 LLM）{% else %}（规则引擎 Mock，未配置 API Key）{% endif %}
    ｜ 被测系统：{{ report.base_url }}
    ｜ 开始 {{ report.started_at }} ～ 结束 {{ report.finished_at }}
  </div>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="num">{{ report.summary.total }}</div><div class="label">用例总数</div></div>
    <div class="card"><div class="num ok">{{ report.summary.passed }}</div><div class="label">通过</div></div>
    <div class="card"><div class="num fail">{{ report.summary.failed }}</div><div class="label">失败</div></div>
    <div class="card"><div class="num fail">{{ report.summary.defects }}</div><div class="label">发现缺陷</div></div>
  </div>

  {% if report.defects %}
  <h2>🐞 发现的缺陷（Agent 归因）</h2>
  {% for d in report.defects %}
  <div class="defect {{ 'flaky' if d.category == 'flaky' else '' }}">
    <h3>{{ d.id }} {{ d.title }}
      <span class="tag">{{ d.severity }} / {{ d.category }}</span>
    </h3>
    <p><b>端点：</b>{{ d.endpoint }}</p>
    <p><b>证据：</b>{{ d.evidence }}</p>
    <p><b>建议：</b>{{ d.suggestion }}</p>
    <p class="muted">关联用例：{{ d.related_case_ids | join(', ') }}</p>
  </div>
  {% endfor %}
  {% else %}
  <p class="muted">本次运行未发现缺陷 🎉</p>
  {% endif %}

  <h2>📋 用例执行明细</h2>
  <table>
    <tr><th>ID</th><th>标题</th><th>类型</th><th>期望</th><th>实际</th><th>结果</th><th>耗时</th><th>备注</th></tr>
    {% for c in report.cases %}
    <tr>
      <td>{{ c.case_id }}</td>
      <td>{{ c.title }}</td>
      <td><span class="badge {{ 'ui' if c.kind == 'ui' else 'api' }}">{{ c.kind }}</span></td>
      <td>{{ c.expected }}</td>
      <td>{{ c.actual[:120] }}</td>
      <td><span class="badge {{ 'pass' if c.ok else 'fail' }}">{{ 'PASS' if c.ok else 'FAIL' }}</span></td>
      <td>{{ c.duration_ms }}ms</td>
      <td>
        {% if c.error %}<div class="fail" style="font-size:12px">{{ c.error[:200] }}</div>{% endif %}
        {% if c.retried %}<div class="warn muted">已重试 {{ c.attempts - 1 }} 次</div>{% endif %}
        {% if c.evidence %}<div class="muted">{{ c.evidence[:200] }}</div>{% endif %}
        {% if c.kind == 'ui' and shot_exists(c.case_id) %}<img class="shot" src="screenshot_{{ c.case_id }}.png" alt="截图">{% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>

  <h2>🗺️ 覆盖率</h2>
  <table>
    <tr><th>端点 / 页面</th><th>覆盖用例</th></tr>
    {% for ep, ids in report.coverage.items() %}
    <tr><td>{{ ep }}</td><td>{{ ids | join(', ') }}</td></tr>
    {% endfor %}
  </table>

  <h2>🧠 Agent 思考轨迹（trace）</h2>
  <div class="trace">
    {% for line in report.trace %}
    <div>{{ line }}</div>
    {% endfor %}
  </div>
</main>
</body>
</html>
"""


def render_html_report(report: TestReport, report_dir=None) -> str:
    env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))
    template = env.from_string(TEMPLATE)
    report_dir = Path(report_dir) if report_dir else Path("reports")

    def shot_exists(case_id: str) -> bool:
        return (report_dir / f"screenshot_{case_id}.png").exists()

    template.globals["shot_exists"] = shot_exists
    return template.render(report=report)
