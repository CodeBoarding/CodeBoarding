from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeboarding_workflows.analysis import BaselineUnavailableError, run_incremental, run_incremental_workflow
from diagram_analysis.run_context import RunContext, RunPaths
from repo_utils.change_detector import ChangeSet


@pytest.fixture
def patched(tmp_path: Path):
    """Patch the collaborators of ``run_incremental`` and yield their mocks.

    Detection is git-free via ``detect_changes_from_fingerprint``. The warm-start
    source_sha is computed inside the generator (pre_analysis), not here.
    """
    with ExitStack() as stack:
        gen_cls = stack.enter_context(patch("codeboarding_workflows.analysis.DiagramGenerator"))
        workflow = stack.enter_context(
            patch("codeboarding_workflows.analysis.run_incremental_workflow", return_value=tmp_path / "analysis.json")
        )
        detect = stack.enter_context(
            patch(
                "codeboarding_workflows.analysis.detect_changes_from_fingerprint",
                return_value=ChangeSet(files=[]),
            )
        )
        stack.enter_context(
            patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value={"depth_level": 1})
        )
        yield gen_cls, workflow, detect


def _invoke(tmp_path: Path, **kwargs) -> None:
    run_incremental(
        RunPaths(repo_path=tmp_path, output_dir=tmp_path / "out", project_name="proj"),
        RunContext(run_id="rid", log_path="logs/run.log", repo_dir=tmp_path),
        **kwargs,
    )


def test_run_incremental_detects_changes_git_free(tmp_path: Path, patched) -> None:
    _gen_cls, _workflow, detect = patched

    _invoke(tmp_path)

    detect.assert_called_once_with(tmp_path, tmp_path / "out")


def test_run_incremental_threads_changeset_to_generator(tmp_path: Path, patched) -> None:
    gen_cls, _workflow, detect = patched

    _invoke(tmp_path)

    assert gen_cls.call_args.kwargs["changes"] is detect.return_value


def test_run_incremental_forwards_static_analyzer_to_generator(tmp_path: Path, patched) -> None:
    """Warm-LSP injection: ``static_analyzer`` must reach ``DiagramGenerator.__init__``.

    Why: the wrapper hands a long-lived StaticAnalyzer with warm LSP servers in
    via this kwarg; if dropped, incremental cold-starts a new analyzer.
    """
    gen_cls, _workflow, _detect = patched
    sentinel_analyzer = MagicMock(name="static_analyzer")

    _invoke(tmp_path, static_analyzer=sentinel_analyzer)

    assert gen_cls.call_args.kwargs["static_analyzer"] is sentinel_analyzer


def test_run_incremental_no_baseline_raises(tmp_path: Path, patched) -> None:
    _gen_cls, _workflow, _detect = patched
    with patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value=None):
        with pytest.raises(BaselineUnavailableError, match="No baseline"):
            _invoke(tmp_path)


def test_incremental_workflow_detects_changes_for_external_caller(tmp_path: Path) -> None:
    generator = MagicMock()
    generator.output_dir = tmp_path / "out"
    generator.repo_location = tmp_path
    generator.changes = None
    detected = ChangeSet.from_changed_files([], ["pkg/app.py"], [])
    generator.generate_analysis_incremental.return_value = tmp_path / "out" / "analysis.json"
    root = MagicMock(components=[MagicMock()])

    with (
        patch("codeboarding_workflows.analysis.load_full_analysis", return_value=(root, {})),
        patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value={"depth_level": 1}),
        patch("codeboarding_workflows.analysis.detect_changes_from_fingerprint", return_value=detected) as detect,
    ):
        result = run_incremental_workflow(generator)

    detect.assert_called_once_with(tmp_path, tmp_path / "out")
    assert generator.changes is detected
    generator.generate_analysis_incremental.assert_called_once_with(root, {})
    assert result == tmp_path / "out" / "analysis.json"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (ChangeSet(), "at least one source modification"),
        (ChangeSet(error="fingerprint read failed"), "untrustworthy"),
    ],
)
def test_incremental_workflow_rejects_empty_or_untrustworthy_changes(
    tmp_path: Path,
    changes: ChangeSet,
    message: str,
) -> None:
    generator = MagicMock()
    generator.output_dir = tmp_path / "out"
    generator.repo_location = tmp_path
    generator.changes = changes
    root = MagicMock(components=[MagicMock()])

    with (
        patch("codeboarding_workflows.analysis.load_full_analysis", return_value=(root, {})),
        patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value={"depth_level": 1}),
        pytest.raises(BaselineUnavailableError, match=message),
    ):
        run_incremental_workflow(generator)

    generator.generate_analysis_incremental.assert_not_called()
