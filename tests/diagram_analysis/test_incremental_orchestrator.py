"""Tests for the incremental analysis orchestrator (DiagramGenerator methods)."""

from unittest.mock import Mock, patch

import pytest

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.incremental_models import (
    EscalationLevel,
    IncrementalSummaryKind,
    ImpactedComponent,
    TraceResult,
    TraceStopReason,
)
from diagram_analysis.diagram_generator import DiagramGenerator, IncrementalAnalysisRequiresFullError
from repo_utils.change_detector import ChangeSet


def _make_generator(tmp_path):
    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path / "temp",
        repo_name="test-repo",
        output_dir=tmp_path / ".codeboarding",
        depth_level=2,
        run_id="test-run",
        log_path=str(tmp_path / "log"),
    )
    return gen


def _make_root_analysis(n_components: int = 3) -> AnalysisInsights:
    components = [
        Component(
            name=f"Component{i}",
            component_id=str(i),
            description=f"Component {i}",
            key_entities=[],
        )
        for i in range(1, n_components + 1)
    ]
    return AnalysisInsights(
        description="Test project",
        components=components,
        components_relations=[],
    )


# -------------------------------------------------------------------
# Step 1: missing baseline error
# -------------------------------------------------------------------
def test_incremental_requires_baseline(tmp_path):
    gen = _make_generator(tmp_path)
    (tmp_path / ".codeboarding").mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="requires an existing analysis"):
        gen.generate_analysis_incremental()


def test_incremental_skips_llm_init_when_trace_plan_is_empty(tmp_path):
    gen = _make_generator(tmp_path)
    output_dir = tmp_path / ".codeboarding"
    output_dir.mkdir(parents=True, exist_ok=True)
    gen.static_analysis = Mock()

    checkpoint = Mock(checkpoint_ref="refs/codeboarding/checkpoints/latest")
    root_analysis = _make_root_analysis(2)
    delta = Mock(is_purely_additive=False, file_deltas=[Mock()])
    changes = Mock(parsed_diff=None, deleted_files=[])
    saved_path = output_dir / "analysis.json"

    with (
        patch.object(
            gen,
            "_load_incremental_baseline",
            return_value=(checkpoint, root_analysis, {}, Mock(), {}),
        ),
        patch("diagram_analysis.diagram_generator.detect_changes", return_value=changes),
        patch.object(gen, "_compute_incremental_delta", return_value=delta),
        patch(
            "diagram_analysis.incremental_tracer.build_trace_plan",
            return_value=Mock(groups=[], fast_path_impacted_methods=[]),
        ),
        patch(
            "diagram_analysis.incremental_tracer.classify_scope",
            return_value=TraceResult(),
        ),
        patch("diagram_analysis.incremental_updater.apply_delta") as mock_apply_delta,
        patch("diagram_analysis.diagram_generator.initialize_llms") as mock_initialize_llms,
        patch.object(gen, "_run_semantic_trace") as mock_run_semantic_trace,
        patch.object(gen, "_determine_escalation", return_value=EscalationLevel.NONE),
        patch.object(gen, "_save_incremental_result", return_value=saved_path) as mock_save_result,
    ):
        result = gen.generate_analysis_incremental()

    mock_apply_delta.assert_called_once()
    mock_initialize_llms.assert_not_called()
    mock_run_semantic_trace.assert_not_called()
    mock_save_result.assert_called_once()
    assert result == saved_path
    assert gen.last_incremental_summary is not None
    assert gen.last_incremental_summary.kind == IncrementalSummaryKind.COSMETIC_ONLY
    assert gen.last_incremental_summary.used_llm is False


def test_incremental_runs_semantic_trace_for_purely_additive_delta(tmp_path):
    gen = _make_generator(tmp_path)
    output_dir = tmp_path / ".codeboarding"
    output_dir.mkdir(parents=True, exist_ok=True)
    gen.static_analysis = Mock()

    checkpoint = Mock(checkpoint_ref="refs/codeboarding/checkpoints/latest")
    root_analysis = _make_root_analysis(2)
    file_component_index = Mock()
    delta = Mock(is_purely_additive=True, file_deltas=[Mock(file_status="modified")])
    changes = Mock(parsed_diff=None, deleted_files=[])
    saved_path = output_dir / "analysis.json"
    agent_llm = Mock()
    parsing_llm = Mock()
    trace_result = TraceResult()

    with (
        patch.object(
            gen,
            "_load_incremental_baseline",
            return_value=(checkpoint, root_analysis, {}, file_component_index, {}),
        ),
        patch("diagram_analysis.diagram_generator.detect_changes", return_value=changes),
        patch.object(gen, "_compute_incremental_delta", return_value=delta),
        patch(
            "diagram_analysis.incremental_tracer.build_trace_plan",
            return_value=Mock(groups=[Mock()], fast_path_impacted_methods=[]),
        ),
        patch("diagram_analysis.incremental_updater.apply_delta") as mock_apply_delta,
        patch(
            "diagram_analysis.diagram_generator.initialize_llms",
            return_value=(agent_llm, parsing_llm),
        ) as mock_initialize_llms,
        patch.object(gen, "_run_semantic_trace", return_value=trace_result) as mock_run_semantic_trace,
        patch.object(gen, "_determine_escalation", return_value=EscalationLevel.NONE),
        patch.object(gen, "_save_incremental_result", return_value=saved_path) as mock_save_result,
    ):
        result = gen.generate_analysis_incremental()

    mock_apply_delta.assert_called_once()
    mock_initialize_llms.assert_called_once()
    mock_run_semantic_trace.assert_called_once_with(
        delta,
        {},
        gen.static_analysis,
        checkpoint.checkpoint_ref,
        file_component_index,
        agent_llm,
        parsing_llm,
        None,
    )
    mock_save_result.assert_called_once()
    assert result == saved_path
    assert gen.last_incremental_summary is not None
    assert gen.last_incremental_summary.kind == IncrementalSummaryKind.ADDITIVE_ONLY
    assert gen.last_incremental_summary.used_llm is True


def test_incremental_summary_reports_no_changes_when_worktree_is_clean(tmp_path):
    gen = _make_generator(tmp_path)
    output_dir = tmp_path / ".codeboarding"
    output_dir.mkdir(parents=True, exist_ok=True)
    gen.static_analysis = Mock()

    checkpoint = Mock(checkpoint_ref="refs/codeboarding/checkpoints/latest")
    root_analysis = _make_root_analysis(1)
    changes = ChangeSet()
    restored_path = output_dir / "analysis.json"

    with (
        patch.object(
            gen,
            "_load_incremental_baseline",
            return_value=(checkpoint, root_analysis, {}, Mock(), {}),
        ),
        patch("diagram_analysis.diagram_generator.detect_changes", return_value=changes),
        patch.object(gen, "_compute_incremental_delta", return_value=None),
        patch.object(gen, "_restore_or_raise", return_value=restored_path),
    ):
        result = gen.generate_analysis_incremental()

    assert result == restored_path
    assert gen.last_incremental_summary is not None
    assert gen.last_incremental_summary.kind == IncrementalSummaryKind.NO_CHANGES
    assert gen.last_incremental_summary.used_llm is False


def test_build_incremental_summary_uses_semantic_impact_sentence():
    trace = TraceResult(
        stop_reason=TraceStopReason.CLOSURE_REACHED,
        impacted_components=[ImpactedComponent(component_id="1", impacted_methods=["pkg.alpha"])],
        semantic_impact_summary="The change broadens validation behavior to reject malformed inputs earlier.",
    )

    summary = DiagramGenerator._build_incremental_summary(
        delta=Mock(file_deltas=[Mock(file_status="modified")], is_purely_additive=False),
        trace_result=trace,
        escalation=EscalationLevel.NONE,
        used_llm=True,
    )

    assert summary.kind == IncrementalSummaryKind.MATERIAL_IMPACT
    assert summary.message == trace.semantic_impact_summary
    assert summary.used_llm is True


def test_build_incremental_summary_prioritizes_material_impact_over_additive():
    trace = TraceResult(
        stop_reason=TraceStopReason.CLOSURE_REACHED,
        impacted_components=[ImpactedComponent(component_id="1", impacted_methods=["pkg.alpha"])],
        semantic_impact_summary="The new helper changes component behavior enough to update the diagram.",
    )

    summary = DiagramGenerator._build_incremental_summary(
        delta=Mock(file_deltas=[Mock(file_status="modified")], is_purely_additive=True),
        trace_result=trace,
        escalation=EscalationLevel.NONE,
        used_llm=True,
    )

    assert summary.kind == IncrementalSummaryKind.MATERIAL_IMPACT
    assert summary.message == trace.semantic_impact_summary


def test_build_incremental_summary_marks_requires_full_analysis_for_syntax_error():
    summary = DiagramGenerator._build_incremental_summary(
        delta=Mock(file_deltas=[Mock(file_status="modified")], is_purely_additive=False),
        trace_result=TraceResult(stop_reason=TraceStopReason.SYNTAX_ERROR),
        used_llm=False,
    )

    assert summary.kind == IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS
    assert summary.requires_full_analysis is True


def test_incremental_syntax_error_requires_full_analysis(tmp_path):
    gen = _make_generator(tmp_path)
    output_dir = tmp_path / ".codeboarding"
    output_dir.mkdir(parents=True, exist_ok=True)
    gen.static_analysis = Mock()

    checkpoint = Mock(checkpoint_ref="refs/codeboarding/checkpoints/latest")
    root_analysis = _make_root_analysis(2)
    delta = Mock(is_purely_additive=False, file_deltas=[Mock(file_status="modified")])
    changes = Mock(parsed_diff=None, deleted_files=[])
    agent_llm = Mock()
    parsing_llm = Mock()
    trace_result = TraceResult(stop_reason=TraceStopReason.SYNTAX_ERROR)

    with (
        patch.object(
            gen,
            "_load_incremental_baseline",
            return_value=(checkpoint, root_analysis, {}, Mock(), {}),
        ),
        patch("diagram_analysis.diagram_generator.detect_changes", return_value=changes),
        patch.object(gen, "_compute_incremental_delta", return_value=delta),
        patch(
            "diagram_analysis.incremental_tracer.build_trace_plan",
            return_value=Mock(groups=[Mock()], fast_path_impacted_methods=[]),
        ),
        patch("diagram_analysis.incremental_updater.apply_delta"),
        patch(
            "diagram_analysis.diagram_generator.initialize_llms",
            return_value=(agent_llm, parsing_llm),
        ),
        patch.object(gen, "_run_semantic_trace", return_value=trace_result),
        patch.object(gen, "_determine_escalation") as mock_determine_escalation,
        patch.object(gen, "_save_incremental_result") as mock_save_result,
    ):
        with pytest.raises(IncrementalAnalysisRequiresFullError, match="Run a full analysis instead"):
            gen.generate_analysis_incremental()

    mock_determine_escalation.assert_not_called()
    mock_save_result.assert_not_called()
    assert gen.last_incremental_summary is not None
    assert gen.last_incremental_summary.kind == IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS
    assert gen.last_incremental_summary.requires_full_analysis is True


def test_incremental_root_escalation_requires_full_analysis(tmp_path):
    gen = _make_generator(tmp_path)
    output_dir = tmp_path / ".codeboarding"
    output_dir.mkdir(parents=True, exist_ok=True)
    gen.static_analysis = Mock()

    checkpoint = Mock(checkpoint_ref="refs/codeboarding/checkpoints/latest")
    root_analysis = _make_root_analysis(2)
    delta = Mock(is_purely_additive=False, file_deltas=[Mock(file_status="modified")])
    changes = Mock(parsed_diff=None, deleted_files=[])
    agent_llm = Mock()
    parsing_llm = Mock()
    trace_result = TraceResult(stop_reason=TraceStopReason.CLOSURE_REACHED)

    with (
        patch.object(
            gen,
            "_load_incremental_baseline",
            return_value=(checkpoint, root_analysis, {}, Mock(), {}),
        ),
        patch("diagram_analysis.diagram_generator.detect_changes", return_value=changes),
        patch.object(gen, "_compute_incremental_delta", return_value=delta),
        patch(
            "diagram_analysis.incremental_tracer.build_trace_plan",
            return_value=Mock(groups=[Mock()], fast_path_impacted_methods=[]),
        ),
        patch("diagram_analysis.incremental_updater.apply_delta"),
        patch(
            "diagram_analysis.diagram_generator.initialize_llms",
            return_value=(agent_llm, parsing_llm),
        ),
        patch.object(gen, "_run_semantic_trace", return_value=trace_result),
        patch.object(gen, "_determine_escalation", return_value=EscalationLevel.ROOT),
        patch.object(gen, "_save_incremental_result") as mock_save_result,
        patch.object(gen, "generate_analysis") as mock_generate_analysis,
    ):
        with pytest.raises(IncrementalAnalysisRequiresFullError, match="Run a full analysis instead"):
            gen.generate_analysis_incremental()

    mock_save_result.assert_not_called()
    mock_generate_analysis.assert_not_called()
    assert gen.last_incremental_summary is not None
    assert gen.last_incremental_summary.kind == IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS
    assert gen.last_incremental_summary.requires_full_analysis is True


# -------------------------------------------------------------------
# Step 12: escalation rules
# -------------------------------------------------------------------
class TestEscalation:
    def test_uncertain_trace_triggers_scoped(self):
        trace = TraceResult(stop_reason=TraceStopReason.UNCERTAIN)
        root = _make_root_analysis(3)
        changes = ChangeSet()

        level = DiagramGenerator._determine_escalation(trace, root, changes)
        assert level == EscalationLevel.SCOPED

    def test_majority_components_triggers_root(self):
        trace = TraceResult(
            stop_reason=TraceStopReason.CLOSURE_REACHED,
            impacted_components=[
                ImpactedComponent(component_id="1"),
                ImpactedComponent(component_id="2"),
            ],
        )
        root = _make_root_analysis(3)  # 2/3 > 50%
        changes = ChangeSet()

        level = DiagramGenerator._determine_escalation(trace, root, changes)
        assert level == EscalationLevel.ROOT

    def test_minority_components_no_escalation(self):
        trace = TraceResult(
            stop_reason=TraceStopReason.CLOSURE_REACHED,
            impacted_components=[
                ImpactedComponent(component_id="1"),
            ],
        )
        root = _make_root_analysis(5)  # 1/5 = 20% < 50%
        changes = ChangeSet()

        level = DiagramGenerator._determine_escalation(trace, root, changes)
        assert level == EscalationLevel.NONE

    def test_no_impact_no_escalation(self):
        trace = TraceResult(
            stop_reason=TraceStopReason.NO_MATERIAL_IMPACT,
            impacted_components=[],
        )
        root = _make_root_analysis(3)
        changes = ChangeSet()

        level = DiagramGenerator._determine_escalation(trace, root, changes)
        assert level == EscalationLevel.NONE

    def test_nested_component_id_extracts_root(self):
        """Component ID '1.2.3' should map to root component '1'."""
        trace = TraceResult(
            stop_reason=TraceStopReason.CLOSURE_REACHED,
            impacted_components=[
                ImpactedComponent(component_id="1.2.3"),
                ImpactedComponent(component_id="2.1"),
                ImpactedComponent(component_id="3.1.1"),
            ],
        )
        root = _make_root_analysis(3)  # 3/3 = 100% > 50%
        changes = ChangeSet()

        level = DiagramGenerator._determine_escalation(trace, root, changes)
        assert level == EscalationLevel.ROOT


# -------------------------------------------------------------------
# Parent sub-analysis lookup
# -------------------------------------------------------------------
class TestFindParentSubAnalysis:
    def test_direct_match(self):
        sub_analyses = {
            "1": AnalysisInsights(description="sub1", components=[], components_relations=[]),
        }
        assert DiagramGenerator._find_parent_sub_analysis("1", sub_analyses) == "1"

    def test_parent_match(self):
        sub_analyses = {
            "1": AnalysisInsights(
                description="sub1",
                components=[
                    Component(name="A", component_id="1.1", description="A", key_entities=[]),
                    Component(name="B", component_id="1.2", description="B", key_entities=[]),
                ],
                components_relations=[],
            ),
        }
        assert DiagramGenerator._find_parent_sub_analysis("1.2", sub_analyses) == "1"

    def test_search_all(self):
        sub_analyses = {
            "2": AnalysisInsights(
                description="sub2",
                components=[
                    Component(name="X", component_id="2.1", description="X", key_entities=[]),
                ],
                components_relations=[],
            ),
        }
        assert DiagramGenerator._find_parent_sub_analysis("2.1", sub_analyses) == "2"

    def test_not_found(self):
        sub_analyses = {
            "1": AnalysisInsights(description="sub1", components=[], components_relations=[]),
        }
        assert DiagramGenerator._find_parent_sub_analysis("99", sub_analyses) is None


def test_patch_impacted_components_parallel_deduplicates_parents(tmp_path):
    gen = _make_generator(tmp_path)
    root = _make_root_analysis(2)
    sub_analyses = {
        "1": AnalysisInsights(description="sub1", components=[], components_relations=[]),
        "2": AnalysisInsights(description="sub2", components=[], components_relations=[]),
    }
    trace = TraceResult(
        impacted_components=[
            ImpactedComponent(component_id="1.1", impacted_methods=["a"]),
            ImpactedComponent(component_id="1.2", impacted_methods=["b"]),
            ImpactedComponent(component_id="2.1", impacted_methods=["c"]),
        ]
    )
    parsing_llm = object()
    agent_llm = object()

    def fake_patch(sub, parent_id, impact, parsing_model):
        return AnalysisInsights(description=f"{parent_id}-updated", components=[], components_relations=[])

    with patch("diagram_analysis.analysis_patcher.patch_sub_analysis", side_effect=fake_patch) as mock_patch:
        gen._patch_impacted_components(root, sub_analyses, trace, agent_llm, parsing_llm)

    assert mock_patch.call_count == 2
    called_parents = {call.args[1] for call in mock_patch.call_args_list}
    assert called_parents == {"1", "2"}
    for call in mock_patch.call_args_list:
        assert call.args[3] is parsing_llm
    assert sub_analyses["1"].description == "1-updated"
    assert sub_analyses["2"].description == "2-updated"
