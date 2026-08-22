from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, ComponentArchitecture, SourceCodeReference
from agents.file_index_models import FileMethodGroup
from agents.repair import (
    ComponentRepairContext,
    ensure_unique_key_entities,
    repair_component_group_names,
    repair_key_entities,
)
from agents.validation import ValidationContext, validate_group_name_coverage, validate_key_entities
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.config import Language, NodeType
from static_analyzer.clustering import ClusterGroup, ClusterResult, ClusterScopeResult
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
    scope = ClusterScopeResult(
        scope_id="root",
        leaf_clusters_by_language=cluster_results,
        groups=[ClusterGroup(group_id="1", cluster_ids=[1])],
    )
    return ComponentRepairContext(
        reference_resolver=StaticReferenceResolver(Path("/tmp/fake-repo"), static_analysis),
        cluster_results=cluster_results,
        clustering=scope,
        group_ids={"Group 1": [1]},
    )


def test_component_repairs_canonicalize_names_and_keep_only_resolved_in_scope_entities() -> None:
    architecture = ComponentArchitecture(
        description="Test architecture.",
        components=[
            Component(
                name="API",
                description="Handles requests.",
                source_group_names=["Grop 1"],
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

    assert architecture.components[0].source_group_names == ["Group 1"]
    assert [entity.qualified_name for entity in architecture.components[0].key_entities] == ["pkg.service.run"]


def test_component_validators_do_not_mutate_repairable_metadata() -> None:
    architecture = ComponentArchitecture(
        description="Test architecture.",
        components=[
            Component(
                name="API",
                description="Handles requests.",
                source_group_names=["Grop 1"],
                key_entities=[SourceCodeReference(qualified_name="pkg/service.run")],
            )
        ],
    )
    repair_context = _component_repair_context()
    validation_context = ValidationContext(
        cluster_results=repair_context.cluster_results,
        static_analysis=repair_context.reference_resolver.static_analysis,
        clustering=repair_context.clustering,
    )
    before = architecture.model_dump()

    validate_group_name_coverage(architecture, validation_context)
    validate_key_entities(architecture, validation_context)

    assert architecture.model_dump() == before


def test_unique_key_entity_moves_to_the_component_that_owns_its_file() -> None:
    shared = SourceCodeReference(qualified_name="pkg.service.run", reference_file="pkg/service.py")
    first = Component(name="First", description="", key_entities=[shared.model_copy(deep=True)])
    second = Component(
        name="Second",
        description="",
        key_entities=[shared.model_copy(deep=True)],
        file_methods=[FileMethodGroup(file_path="pkg/service.py")],
    )
    analysis = AnalysisInsights(description="", components=[first, second], components_relations=[])

    ensure_unique_key_entities(analysis)

    assert first.key_entities == []
    assert [entity.qualified_name for entity in second.key_entities] == ["pkg.service.run"]
