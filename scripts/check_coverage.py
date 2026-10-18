"""per-file 覆盖率门禁：拦住「单个模块整体失守」这类仓库级门槛看不见的退化。

## 为什么需要这个脚本

三个仓原来的覆盖率门禁都是**仓库级**的（`[tool.coverage.report] fail_under`
或 CI 命令行的 `--cov-fail-under`）。仓库级门槛能拦住「整个仓库都烂了」，
但结构上看不见**单点失守**：

* 一个 122 行的主入口写成 0%，总覆盖率只掉 8 个百分点，仍高于门槛；
* 反过来，把某个 300 行模块从 90% 拉到 98%，也能把另一个模块的崩塌**补回来**。

本仓库自己早就写明了这个判断（见 README「覆盖率门槛」一节），但一直记成
后续任务。这个脚本就是那个后续任务。

## 为什么不是配 coverage 的 per-file fail_under

因为**它不存在**。coverage 7.16.2 的 `CoverageConfig` 只有全局
`fail_under`：

    >>> [a for a in dir(CoverageConfig()) if 'fail' in a.lower()]
    ['fail_under']

所以只能读 `--cov-report=json` 的产物自己判。那正是本脚本做的事 ——
它不重新跑测试，只读 already-produced 的 JSON，因此很快、且与
`pytest` 那次运行的口径**逐字一致**（不用两条命令各算一遍覆盖率）。

## 配置

住在 `pyproject.toml` 的自定义表里（coverage 不读它，只有本脚本读）：

    [tool.coverage.perfile]
    fail_under = 60          # 每个入选文件的百分比下限
    min_statements = 10      # 语句数低于此的文件不参与（避免小文件拉高噪音）
    exclude = [
      "agenttest/__main__.py",   # 精确路径：CLI 薄壳，单独跑没有断言价值
      "testcases/",              # 结尾带 / = 前缀，排除整个目录
    ]

## 三条刻意的设计

1. **配置缺失 = 退出 1，不是静默通过。** 一个「配置写错了就当通过」的门禁
   比没有门禁更危险 —— 它会让人以为有保护。
2. **`exclude` 里每条路径都会验证真实存在。** 写错的排除等于没写，但它
   **看起来**写了 —— 这是最坏的一种假安全感。
3. **失败输出按百分比升序**，最烂的排第一，并带上缺失行号区间，
   让人能立刻跳过去看，而不是先在 40 个文件里找哪一行红。

## 用法

    python scripts/check_coverage.py [--report .coverage.json] [--pyproject pyproject.toml]

退出码 0 = 全通过，1 = 有文件低于门槛或配置有误。
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

# fixture 名（相对仓库根）→ 展示用；不用 CoverageData，只读 JSON，避免依赖 coverage API

DEFAULT_REPORT = ".coverage.json"
DEFAULT_PYPROJECT = "pyproject.toml"


def _force_utf8_stdio() -> None:
    """让输出在所有平台上都是 UTF-8。

    不是洁癖：本脚本要打印中文文件名与 `→`/`↔` 一类符号，而 Windows
    控制台默认是 GBK，实测会直接抛

        UnicodeEncodeError: 'gbk' codec can't encode character '\\u2192'

    也就是说这个检查在 Windows 上**还没开始判定就先崩了**。与
    `scripts/check_deps_sync.py`、`platform_cli.py` 的 `_make_stdout_utf8()`
    是同一个理由。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # 已被重定向成不可重配置的对象
                pass


_force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """配置本身有问题 —— 与「覆盖率不达标」是两种性质的失败。"""


def _norm(path: str | Path) -> str:
    """把路径统一成 `a/b/c.py` 形式。

    coverage JSON 在 Windows 上写的是反斜杠（`agenttest\\runner.py`），
    配置里人写的是正斜杠。不归一化就会出现「配置里排除了这个文件，
    但比对时字符串不相等，于是它照样参与判定」这种**看起来排除了、
    实际没排除**的静默失效。
    """
    return str(path).replace("\\", "/").lstrip("./")


def load_perfile_config(pyproject: Path) -> tuple[float, int, list[str]]:
    """读出 `[tool.coverage.perfile]`，缺任何字段都算配置错误。"""
    if not pyproject.is_file():
        raise ConfigError(f"找不到 {pyproject}")

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    table = data.get("tool", {}).get("coverage", {}).get("perfile") if isinstance(data.get("tool"), dict) else None
    if not isinstance(table, dict):
        raise ConfigError(
            f"{pyproject} 里没有 [tool.coverage.perfile] 表。\n"
            "  这个门禁没有配置就不该当作通过 —— 请补上 fail_under / min_statements / exclude。"
        )

    try:
        fail_under = float(table["fail_under"])
    except KeyError as exc:
        raise ConfigError("[tool.coverage.perfile] 缺 fail_under") from exc
    try:
        min_statements = int(table["min_statements"])
    except KeyError as exc:
        raise ConfigError("[tool.coverage.perfile] 缺 min_statements") from exc

    raw_exclude = table.get("exclude", [])
    if not isinstance(raw_exclude, list) or not all(isinstance(x, str) for x in raw_exclude):
        raise ConfigError("[tool.coverage.perfile] exclude 必须是字符串数组")

    return fail_under, min_statements, [_norm(x) for x in raw_exclude]


def verify_excludes(excludes: list[str]) -> list[str]:
    """确认每个排除项在仓库里真实存在，返回不存在的那几个。

    写错的排除项**不会报错**（它只是匹配不到任何文件），于是这个门禁
    会带着一条「以为排除了、其实没排除」的配置长期运行 —— 这正是本仓库
    反复在文档里警惕的「静默放行」，只是换了个地方。
    """
    missing = []
    for entry in excludes:
        target = ROOT / entry
        # 结尾带 / 的当作目录前缀；否则当作精确文件
        if entry.endswith("/"):
            exists = target.is_dir()
        else:
            exists = target.is_file()
        if not exists:
            missing.append(entry)
    return missing


def is_excluded(rel_path: str, excludes: list[str]) -> bool:
    """前缀（结尾带 /）或精确路径匹配。"""
    for entry in excludes:
        if entry.endswith("/"):
            if rel_path.startswith(entry):
                return True
        elif rel_path == entry:
            return True
    return False


def load_report(report: Path) -> dict:
    if not report.is_file():
        raise ConfigError(
            f"找不到覆盖率产物 {report}。\n"
            "  生成方式（注意必须重复写 --cov=，逗号写法会静默收集不到数据）：\n"
            "    pytest ... --cov-report=json:.coverage.json"
        )
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{report} 不是合法 JSON：{exc}") from exc
    if "files" not in data:
        raise ConfigError(f"{report} 里没有 files 字段，可能不是 --cov-report=json 的产物")
    return data


def collect(
    data: dict, fail_under: float, min_statements: int, excludes: list[str]
) -> tuple[list[tuple[str, float, int, str]], int]:
    """返回 (低于门槛的文件, 参与判定的文件数)。

    每个元素是 (路径, 百分比, 语句数, 缺失行摘要)。
    """
    failures: list[tuple[str, float, int, str]] = []
    considered = 0

    for raw_path, info in data["files"].items():
        rel = _norm(raw_path)
        if is_excluded(rel, excludes):
            continue

        summary = info.get("summary", {})
        stmts = int(summary.get("num_statements", 0))
        if stmts < min_statements:
            continue

        considered += 1
        pct = float(summary.get("percent_covered", 0.0))
        if pct < fail_under:
            # 注意：`summary["missing_lines"]` 是**数量**，不是行号列表 ——
            # 行号列表在文件对象的顶层（`files[path]["missing_lines"]`）。
            # 这两个同名不同义，混用会得到 "TypeError: 'int' object is not iterable"。
            missing = info.get("missing_lines") or []
            excerpt = _summarize_lines(missing)
            failures.append((rel, pct, stmts, excerpt))

    failures.sort(key=lambda row: (row[1], row[0]))
    return failures, considered


def _summarize_lines(missing: list[int], limit: int = 4) -> str:
    """把缺失行号压成 `10-188, 190` 这种短串，最多列 limit 段。"""
    if not missing:
        return "—"
    nums = sorted(int(n) for n in missing)
    spans: list[tuple[int, int]] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        spans.append((start, prev))
        start = prev = n
    spans.append((start, prev))

    parts = [f"{a}" if a == b else f"{a}-{b}" for a, b in spans[:limit]]
    if len(spans) > limit:
        parts.append(f"…共 {len(spans)} 段")
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_coverage.py",
        description="per-file 覆盖率门禁：读 --cov-report=json 的产物判定每个文件",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(DEFAULT_REPORT),
        help=f"coverage json 产物（默认 {DEFAULT_REPORT}）",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path(DEFAULT_PYPROJECT),
        help=f"配置来源（默认 {DEFAULT_PYPROJECT}）",
    )
    args = parser.parse_args(argv)

    try:
        fail_under, min_statements, excludes = load_perfile_config(args.pyproject)
    except ConfigError as exc:
        print(f"[coverage-perfile] 配置错误：{exc}")
        return 1

    missing_excludes = verify_excludes(excludes)
    if missing_excludes:
        print("[coverage-perfile] 配置错误：这些 exclude 路径在仓库里不存在")
        for entry in missing_excludes:
            print(f"  ✗ {entry}")
        print("  写错的排除等于没写，但它看起来写了 —— 请修正路径或删掉该行。")
        return 1

    try:
        data = load_report(args.report)
    except ConfigError as exc:
        print(f"[coverage-perfile] 读取产物失败：{exc}")
        return 1

    failures, considered = collect(data, fail_under, min_statements, excludes)

    print(
        f"[coverage-perfile] 门槛 {fail_under:g}%（分支口径）· 参与 {considered} 个文件"
        f"（语句数 ≥ {min_statements}）· 排除 {len(excludes)} 项"
    )

    if not failures:
        print(f"[coverage-perfile] 通过：所有参与判定的文件都不低于 {fail_under:g}%")
        return 0

    print(f"[coverage-perfile] 未通过：{len(failures)} 个文件低于 {fail_under:g}%")
    width = max(len(p) for p, *_ in failures)
    for rel, pct, stmts, excerpt in failures:
        print(f"  ✗ {rel:<{width}}  {pct:5.1f}%  （{stmts} 语句；缺失 {excerpt}）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
