from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeboarding_workflows.analysis import (
    BaselineUnavailableError,
    run_full,
    run_incremental,
    run_incremental_workflow,
)
from diagram_analysis.exceptions import ScopeSemanticsError
from diagram_analysis.run_context import RunContext, RunPaths
from repo_utils.change_detector import ChangeSet


@pytest.fixture
def patched(tmp_path: Path):
    """Patch the collaborators of ``run_incremental`` and yield their mocks.

    Detection is git-free via ``detect_changes_from_fingerprint``. The warm-start
    source_sha is computed inside the generator (prepare_analysis), not here.
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
        RunContext(run_id="rid", log_path="logs/run.log"),
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


def _fail_after_a_progress_save(analysis_path: Path):
    def run(*_args):
        analysis_path.write_text("partial", encoding="utf-8")
        raise ScopeSemanticsError("root", telemetry_properties={})

    return run


def test_run_full_restores_the_previous_analysis_when_it_fails(tmp_path: Path) -> None:
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text("previous", encoding="utf-8")

    with patch("codeboarding_workflows.analysis.DiagramGenerator") as gen_cls:
        gen_cls.return_value.generate_analysis.side_effect = _fail_after_a_progress_save(analysis_path)
        with pytest.raises(ScopeSemanticsError):
            run_full(
                RunPaths(repo_path=tmp_path, output_dir=tmp_path, project_name="proj"),
                RunContext(run_id="rid", log_path="logs/run.log"),
            )

    assert analysis_path.read_text(encoding="utf-8") == "previous"


def test_run_incremental_workflow_removes_an_analysis_the_failed_run_created(tmp_path: Path) -> None:
    analysis_path = tmp_path / "analysis.json"
    generator = MagicMock(output_dir=tmp_path)
    generator.generate_analysis.side_effect = _fail_after_a_progress_save(analysis_path)

    with pytest.raises(ScopeSemanticsError):
        run_incremental_workflow(generator)

    assert not analysis_path.exists()
