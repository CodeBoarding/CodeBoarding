"""Unit tests for :func:`diagram_analysis.io_utils.load_analysis_commit_hash`."""

import json
from pathlib import Path

from diagram_analysis.io_utils import load_analysis_commit_hash


def test_returns_metadata_commit_when_present(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"metadata": {"commit_hash": "abc123", "repo_name": "r"}}))
    assert load_analysis_commit_hash(tmp_path) == "abc123"


def test_returns_none_when_metadata_missing(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"snapshotCommit": "xyz"}))
    assert load_analysis_commit_hash(tmp_path) is None


def test_returns_none_when_commit_hash_missing(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"metadata": {"repo_name": "r"}}))
    assert load_analysis_commit_hash(tmp_path) is None


def test_returns_none_when_commit_hash_empty(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"metadata": {"commit_hash": "", "repo_name": "r"}}))
    assert load_analysis_commit_hash(tmp_path) is None


def test_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert load_analysis_commit_hash(tmp_path) is None


def test_returns_none_when_file_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text("not json")
    assert load_analysis_commit_hash(tmp_path) is None


def test_returns_none_when_commit_hash_not_string(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"metadata": {"commit_hash": 12345, "repo_name": "r"}}))
    assert load_analysis_commit_hash(tmp_path) is None


def test_does_not_fall_back_to_snapshot_commit(tmp_path: Path) -> None:
    """Distinct from ``snapshotCommit``: only ``metadata.commit_hash`` is read."""
    (tmp_path / "analysis.json").write_text(json.dumps({"snapshotCommit": "snap-A", "metadata": {"repo_name": "r"}}))
    assert load_analysis_commit_hash(tmp_path) is None
