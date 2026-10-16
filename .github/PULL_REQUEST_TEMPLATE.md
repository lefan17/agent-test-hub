# 描述

<!-- 一两句话说明这个 PR 做了什么、为什么需要它。 -->

Closes #

## 改动类型

<!-- 勾选一个或多个；标题与本 PR 的 Conventional Commits 前缀应当对得上 -->

- [ ] `feat` 新功能
- [ ] `fix` 缺陷修复
- [ ] `docs` 文档
- [ ] `refactor` 重构（不改变外部行为）
- [ ] `perf` 性能优化
- [ ] `test` 测试补充 / 修正
- [ ] `build` 构建、依赖、容器
- [ ] `ci` CI 配置
- [ ] `chore` 杂项
- [ ] `revert` 回滚

## 改了什么

<!-- 按模块列点。读者应该能只看这一段就知道 diff 的形状。 -->

-

## 验收证据

<!--
不要只写「我跑过了」。把命令和关键输出行贴上来（可截尾），
让 reviewer 能自己复现。
-->

```text
# 1) 测试套件（不含 UI）
$ .venv/Scripts/python.exe -m pytest -o addopts= -q --ignore=tests/test_ui_tool.py
161 passed, 15 xfailed, 0 failed

# 2) 覆盖率门槛
$ .venv/Scripts/python.exe -m pytest -o addopts= -q --ignore=tests/test_ui_tool.py \
    --cov=agenttest --cov=demo_app --cov-report=term-missing --cov-fail-under=70
TOTAL  ...
Required test coverage of 70.0% reached.

# 3) 类型检查
$ .venv/Scripts/python.exe -m mypy
Success: no issues found in 24 source files

# 4) 代码规范
$ .venv/Scripts/python.exe -m ruff check .
All checks passed!
```

## 检查清单

- [ ] **测试数字没有下降**：仍然 `161 passed, 15 xfailed, 0 failed`
- [ ] 没有改 `tests/**` 的既有断言（若改了，已在下方说明**为什么原断言是错的**）
- [ ] 没有改 `demo_app/**` 的注入缺陷（9 个缺陷是**被测对象**，不是待修的 bug）
- [ ] 没有为了让 CI 变绿而下调覆盖率门槛 / 加 `@pytest.mark.skip` / 加 `# type: ignore` 糊 mypy
- [ ] 新增/修改的 YAML 已用 `yaml.safe_load` 解析通过
- [ ] 若改了 `requirements.txt`，已重新生成 `requirements.lock`
- [ ] 若改了 `Dockerfile` / `docker-compose.yml`，已实际构建过（CI 的 `container` job 会真构建）
- [ ] 提交信息符合 [Conventional Commits](CONTRIBUTING.md#3-提交信息规范conventional-commits)
- [ ] 已在 `[Unreleased]` 段补充 CHANGELOG 条目（若属用户可见变更）

## 影响面与风险

<!--
有没有破坏性变更？有没有需要同步改文档/配置的地方？
有没有本机无法验证、只能等 CI 的部分？如有，明确写出来。
-->

- 破坏性变更：无 / 有（说明：）
- 未验证项（本机跑不了，只能等 CI）：

## 截图 / 报告

<!-- UI 相关改动请附截图；报告相关改动请附 report.html 片段或数据。 -->
