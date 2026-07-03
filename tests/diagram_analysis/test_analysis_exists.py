"""Unit tests for :func:`diagram_analysis.io_utils.analysis_exists`."""

from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis.io_utils import analysis_exists, save_analysis


def test_true_when_parseable_analysis_present(tmp_path: Path) -> None:
    save_analysis(
        AnalysisInsights(description="d", components=[], components_relations=[]),
        tmp_path,
        repo_name="repo",
        commit_hash="head",
    )
    assert analysis_exists(tmp_path) is True


def test_false_when_file_missing(tmp_path: Path) -> None:
    assert analysis_exists(tmp_path) is False


def test_false_when_file_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text("not json")
    assert analysis_exists(tmp_path) is False
