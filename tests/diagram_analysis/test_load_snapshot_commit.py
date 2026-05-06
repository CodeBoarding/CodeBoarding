"""Unit tests for :func:`diagram_analysis.io_utils.load_snapshot_commit`."""

import json
from pathlib import Path

from diagram_analysis.io_utils import load_snapshot_commit


def test_returns_snapshot_commit_when_present(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"snapshotCommit": "abc123"}))
    assert load_snapshot_commit(tmp_path) == "abc123"


def test_returns_none_when_field_missing(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"metadata": {"commit_hash": "xyz"}}))
    assert load_snapshot_commit(tmp_path) is None


def test_returns_none_when_field_empty(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"snapshotCommit": ""}))
    assert load_snapshot_commit(tmp_path) is None


def test_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert load_snapshot_commit(tmp_path) is None


def test_returns_none_when_file_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text("not json")
    assert load_snapshot_commit(tmp_path) is None


def test_returns_none_when_field_not_string(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"snapshotCommit": 12345}))
    assert load_snapshot_commit(tmp_path) is None
