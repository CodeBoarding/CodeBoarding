from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeboarding_workflows.analysis import BaselineUnavailableError, run_incremental
from repo_utils.change_detector import ChangeSet


@pytest.fixture
def patched(tmp_path: Path):
    """Patch the four collaborators of ``run_incremental`` and yield their mocks."""
    with ExitStack() as stack:
        gen_cls = stack.enter_context(patch("codeboarding_workflows.analysis.DiagramGenerator"))
        workflow = stack.enter_context(
            patch("codeboarding_workflows.analysis.run_incremental_workflow", return_value=tmp_path / "analysis.json")
        )
        detect = stack.enter_context(patch("codeboarding_workflows.analysis.detect_changes"))
        # Metadata read happens before detect_changes; supply a non-None default
        # so existing tests don't fail the cold-start guard.
        stack.enter_context(
            patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value={"depth_level": 1})
        )
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

    with pytest.raises(BaselineUnavailableError, match="bad object"):
        _invoke(tmp_path, base_ref="deadbeef")

    gen_cls.assert_not_called()


# ---------------------------------------------------------------------------
# run_partial tests
# ---------------------------------------------------------------------------

from agents.agent_responses import AnalysisInsights, Component
from codeboarding_workflows.analysis import run_partial


@pytest.fixture
def partial_patched(tmp_path: Path):
    """Stub the IO + generator collaborators of ``run_partial``.

    ``run_partial`` reads metadata, loads the full in-memory tree, calls
    ``process_component`` on the target, then hands the tree to
    ``generator.finalize_and_save`` (finalize + persist). We stub the IO and the
    generator so the test stays on the workflow's control flow; the save itself
    lives inside the mocked generator.
    """
    with ExitStack() as stack:
        gen_cls = stack.enter_context(patch("codeboarding_workflows.analysis.DiagramGenerator"))
        stack.enter_context(
            patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value={"depth_level": 2})
        )
        load_full = stack.enter_context(patch("codeboarding_workflows.analysis.load_full_analysis"))
        yield gen_cls, load_full


def _stub_generator(gen_cls, sub_analysis) -> MagicMock:
    gen = MagicMock()
    gen.process_component.return_value = ("ignored", sub_analysis, [])
    gen_cls.return_value = gen
    return gen


def test_run_partial_processes_root_component_and_rebuilds_relations(tmp_path: Path, partial_patched) -> None:
    """A root-level component_id is found, process_component runs, then the tree is finalized and saved."""
    gen_cls, load_full = partial_patched
    root_comp = Component(
        name="API",
        component_id="1",
        description="API root",
        key_entities=[],
        file_methods=[],
    )
    root_analysis = AnalysisInsights(description="fake", components=[root_comp], components_relations=[])
    load_full.return_value = (root_analysis, {})
    sub = AnalysisInsights(description="API sub", components=[], components_relations=[])
    gen = _stub_generator(gen_cls, sub)

    run_partial(
        repo_path=tmp_path,
        output_dir=tmp_path / "out",
        project_name="proj",
        component_id="1",
        run_id="rid",
        log_path="logs/run.log",
    )

    gen.pre_analysis.assert_called_once()
    gen.process_component.assert_called_once_with(root_comp)
    # persist_side_artifacts=False: expansion must not touch the static-analysis
    # cache/SHA tag or rewrite file_coverage.json.
    gen.finalize_and_save.assert_called_once_with(root_analysis, {"1": sub}, persist_side_artifacts=False)


def test_run_partial_processes_nested_component(tmp_path: Path, partial_patched) -> None:
    """A nested component_id is located inside a sub-analysis and passed to process_component."""
    gen_cls, load_full = partial_patched
    nested = Component(
        name="Auth",
        component_id="1.1",
        description="Auth",
        key_entities=[],
        file_methods=[],
    )
    root_comp = Component(
        name="API",
        component_id="1",
        description="API root",
        key_entities=[],
        file_methods=[],
    )
    root_analysis = AnalysisInsights(description="fake", components=[root_comp], components_relations=[])
    api_sub = AnalysisInsights(description="API sub", components=[nested], components_relations=[])
    load_full.return_value = (root_analysis, {"1": api_sub})
    new_sub = AnalysisInsights(description="Auth sub", components=[], components_relations=[])
    gen = _stub_generator(gen_cls, new_sub)

    run_partial(
        repo_path=tmp_path,
        output_dir=tmp_path / "out",
        project_name="proj",
        component_id="1.1",
        run_id="rid",
        log_path="logs/run.log",
    )

    gen.process_component.assert_called_once_with(nested)
    # The new sub-analysis is slotted in next to (not replacing) the API sub.
    finalize_call = gen.finalize_and_save.call_args
    assert finalize_call.args[1] == {"1": api_sub, "1.1": new_sub}


def test_run_partial_unknown_component_does_not_process(tmp_path: Path, partial_patched) -> None:
    """An unknown component_id logs an error and never invokes the generator's expansion path."""
    gen_cls, load_full = partial_patched
    root_analysis = AnalysisInsights(description="fake", components=[], components_relations=[])
    load_full.return_value = (root_analysis, {})

    gen = MagicMock()
    gen_cls.return_value = gen

    run_partial(
        repo_path=tmp_path,
        output_dir=tmp_path / "out",
        project_name="proj",
        component_id="99",
        run_id="rid",
        log_path="logs/run.log",
    )

    gen.process_component.assert_not_called()
    gen.finalize_and_save.assert_not_called()


def test_run_partial_failed_process_does_not_save(tmp_path: Path, partial_patched) -> None:
    """If process_component returns None (LLM failure), don't finalize or overwrite analysis.json."""
    gen_cls, load_full = partial_patched
    root_comp = Component(
        name="API",
        component_id="1",
        description="API root",
        key_entities=[],
        file_methods=[],
    )
    root_analysis = AnalysisInsights(description="fake", components=[root_comp], components_relations=[])
    load_full.return_value = (root_analysis, {})
    gen = MagicMock()
    gen.process_component.return_value = (None, None, [])
    gen_cls.return_value = gen

    run_partial(
        repo_path=tmp_path,
        output_dir=tmp_path / "out",
        project_name="proj",
        component_id="1",
        run_id="rid",
        log_path="logs/run.log",
    )

    gen.process_component.assert_called_once()
    gen.finalize_and_save.assert_not_called()


def test_run_partial_missing_baseline_raises(tmp_path: Path) -> None:
    """No metadata on disk means no baseline; run_partial must refuse to proceed."""
    with patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value=None):
        with pytest.raises(BaselineUnavailableError):
            run_partial(
                repo_path=tmp_path,
                output_dir=tmp_path / "out",
                project_name="proj",
                component_id="1",
                run_id="rid",
                log_path="logs/run.log",
            )
