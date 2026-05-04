from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeboarding_workflows.analysis import run_incremental
from repo_utils.change_detector import ChangeSet


@pytest.fixture
def patched(tmp_path: Path):
    """Patch the four collaborators of ``run_incremental`` and yield their mocks."""
    with ExitStack() as stack:
        gen_cls = stack.enter_context(patch("codeboarding_workflows.analysis.DiagramGenerator"))
        workflow = stack.enter_context(
            patch("codeboarding_workflows.analysis.run_incremental_workflow", return_value=tmp_path / "analysis.json")
        )
        last = stack.enter_context(patch("codeboarding_workflows.analysis.last_successful_commit"))
        detect = stack.enter_context(patch("codeboarding_workflows.analysis.detect_changes"))
        yield gen_cls, workflow, last, detect


def _invoke(tmp_path: Path, **kwargs) -> None:
    run_incremental(
        repo_path=tmp_path,
        output_dir=tmp_path / "out",
        project_name="proj",
        run_id="rid",
        log_path="logs/run.log",
        **kwargs,
    )


def test_run_incremental_forwards_static_analyzer_to_generator(tmp_path: Path, patched) -> None:
    """Warm-LSP injection: ``static_analyzer`` must reach ``DiagramGenerator.__init__``.

    Why: the wrapper hands a long-lived StaticAnalyzer with warm LSP servers
    in via this kwarg; if it's silently dropped on the workflow boundary,
    incremental analysis cold-starts a new analyzer instead.
    """
    gen_cls, _workflow, last, _detect = patched
    last.return_value = None
    sentinel_analyzer = MagicMock(name="static_analyzer")

    _invoke(tmp_path, static_analyzer=sentinel_analyzer)

    gen_cls.assert_called_once()
    assert gen_cls.call_args.kwargs["static_analyzer"] is sentinel_analyzer


def test_run_incremental_auto_detects_baseline_from_metadata(tmp_path: Path, patched) -> None:
    gen_cls, _workflow, last, detect = patched
    last.return_value = "deadbeef"
    detect.return_value = ChangeSet(base_ref="deadbeef", target_ref="", files=[])

    _invoke(tmp_path)

    detect.assert_called_once_with(tmp_path, "deadbeef", "")
    assert gen_cls.return_value.changes is detect.return_value


def test_run_incremental_uses_explicit_base_ref_over_metadata(tmp_path: Path, patched) -> None:
    _gen_cls, _workflow, last, detect = patched
    last.return_value = "from-metadata"
    detect.return_value = ChangeSet(base_ref="abc", target_ref="", files=[])

    _invoke(tmp_path, base_ref="abc")

    detect.assert_called_once_with(tmp_path, "abc", "")


def test_run_incremental_passes_target_ref_through(tmp_path: Path, patched) -> None:
    _gen_cls, _workflow, _last, detect = patched
    detect.return_value = ChangeSet(base_ref="abc", target_ref="HEAD", files=[])

    _invoke(tmp_path, base_ref="abc", target_ref="HEAD")

    detect.assert_called_once_with(tmp_path, "abc", "HEAD")


def test_run_incremental_no_baseline_runs_unscoped(tmp_path: Path, patched) -> None:
    gen_cls, _workflow, last, detect = patched
    last.return_value = None

    _invoke(tmp_path)

    detect.assert_not_called()
    assert gen_cls.return_value.changes is None


def test_run_incremental_diff_error_falls_back_to_unscoped(tmp_path: Path, patched) -> None:
    gen_cls, _workflow, last, detect = patched
    last.return_value = "deadbeef"
    detect.return_value = ChangeSet(base_ref="deadbeef", target_ref="", error="bad object")

    _invoke(tmp_path)

    assert gen_cls.return_value.changes is None
