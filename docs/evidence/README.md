# evidence/ · 可复核的证据

本目录把一次**真实 LLM 运行**的关键指标固化下来。

## 为什么这份特别重要

`reports/` 被 `.gitignore` 忽略，所以「Agent 真的调用了 LLM」这件事原本只存在于本机。
本文件把那次运行的身份信息（brain / fallback_reason / 时间）写进仓库，
配合在线报告页面上的 `Brain：llm（真实 LLM）` 一起构成证据链。

## summary.json

| 字段 | 含义 |
| --- | --- |
`brain` | 实际使用的 brain（`llm` 或 `mock`） |
`brain_fallback_reason` | 为空表示没有降级；有值说明为什么降级 |
`produced_at` / `finished_at` | 这次运行的起止时间 |
`metrics.total / passed / failed` | 26 / 18 / 8 |
`defects` | 5 条缺陷的 id、严重级、类别、标题 |

## 怎么复核

```bash
export DEEPSEEK_API_KEY=...
python -m agenttest.cli run --base-url http://127.0.0.1:5001
```

跑完检查 `reports/report.json` 的 `brain_name` 与 `brain_fallback_reason`。

## 口径提醒

- **26 条 ≠ 332 条**。26 是 Agent 一次运行生成的用例数，332 是 pytest 套件的收集数。
- **recall@5 = 4/7** 是在 11 篇语料、7 条标注查询上算的，样本很小，
  只能说明「这个实现里有检索质量度量」，不能说明「这个系统检索效果好」。
