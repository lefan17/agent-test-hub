# 贡献指南（Contributing）

感谢愿意为 AgentTest Hub 出力。本文件说明**怎么在本仓库干活**——包括本地环境、提交规范、以及在哪些环节你会被自动化拦住。

> 只想知道怎么跑起来？看 [README.md](README.md) 的「快速开始」。

---

## 1. 本地环境

| 项目 | 要求 |
| --- | --- |
| Python | 3.9+（CI 与容器统一用 **3.12**，本机验证版本 3.12.10） |
| 浏览器 | 可选。装 Chrome 或 Edge 才能跑 `tests/test_ui_tool.py`；没有也不影响 API / RAG 测试 |
| Node.js | 可选。只有跑 `commitlint` 本地钩子才需要；有零依赖的 Python 替代方案，见第 4 节 |

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Windows
# .venv/bin/python -m pip install -r requirements-dev.txt          # Linux / macOS
```

`requirements.txt` 是**运行依赖**（被测系统 + Agent），`requirements-dev.txt` 在其之上叠加**工程化依赖**（pytest-cov / mypy / ruff / pip-audit / pre-commit）。想只跑被测系统就装前者，想跑测试与检查就装后者。

---

## 2. 提交前必须自测（与 CI 同一条命令）

```bash
# 测试套件（不含 UI），期望：129 passed, 15 xfailed, 0 failed
.venv/Scripts/python.exe -m pytest -o addopts= -q --ignore=tests/test_ui_tool.py

# 类型检查，期望：Success: no issues found
.venv/Scripts/python.exe -m mypy

# 代码规范，期望：All checks passed!
.venv/Scripts/python.exe -m ruff check .
```

### ⚠️ 一定是 `python -m pytest`，不是裸 `pytest`

本仓库的依赖清单里**没有** `pip install -e .`，`demo_app/` 与 `agenttest/` 并未装进 `site-packages`。裸 `pytest` 的控制台脚本入口不会把当前目录加进 `sys.path`，`tests/conftest.py` 的 `from demo_app.app import create_app` 会直接 `ModuleNotFoundError`，**整个套件一条都跑不起来**（exit code 4）。

`python -m pytest` 会把当前目录放进 `sys.path[0]`，与 CI 及容器内的调用方式完全一致。这条坑在 commit `8157993` 里修过，**不要改回去**。

### ⚠️ `-o addopts=` 是干什么的

`pyproject.toml` 里设了 `addopts = "-q"`，而 `-q` 会连同末尾的 `129 passed, ...` 汇总行一起吞掉。命令行传 `-o addopts=` 把配置值覆盖为空，计数才会出现在输出（以及 CI 日志）里。覆盖率参数同理一律走命令行，不塞进 `addopts`。

---

## 3. 提交信息规范（Conventional Commits）

格式：

```
<type>(<scope>): <subject>
```

- **type**（必填，小写）：`feat` `fix` `docs` `style` `refactor` `perf` `test` `build` `ci` `chore` `revert`
- **scope**（可选）：`ci` / `rag` / `api` / `ui` / `deps` / `docs` … 描述影响面
- **subject**：一句话说清做了什么，不超过 72 字符，**不要**以句号结尾
- **破坏性变更**：`feat!:` 或正文里写 `BREAKING CHANGE:`
- **Git 自动生成的** `Merge ...` / `Revert ...` / `fixup! ...` / `squash! ...` 一律豁免

示例：

```
feat(rag): 新增并列排序稳定性断言
fix(ci): 用 python -m pytest 替代裸 pytest，修复 CI 全线导入失败
docs: 补充容器化说明与本机未验证项
```

规则主体在 `commitlint.config.mjs`（Node 生态标准实现；必须是 `.mjs`，该 action 不接受 `.js`）。CI 上由 `wagoid/commitlint-github-action` 强制。

改这个文件后请务必本地跑一次：

```bash
node --check commitlint.config.mjs
node -e "import('./commitlint.config.mjs').then(m => console.log(Object.keys(m.default.rules)))"
```

第一版用的是 `.js` 扩展名，且开头用了 6 行 `#` 注释（Python/shell 风格，**不是合法 JavaScript**），因此它从来没被任何 JS 解析器读过，CI 一上就红。教训：**给 JS 生态写的文件，必须用 JS 解析器验证过再提交**。

参考最近的真实提交：`git log --oneline -20`。

---

## 4. 为什么同时存在 commitlint 和一份 Python 实现

`commitlint` 是 Node 生态的事实标准，但**它要求本机有 Node.js 与 `npm install`**。在只有 Python 的机器（同事本机、精简容器、纯 Python CI 镜像）上，它就废了。

因此仓库里另有一份零依赖实现 **`scripts/check_commit_msg.py`**（纯标准库），校验同一套规则：

```bash
python scripts/check_commit_msg.py --message "feat(ci): 演示"
python scripts/check_commit_msg.py --range "HEAD~3..HEAD"
```

两者关系：

| 场景 | 走哪个 |
| --- | --- |
| 有 Node 的开发者本机 | `.husky/commit-msg` → `commitlint`（读 `commitlint.config.mjs`） |
| 只有 Python 的机器 | `.pre-commit-config.yaml` 的 local hook → `scripts/check_commit_msg.py` |
| CI（PR / push） | `commitlint` job（Node 标准工具）+ 同一份 Python 脚本复核，双保险 |

有了 Python 版，即使某天移除 `package.json`，提交规范门禁依然成立——这是**降级路径**，不是重复劳动。

---

## 5. 本地钩子（pre-commit）

`pre-commit` 需要为每个 hook 创建独立虚拟环境，因此**首次运行需要网络**。装了之后：

```bash
.venv/Scripts/python.exe -m pre-commit install          # 装 pre-commit 与 commit-msg 两个阶段
.venv/Scripts/python.exe -m pre-commit run --all-files  # 手动全量跑
```

钩子内容见 `.pre-commit-config.yaml`，只用**已在 CI 里出现过的工具**（ruff / mypy / 基础文件卫生），刻意不引入重型或需要额外网络的 hook。

> 本仓库的开发沙箱无法运行 `pre-commit`（沙箱拒绝创建虚拟环境，`ensurepip` 报 `PermissionError: [WinError 5]`），因此该配置只做过**静态 YAML 语法校验**，从未真实执行。等价检查由 CI 的 `lint` job 承担。如果你在本地发现 hook 有问题，欢迎提 issue。

---

## 6. 容器化

```bash
# 静态校验（不需要 daemon）
# ⚠️ 必须带 --profile '*'：两个 service 都挂了 profiles，不带 profile 时
#    config 会把它们全过滤掉，退化成 `services: {}` 却依然 EXIT=0（假绿）。
docker compose --profile '*' -f docker-compose.yml config --quiet

# 路径一：只跑被测系统
docker compose --profile app up --build demo-app

# 路径二：只跑测试套件（含覆盖率门槛，等价 CI 的 test job）
docker compose --profile test up --build --abort-on-container-exit tests
```

改 `Dockerfile` / `docker-compose.yml` 时请务必**两个 profile 都实际构建一次**——CI 的 `container` job 会真的 `docker build` 并冒烟，本地只看语法是不够的。

**容器里的绑定地址必须是 `0.0.0.0`**（`AGENTTEST_BIND_HOST`）：`demo_app/app.py` 默认绑 `127.0.0.1`，在容器里那是容器自身的回环，`-p 5001:5001` 的宿主侧会被 reset（`curl: (56) Recv failure: Connection reset by peer`），而容器日志仍显示 `Running on http://127.0.0.1:5001`，极具误导性。默认值刻意保持不变，只在 `Dockerfile` 与 `docker-compose.yml` 里注入覆盖，所以本机开发行为不受影响。

---

## 7. 要动代码之前，先知道这几条铁律

1. **不改业务逻辑**：`demo_app/` 的 9 个注入缺陷是**被测对象**，不是 bug。想「顺手修好」会直接让 15 条 xfail 基准集体 XPASS 失败——那是故意的警报，不是可以忽略的噪音。
2. **不改既有测试的断言**：`tests/**` 的断言是回归网。要改测试，请在 PR 里说明**为什么原断言是错的**。
3. **覆盖率门槛不许为了让 PR 变绿而下调**：`--cov-fail-under` 的值来自实测（语句覆盖 78.09% / 分支覆盖 75.28%），门槛取 70。它拦住的是「整块模块失去覆盖」，不是「少写一行测试」。
4. **不让测试变绿的手段是补齐测试**，不是加 `@pytest.mark.skip`、不是 `# type: ignore` 糊 mypy、不是删用例。

### 关于 15 条 xfail

它们用 `@pytest.mark.xfail(strict=True)` 包住「断言契约期望行为」的用例。被测系统的缺陷**存在** → xfail（符合预期）；缺陷被修好 → **XPASS 报错**，提醒你更新基准。所以「xfail 突然变红」通常意味着被测系统被人改了，而不是测试写坏了。

---

## 8. 提交 PR 的流程

1. 从 `main` 开分支：`git switch -c feat/你的改动`
2. 改代码 → 跑第 2 节的三条命令 → 全绿
3. `git commit`（提交信息遵循第 3 节）
4. 推分支、开 PR，按模板填写（`.github/PULL_REQUEST_TEMPLATE.md`）
5. 等 CI 六个 job 全绿：`test` / `lint` / `container` / `security` / `commit-msg`，以及失败时才触发的 `notify`
6. 等 CODEOWNERS 指定的 reviewer 通过

**CI 失败时**：`notify` job 会把摘要写进运行页面的 Summary，并且——**只在 `push` 到分支或 `schedule` 定时回归失败时**——自动开一个带 `ci-failure` 标签的 issue（使用仓库内置 `GITHUB_TOKEN`，不需要任何 secret）。修好后重新推送，同一 issue 会被追加评论。

PR 失败不开 issue：状态本来就显示在 PR 页面上，重复通知只会让人开始忽略这个邮箱。

---

## 9. 依赖与安全

- **新依赖要慎重**：本项目的卖点之一是 RAG 检索走**纯标准库 BM25**，零额外检索依赖。加 `numpy` / `rank_bm25` 这类包之前先想清楚是否必要。
- **版本要钉死**：`requirements.txt` 全 `==`。改完运行 `python -m piptools compile requirements.txt -o requirements.lock` 重新生成锁文件（CI 的 `lint` job 只检查 `requirements.lock` 存在且每行是合法 pin，不重新解析依赖树）。
- **漏洞扫描**：CI 的 `security` job 跑 `pip-audit`（**非阻塞**——它查的是外部漏洞库，与本次改动的代码质量无关，一条正确的提交也可能隔天因为某个依赖被披露新 CVE 而变红）。Dependabot（`.github/dependabot.yml`）每周为 pip 与 github-actions 开依赖升级 PR。

---

## 10. 代码风格

- **ruff** 管 lint 规则，配置在 `pyproject.toml` 的 `[tool.ruff]`。**刻意未开启 `E501`（行长度）**：实测 `ruff check --select E501` 在 `line-length=88` 下报 278 处、在本仓库的 `line-length=120` 下报 18 处，开启会让 CI 一上线就是红的。同理**不跑 `ruff format`**（实测 35 个文件会被重排，绝大多数是业务代码）。两者都属于历史技术债，要清理请单开一个 PR——一次性格式化会制造跨越多个业务文件的大 diff，让本次改动无法被审阅。
- **mypy** 管类型，配置在 `pyproject.toml` 的 `[tool.mypy]`。`python_version` 与 CI 的 Python 版本对齐（3.12），扫 `agenttest` 与 `demo_app`。CI 与本地跑的是同一条命令（`python -m mypy`，不加额外参数），否则会出现「本地报错、CI 放过」的口径漂移。
- 注释写**为什么**，不写**是什么**。中文注释是本仓库的既有风格，欢迎沿用。

---

## 11. 有问题

- 用起来有疑问 → 先看 [README.md](README.md) 的「常见问题」与 [docs/操作指南.md](docs/操作指南.md)
- 发现 Bug → 用 `.github/ISSUE_TEMPLATE/bug_report.md`
- 想要新功能 → 用 `.github/ISSUE_TEMPLATE/feature_request.md`

## License

贡献即表示你同意你的代码以本仓库的 [MIT License](LICENSE) 发布。
