"""Tests for codeboarding_web.component_data helpers."""

import json
from pathlib import Path
from types import SimpleNamespace

from codeboarding_web.component_data import changed_files, component_diff, component_files, load_warning_counts


def test_load_warning_counts_absent(tmp_path: Path) -> None:
    assert load_warning_counts(tmp_path) == {}


def test_load_warning_counts_reads_health(tmp_path: Path) -> None:
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "health_report.json").write_text(
        json.dumps({"file_summaries": [{"file_path": "a.py", "warning_findings": 3}]})
    )
    assert load_warning_counts(tmp_path) == {"a.py": 3}


def test_load_warning_counts_multiple_entries(tmp_path: Path) -> None:
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "health_report.json").write_text(
        json.dumps(
            {
                "file_summaries": [
                    {"file_path": "a.py", "warning_findings": 2},
                    {"file_path": "b.py", "warning_findings": 5},
                ]
            }
        )
    )
    result = load_warning_counts(tmp_path)
    assert result == {"a.py": 2, "b.py": 5}


def test_load_warning_counts_corrupt_json(tmp_path: Path) -> None:
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "health_report.json").write_text("{not json")
    assert load_warning_counts(tmp_path) == {}


def test_changed_files_non_git(tmp_path: Path) -> None:
    assert changed_files(tmp_path) == set()


def _make_comp(reference_file: str | None, qualified_name: str = "foo") -> object:
    """Build a minimal fake Component-like object for component_files tests."""
    ref = SimpleNamespace(reference_file=reference_file, qualified_name=qualified_name)
    return SimpleNamespace(key_entities=[ref])


def test_component_files_relative(tmp_path: Path) -> None:
    comp = _make_comp("src/foo.py")
    result = component_files(comp, tmp_path)  # type: ignore[arg-type]
    assert result == {"src/foo.py"}


def test_component_files_absolute(tmp_path: Path) -> None:
    abs_path = str(tmp_path / "src" / "foo.py")
    comp = _make_comp(abs_path)
    result = component_files(comp, tmp_path)  # type: ignore[arg-type]
    assert result == {"src/foo.py"}


def test_component_files_none_skipped(tmp_path: Path) -> None:
    comp = _make_comp(None)
    result = component_files(comp, tmp_path)  # type: ignore[arg-type]
    assert result == set()


def test_component_files_normalizes_relative(tmp_path: Path) -> None:
    ref1 = SimpleNamespace(reference_file="./src/foo.py")
    ref2 = SimpleNamespace(reference_file="src/bar.py")
    ref3 = SimpleNamespace(reference_file=None)
    comp = SimpleNamespace(key_entities=[ref1, ref2, ref3])
    result = component_files(comp, tmp_path)  # type: ignore[arg-type]
    assert result == {"src/foo.py", "src/bar.py"}


def test_component_diff_empty_files_returns_empty(tmp_path: Path) -> None:
    assert component_diff(tmp_path, []) == ""


def test_component_diff_non_git_returns_empty(tmp_path: Path) -> None:
    assert component_diff(tmp_path, ["main.py"]) == ""
