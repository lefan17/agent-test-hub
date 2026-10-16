# 安全策略

这是**Agent 自动测试系统**，自带被测 Demo 应用（`demo_app/` 故意留有 2 个 API 契约缺陷、
1 处 Flaky 注入与 6 个 RAG 检索缺陷开关），不部署、不处理真实用户数据。所以这里的
"安全"主要指依赖链、LLM 调用凭据与测试代码本身。

## 支持的版本

只维护 `main` 的最新提交；历史提交与 fork 不接收修复。没有发布版本（`gh release list` 为空），也没有任何 tag。

## 怎么私下报告

走 GitHub Security Advisories：<https://github.com/lefan17/agent-test-hub/security/advisories/new>
**不要开公开 issue**——本仓库没有安全邮箱，Advisory 是唯一渠道，修复发布前对公众不可见。

## 我们怎么回应

3 个工作日内确认收到；确认后 7 个工作日内给出初步判断（能否复现、影响哪一部分、怎么修）。
修好后是否署名由报告者决定。

## 依赖漏洞与凭据

- `Dependabot` 覆盖 `pip`、`github-actions`（每周）与 `npm`（每月）三个 ecosystem。`pip`
  忽略主版本升级（`langchain` 一族互相锁版本，单独升必然装不上）；`github-actions` **刻意
  不忽略 major**——action 的 major 跳变常是 Node 运行时换代，旧引用会直接失效。它只开 PR。
- CI 的 `security` job 跑 `pip-audit -r requirements.lock`，设了 `continue-on-error: true`
  **非阻塞**：它查的是外部漏洞库快照，同一条提交可能今天绿明天红。报告进 step summary 与
  artifact，由人判断要不要处理。
- LLM 与 Embedding 凭据只从环境变量或 `.env` 读（`.env` 已被 `.gitignore` 忽略）。入库的
  `.env.example` 里 key 行是注释掉的占位，三个入库档案 `scripts/env/*.env` 都不写 Key。