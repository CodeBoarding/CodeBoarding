from pathlib import Path
from unittest.mock import MagicMock, patch

from codeboarding_workflows.incremental import run_incremental_workflow
from diagram_analysis.incremental_models import IncrementalRunResult, IncrementalSummary, IncrementalSummaryKind
from diagram_analysis.incremental_payload import IncrementalCompletedPayload
from repo_utils.change_detector import ChangeDetectionError


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

    with patch("codeboarding_workflows.incremental.load_full_analysis", return_value=("root", {"1": "sub"})):
        with patch("codeboarding_workflows.incremental.load_analysis_metadata", return_value={"commit_hash": "abc123"}):
            with patch("codeboarding_workflows.incremental.get_git_commit_hash", return_value="def456"):
                with patch("codeboarding_workflows.incremental.detect_changes") as detect_changes:
                    detect_changes.return_value = MagicMock(is_empty=lambda: False)
                    with patch("codeboarding_workflows.incremental.run_incremental_pipeline") as run_pipeline:
                        payload = IncrementalCompletedPayload(
                            result=IncrementalRunResult(
                                summary=IncrementalSummary(
                                    kind=IncrementalSummaryKind.MATERIAL_IMPACT,
                                    message="ok",
                                ),
                                analysis_path=tmp_path / "analysis.json",
                            ),
                            base_ref="abc123",
                            target_ref="def456",
                            resolved_target_commit="def456",
                            change_set=MagicMock(),
                            incremental_delta=MagicMock(),
                            metadata_path=tmp_path / "incremental_run_metadata.json",
                        )
                        run_pipeline.return_value = payload

                        result = run_incremental_workflow(generator)

    detect_changes.assert_called_once_with(tmp_path, "abc123", "def456", raise_on_error=True)
    run_pipeline.assert_called_once_with(generator, base_ref="abc123", target_ref="def456")
    assert result == tmp_path / "analysis.json"


def test_run_incremental_workflow_raises_when_change_detection_fails(tmp_path: Path):
    generator = MagicMock()
    generator.output_dir = tmp_path
    generator.repo_location = tmp_path

    with patch("codeboarding_workflows.incremental.load_full_analysis", return_value=("root", {"1": "sub"})):
        with patch("codeboarding_workflows.incremental.load_analysis_metadata", return_value={"commit_hash": "abc123"}):
            with patch("codeboarding_workflows.incremental.get_git_commit_hash", return_value="def456"):
                with patch(
                    "codeboarding_workflows.incremental.detect_changes",
                    side_effect=ChangeDetectionError("bad diff"),
                ):
                    try:
                        run_incremental_workflow(generator)
                    except ChangeDetectionError as exc:
                        assert str(exc) == "bad diff"
                    else:
                        raise AssertionError("Expected ChangeDetectionError to be raised")

    generator.generate_analysis.assert_not_called()
    generator.generate_analysis_incremental.assert_not_called()
