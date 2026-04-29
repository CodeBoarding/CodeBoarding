from pathlib import Path
from unittest.mock import MagicMock, patch

from codeboarding_workflows.incremental import run_incremental_workflow


def test_run_incremental_workflow_falls_back_when_commit_hash_missing(tmp_path: Path):
    generator = MagicMock()
    generator.output_dir = tmp_path
    generator.repo_location = tmp_path
    generator.generate_analysis.return_value = tmp_path / "analysis.json"

    with patch("codeboarding_workflows.incremental.load_full_analysis", return_value=(MagicMock(), {})):
        with patch("codeboarding_workflows.incremental.load_analysis_metadata", return_value={"commit_hash": ""}):
            result = run_incremental_workflow(generator)

    generator.generate_analysis.assert_called_once_with()
    generator.generate_analysis_incremental.assert_not_called()
    assert result == tmp_path / "analysis.json"


def test_run_incremental_workflow_falls_back_without_baseline(tmp_path: Path):
    generator = MagicMock()
    generator.output_dir = tmp_path
    generator.repo_location = tmp_path
    generator.generate_analysis.return_value = tmp_path / "analysis.json"

    with patch("codeboarding_workflows.incremental.load_full_analysis", return_value=None):
        with patch("codeboarding_workflows.incremental.load_analysis_metadata", return_value=None):
            result = run_incremental_workflow(generator)

    generator.generate_analysis.assert_called_once_with()
    generator.generate_analysis_incremental.assert_not_called()
    assert result == tmp_path / "analysis.json"


def test_run_incremental_workflow_dispatches_incremental_generator(tmp_path: Path):
    generator = MagicMock()
    generator.output_dir = tmp_path
    generator.repo_location = tmp_path
    generator.generate_analysis_incremental.return_value = tmp_path / "analysis.json"

    with patch("codeboarding_workflows.incremental.load_full_analysis", return_value=("root", {"1": "sub"})):
        with patch("codeboarding_workflows.incremental.load_analysis_metadata", return_value={"commit_hash": "abc123"}):
            with patch("codeboarding_workflows.incremental.get_git_commit_hash", return_value="def456"):
                with patch("codeboarding_workflows.incremental.detect_changes") as detect_changes:
                    detect_changes.return_value = MagicMock(is_empty=lambda: False)

                    result = run_incremental_workflow(generator)

    generator.generate_analysis_incremental.assert_called_once()
    assert result == tmp_path / "analysis.json"
