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

### 新增（2026-10-18：per-file 覆盖率门禁，补上仓库级门槛结构性看不见的那块）

**先讲清为什么要做这件事。** 仓库级 `--cov-fail-under=70` 有一个结构性盲区：它只看总量。
本仓的活证据是 `agenttest/runner.py` —— 126 语句 / 34 分支的系统主入口，
**原先是 0% 覆盖（missing 10-198 全段）**，而总覆盖率仍有 76.88%、稳稳高于 70。
也就是说：把这套系统的 CLI 整个删掉，70 这道门禁不会响。一个 0% 的文件对
「总量百分比」的贡献是零，所以它对总量门禁完全隐形。
（这件事 README 早就写清楚了，只是当时没有工具去判 —— 记在旧 Unreleased 段的
「后续任务」里。）

- **`scripts/check_coverage.py`（新增，约 300 行）**：读 `--cov-report=json` 的产物，
  按 `pyproject.toml` 的 `[tool.coverage.perfile]` 逐文件判是否达标。
  之所以要自己写：**coverage 7.16.2 的 `CoverageConfig` 只有全局 `fail_under`，
  没有 per-file 版本**（`[a for a in dir(CoverageConfig()) if 'fail' in a]` 实测只返回
  `['fail_under']`），所以只能读 JSON 自己判。
  四条刻意的设计：① 配置缺失（缺 `[tool.coverage.perfile]` 表 / 缺 `fail_under` /
  缺 `min_statements` / 缺 JSON 产物）一律**退出 1**，不静默通过 ——
  一个「配置写错就等于没门禁」的门禁是最坏的门禁；② `exclude` 里每条路径都要
  **验证真实存在**，写错的排除等于「看起来写了实际没写」；③ 失败输出按百分比**升序**
  并带缺失行号区间，日志里一眼看到最差的文件和该补哪几行；④ 强制 UTF-8 stdout
  （`_force_utf8_stdio()`），否则 Windows GBK 控制台下打 `↔` 会直接 `UnicodeEncodeError`。
- **`pyproject.toml` 新增 `[tool.coverage.perfile]`**：`fail_under = 60`、
  `min_statements = 10`、`exclude = []`。**刻意留空 `exclude`**——
  空清单是可验证状态，而一条随手写的排除会让这张清单慢慢变成静音名单。
- **CI `test` job 新增独立一步「per-file 覆盖率门禁（拦单点失守）」**，
  `if: always()`、**阻塞**（不是 `continue-on-error`）。独立成步的另一个好处是
  失败信息独立成段，日志里直接看到哪个文件、缺哪些行。
- **补上四处真缺口**（覆盖率 76.88% → 91.25%，新增 138 条用例）：

  | 文件 | 覆盖 | 新增测试 | 补的是什么 |
  | --- | --- | --- | --- |
  | `agenttest/runner.py` | **0% → 92.5%** | `tests/test_runner_cli.py`（27 条） | `parse_args` 的每个 flag 与非法值、`pick_brain` 三种分支、`wait_for_health` 的超时与重试、`DemoServer` 生命周期、`main()` 的 0/1 两条退出路径 |
  | `agenttest/config.py` | **39% → 97.4%** | `tests/test_config.py`（40 条） | 三层优先级（环境变量 > 档案 > 代码默认）逐层验证、dotenv 不覆盖已有变量、非法环境名报错、`llm_configured` 真假两态、`_find_chrome` 路径有效性 |
  | `agenttest/tools/ui_tool.py` | **78% → 99.2%** | `tests/test_ui_tool_errors.py`（31 条） | 不依赖浏览器：chrome 非零退出/空输出、无 Chrome 时抛 `UiUnavailableError`、**未实现的断言类型必须判失败**（两个后端各测）、selenium 异常时 driver 仍 quit |
  | `agenttest/tools/spec_tool.py` | **32% → 100%** | `tests/test_spec_tool.py`（13 条） | 契约解析异常分支：缺 `paths`、非法 JSON、非 dict 响应 |
  | `scripts/check_coverage.py` | 100% | `tests/test_check_coverage_script.py`（25 条） | 门禁脚本自身的测试：配置缺失要退 1、排除路径写错要报错、失败按百分比升序、JSON 里改低 1pp 必须被抓到 |
- 顺带修掉一处真实缺陷：`DemoServer.__init__` 里 `int(url.rsplit(":", 1)[-1])` 在
  `--demo-url` 不带端口时抛裸 `ValueError: invalid literal for int()`，
  看不出是哪个参数错了。改成能照着改的报错（说清要什么、收到了什么、解析出的端口部分是什么），
  由 `tests/test_runner_cli.py` 钉住。

### 设计取舍（本轮新增）

- **门槛是棘轮，不是目标。** 首轮取 30，只抓 `runner.py` 一个文件、跑出那条可解释的红
  （`runner.py 0.0% 122 语句；缺失 10, 12-18, 20-21, …共 26 段`），留证后再修；
  补完四处缺口后参与判定的 16 个文件最低是 `demo_app/rag.py` 81.1%，
  于是抬到 **60**。以后每清完一轮再抬，每轮只抬一次。
- **为什么不一次抬到 75**：离 `rag.py` 只剩 6pp，随便加一个分支就红。
  一个经常无故变红的门禁，最后一定会被 `continue-on-error` 绕过 —— 那比没有门禁更坏。
  **本仓有前例**：`ruff format` 曾经长期挂着 COE，后来才被清干净改成阻塞项。
- **为什么不追 `llm_brain.py` 剩下的 15.7%**：那几行是真实联网调用与结构化解析，
  需要真 API key。它属于「链路通不通」的验证，不属于 PR 门禁，留给手动触发的
  `pytest -m llm_smoke` 冒烟（见下一条新增）。
- **仓库级 70 为什么没往上抬**：补完测试后总量 91%，70 的余量已经 21pp，
  它现在只剩「拦住大面积同时失守」这一个作用；提高它不会增加单点保护能力，
  单点保护交给 per-file 门禁。两个门禁分工明确，不是一个顶替另一个。

### 新增（2026-10-19：真实 LLM 冒烟，把「链路还通不通」从口头承诺变成可执行断言）

**为什么它与主 CI 分开。** 主 CI 问的是「这个 diff 有没有引入错误」，冒烟问的是
「这条链路在一个真实 provider 上今天还通不通」。后者**贵**（每条用例至少一次规划 +
一次归因两次真实调用，按 PR 数烧额度）、**慢且不确定**（LLM 延迟让 PR 反馈时间不可
预测，而 PR 门禁的价值一半在于快）、**判据也不同**。合理频率是发版前 / 换模型时 /
每月一次，所以只挂 `workflow_dispatch`，无定时、无 push/PR 触发。

反面教材就在本仓历史里：一个挂在 PR 上、又贵又不稳定的检查，最后一定会被改成
`continue-on-error` 或者被绕过 —— `ruff format` 就是这么被废掉过一次的。

- `tests/test_llm_smoke.py`（5 条）：4 条真实链路 + 1 条**不需要 Key** 的反向验证。
  断言的是**结构契约而非结果好坏** —— LLM 生成的用例每次都不同，断言「必须发现 3 个缺陷」
  只会制造一个随机红灯。具体钉住：全链路退出码 0 且 `report.json` 落盘；
  `brain_name == "llm"` 且 `brain_fallback_reason` 为空（静默降级会让报告撒谎，
  这是本项目一直在防的那类缺陷）；用例 `kind` ∈ {api, ui, rag} 且 `attempts ≥ 1`
  （结构化输出退化成自由文本、或用例没真的进 executor 都会在这里露出来）；
  缺陷 `category` 落在六个枚举内（反向验证 `Literal` 约束在 JSON 反序列化路径上真的生效）；
  `related_case_ids` 每条都来自本次真实失败集（钉 `_resolve_related_case_ids()` 的白名单）。
- **那条不需要 Key 的反向验证**（`test_fallback_path_is_visible_when_llm_breaks`）用替身
  把 `_chat()` 打坏，断言 `last_fallback_reason` 非空。它的作用是证明上面那句
  「`brain_fallback_reason` 为空」不是永远为真的空断言 —— 如果降级从不写这个字段，
  那条断言就毫无意义。它用假 Key 即可，所以在任何机器上都真跑、不 skip。
- `.github/workflows/llm-smoke.yml`：只挂 `workflow_dispatch`，带 `model` / `base_url`
  两个输入。**没配 secret `LLM_API_KEY` 时第一个 step 显式失败并打印怎么配**，
  而不是静默跳过 —— 一个静默跳过的冒烟等于没有冒烟，点按钮的人会以为跑过了。
- `pyproject.toml` 注册 `llm_smoke` marker（没有这条会出 `PytestUnknownMarkWarning`，
  而一个没人能过滤的 marker 等于没有 marker）。

**无 Key 时的行为**：那 4 条 skip（不是 fail），全量套件照常绿（308 passed, 4 skipped,
15 xfailed；同一台机器配上 Key 之后是 312 passed, 15 xfailed）。收集数从 316 涨到 327（4 条 LLM 冒烟 + 3 条 `invalid_case` 回归 +
4 条把 `.env` 从开发机依赖变成显式输入）。

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
- **为什么 `ruff format` 起初既没进 pre-commit、也没进 CI**：实测
  `ruff format --check .` → **「35 files would be reformatted, 11 files already
  formatted」（EXIT=1）**。挂上它，任何人 commit 一次就会被迫格式化 35 个文件
  （绝大多数是 `agenttest/` 与 `demo_app/` 的**业务代码**），而本次交付的边界是
  「只动工程化外壳」。因此格式化留作独立任务，两端口径一致：本地与 CI 都只跑
  `ruff check`。仓库另有超长行技术债（实测 `ruff check --select E501`：
  `line-length=88` → 278 处，`line-length=120` → 18 处），E501 亦未启用。
  > **后续（2026-10-18）**：这个「独立任务」已经做完了——`ruff format .` 重排
  > 38 个文件（当时是 38 而非 35，因为期间又加了新文件），重排后
  > `50 files already formatted`；同一棵树复跑 `pytest -o addopts= -q` 仍是
  > 165 passed / 15 xfailed、`python -m mypy` 仍是 24 source files 零问题。
  > 存量清零后 `ruff-format` 已同时挂进 `.pre-commit-config.yaml` 与 CI 的 `lint`
  > job（两边都跑 `--check`）。E501 仍刻意不启用，理由不变：它是风格债，
  > 不是正确性问题，且与 `[tool.ruff]` 的 line-length 选择绑定。

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

### 修复（第三轮：RAG 覆盖键、缺陷关联用例、评测集端到端判阈值）

三个问题都不是「代码写错了」，而是**报告与判据说了不真实的话**，故各自补了能证伪的测试。

- **coverage 键硬编码**（`agenttest/graph/nodes.py`）：RAG 分支原先一律返回
  `RAG GET /api/rag/search`，于是 10 条 RAG 用例（含依次打了 5 个端点的索引一致性探针）
  在报告里塌成同一个键，而 `/api/rag/index`、`/api/rag/reindex`、`/api/rag/docs`
  这三个真实被调用的端点显示为「无人覆盖」。现改为 `_coverage_keys()` 返回列表：
  检索与契约用例 1 个键，一致性探针 5 个键；键统一为 `method + path`。
  全量 28 条用例的键数 13 → 17（`--no-ui` 时 10 → 14）。
  局限已知并写进代码注释：coverage 是**声明**（用例将打哪些端点），不是执行期实测；
  `tests/test_rag_coverage.py` 用 `rag_tool` 的真实调用记录核对声明表，
  谁给 `rag_tool` 加了端点却没同步声明，测试就变红。
- **缺陷关联用例挂全部失败用例**（`agenttest/brain/llm_brain.py`）：
  `related_case_ids=[r.case_id for r in failed]` 让每个缺陷都挂上本次**所有**失败用例，
  HTML 报告里「关联用例」一栏因此恒等于失败清单，等于没关联。现改为：
  LLM 在 `_DefectDraft.case_ids` 里自报，本地用失败用例 id 做白名单过滤（滤掉幻觉 id），
  过滤后为空时收窄到「与缺陷 endpoint 同 kind 的失败用例」——不再退回全部失败用例。
  MockBrain 一侧本来就一缺陷一用例，行为未变（有对照组测试钉住）。
- **RAG 评测集只跑通不判定**（`agenttest/tools/rag_metrics.py` / `rag_tool.py`）：
  `rag_evaluate` 的 `ok` 只表示 HTTP 调用成功，对检索质量没有任何阈值结论，
  读报告的人无法从返回体看出「recall@5 是否达标」。现新增 `RAG_EVAL_SET`（20 条查询）
  与 `judge_quality()`，`rag_evaluate(..., min_recall=0.8, min_mrr=0.8)` 把结论写进
  `quality` / `quality_ok`。判定一律写成返回值，**不靠抛异常**；三类情况判失败而非放行：
  没给任何阈值、评测集没有可用标注（`labeled_count == 0`）、被测量缺失或不是有限实数。
  实测：缺陷实现 recall@5 = 0.7000 判失败，正确实现 1.0000 判通过。
  不传阈值时 `rag_evaluate` 返回体逐键不变（有回归测试钉住）。
- **数字口径再对齐**：`README.md`、`docs/操作指南.md`、`docs/面试QA.md`、`docs/简历描述.md`
  的测试数与覆盖率同步为本机实测现值。采集数 `154 → 180`（不含 UI 由 `135 passed + 15 xfailed`
  变为 `161 passed + 15 xfailed`；含 UI 由 `139` 变为 `165`）；覆盖率分支口径
  `TOTAL 1500 stmts / 311 miss / 420 branch = 76.88%`（`--ignore=tests/test_ui_tool.py`；
  同一命令连跑 5 次为 4 次 76.88% + 1 次 76.98%），关掉分支的语句口径
  `TOTAL 1505 stmts / 315 miss = 79.07%`。
  门槛仍取 70，余量约 6.9 个百分点。
- **未同步**：`CONTRIBUTING.md`、`.github/PULL_REQUEST_TEMPLATE.md`、`.github/workflows/test.yml`
  里仍有 `135 passed` / `139 passed` / `75.87%` 等旧数字，本轮改动边界不含这三个文件。

### 变更（补齐边界外文件：161 / 165 passed，覆盖率 76.88%）

> 上节末尾列出的「未同步」项（`CONTRIBUTING.md`、`.github/PULL_REQUEST_TEMPLATE.md`、
> `.github/workflows/test.yml`），以及同样写着旧数字的 `pyproject.toml` 注释与
> `requirements-dev.txt` 注释，本轮一次补齐为同一份实测值。上节保留原样作为历史记录。

- **测试数字**（本机 `.venv`，Python 3.12.10；`-o addopts=` 必加，否则 `-q` 吞掉计数行）：
  `180 collected`；不含 UI `161 passed, 15 xfailed, 0 failed`；
  含 UI（本机 Chrome 可用）`165 passed, 15 xfailed`，4 条 UI 用例全计在内。
- **覆盖率**：默认分支口径 `TOTAL 1500 stmts / 311 miss / 420 branch = 76.88%`
  （`--ignore=tests/test_ui_tool.py --cov=agenttest --cov=demo_app`，连跑 3 次一致）；
  关掉分支的语句口径 `TOTAL 1505 stmts / 315 miss = 79.07%`。
  门槛仍取 70，余量约 6.9 个百分点；分支比纯语句口径低约 2.2 个百分点。
- **反推表按新分母重算**：分母 = 1500 语句 + 420 分支 = 1920，分子 = 1189 已覆盖行
  + 287 已覆盖分支 = 1476；某模块整体失守 = `(1476 − 该模块已覆盖的行与分支) / 1920`。
  得 `agenttest/graph/nodes.py`（254 行）60.5%、`demo_app/rag.py`（264 行）62.6%、
  `demo_app/app.py`（180 行）66.7%，三个都拦得住；`agenttest/brain/llm_brain.py`（110 行）
  70.7% 仍拦不住。**这是算术推算，不是逐模块删掉覆盖后重跑的实测。**
- **CI runner 的真实计数与本地不同**（run 38936350756）：非 UI step 为
  `161 passed, 15 xfailed`（与本地一致）；全量 step 为 `161 passed, 4 skipped, 15 xfailed`
  ——本仓的 Chrome 探测只认 Windows 安装路径，Linux runner 上探测不到，那 4 条 UI 用例
  是 **skipped** 而不是 failed；该 run 的覆盖率为
  `TOTAL 1500 stmts / 316 miss / 420 branch = 76.67%`（抖动来自 `FLAKY-001`）。
- **同步位置**：`CONTRIBUTING.md`（第 2 节期望值、`-o addopts=` 说明、第 7 节门槛来源）、
  `.github/PULL_REQUEST_TEMPLATE.md`（样例输出 + 检查清单）、
  `.github/workflows/test.yml`（门槛注释、summary 说明、两处 step 名、UI 步骤的期望结果、
  耗时余量注释）、`pyproject.toml`（两处 coverage 注释块）、
  `requirements-dev.txt`（口径差异说明）。**有效配置一律未动**：`COV_FAIL_UNDER`、
  `fail_under`、`branch = true`、命令与触发条件保持原样。

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
