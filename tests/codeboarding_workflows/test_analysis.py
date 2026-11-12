import json
import shutil
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeboarding_workflows.analysis import (
    BaselineUnavailableError,
    run_full,
    run_incremental,
    run_incremental_workflow,
    run_partial,
)
from agents.agent_responses import AnalysisInsights, Component
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.io_utils import load_full_analysis
from static_analyzer.clustering.names import ComponentRule, ScopeSpec, TreeSpec
from diagram_analysis.exceptions import ScopeSemanticsError
from diagram_analysis.run_context import RunContext, RunPaths
from infra.repo_utils.change_detector import ChangeSet

LEGACY_ANALYSIS = Path(__file__).parents[1] / "fixtures" / "legacy_analysis.json"


@pytest.fixture
def patched(tmp_path: Path):
    """Patch the collaborators of ``run_incremental`` and yield their mocks.

    Detection is git-free via ``detect_changes_from_fingerprint``. The warm-start
    source_sha is computed inside the generator (prepare_analysis), not here.
    """
    with ExitStack() as stack:
        gen_cls = stack.enter_context(patch("codeboarding_workflows.analysis.DiagramGenerator"))
        baseline = stack.enter_context(
            patch(
                "codeboarding_workflows.analysis.load_full_analysis",
                return_value=(AnalysisInsights(description="", components=[], components_relations=[]), {}),
            )
        )
        detect = stack.enter_context(
            patch(
                "codeboarding_workflows.analysis.detect_changes_from_fingerprint",
                return_value=ChangeSet(files=[]),
            )
        )
        stack.enter_context(
            patch(
                "codeboarding_workflows.analysis.load_analysis_metadata",
                return_value={"depth_level": 1, "format_version": "v1"},
            )
        )
        yield gen_cls, baseline, detect


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


def test_run_incremental_refuses_another_engines_baseline_before_starting_anything(tmp_path: Path, patched) -> None:
    """Why: the IDE prompts on ``BaselineUnavailableError``; a full run there would spend the user's own credits."""
    gen_cls, _workflow, detect = patched
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "static_analysis.pkl").write_bytes(b"not-a-real-pickle")
    (output_dir / "static_analysis.sha").write_text("v1\ndeadbeef\n")

    with pytest.raises(BaselineUnavailableError, match="different engine version"):
        _invoke(tmp_path)

    gen_cls.assert_not_called()
    detect.assert_not_called()


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


def test_run_incremental_refuses_a_baseline_in_an_older_format(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    shutil.copy(LEGACY_ANALYSIS, output_dir / "analysis.json")

    with patch("codeboarding_workflows.analysis.DiagramGenerator") as gen_cls:
        with pytest.raises(BaselineUnavailableError, match="predates format versioning"):
            _invoke(tmp_path)

    gen_cls.assert_not_called()


def test_run_partial_refuses_a_baseline_in_an_older_format(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    shutil.copy(LEGACY_ANALYSIS, output_dir / "analysis.json")

    with patch("codeboarding_workflows.analysis.DiagramGenerator") as gen_cls:
        with pytest.raises(BaselineUnavailableError, match="predates format versioning"):
            run_partial(
                RunPaths(repo_path=tmp_path, output_dir=output_dir, project_name="proj"),
                RunContext(run_id="rid", log_path="logs/run.log"),
                component_id="1",
            )

    gen_cls.assert_not_called()


def test_run_incremental_refuses_a_baseline_whose_body_is_unreadable(tmp_path: Path) -> None:
    """Why: the format check reads only metadata; a broken body must still ask for a full run, not start one."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "analysis.json").write_text(json.dumps({"metadata": {"format_version": "v1"}}))

    with (
        patch("codeboarding_workflows.analysis.DiagramGenerator") as gen_cls,
        patch("codeboarding_workflows.analysis.detect_changes_from_fingerprint", return_value=ChangeSet(files=[])),
    ):
        with pytest.raises(BaselineUnavailableError, match="not a valid 'v1' document"):
            _invoke(tmp_path)

    gen_cls.return_value.generate_analysis.assert_not_called()


def test_run_incremental_workflow_runs_in_full_over_a_baseline_whose_body_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text(json.dumps({"metadata": {"format_version": "v1"}}))
    generator = MagicMock(output_dir=tmp_path)

    run_incremental_workflow(generator)

    generator.generate_analysis.assert_called_once_with()


def test_run_incremental_workflow_runs_in_full_over_a_baseline_in_an_older_format(tmp_path: Path) -> None:
    shutil.copy(LEGACY_ANALYSIS, tmp_path / "analysis.json")
    generator = MagicMock(output_dir=tmp_path)

    run_incremental_workflow(generator)

    generator.generate_analysis.assert_called_once_with()
    generator.generate_analysis_incremental.assert_not_called()


def saving_generator(**kwargs) -> DiagramGenerator:
    """A DiagramGenerator built like ``build_generator``'s whose analysis is a fixed tree, saved through the real path.

    Its ``generate_analysis`` makes a progress save and the final save, as a full run does, with no LLM or
    static analysis behind them.
    """
    generator = DiagramGenerator.__new__(DiagramGenerator)
    generator.output_dir = kwargs["output_dir"]
    generator.repo_location = Path(kwargs["repo_location"])
    generator.repo_name = kwargs["repo_name"]
    generator.depth_cap = kwargs["depth_cap"]
    generator.static_analysis = None
    generator.clustering_hierarchy = None
    generator._baseline_global_relations = None
    generator._scopes_unnamed = generator._scopes_enriched = 0
    generator.tree_spec = TreeSpec(scopes={ROOT_SCOPE_ID: ScopeSpec(ROOT_SCOPE_ID, [ComponentRule("1", "Parent")])})
    generator.file_coverage_data = {
        "analyzed_files": ["a.py"],
        "not_analyzed_files": [],
        "summary": {"total_files": 1, "analyzed": 1, "not_analyzed": 0, "not_analyzed_by_reason": {}},
    }

    def generate_analysis() -> Path:
        members = [
            FileMethodGroup(
                file_path="a.py",
                methods=[MethodEntry(qualified_name="a.f", start_line=1, end_line=2, node_type="FUNCTION")],
            )
        ]
        root = AnalysisInsights(
            description="d",
            components=[Component(name="A", component_id="1", description="", key_entities=[], file_methods=members)],
            components_relations=[],
        )
        with (
            patch.object(DiagramGenerator, "_source_tree_hash", return_value=""),
            patch.object(DiagramGenerator, "_source_tree_fingerprint_map", return_value={}),
            patch.object(DiagramGenerator, "_persist_static_analysis_artifact"),
        ):
            generator._save_progress(root, {})
            return generator.finalize_and_save(root, {})

    generator.generate_analysis = generate_analysis  # type: ignore[method-assign]
    return generator


def test_a_full_run_over_an_unreadable_analysis_rewrites_it(tmp_path: Path) -> None:
    """Why: the error tells the user to run a full analysis, so the full run must not need the old file."""
    (tmp_path / "analysis.json").write_text("{not json")

    with patch("codeboarding_workflows.analysis.DiagramGenerator", side_effect=saving_generator):
        run_full(
            RunPaths(repo_path=tmp_path, output_dir=tmp_path, project_name="proj"),
            RunContext(run_id="rid", log_path="logs/run.log"),
        )

    loaded = load_full_analysis(tmp_path)
    assert loaded is not None
    assert [component.component_id for component in loaded[0].components] == ["1"]


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
