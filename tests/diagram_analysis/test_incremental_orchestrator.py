"""Tests for the incremental analysis orchestrator (DiagramGenerator methods)."""

import pytest

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.incremental_models import (
    EscalationLevel,
    ImpactedComponent,
    TraceResult,
    TraceStopReason,
)
from diagram_analysis.diagram_generator import DiagramGenerator
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

    with pytest.raises(RuntimeError, match="requires an existing baseline"):
        gen.generate_analysis_incremental()


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
