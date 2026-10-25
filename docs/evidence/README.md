# evidence/ · 可复核的证据

本目录把一次**真实 LLM 运行**的关键指标固化下来。

## 为什么这份特别重要

`reports/` 被 `.gitignore` 忽略，所以「Agent 真的调用了 LLM」这件事原本只存在于本机。
本文件把那次运行的身份信息（brain / fallback_reason / 时间）写进仓库，
配合在线报告页面上的 `Brain：llm（真实 LLM）` 一起构成证据链。

## summary.json

| 字段 | 含义 |
| --- | --- |
| `brain` | 实际使用的 brain（`llm` 或 `mock`） |
| `brain_fallback_reason` | 为空表示没有降级；有值说明为什么降级 |
| `produced_at` / `finished_at` | 这次运行的起止时间 |
| `metrics.total / passed / failed` | 26 / 18 / 8 |
| `defects` | 5 条缺陷的 id、严重级、类别、标题 |

## 怎么复核

```bash
export DEEPSEEK_API_KEY=...
python -m agenttest.cli run --base-url http://127.0.0.1:5001
```

跑完检查 `reports/report.json` 的 `brain_name` 与 `brain_fallback_reason`。

## petstore/ · 对第三方契约的一次真实运行

`summary.json` 记录的是「自己写的被测服务」那次。`petstore/` 是另一半证据：
**同一套流程指向公网上的第三方服务**（Swagger 官方 Petstore v3），契约从对方
`/openapi.json` 现取，用例由 LLM 现场生成。

| 文件 | 内容 |
| --- | --- |
| `petstore/report.json` | 2026-10-25 12:16:00 → 12:16:36 那次运行的完整结果（20 用例 / 16 通过 / 4 失败 / 4 缺陷） |
| `petstore/report.html` | 同一次运行的可视化报告，与在线报告同模板 |

三条它判成「对方没兑现契约」的：`POST /pet` 不校验 required 的 `name`/`photoUrls`、
不校验 `status` 枚举、`GET /user/login` 空凭据也建会话。
第四条被它自己判成 `test_issue`（依赖固定 id=1 的测试数据）。

为什么这件事重要、三个踩过的坑与诚实边界都在
[`../第三方契约验证.md`](../第三方契约验证.md)。

## 口径提醒

- **26 条 ≠ 332 条**。26 是 Agent 一次运行生成的用例数，332 是 pytest 套件的收集数。
- **recall@5 = 4/7** 是在 11 篇语料、7 条标注查询上算的，样本很小，
  只能说明「这个实现里有检索质量度量」，不能说明「这个系统检索效果好」。
