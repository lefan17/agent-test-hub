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
- **提交规范工具化**：`commitlint.config.mjs` + `.husky/commit-msg`（Node 生态标准做法），
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

### 修复（Dependabot 分组升级的提交标题超长）

Dependabot 的分组升级标题是固定句式，仓库无法控制其长度：

```
chore(deps): bump the langchain-stack group across 1 directory with 2 updates   ← 77 字符
chore(deps-dev): bump the commitlint group across 1 directory with 2 updates    ← 76 字符
ci(deps): bump the actions group across 1 directory with 6 updates              ← 66 字符，擦线通过
```

前两条撞上本仓自定的 `header-max-length = 72`，CI 的 `commit-msg` job 判红。

**为什么不能简单放宽到 80**：72 是对**人**的约束（`CONTRIBUTING.md` 与
`scripts/check_commit_msg.py` 的 `SUBJECT_MAX` 同口径），为了迁就机器人放宽，等于对所有人工提交
一起放松。**为什么是本仓独有的问题**：另一个校验器 `scripts/check_commit_msg.py` 有
`SKIP_AUTHORS = ("dependabot[bot]", "github-actions[bot]")`，本来就不红；但
`wagoid/commitlint-github-action@v6` 只拿到提交信息文本、拿不到作者，所以只能在配置侧豁免。

**修法**：`commitlint.config.mjs` 新增 `ignores: [isDependabotGroupBump]`，谓词只匹配
「分组升级 + `with N updates` 收尾」的完整句式。已用本机真实 `@commitlint/load` +
`@commitlint/lint` 跑 10 个样本验证边界：三条真实机器标题全部放行，而人工超长 ASCII 标题、
带尾巴的伪句式、非法 type、大写 scope 仍然照常判失败。`header-max-length` 的取值**一个字未改**。

### 修复（Dependabot 与失败通知的噪声）

`.github/dependabot.yml` 与 `notify` job 在 2026-10-13 首次真正生效，产生三类可避免的通知噪声：

- **Dependabot 的 `labels` 指向了不存在的标签**：`dependencies` / `github-actions` 等标签在仓库里没有，Dependabot 不会替你创建，于是**每个 PR** 都留一条「The following labels could not be found」评论——24 个 PR 共 24 条完全相同的评论，每条一封邮件。已补齐标签（三仓一致），并在配置头部写明「改 `labels` 之前先跑 `gh label list` 核对」。
- **`reviewers` 指向自己（冗余，非噪声源）**：`reviewers: ["lefan17"]` 已从三个 ecosystem 全部移除，但**这不是「Review requested」邮件的来源**——真正的来源是 `CODEOWNERS`：实测 `atd` 与 `p02` 的 dependabot 配置从来没有 `reviewers` 字段，它们的 PR 一样被请求 review。移除它的理由是官方已把该属性标记为弃用（"the `reviewers` property is closing down"）且与 `CODEOWNERS` 重复声明。**`CODEOWNERS` 在单人仓库里同样生效，所以每开一个 PR 仍会有一封 review 请求邮件 —— 代码侧关不掉。**
- **`notify` job 在 PR 失败时也开 issue**：`title = "CI 失败：" + ref`，而每个 dependabot PR 的 merge ref 都不同（`5/merge`、`6/merge` …），于是一次失败开一个 issue，4 个 PR 产生 4 个无法去重的 issue。守卫改为 `failure() && (github.event_name == 'push' || github.event_name == 'schedule')`，与 fullstack-test-framework 的写法对齐。已积的 5 个陈旧 issue（`#1`、`#9`–`#12`）已逐个说明原因后关闭。

配置本身也做了一处顺带修正：`groups` 的**顺序**现在按「具体组在前、通配组在后」排列，因为 Dependabot 是 first-match-wins——顺序反了的话，`langchain` 的补丁级更新会被 `pip-patch` 先抓走，产生「只升 `langchain`、没升 `langgraph`」的中间态，而这两个包互相锁版本、装不上。同时给 `pip-patch` 加了 `exclude-patterns: ["langchain*", "langgraph*"]` 作为第二道保险，并把 pip 的 major 升级整体排除（major 跳变该由人显式决定并重跑回归，不该藏在例行公事的 bump PR 里）。

### 修复（CI 首次运行暴露的两条红）

推送后 CI run 两条 job 变红，均为真实缺陷，已修复：

- **容器 job 冒烟失败**：`demo_app/app.py` 把服务绑在 `127.0.0.1`，在容器里即容器自身的回环，
  宿主侧 `-p 5001:5001` 的连接被 reset（`curl: (56) Recv failure: Connection reset by peer`），
  容器日志却显示一切正常。改为 `os.getenv("AGENTTEST_BIND_HOST", "127.0.0.1")`——
  **默认值与改动前逐字符一致**，仅由 `Dockerfile` 与 `docker-compose.yml` 注入 `0.0.0.0`。
  本机以 `netstat -ano` 实测两侧：不设变量 → `TCP 127.0.0.1:5001 LISTENING`；
  设 `0.0.0.0` → `TCP 0.0.0.0:5001 LISTENING`。
  冒烟步骤同时改为**双段探活**（`docker exec` 容器内 + `curl` 宿主侧），
  失败时能自证是「服务没起来」还是「宿主机不可达」，不再只丢一句 reset。
- **提交规范 job 失败**：配置文件名原先用了 `.js` 扩展名，且开头是 6 行 `#` 注释
  （Python/shell 风格，**不是合法 JavaScript**），该文件从未被任何 JS 解析器读过；
  而 `wagoid/commitlint-github-action@v6` 只接受 `.mjs`。
  已改名为 `commitlint.config.mjs` 并写成合法 ESM（`export default {...}`，注释改 `//`），
  全部引用同步更新。本机以真实 `@commitlint/load` + `@commitlint/lint`（npm 走代理装成功）
  验证 7 个用例全部符合预期：4 条合规消息通过，非法 type / 无 type / 大写 scope 各被对应规则拒绝。

### 修复（第一轮：mypy 三个类型错误）

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
  （`121 passed, 15 xfailed`；覆盖率实测 语句 76% / 分支 73.46%，门槛 70）。
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
- **`commitlint` / husky**：`npm install` 走本机代理
  （`HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897`）**已成功**装出 `node_modules/`，
  因此 `commitlint.config.mjs` 的语法与规则已用**真实** `@commitlint/load` +
  `@commitlint/lint` 验证（7 个用例全部符合预期，见上文「修复」段）。
  但**完整的 `commitlint` CLI 仍无法本机运行**：它会 spawn `git` 取提交信息，
  而沙箱的命名管道限制让 `spawn` 报 `Error: spawn EPERM`——这是沙箱边界，
  不是配置问题。`.husky/commit-msg` 同样从未真实触发过。
  CI 的 `commit-msg` job 覆盖同一份规则。

### 变更（本轮：文档口径对齐实测 + 降级路径可观测 + 锁文件重生）

> 上一段「### 变更」里写的 `121 passed, 15 xfailed`、覆盖率 `语句 76% / 分支 73.46%`
> 是**当时**的实测值；本轮测试数增加、覆盖率上移，那些数字已过期。本节给出现值，
> 上面那段保留原样作为历史记录。

- **测试数字**：148 collected（上一轮 140）。不含 UI 实测 `129 passed, 15 xfailed`；
  含 UI 为 `4 failed, 129 passed, 15 xfailed`。新增 `tests/test_llm_brain.py`（6 条）
  与 `tests/test_report.py` 的 2 条，钉住「LLM 调用失败必须降级并如实标注」。
- **覆盖率**：仓库默认口径（分支覆盖）`TOTAL 1410 stmts / 309 miss / 366 branch = 75.28%`；
  关掉分支对照 `1415 stmts / 313 miss = 78.09%`。门槛仍取 70。
  README 的「门槛能拦住什么」反推表按新总数重算：
  `graph/nodes.py` 整体失守 → 59.5%、`demo_app/rag.py` → 60.1%、`demo_app/app.py` → 64.3%，
  三个都拦得住；但 `llm_brain.py`（75 行）整体失守仍剩 71.1%，拦不住。
  诚实结论随之修改：门槛拦得住「大模块整体失守」，拦不住单个中小模块的完全失守。
- **`docs/` 与 README 的口径修正**：把 `73.46% / 1379 / 121 passed / 140 collected`
  这一批旧数字统一改为现值（`README.md`、`CONTRIBUTING.md`、`pyproject.toml`、
  `.github/PULL_REQUEST_TEMPLATE.md`、`.github/workflows/test.yml` 的 step 名、
  `docs/操作指南.md`、`docs/简历描述.md`、`docs/面试QA.md`、`requirements-dev.txt`）。
- **删掉两处不实描述**：
  1. `README.md` 原写「Tool 抽象与 **ReAct** 设计」——代码里 `ReAct` 零命中，
     `graph/build.py` 是四条直边的确定性流水线（无 `add_conditional_edges`、无回边）。
     改为「Tool 抽象、LLM 调用失败时的降级与可观测 trace」。
  2. 三处「本机**无** Chrome」——实测 `C:\Program Files\Google\Chrome\Application\chrome.exe`
     存在且 `_find_chrome()` 能解析到，是沙箱禁止它启动子进程
     （`crashpad_client_win.cc:430 OpenProcess: 拒绝访问 (0x5)`），所以是 4 **failed** 不是 skip。
     同时如实记录 `config.py` 的已知局限：`auto` 后端只看 Chrome **是否存在**，不试运行，
     因此 Chrome 存在但起不来时不会退回 Selenium。
- **删掉死配置 `max_plan_rounds`**：`agenttest/config.py` 里它只有「定义」与
  「从 `AGENTTEST_MAX_PLAN_ROUNDS` 赋值」两处，全仓**零读取点**。
  留着会被当成「有功能没写完」，故连同 `.env.example` 的那行注释一并删除。
- **`requirements.lock` 重新生成**：原锁文件停在 `langgraph==0.2.76` /
  `langchain-openai==0.3.9`，而 `requirements.txt` 已要求 `0.6.11` / `0.3.35`，
  漂移两个 minor（`openai` 1.109.1 → 2.54.0 亦未跟上）。
  现按 `pip-compile --no-emit-index-url --no-emit-trusted-host --strip-extras` 重生成，
  228 行 / 71 个包，`requirements.txt` 的 8 条 pin 全部命中且一致。
  同时把 README 里「由 CI 的 **deps-lock** job 校验与 requirements.txt 不漂移」
  改为事实：**没有 deps-lock job**，CI 只在 `lint` job 里做「文件存在 + 每行是合法 pin」
  的轻量校验，不重新解析依赖树（那需要联网访问 PyPI）。
- **`.github/workflows/test.yml`**：两处 step 名与一处注释里的旧数字同步为 `129 passed`；
  「本地沙箱无 Chrome」改为「Chrome 存在但启不了子进程」。

### 变更（数字口径再对齐：测试数 154 / 分支覆盖 75.87%）

> 上一节写的 `148 collected / 129 passed / 分支覆盖 75.28% / 语句覆盖 78.09%` 是**当时**的
> 实测值；此后又落了几个修 bug 与补测试的提交，测试数与覆盖率都已上移。本节给出现值，
> 上面那段保留原样作为历史记录。

- **测试数字**：`154 collected`（不含 UI 为 `135 passed + 15 xfailed = 150`；
  含 UI 且能启动 Chrome 子进程时为 `139 passed, 15 xfailed, 0 failed`）。
  每文件条数三处上移：`tests/test_checks.py` 16 → 19、`tests/test_mock_brain.py` 5 → 7、
  `tests/test_rag_engine.py` 38 → 39；README 表里 11 个文件相加等于 154。
- **覆盖率**：仓库默认口径（分支覆盖）`TOTAL 1423 stmts / 306 miss / 380 branch = 75.87%`；
  关掉分支对照 `TOTAL 1428 stmts / 310 miss = 78.29%`。门槛仍取 70，余量约 5.9 个百分点。
  README 的「门槛能拦住什么」反推表按新分母（1423 语句 + 380 分支 = 1803）重算：
  `graph/nodes.py`（实测 246 行，原写 234 行）整体失守 → 59.1%、`demo_app/rag.py` → 60.9%、
  `demo_app/app.py` → 65.1%，三个都拦得住；`llm_brain.py`（75 行）整体失守仍剩 71.7%，拦不住。
- **同步位置**：`README.md`、`CONTRIBUTING.md`、`pyproject.toml`、
  `.github/workflows/test.yml`、`.github/PULL_REQUEST_TEMPLATE.md`、
  `docs/操作指南.md`、`docs/面试QA.md`、`docs/简历描述.md`。
- **UI 用例口径澄清**：4 条 UI 用例的结果取决于运行环境，不再是「本沙箱 4 failed」这一固定值，
  因此行文不再把 `4 failed` 当成当前实测；能启动 Chrome 子进程时它们全部通过
  （`139 passed`），只有禁止创建子进程的环境里才以 failed 收场。

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
