"""`scripts/check_coverage.py` 自身的测试。

这个脚本是**门禁的门禁** —— 它判错的后果是「覆盖率看着绿、其实有模块失守」。
所以它不能只有一条 happy path 测试，必须证明它在**该红的时候真的会红**。

三条重点（都是它可能悄悄失效的方式）：

1. 配置缺失时退出 1 而不是静默通过；
2. `exclude` 里写错的路径必须被报出来（写错的排除 = 看起来排除了、实际没排除）；
3. 路径分隔符差异（Windows 的 `\\` vs 配置里的 `/`）必须归一化，
   否则「配置里排除了这个文件，但比对时字符串不等，于是它照样参与判定」。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_coverage.py"


@pytest.fixture(scope="module")
def checker():
    """按文件路径加载脚本（它在 scripts/ 下，不是可导入的包）。"""
    spec = importlib.util.spec_from_file_location("check_coverage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file_entry(pct: float, stmts: int, missing: list[int] | None = None) -> dict:
    """造一个 coverage json 里的文件条目（字段名与真实产物一致）。"""
    return {
        "executed_lines": [],
        "summary": {
            "num_statements": stmts,
            "percent_covered": pct,
            # 注意：summary 里这个字段是**数量**，不是行号列表。
            # 行号列表在文件对象的顶层，见下面 "missing_lines"。
            "missing_lines": len(missing or []),
        },
        "missing_lines": list(missing or []),
    }


def _report(files: dict[str, dict]) -> dict:
    return {"files": files, "totals": {}}


class TestNormalization:
    """路径归一化 —— 这是「排除静默失效」的唯一防线。"""

    def test_backslashes_become_slashes(self, checker):
        assert checker._norm("agenttest\\runner.py") == "agenttest/runner.py"

    def test_leading_dot_slash_is_stripped(self, checker):
        assert checker._norm("./agenttest/runner.py") == "agenttest/runner.py"


class TestExclusion:
    def test_exact_path_matches(self, checker):
        assert checker.is_excluded("agenttest/runner.py", ["agenttest/runner.py"])

    def test_exact_path_does_not_match_sibling(self, checker):
        assert not checker.is_excluded("agenttest/runner2.py", ["agenttest/runner.py"])

    def test_trailing_slash_is_a_prefix(self, checker):
        assert checker.is_excluded("testcases/platform/a.py", ["testcases/"])

    def test_prefix_does_not_match_a_different_root(self, checker):
        # 前缀排除必须是目录前缀，不能变成子串匹配
        assert not checker.is_excluded("other/testcases/a.py", ["testcases/"])


class TestListSummary:
    def test_consecutive_lines_collapse_to_a_range(self, checker):
        assert checker._summarize_lines([10, 11, 12]) == "10-12"

    def test_gaps_produce_separate_spans(self, checker):
        assert checker._summarize_lines([10, 11, 20, 30]) == "10-11, 20, 30"

    def test_empty_is_a_dash(self, checker):
        assert checker._summarize_lines([]) == "—"

    def test_long_lists_are_truncated_with_a_count(self, checker):
        out = checker._summarize_lines([1, 3, 5, 7, 9, 11], limit=2)
        assert "共 6 段" in out


class TestCollect:
    def test_file_below_threshold_is_reported_with_lines(self, checker):
        data = _report({"agenttest/runner.py": _file_entry(0.0, 122, [10, 12, 13])})
        failures, considered = checker.collect(data, fail_under=30, min_statements=10, excludes=[])
        assert considered == 1
        assert len(failures) == 1
        rel, pct, stmts, excerpt = failures[0]
        assert rel == "agenttest/runner.py"
        assert pct == 0.0
        assert stmts == 122
        assert "10" in excerpt and "12-13" in excerpt

    def test_file_at_threshold_passes(self, checker):
        # 恰好等于门槛 = 通过（"不低于" 语义）
        data = _report({"a.py": _file_entry(30.0, 50)})
        failures, _ = checker.collect(data, fail_under=30, min_statements=10, excludes=[])
        assert failures == []

    def test_small_file_is_skipped(self, checker):
        data = _report({"a.py": _file_entry(0.0, 5)})
        failures, considered = checker.collect(data, fail_under=30, min_statements=10, excludes=[])
        assert failures == []
        assert considered == 0

    def test_excluded_file_is_skipped_even_at_zero(self, checker):
        data = _report({"agenttest/__main__.py": _file_entry(0.0, 50)})
        failures, considered = checker.collect(
            data, fail_under=30, min_statements=10, excludes=["agenttest/__main__.py"]
        )
        assert failures == []
        assert considered == 0

    def test_windows_separators_in_report_still_match_excludes(self, checker):
        """coverage 在 Windows 上写反斜杠，配置写正斜杠 —— 必须能匹配上。"""
        data = _report({"agenttest\\runner.py": _file_entry(0.0, 122)})
        failures, considered = checker.collect(data, fail_under=30, min_statements=10, excludes=["agenttest/runner.py"])
        assert failures == []
        assert considered == 0

    def test_failures_are_sorted_worst_first(self, checker):
        data = _report(
            {
                "b.py": _file_entry(25.0, 40),
                "a.py": _file_entry(0.0, 40),
                "c.py": _file_entry(12.0, 40),
            }
        )
        failures, _ = checker.collect(data, fail_under=30, min_statements=10, excludes=[])
        assert [f[1] for f in failures] == [0.0, 12.0, 25.0]


class TestConfigErrors:
    def test_missing_perfile_table_is_an_error(self, checker, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.coverage.report]\nfail_under = 70\n", encoding="utf-8")
        with pytest.raises(checker.ConfigError, match="没有 \\[tool.coverage.perfile\\]"):
            checker.load_perfile_config(pyproject)

    def test_missing_fail_under_is_an_error(self, checker, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.coverage.perfile]\nmin_statements = 10\nexclude = []\n", encoding="utf-8")
        with pytest.raises(checker.ConfigError, match="fail_under"):
            checker.load_perfile_config(pyproject)

    def test_missing_min_statements_is_an_error(self, checker, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.coverage.perfile]\nfail_under = 30\nexclude = []\n", encoding="utf-8")
        with pytest.raises(checker.ConfigError, match="min_statements"):
            checker.load_perfile_config(pyproject)

    def test_valid_config_is_read(self, checker, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.coverage.perfile]\nfail_under = 42\nmin_statements = 7\nexclude = ["a.py", "b/"]\n',
            encoding="utf-8",
        )
        fail_under, min_statements, excludes = checker.load_perfile_config(pyproject)
        assert fail_under == 42.0
        assert min_statements == 7
        assert excludes == ["a.py", "b/"]

    def test_missing_report_file_is_an_error(self, checker, tmp_path):
        with pytest.raises(checker.ConfigError, match="找不到覆盖率产物"):
            checker.load_report(tmp_path / "nope.json")


class TestVerifyExcludes:
    def test_nonexistent_exclude_paths_are_reported(self, checker):
        # 这两条都不存在；写错的排除不会报错，所以必须主动核对
        missing = checker.verify_excludes(["no_such_file_xyz.py", "no_such_dir_xyz/"])
        assert missing == ["no_such_file_xyz.py", "no_such_dir_xyz/"]

    def test_existing_paths_pass(self, checker):
        # agenttest/ 目录与 scripts/check_coverage.py 都真实存在
        assert checker.verify_excludes(["agenttest/", "scripts/check_coverage.py"]) == []


class TestEndToEnd:
    """走 main()，确认退出码语义（0 = 通过，1 = 不通过或配置错误）。"""

    def test_exit_zero_when_all_pass(self, checker, tmp_path, monkeypatch):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.coverage.perfile]\nfail_under = 50\nmin_statements = 10\nexclude = []\n",
            encoding="utf-8",
        )
        report = tmp_path / "cov.json"
        report.write_text(json.dumps(_report({"a.py": _file_entry(90.0, 100)})), encoding="utf-8")

        code = checker.main(["--report", str(report), "--pyproject", str(pyproject)])
        assert code == 0

    def test_exit_one_and_names_the_file_when_below(self, checker, tmp_path, capsys):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.coverage.perfile]\nfail_under = 50\nmin_statements = 10\nexclude = []\n",
            encoding="utf-8",
        )
        report = tmp_path / "cov.json"
        report.write_text(
            json.dumps(_report({"agenttest/runner.py": _file_entry(0.0, 122, [10, 11])})),
            encoding="utf-8",
        )

        code = checker.main(["--report", str(report), "--pyproject", str(pyproject)])
        out = capsys.readouterr().out
        assert code == 1
        assert "agenttest/runner.py" in out
        assert "0.0%" in out
