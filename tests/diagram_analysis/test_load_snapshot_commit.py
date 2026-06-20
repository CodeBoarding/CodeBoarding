"""Unit tests for :func:`diagram_analysis.io_utils.load_snapshot_commit`."""

import json
from pathlib import Path

from agents.analysis_models import AnalysisInsights
from diagram_analysis.io_utils import load_snapshot_commit, save_analysis


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


def test_save_analysis_preserves_existing_snapshot_commit(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(
        json.dumps(
            {
                "snapshotCommit": "snap-A",
                "metadata": {"repo_name": "repo", "commit_hash": "old"},
                "description": "old",
                "components": [],
                "components_relations": [],
                "files": {},
                "methods_index": {},
            }
        )
    )

    save_analysis(
        AnalysisInsights(description="new", components=[], components_relations=[]),
        tmp_path,
        repo_name="repo",
        commit_hash="head",
    )

    assert load_snapshot_commit(tmp_path) == "snap-A"


def test_save_analysis_can_write_explicit_snapshot_commit(tmp_path: Path) -> None:
    save_analysis(
        AnalysisInsights(description="new", components=[], components_relations=[]),
        tmp_path,
        repo_name="repo",
        commit_hash="head",
        snapshot_commit="snap-B",
    )

    assert load_snapshot_commit(tmp_path) == "snap-B"
