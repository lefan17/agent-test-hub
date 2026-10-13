# 更新日志（Changelog）

本文件记录本项目所有值得注意的变更。

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **关于「版本号」的诚实说明**：本仓库目前**没有任何 git tag，`pyproject.toml` 的
> `version` 停留在 `0.1.0`**，也从未正式发布过 release。因此下面按
> **日期 + 提交**记录，而不是编造版本号。等第一次打 tag 时，再把对应日期的
> 条目收进正式版本段落。
>
> 所有条目**均来自真实提交历史**（`git log --pretty=format:"%h|%ad|%s" --date=short`），
> 未做任何虚构。

---

## [Unreleased]

企业级工程化外壳补强：容器化、覆盖率门槛、静态类型检查、依赖漏洞扫描、
依赖锁文件、pre-commit、协作模板、提交规范工具化、CI 定时与失败通知、
环境分层。**不改任何业务逻辑与既有测试断言。**

### 新增

- **容器化**：`Dockerfile`（`python:3.12-slim`，`ARG INSTALL_DEV` 区分运行/测试两批依赖，
  依赖层与源码层分离以复用构建缓存）+ `docker-compose.yml`
  （`app` / `test` 两个 profile 分别对应「起被测系统」与「跑测试套件」两条路径，
  测试服务用标准库 `urllib` 探活、`depends_on: service_healthy`）。
- **`.dockerignore`**（82 行）：排除 `.git/`、`.venv/`、`__pycache__/`、各类工具缓存、
  `reports/`、`.tmp/`、`.pkgs/`、`_whl/`、`_shim/`、`.dsh-vision-router/`（本机调试截图，
  文件名还编码了本机绝对路径）、`.env*`（可能含 LLM Key，镜像层里删不掉）、
  `node_modules/`、`.github/` 等。**刻意不排除** `README.md` / `LICENSE` /
  `pyproject.toml`：`pyproject.toml` 声明了 `readme = "README.md"`，
  将来若在 Dockerfile 里加 `pip install .`，缺这些文件会直接构建失败。
- **CI：容器化 job**（`container`）——`docker compose --profile '*' config --quiet` 静态校验 +
  两个镜像真实 `docker build` + 运行镜像探活冒烟 + 测试镜像内跑测试套件。
  这一 job 的存在是为了把「本机 Docker daemon 起不来、只能静态校验」这件事
  在 CI 上真正验证掉。
- **覆盖率门槛**：CI 接 `pytest-cov`（`--cov=agenttest --cov=demo_app`），
  `--cov-fail-under=70`。门槛值来自实测而非拍脑袋（见下）。
- **`[tool.coverage.*]` 配置**（`pyproject.toml`）：开启分支覆盖（`branch = true`）、
  `omit` 掉纯 CLI 薄壳 `agenttest/__main__.py`。
- **类型检查**：`mypy` 接入 CI 新增的 `lint` job；`pyproject.toml` 新增
  `[tool.mypy]`（`python_version = "3.12"` 与 CI 运行时对齐，扫 `agenttest` + `demo_app`）。
  同时**修掉了 3 个真实类型错误**（见「修复」）。
- **代码规范**：`ruff` 接入 CI 的 `lint` job，规则集写在 `pyproject.toml [tool.ruff]`。
  刻意**未开启 `E501`（行长度）**——仓库现状有大量中文注释与长断言，
  一次性格式化会制造横跨多个业务文件的大 diff，属于把历史技术债混进本次交付。
- **安全扫描**：`.github/dependabot.yml`（`pip` + `github-actions` 两个 ecosystem，weekly）
  + CI 的 `security` job 跑 `pip-audit`（**非阻塞**，见「设计取舍」）。
- **依赖锁文件**：`requirements.lock`（由 `python -m piptools compile` 生成）；
  CI 的 `lint` job 校验其存在且每行都是合法 pin。
- **依赖分层**：`requirements-dev.txt`——在 `requirements.txt` 之上叠加
  pytest-cov / coverage / mypy / ruff / pip-audit / pip-tools / pre-commit / commitizen，
  镜像可用 `INSTALL_DEV=0` 只装运行依赖。
- **pre-commit**：`.pre-commit-config.yaml`——基础文件卫生 + ruff + mypy +
  提交信息规范 local hook。hooks 只用**已在 CI 里出现过的工具**，
  不引入需要额外网络或重型环境的 hook。
- **提交规范工具化**：`commitlint.config.js` + `.husky/commit-msg`（Node 生态标准做法），
  以及零依赖替代实现 `scripts/check_commit_msg.py`（纯标准库，
  供没有 Node 的机器与 CI 复核使用）。
- **协作模板**：`.github/PULL_REQUEST_TEMPLATE.md`、
  `.github/ISSUE_TEMPLATE/{bug_report.md,feature_request.md,config.yml}`、
  `CODEOWNERS`、`CONTRIBUTING.md`、`CHANGELOG.md`（本文件）。
- **CI 定时与并发**：`schedule`（每周日 19:00 UTC 夜间回归）+
  `concurrency`（同 ref 取消旧运行）+ 每个 job 的 `timeout-minutes` +
  `workflow_dispatch` 手动触发。
- **CI 失败通知（零 secret）**：`notify` job 在 `failure()` 时把摘要写进
  `GITHUB_STEP_SUMMARY`，并用内置 `GITHUB_TOKEN` + `actions/github-script`
  自动开/更新带 `ci-failure` 标签的 issue。需要 webhook secret 的做法
  只在注释里留说明，代码中**不引用任何 `secrets.*`**。
- **环境分层**：`AGENTTEST_ENV` 选择配置档案，
  档案文件在 `scripts/env/{dev,staging,prod}.env`。
  优先级：真实环境变量（含 `.env`）> 档案文件 > 代码默认值。
  `scripts/env/dev.env` 的取值与代码默认值逐条一致，
  **因此不设置 `AGENTTEST_ENV` 时的行为与引入分层之前完全相同**。
- **`.env.example`** 补上环境分层说明。

### 修复

修掉 `mypy agenttest demo_app` 报出的 3 个真实类型错误（均为最小改动，未使用 `# type: ignore`）：

- `agenttest/config.py:52` —— `_load_dotenv(path: Path = None)`
  的默认值类型与形参声明不符。改为 `path: Optional[Path] = None`。
- `agenttest/tools/rag_tool.py:81-82` ——
  `index_detail.get("body")` 的结果类型是 `Any | dict[Any, Any] | None`，
  直接 `.get(...)` 会报 `Item "None" ... has no attribute "get"`。
  改为先取出 `raw_body`，再用 `isinstance(raw_body, dict)` 收窄并标注为 `Dict[str, Any]`。
- `agenttest/runner.py:101-105` ——
  在 `TextIO | Any` 上直接调用 `.reconfigure()` 不成立。改为
  `reconfigure = getattr(stream, "reconfigure", None)`，取不到就 `continue`，
  取到再调用（保留「尽量把 stdout/stderr 切到 UTF-8」的原意）。

### 变更

- **`README.md`**：补充工程化能力说明；测试数字更新为本机实测值
  （`121 passed, 15 xfailed`；覆盖率实测 语句 76% / 分支 73.44%，门槛 70）。
- **`pyproject.toml`**：新增 `[tool.mypy]`、`[tool.ruff]`、`[tool.coverage.run]`、
  `[tool.coverage.report]`。
- **`agenttest/config.py`**：为支持环境分层而重构配置读取
  （抽出 `_parse_dotenv_text` / `resolve_env_name` / `load_profile`，
  `from_env()` 内改为 `pick` / `pick_int` 三级取值）。
  **默认路径行为不变**，已由既有测试套件回归。
- **`requirements-dev.txt`**：补上「钉版 vs `.venv` 实测版本」的逐条核对表。
  前六个包（pytest-cov 7.1.0 / coverage 7.16.2 / ruff 0.17.0 / mypy 2.4.0 /
  pip-audit 2.10.1 / pip-tools 7.6.2）**与实测完全一致，已核对一致**；
  后两个包（pre-commit 4.3.0 / commitizen 4.9.1）**本机从未安装过**，
  如实标注为「无法核对」，不留一个无人知晓的未验证版本号。

### 设计取舍（面试常被追问，故在此留档）

- **为什么 `pip-audit` 是非阻塞的**：它查的是**外部漏洞库**，不是本仓库的代码质量。
  同一条正确无误的提交可能今天全绿、明天因为某个依赖被披露新 CVE 而变红；
  设成阻塞会让「与本次改动无关的失败」卡住所有人的 PR。
  真正需要强迫升级时，由 Dependabot 自动开 PR。
- **为什么同时有 `commitlint` 和 `scripts/check_commit_msg.py`**：
  `commitlint` 要求本机有 Node + `npm install`；在只有 Python 的机器上就废了。
  Python 版零依赖，是**降级路径**而非重复劳动，也保证即使移掉
  `package.json`，提交规范门禁依然成立。
- **为什么 `ruff format` 既没进 pre-commit、也没进 CI**：实测
  `ruff format --check .` → **「35 files would be reformatted, 11 files already
  formatted」（EXIT=1）**。挂上它，任何人 commit 一次就会被迫格式化 35 个文件
  （绝大多数是 `agenttest/` 与 `demo_app/` 的**业务代码**），而本次交付的边界是
  「只动工程化外壳」。因此格式化留作独立任务，两端口径一致：本地与 CI 都只跑
  `ruff check`。仓库另有超长行技术债（实测 `ruff check --select E501`：
  `line-length=88` → 278 处，`line-length=120` → 18 处），E501 亦未启用。

### 已知未验证项（诚实声明）

- **容器**：本机沙箱 Docker daemon 无法启动（WSL `E_ACCESSDENIED`），
  `Dockerfile` / `docker-compose.yml` **只做过 `docker compose --profile '*' config --quiet`
  静态校验（EXIT=0），从未真实 `docker build` 或 `docker run`**。
  真实构建与冒烟由 CI 的 `container` job 承担。
  该静态校验的**强度有限**：只证明 YAML 合法 + 插值可求值 + service 结构正确，
  证明不了依赖能否装上、容器能否起来。
- **静态校验本身的假绿陷阱（已修）**：`docker compose config` 在**不激活任何
  profile** 时，会把挂了 `profiles:` 的 service 全部过滤掉，解析结果退化成
  `services: {}`，却**依然 EXIT=0**。本机实测：不带 profile → `services: {}`；
  带 `--profile '*'` → `demo-app` 与 `tests` 都在。所有校验命令
  （CI、README、CONTRIBUTING、docker-compose.yml 头注释）已统一改为
  `--profile '*'`。若不修，这条「验收通过」等于没验收。
- **`pip-audit`**：本机无法执行——它会创建临时虚拟环境，而沙箱拒绝 `ensurepip`
  （`PermissionError: [WinError 5]`）；且它把缓存写死在 `%LOCALAPPDATA%\pip-audit`，
  `--cache-dir` 也绕不过。**仅在 CI 的 `security` job 里被真实验证。**
- **`pre-commit`**：同上，需要为 hook 创建虚拟环境，本机从未执行；
  配置只做过 `yaml.safe_load` 静态语法校验。等价检查由 CI 的 `lint` job 承担。
- **`commitlint` / husky**：本机无 `node_modules`，`.husky/commit-msg`
  从未真实触发过。CI 的 `commit-msg` job 覆盖同一份规则。

---

## 2026-10-13

### 修复

- `fix(ci)`：用 `python -m pytest` 替代裸 `pytest`，修复 CI 全线导入失败（`8157993`）。
  裸 `pytest` 不会把当前目录加进 `sys.path`，`tests/conftest.py` 的
  `from demo_app.app import create_app` 直接 `ModuleNotFoundError`，
  整个套件一条都跑不起来（exit code 4）。
- `fix`：UI 断言对未实现的 `check.type` 判失败（消灭 `all([])` 假绿）+ 补三路径回归用例（`11049c9`）。
- `fix`：修复 RAG 检索链路 7 处缺陷（覆盖写入 / 并发 / 假绿断言 / 文档漂移）（`e34e66c`）。

### 变更

- `chore(ci)`：新增 GitHub Actions CI 并清理误入库的本机调试产物（`5a96f31`）。

### 新增

- `feat`：RAG 检索链路测试能力——BM25 引擎 + 6 类注入缺陷 +
  契约/不变量/质量三层断言（`87f0cc2`）。

---

## 2026-09

### 新增 / 变更

- `f1b8f85`（09-02）：修正 LICENSE 署名：改为项目真实归属。
- `a215e9a`（09-01）：`docs`：更新配套项目链接（software-testing-practice）。
- `b797327`（09-01）：`docs`：关联配套项目 software-testing-week1。
- `21f07c5`（08-25）：`fix`：`publish_report.sh` 兼容 `worktree prune` 用法。
- `f79badc`（08-25）：`fix`：`publish_report.sh` worktree 清理逻辑。
- `f5e5b93`（08-25）：`chore`：新增在线报告发布脚本 `scripts/publish_report.sh`。
- `e58fd30`（08-25）：`docs`：README 增加在线测试报告链接（GitHub Pages）。
- `fa4ce80`（08-25）：`docs`：新增操作指南（`docs/操作指南.md`）并在 README 加入口。
- `9881200`（08-25）：`feat`：Config 支持读取 `.env`（与 `.env.example` 文档保持一致）。
- `194d327`（08-25）：`chore`：添加 `.gitattributes` 统一行尾。
- `b56825e`（08-25）：`feat`：Agent 自动测试系统（LangGraph + LangChain，API/UI 双覆盖）——**首个提交**。

---

<!--
维护约定：
  - 每个 PR 请把自己的改动写进 [Unreleased] 的 新增 / 变更 / 修复 / 移除 段。
  - 「未验证项」不要删——它是给读者的诚实交代，也是下次该补的活。
  - 打第一个 tag 时，把 [Unreleased] 整段挪到 `## [x.y.z] - YYYY-MM-DD` 之下，并留一个空的 [Unreleased]。
-->
