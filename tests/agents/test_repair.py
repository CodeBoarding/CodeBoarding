from pathlib import Path

from agents.agent_responses import (
    ClusterAnalysis,
    ClustersGroup,
    Component,
    ComponentArchitecture,
    SourceCodeReference,
)
from static_analyzer.clustering.models import ClusteringResults
from agents.repair import ComponentRepairContext, repair_component_group_names, repair_key_entities
from agents.validation import ValidationContext, validate_group_name_coverage, validate_key_entities
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.node import Node
from static_analyzer.reference_resolver import StaticReferenceResolver


def _component_repair_context() -> ComponentRepairContext:
    static_analysis = StaticAnalysisResults()
    static_analysis.add_references(
        Language.PYTHON,
        [
            Node(
                fully_qualified_name="pkg.service.run",
                node_type=NodeType.FUNCTION,
                file_path="/tmp/fake-repo/pkg/service.py",
                line_start=1,
                line_end=2,
            ),
            Node(
                fully_qualified_name="other.service.run",
                node_type=NodeType.FUNCTION,
                file_path="/tmp/fake-repo/other/service.py",
                line_start=1,
                line_end=2,
            ),
        ],
    )
    cluster_results = {
        "python": ClusterResult(
            clusters={1: {"pkg.service.run"}},
            file_to_clusters={},
            cluster_to_files={},
            strategy="test",
        )
    }
    cluster_analysis = ClusterAnalysis(
        cluster_groups=[ClustersGroup(name="API Handler", cluster_ids=[1], description="Handles requests.")]
    )
    return ComponentRepairContext(
        reference_resolver=StaticReferenceResolver(Path("/tmp/fake-repo"), static_analysis),
        clustering=ClusteringResults(
            cluster_results=cluster_results,
            cfg_graphs={},
            cluster_analysis=cluster_analysis,
            static_analysis=static_analysis,
        ),
    )


def test_component_repairs_canonicalize_names_and_keep_only_resolved_in_scope_entities() -> None:
    architecture = ComponentArchitecture(
        description="Test architecture.",
        components=[
            Component(
                name="API",
                description="Handles requests.",
                source_group_names=["API Handlr"],
                key_entities=[
                    SourceCodeReference(qualified_name="pkg/service.run"),
                    SourceCodeReference(qualified_name="other.service.run"),
                    SourceCodeReference(qualified_name="missing.service.run"),
                ],
            )
        ],
    )
    context = _component_repair_context()

    repair_component_group_names(architecture, context)
    repair_key_entities(architecture, context)

    assert architecture.components[0].source_group_names == ["API Handler"]
    assert [entity.qualified_name for entity in architecture.components[0].key_entities] == ["pkg.service.run"]


def test_component_validators_do_not_mutate_repairable_metadata() -> None:
    architecture = ComponentArchitecture(
        description="Test architecture.",
        components=[
            Component(
                name="API",
                description="Handles requests.",
                source_group_names=["API Handlr"],
                key_entities=[SourceCodeReference(qualified_name="pkg/service.run")],
            )
        ],
    )
    repair_context = _component_repair_context()
    validation_context = ValidationContext.for_shell(repair_context.clustering)
    before = architecture.model_dump()

    validate_group_name_coverage(architecture, validation_context)
    validate_key_entities(architecture, validation_context)

    assert architecture.model_dump() == before
