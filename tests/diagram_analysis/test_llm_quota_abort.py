"""An exhausted LLM quota stops the run; it never degrades into a diagram without AI naming."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from agents.agent_responses import AnalysisInsights, Component
from agents.llm_errors import LLMQuotaError
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.io_utils import restore_analysis_on
from run_diagnostics.catalog import names_not_generated
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from utils import ANALYSIS_FILENAME


def _quota_error() -> LLMQuotaError:
    return LLMQuotaError(
        "quota exhausted",
        provider="openai",
        status_code=402,
        provider_message="Resource exhausted: token limit reached",
        telemetry_properties={"error_type": "quota"},
    )


def _generator(tmp_path: Path) -> DiagramGenerator:
    repo = tmp_path / "repo"
    repo.mkdir()
    return DiagramGenerator(
        repo_location=repo,
        temp_folder=tmp_path / "temp",
        repo_name="repo",
        output_dir=tmp_path / "out",
        depth_cap=3,
        run_id="run",
        log_path="repo/run-log",
    )


def _empty_analysis() -> AnalysisInsights:
    return AnalysisInsights(description="", components=[], components_relations=[])


def test_scope_semantics_reraise_quota_and_later_scopes_never_call_the_llm(tmp_path: Path) -> None:
    gen = _generator(tmp_path)
    gen.scope_analysis_agent = MagicMock()
    gen.scope_analysis_agent.analyze.side_effect = _quota_error()

    with pytest.raises(LLMQuotaError):
        gen._enrich_scope(ClusterScopeResult(scope_id="root"), _empty_analysis(), {"1"})
    with pytest.raises(LLMQuotaError):
        gen._enrich_scope(ClusterScopeResult(scope_id="1"), _empty_analysis(), {"1.1"})

    assert gen.scope_analysis_agent.analyze.call_count == 1
    assert gen._scopes_unnamed == 0


def test_quota_in_one_component_cancels_the_rest_of_the_expansion(tmp_path: Path) -> None:
    gen = _generator(tmp_path)
    components = [Component(name=f"C{i}", description="", key_entities=[], component_id=str(i)) for i in range(1, 6)]
    gen.clustering_hierarchy = ClusterScopeResult(
        scope_id="root",
        groups=[
            ClusterGroup(
                group_id=component.component_id,
                cluster_ids=[index],
                children=ClusterScopeResult(
                    scope_id=component.component_id,
                    groups=[ClusterGroup(group_id=f"{component.component_id}.1", cluster_ids=[index])],
                ),
            )
            for index, component in enumerate(components, start=1)
        ],
    )
    gen.clustering_hierarchy.index_hierarchy()
    gen.scope_assembler.build = Mock(side_effect=lambda scope: _empty_analysis())
    gen.scope_assembler.qualify_source_cluster_ids = Mock()
    gen.scope_analysis_agent = MagicMock()
    gen.scope_analysis_agent.analyze.side_effect = _quota_error()
    root = AnalysisInsights(description="", components=components, components_relations=[])

    # One worker makes the queue observable: everything behind the refused scope is cancelled or skipped.
    with patch("diagram_analysis.diagram_generator.os.cpu_count", return_value=1):
        with pytest.raises(LLMQuotaError):
            gen._generate_subcomponents(root, components)

    assert gen.scope_analysis_agent.analyze.call_count == 1
    assert not (gen.output_dir / ANALYSIS_FILENAME).exists()


def test_restore_analysis_on_puts_the_previous_analysis_back(tmp_path: Path) -> None:
    path = tmp_path / ANALYSIS_FILENAME
    path.write_text('{"previous": true}', encoding="utf-8")

    with pytest.raises(LLMQuotaError):
        with restore_analysis_on(tmp_path, (LLMQuotaError,)):
            path.write_text('{"partial": true}', encoding="utf-8")
            raise _quota_error()

    assert path.read_text(encoding="utf-8") == '{"previous": true}'


def test_restore_analysis_on_removes_a_file_the_stopped_run_created(tmp_path: Path) -> None:
    with pytest.raises(LLMQuotaError):
        with restore_analysis_on(tmp_path, (LLMQuotaError,)):
            (tmp_path / ANALYSIS_FILENAME).write_text('{"partial": true}', encoding="utf-8")
            raise _quota_error()

    assert not (tmp_path / ANALYSIS_FILENAME).exists()


def test_restore_analysis_on_leaves_other_failures_alone(tmp_path: Path) -> None:
    path = tmp_path / ANALYSIS_FILENAME
    with pytest.raises(ValueError):
        with restore_analysis_on(tmp_path, (LLMQuotaError,)):
            path.write_text('{"written": true}', encoding="utf-8")
            raise ValueError("not terminal")

    assert path.read_text(encoding="utf-8") == '{"written": true}'


def test_survivable_scope_failure_still_falls_back_and_names_its_reason(tmp_path: Path) -> None:
    gen = _generator(tmp_path)
    gen.scope_analysis_agent = MagicMock()
    gen.scope_analysis_agent.analyze.side_effect = TimeoutError("provider timed out")

    gen._enrich_scope(ClusterScopeResult(scope_id="root"), _empty_analysis(), {"1"})

    assert (gen._scopes_enriched, gen._scopes_unnamed) == (1, 1)
    assert gen._naming_failures.most_common(1) == [("TimeoutError", 1)]


def test_process_component_failure_is_recorded_as_a_diagnostic(tmp_path: Path) -> None:
    gen = _generator(tmp_path)
    gen.clustering_hierarchy = ClusterScopeResult(
        scope_id="root",
        groups=[ClusterGroup(group_id="1", cluster_ids=[1], children=ClusterScopeResult(scope_id="1"))],
    )
    gen.clustering_hierarchy.index_hierarchy()
    gen.scope_assembler.build = Mock(side_effect=RuntimeError("boom"))
    component = Component(name="Engine", description="", key_entities=[], component_id="1")

    assert gen._process_component(component) == (None, None, [])

    [entry] = gen.run_diagnostics.report().entries
    assert entry.code == "diagram.component_not_expanded"
    assert entry.subject == "Engine"
    assert "RuntimeError" in entry.detail


def test_names_not_generated_no_longer_blames_quota() -> None:
    diagnostic = names_not_generated(2, 5, "TimeoutError")

    assert "quota" not in diagnostic.remedy.lower()
    assert "TimeoutError" in diagnostic.detail
