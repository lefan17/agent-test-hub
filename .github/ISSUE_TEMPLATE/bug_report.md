---
name: 🐛 Bug 报告
about: 报告一个缺陷（先确认不是「注入缺陷」——那些是被测对象，不是 bug）
title: "[Bug] "
labels: ["bug", "triage"]
assignees: []
---

<!--
⚠️ 先看这一条，能省你不少时间：

本仓库的 demo_app/ 里有 9 个**故意注入的缺陷**（2 个 API 契约/校验缺陷 + 1 个 Flaky +
6 个 RAG 检索缺陷），它们是被测系统的**被测对象**，不是待修的 bug。
「Agent 能不能发现它们」正是这个项目的卖点。

被测系统注入缺陷清单见 README 的「被测系统与注入缺陷」「RAG 注入缺陷表」两节。
如果你发现的「bug」在那两张表里 → 不是 bug，是有意为之。
如果你**改了 demo_app 之后**发现 15 条 xfail 变成 XPASS 报错 → 那也是设计如此（基准提醒）。
-->

## 缺陷描述

<!-- 一句话说清：什么坏了、你期望什么、实际发生了什么。 -->

## 复现步骤

```bash
# 尽量给出可直接粘贴的最小命令
.venv/Scripts/python.exe -m pytest tests/xxx.py -q
```

1.
2.
3.

## 期望行为

## 实际行为

```text
# 把报错原文贴这里，不要只描述
```

## 环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | <!-- Windows 11 / Ubuntu 24.04 / ... --> |
| Python 版本 | <!-- 运行 .venv/Scripts/python.exe --version --> |
| 仓库版本 / 提交 | <!-- git rev-parse --short HEAD --> |
| 安装的依赖 | <!-- requirements.txt 还是 requirements-dev.txt --> |
| AGENTTEST_ENV | <!-- dev / staging / prod（不设置即为 dev） --> |
| AGENTTEST_UI_BACKEND | <!-- auto / chrome / selenium（UI 问题必填） --> |
| 是否装了 Chrome/Edge | <!-- UI 用例需要 --> |

## 影响面

- [ ] 阻塞我完全无法使用
- [ ] 影响某一类用例（请说明哪一类）
- [ ] 只是不方便

## 补充信息

<!--
有用的线索：
  - 完整的 pytest 输出（含 FAILED/ERROR 的断言差异）
  - 报告产物片段（reports/report.html / reports/report.json）
  - 是不是稳定复现？（偶发请写大致概率，项目里有专门的重试与 flaky 归因逻辑）
  - 如果是 UI 用例失败：`--ui-backend chrome` 与 `--ui-backend selenium` 表现是否一致？
-->

## 自查确认

- [ ] 我已确认这不属于 README 里列出的「注入缺陷」
- [ ] 我已在最新 `main` 上复现（不是旧提交）
- [ ] 我已经贴了**原始报错文本**，而不是转述
