from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeboarding_workflows.analysis import IncrementalUnavailableError, run_incremental
from repo_utils.change_detector import ChangeSet


@pytest.fixture
def patched(tmp_path: Path):
    """Patch the three collaborators of ``run_incremental`` and yield their mocks."""
    with ExitStack() as stack:
        gen_cls = stack.enter_context(patch("codeboarding_workflows.analysis.DiagramGenerator"))
        workflow = stack.enter_context(
            patch("codeboarding_workflows.analysis.run_incremental_workflow", return_value=tmp_path / "analysis.json")
        )
        detect = stack.enter_context(patch("codeboarding_workflows.analysis.detect_changes"))
        yield gen_cls, workflow, detect


def _invoke(tmp_path: Path, *, base_ref: str = "abc", target_ref: str = "HEAD", **kwargs) -> None:
    run_incremental(
        repo_path=tmp_path,
        output_dir=tmp_path / "out",
        project_name="proj",
        run_id="rid",
        log_path="logs/run.log",
        base_ref=base_ref,
        target_ref=target_ref,
        **kwargs,
    )


def test_run_incremental_forwards_static_analyzer_to_generator(tmp_path: Path, patched) -> None:
    """Warm-LSP injection: ``static_analyzer`` must reach ``DiagramGenerator.__init__``.

    Why: the wrapper hands a long-lived StaticAnalyzer with warm LSP servers
    in via this kwarg; if it's silently dropped on the workflow boundary,
    incremental analysis cold-starts a new analyzer instead.
    """
    gen_cls, _workflow, detect = patched
    detect.return_value = ChangeSet(base_ref="abc", target_ref="", files=[])
    sentinel_analyzer = MagicMock(name="static_analyzer")

    _invoke(tmp_path, static_analyzer=sentinel_analyzer)

    gen_cls.assert_called_once()
    assert gen_cls.call_args.kwargs["static_analyzer"] is sentinel_analyzer


def test_run_incremental_passes_base_ref_to_detect_changes(tmp_path: Path, patched) -> None:
    _gen_cls, _workflow, detect = patched
    detect.return_value = ChangeSet(base_ref="abc", target_ref="HEAD", files=[])

    _invoke(tmp_path, base_ref="abc")

    detect.assert_called_once_with(tmp_path, "abc", "HEAD")


def test_run_incremental_passes_target_ref_through(tmp_path: Path, patched) -> None:
    _gen_cls, _workflow, detect = patched
    detect.return_value = ChangeSet(base_ref="abc", target_ref="HEAD", files=[])

    _invoke(tmp_path, base_ref="abc", target_ref="HEAD")

    detect.assert_called_once_with(tmp_path, "abc", "HEAD")


def test_run_incremental_threads_changeset_to_generator(tmp_path: Path, patched) -> None:
    gen_cls, _workflow, detect = patched
    detect.return_value = ChangeSet(base_ref="abc", target_ref="", files=[])

    _invoke(tmp_path, base_ref="abc")

    assert gen_cls.call_args.kwargs["changes"] is detect.return_value


def test_run_incremental_diff_error_raises(tmp_path: Path, patched) -> None:
    """Bad git ref / corrupted worktree: fail loudly with the underlying error message."""
    gen_cls, _workflow, detect = patched
    detect.return_value = ChangeSet(base_ref="deadbeef", target_ref="", error="bad object")

    with pytest.raises(IncrementalUnavailableError, match="bad object"):
        _invoke(tmp_path, base_ref="deadbeef")

    gen_cls.assert_not_called()
