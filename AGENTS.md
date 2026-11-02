# AGENTS.md · agent-test-hub

## 这是什么

一个 **Agent 自动测试系统**：LangGraph + LangChain 编排的 API / Web UI / RAG 三覆盖自动化测试平台。
Agent 从 OpenAPI 契约生成测试计划 → 执行 → 失败重试 → 归因缺陷 → 产出可视化报告。

- 本仓：`D:\DSH\test-projects\agent-test-hub`
- 远端：`https://github.com/lefan17/agent-test-hub`
- 兄弟仓（本仓的**上游被测对象**与**同源技术栈**）：
  - `D:\DSH\test-projects\fullstack-test-framework` —— 接口契约 / UI / 平台层主力仓
  - `D:\DSH\test-projects\perf-baseline-quality-gate` —— 性能与质量门禁仓
- 本仓自带 `.venv`，**不共享**其它仓的 venv。

## 怎么跑

一律用本仓 venv 的绝对路径调 `-m`（不必 activate）：

```bash
VENV=/d/DSH/test-projects/agent-test-hub/.venv/Scripts/python.exe
cd /d/DSH/test-projects/agent-test-hub

$VENV -m pytest -q                                   # 全量用例，361 条收集
                                                     # 实测 342 passed / 15 xfailed / 4 failed（UI 4 条）
$VENV -m pytest -q -o addopts=                       # 同上，但保住末尾的 "N passed" 汇总行
$VENV -m pytest -m llm_smoke -q -o addopts=          # 真调外部 LLM 的冒烟（需 Key，PR 门禁不跑）
$VENV -m mypy --cache-dir /tmp/mypy-cache             # 24 个源文件
$VENV -m ruff check . && $VENV -m ruff format --check .

# 跑一次被测系统的自动化（默认 MockBrain，无需 Key，28 条用例）
$VENV -m agenttest run
$VENV -m agenttest run --no-ui                        # 跳过 UI，25 条
$VENV -m agenttest run --force-llm                    # 走真实 LLM（需 AGENTTEST_LLM_API_KEY）
```

## 环境硬约束（本仓特有，必读）

### 1. `-o addopts=` 不能省（要看见汇总行时）

`pyproject.toml` 里 `addopts = "-q"`，而 `-q` 会把末尾的
`342 passed, 15 xfailed, 4 failed` 汇总行一起吞掉 —— 命令行覆盖为空之后计数才会出现在输出与 CI 日志里。
**要贴数字给别人的命令，一律带 `-o addopts=`。**

### 2. `python -m pytest` 不能换成裸 `pytest`

容器镜像里没有 `pip install -e .`，`demo_app/` 不在 `site-packages`。
裸 `pytest` 不会把 WORKDIR 加进 `sys.path`，容器内直接
`ModuleNotFoundError: No module named 'demo_app'` —— 与 CI 那条坑是同一回事。

### 3. `--cov` 口径变了，per-file 门禁必须重核

per-file 门禁（`scripts/check_coverage.py`，`[tool.coverage.perfile] fail_under = 60`、
`min_statements = 10`）**完全继承旁边那次 coverage 运行的 `--cov` 口径**。
本仓是 `--cov=agenttest --cov=demo_app`，参与判定 16 个文件。

实测踩过：本地用 `--cov=.` 验证时，`tests/test_llm_smoke.py` 被判 **42.1% 失守** ——
那个文件 5 条用例里有 4 条在没配 Key 时 `skip`，被跳过的行当然是"未覆盖"。
**这不是门禁出错，是调用方换了口径。** 两条纪律：

- 本地复现门禁必须抄 CI 那条命令，不要自己拼
- `--cov` 口径变化时，per-file 门槛值要重新核一遍（分母变了，最低文件可能换人）

### 4. `.env` 不是运行前提，是显式输入

LLM 冒烟在**导入期**就调 `_load_dotenv()`（复用 `agenttest.config` 里同一个加载器），
所以「Key 写在 `.env` 里」与「Key 写在环境变量里」等价。

这里踩过一个坑：早期版本在模块导入时读 `os.getenv`，而 `.env` 是由 `Config.from_env()`
在更晚的时候才加载的 —— 于是 Key 明明配好了测试却仍然 skip，skip reason 还反着说「未设置」。
教训：**永远跳过的测试永远绿，提示语还把人指向错误方向。**

### 5. mypy 的缓存目录要可写

本机默认 `.mypy_cache` 在沙箱下可能不可写，表现为：

```
error: INTERNAL ERROR -- Please try using mypy master on GitHub:
  ... mypy/metastore.py, line 172, in connect_db
version: 2.4.0
```

**这不是代码有类型问题，是 mypy 建不了缓存数据库。** 给它一个可写目录即可：

```bash
$VENV -m mypy --cache-dir /可写路径/mypy_cache    # → Success: no issues found in 24 source files
```

## 硬约束

- **不加依赖**：依赖只认 `requirements.txt` / `requirements-dev.txt`，改完必须重生成
  `requirements.lock`（`python -m piptools compile requirements.txt -o requirements.lock`），
  否则 CI `lint` job 的锁文件格式校验步骤红。
- **`llm_smoke` 不进 PR 门禁**：贵（按 PR 数烧额度）、慢且不确定（LLM 延迟让反馈时间不可预测），
  且它验证的是「外部 provider 今天还认不认我们的提示词与结构化输出协议」，
  不是「这个 diff 有没有引入错误」。入口只有 `.github/workflows/llm-smoke.yml`
  （**只挂 `workflow_dispatch`，无定时**）。
- **Agent 只做决策，执行走确定性工具**：pytest / headless Chromium / 自研 BM25 三类工具之外，
  不允许解析 LLM 自由文本来判定结果 —— 那会制造无法复现的误报。
- **未实现的 check 类型一律判失败**，不许静默放行。
- **`CODEOWNERS` 在单人仓库里同样会请求 review**，每开一个 PR 仍会有一封
  「Review requested」邮件 —— 那是 `CODEOWNERS` 生效的代价，代码侧关不掉，别去改它。

## 已知坑

- **Dependabot 三条**（都写在 `.github/dependabot.yml` 头部注释里）：
  ① `labels` 里每个名字**必须先在仓库里存在**，否则每个 PR 留一条
  「The following labels could not be found」评论并随之发邮件（2026-10-13 因此产生 12 条评论）；
  ② **不要写 `reviewers`** —— 该属性正在弃用，且 review 请求已由 `CODEOWNERS` 自动产生；
  ③ `groups` 的顺序有意义：一个依赖命中多个组时只进**第一个**命中的组，
  所以「必须锁在一起」的组要排在通配组前面，否则 `langchain` 的补丁级更新会被 `pip-patch`
  先抓走，出现「只升了 langchain、没升 langgraph」的装不上的中间态。
- **没有 Chrome / Chrome 起不来**：UI 用例会失败并在报告中标注「UI 后端不可用」，
  API 与 RAG 用例照常；也可 `--no-ui` 跳过。注意 `auto` 后端只看 Chrome **是否存在**
  （`config.py` 的 `_find_chrome()` 是 `Path.exists()`），Chrome 存在但被沙箱禁止启动时
  **不会**退回 Selenium。
- **`tests/test_ui_tool.py` 那 4 条的红/绿取决于环境，不是代码问题**：`chrome_present()`
  只判 `Path.exists()`，所以「Chrome 装了但起不了」时它们不是 skip 而是 **failed**。
  本机实测（2026-11-02）报错为 Chrome 自身启动失败：

  ```
  ERROR:file_io_win.cc:181] CreateFile ...\Google\Chrome\User Data\Crashpad\settings.dat: 拒绝访问。(0x5)
  ERROR:chrome_main.cc:210] Missing headless user data directory.
  ```

  **关键判别（实测）**：同一个 Chrome、同一组参数，**直接从 bash 调能出 DOM**
  （`--headless=new --disable-gpu --no-sandbox --dump-dom` → 正常 HTML、rc=0），
  **从 Python `subprocess` 调就失败**（rc=1，即上面两条错）；加
  `--user-data-dir=<可写目录>` 也救不回来（rc=21，`TransactNamedPipe: 管道已结束。(0x6D)`）。
  所以这是**本机沙箱对「孙进程」的限制**，不是 `ui_tool.py` 的参数问题 ——
  改参数不能修，别在这里浪费时间。
  本机期望值：**342 passed / 15 xfailed / 4 failed**；能起 Chrome 子进程的环境里这 4 条全绿。
- **`test_runner_e2e.py` 不能证明 CLI 能用**：它绕过 runner 直接调 `graph.invoke`。
  CLI 那层的证明在 `test_runner_cli.py`。
- **提示词花括号会把 `.format()` 打崩**：JSON 示例进 prompt 时必须转义，
  否则 `KeyError` 在运行时才炸。

## 提交纪律

- 提交信息走 Conventional Commits，CI 的 `commit-msg` job 会校验
  （`scripts/check_commit_msg.py`）。
- 一次提交只做一件事。修 bug 与调格式分开，否则 `git bisect` 落点无意义。
- 改完代码先跑 `$VENV -m pytest -q` + `$VENV -m ruff check .`，两条都绿再提交。
