#!/usr/bin/env python3
"""校验提交信息是否符合 Conventional Commits。

为什么用这个脚本而不是 commitlint：
  commitlint 需要 Node + `npm install @commitlint/cli @commitlint/config-conventional`
  （本机虽有 Node v24.16.0，但沙箱装不上 npm 依赖，且 CI 里已有等价的
  commitlint job 覆盖 PR 场景）。这个脚本零依赖、纯标准库，
  既能当 git commit-msg hook 用，也能在 CI 里对最近 N 条提交批量校验。

用法：
  python scripts/check_commit_msg.py .git/COMMIT_EDITMSG   # hook 传文件路径
  echo "feat: xxx" | python scripts/check_commit_msg.py -   # 从 stdin 读
  python scripts/check_commit_msg.py --message "feat: xxx"   # 直接传字符串（跨平台，无需临时文件）
  python scripts/check_commit_msg.py --range HEAD~5..HEAD   # 校验一段历史

退出码：0 = 全部合规；1 = 有不合法提交信息。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List

# 与本仓库 git log 里实际出现过的 type 对齐（含 CI 改动常用的 chore / docs / fix / feat）
TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)

# 允许的可选 scope：英文小写 + 连字符/下划线
SCOPE_RE = r"[a-z0-9][a-z0-9._/-]*"

# <type>(<scope>): <subject>  或  <type>: <subject>
HEADER_RE = re.compile(
    rf"^(?P<type>{'|'.join(TYPES)})"
    rf"(?:\((?P<scope>{SCOPE_RE})\))?"
    rf"(?P<breaking>!)?"
    rf": (?P<subject>.+)$"
)

SUBJECT_MAX = 72
# 合并提交 / 回滚 / 依赖机器人自动提交，不参与规范校验
SKIP_PREFIXES = ("Merge ", 'Revert "', "fixup!", "squash!")
SKIP_AUTHORS = ("dependabot[bot]", "github-actions[bot]")


def validate_header(header: str) -> List[str]:
    """校验单行 header；返回问题列表（空列表 = 合规）。"""
    problems: List[str] = []
    match = HEADER_RE.match(header)
    if not match:
        problems.append(
            "格式不符合 Conventional Commits，应为 <type>(<scope>): <描述>，其中 type ∈ {" + ", ".join(TYPES) + "}"
        )
        return problems

    subject = match.group("subject")
    if subject != subject.strip():
        problems.append("描述首尾不应有空格")
    if subject.endswith("。"):
        problems.append("描述结尾不要用中文句号")
    if len(header) > SUBJECT_MAX:
        problems.append(f"header 过长（{len(header)} > {SUBJECT_MAX} 字符）")
    return problems


def check_message(text: str) -> List[str]:
    """校验完整提交信息：只看首个非注释、非空行。"""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(SKIP_PREFIXES):
            return []
        return validate_header(line)
    return ["提交信息为空"]


def read_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        raise SystemExit(f"找不到文件：{source}")
    return path.read_text(encoding="utf-8", errors="replace")


def check_range(rev_range: str) -> int:
    """校验一段 git 历史；返回不合法提交数。"""
    fmt = "%H%x1f%an%x1f%s%x1e"
    proc = subprocess.run(
        ["git", "log", f"--format={fmt}", rev_range],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        return 1

    bad = 0
    for record in proc.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        sha, _, rest = record.partition("\x1f")
        author, _, subject = rest.partition("\x1f")
        if author in SKIP_AUTHORS or subject.startswith(SKIP_PREFIXES):
            continue
        problems = validate_header(subject)
        if problems:
            bad += 1
            print(f"✗ {sha[:8]} {subject}")
            for p in problems:
                print(f"    - {p}")
    if bad:
        print(f"\n共 {bad} 条提交信息不合规。")
    else:
        print("全部提交信息合规。")
    return 1 if bad else 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 Conventional Commits 提交信息")
    parser.add_argument("source", nargs="?", help="提交信息文件路径，或 - 表示 stdin")
    parser.add_argument("--message", help="直接传入提交信息文本（便于手工验证，免去临时文件）")
    parser.add_argument("--range", dest="rev_range", help="改为校验一段 git 历史，如 HEAD~5..HEAD")
    args = parser.parse_args(argv)

    if args.rev_range:
        return check_range(args.rev_range)

    if args.message is not None:
        text = args.message
    elif args.source:
        text = read_source(args.source)
    else:
        parser.print_help()
        return 0

    problems = check_message(text)
    if problems:
        print("提交信息不合规：")
        for p in problems:
            print(f"  - {p}")
        print("\n示例：feat(rag): 新增并列排序稳定性断言")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
