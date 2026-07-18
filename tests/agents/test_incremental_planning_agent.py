from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    SourceCodeReference,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from diagram_analysis.exceptions import InvalidIncrementalPlanError
from agents.incremental_planning_agent import (
    IncrementalPlanningAgent,
    _component_ids_by_cluster_ref,
    format_structural_diff,
)
from agents.incremental_agent import _operation_source_cluster_ids
from agents.repair import (
    ScopeOperationRepairContext,
    repair_unambiguous_routing_and_optional_key_entity_metadata,
)
from agents.scope_ids import ROOT_SCOPE_ID
from agents.validation import ScopeOperationValidationContext, validate_scope_update_decision
from diagram_analysis.cluster_delta import (
    ClusterMemberDelta,
    ClusterRef,
    ClusterReshape,
    LanguageStructuralDiff,
    StructuralClusterDiff,
)
from repo_utils.change_detector import ChangeSet, FileChange
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult
from static_analyzer.constants import Language, NodeType
from static_analyzer.program_graph import ProgramGraph
from static_analyzer.reference_resolver import StaticReferenceResolver
from tests.program_graph_factory import make_symbol


def _reference_resolver(*qnames: str) -> StaticReferenceResolver:
    graph = ProgramGraph(
        language=str(Language.PYTHON),
        nodes={
            qname: make_symbol(
                qname,
                NodeType.FUNCTION,
                f"/tmp/fake-repo/reference-{index}.py",
                1,
                2,
                language=Language.PYTHON,
            )
            for index, qname in enumerate(qnames)
        },
    )
    static_analysis = StaticAnalysisResults()
    static_analysis.add_program_graph(Language.PYTHON, graph)
    return StaticReferenceResolver(Path("/tmp/fake-repo"), static_analysis)


def test_format_structural_diff_includes_modified_new_and_changed_members() -> None:
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                modified=[
                    ClusterMemberDelta(
                        old_cluster=ClusterRef(language="python", cluster_id=1),
                        new_cluster=ClusterRef(language="python", cluster_id=1),
                        added_methods={"pkg.new"},
                        dirty_members={"pkg.edited"},
                        dirty_files={"pkg/module.py"},
                    )
                ],
                new=[ClusterRef(language="python", cluster_id=2)],
            )
        }
    )

    rendered = format_structural_diff(structural)

    assert "### Modified clusters" in rendered
    assert "root:python:1 -> root:python:1" in rendered
    assert "pkg.new" in rendered
    # The planner sees the member-granular signal, not the file the members share.
    assert "changed_members=['pkg.edited']" in rendered
    assert "pkg/module.py" not in rendered
    assert "### New clusters" in rendered
    assert "root:python:2" in rendered


def test_format_structural_diff_sorts_cluster_refs_deterministically() -> None:
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                new=[
                    ClusterRef(language="python", cluster_id=10),
                    ClusterRef(language="python", cluster_id=2),
                ],
                removed=[
                    ClusterRef(language="python", cluster_id=9),
                    ClusterRef(language="python", cluster_id=1),
                ],
                reshaped=[
                    ClusterReshape(
                        old_clusters=[
                            ClusterRef(language="python", cluster_id=8),
                            ClusterRef(language="python", cluster_id=3),
                        ],
                        new_clusters=[
                            ClusterRef(language="python", cluster_id=7),
                            ClusterRef(language="python", cluster_id=4),
                        ],
                        overlap_counts={
                            (
                                ClusterRef(language="python", cluster_id=8),
                                ClusterRef(language="python", cluster_id=7),
                            ): 1,
                            (
                                ClusterRef(language="python", cluster_id=3),
                                ClusterRef(language="python", cluster_id=4),
                            ): 2,
                        },
                    )
                ],
            )
        }
    )

    rendered = format_structural_diff(structural)

    assert rendered.index("root:python:2") < rendered.index("root:python:10")
    assert rendered.index("root:python:1") < rendered.index("root:python:9")
    assert "old=[root:python:3, root:python:8] new=[root:python:4, root:python:7]" in rendered
    assert rendered.index("root:python:3->root:python:4:2") < rendered.index("root:python:8->root:python:7:1")


def test_validate_scope_update_decision_enforces_cluster_coverage_and_component_ids() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                component_id="1",
                rationale="Same responsibility.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=1)},
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert result.is_valid


def test_validate_scope_update_decision_accepts_root_scope() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                component_id="1",
                rationale="Root scope should match root structural refs.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=1)},
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert result.is_valid


def test_validate_scope_update_decision_normalizes_empty_root_scope() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=1)],
                component_id="1",
                rationale="An empty LLM scope id still represents root.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=1)},
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert result.is_valid


def test_validate_scope_update_decision_rejects_missing_duplicate_and_unknown_ids() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=1),
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=1),
                ],
                component_id="missing",
                rationale="Bad route.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={
            ClusterRef(language="python", cluster_id=1),
            ClusterRef(language="python", cluster_id=2),
        },
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    feedback = "\n".join(result.feedback_messages)
    assert "unknown component_id" in feedback
    assert "Missing cluster_refs" in feedback
    assert "Duplicate cluster_refs" in feedback


def test_validate_scope_update_decision_allows_update_for_new_cluster_when_llm_chooses_existing_owner() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=5)],
                component_id="1",
                rationale="The structural diff extends the existing services responsibility.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=5)},
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert result.is_valid


def test_validate_scope_update_decision_allows_create_when_llm_chooses_new_component() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=7)],
                name="Document Utilities",
                description="Shared document utilities.",
                key_entities=[SourceCodeReference(qualified_name="docs.render")],
                rationale="The structural diff introduces a distinct responsibility.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=7)},
        existing_component_ids={"3"},
    )

    result = validate_scope_update_decision(decision, context)

    assert result.is_valid


def test_repair_scope_update_decision_repairs_full_scope_planner_output() -> None:
    components = [
        Component(
            name="Orchestration & Dispatcher",
            description="Routes conversions.",
            key_entities=[],
            component_id="1",
            source_cluster_ids=["1", "2", "5"],
        )
    ]
    modified = [
        ClusterMemberDelta(
            old_cluster=ClusterRef(language="python", cluster_id=cluster_id),
            new_cluster=ClusterRef(language="python", cluster_id=cluster_id),
        )
        for cluster_id in (1, 2, 5)
    ]
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                modified=modified,
                new=[
                    ClusterRef(language="python", cluster_id=31),
                    ClusterRef(language="python", cluster_id=32),
                    ClusterRef(language="python", cluster_id=33),
                ],
            )
        }
    )
    update_refs = [1, 2, 5, 31, 32]
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=cluster_id)
                    for cluster_id in update_refs
                ],
                name="Orchestration & Dispatcher",
                description="Routes conversions and result handling.",
                component_id=None,
                rationale="The responsibility expanded.",
            ),
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=33)],
                name="OCR Extension",
                description="Adds OCR converters.",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="packages.markitdown_ocr.src.markitdown_ocr._plugin.register_converters"
                    ),
                    SourceCodeReference(qualified_name="hallucinated.missing"),
                ],
                rationale="A new extension package was added.",
            ),
        ]
    )
    expected_refs = {ClusterRef(language="python", cluster_id=cluster_id) for cluster_id in (*update_refs, 33)}
    canonical_qname = "packages.markitdown-ocr.src.markitdown_ocr._plugin.register_converters"
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(canonical_qname),
        allowed_key_entity_qnames={canonical_qname},
        component_ids_by_cluster_ref=_component_ids_by_cluster_ref("root", components, structural),
        component_ids_by_name={"orchestration & dispatcher": "1"},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=expected_refs,
        existing_component_ids={"1"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert result.is_valid
    assert decision.operations[0].component_id == "1"
    assert [entity.qualified_name for entity in decision.operations[1].key_entities] == [canonical_qname]


def test_repair_trims_redundant_owned_cluster_refs_the_planner_echoed() -> None:
    """Why: the planner echoes a component's full ``clusters=[...]`` display, but only changed clusters are actionable."""
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=cluster_id)
                    for cluster_id in (2, 11, 18)
                ],
                component_id="1",
                rationale="A file owned by this component changed.",
            )
        ]
    )
    actionable = {ClusterRef(language="python", cluster_id=2)}
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        scope_id="root",
        actionable_cluster_refs=actionable,
        owned_cluster_ids_by_component_id={"1": {"2", "11", "18"}},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=actionable,
        existing_component_ids={"1"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert result.is_valid
    assert [ref.cluster_id for ref in decision.operations[0].cluster_refs] == [2]


def test_repair_keeps_cross_component_owned_refs_so_theft_still_fails() -> None:
    """A ref owned by a *different* component is not trimmed, so cross-component theft still surfaces."""
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=2),
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=7),
                ],
                component_id="1",
                rationale="Claims a cluster still owned by another component.",
            )
        ]
    )
    actionable = {ClusterRef(language="python", cluster_id=2)}
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        scope_id="root",
        actionable_cluster_refs=actionable,
        owned_cluster_ids_by_component_id={"1": {"2"}, "2": {"7"}},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=actionable,
        existing_component_ids={"1", "2"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert not result.is_valid
    assert "Unexpected cluster_refs: root:python:7" in "\n".join(result.feedback_messages)


def test_repair_leaves_no_change_update_for_validator_to_reject() -> None:
    """An update whose refs are all owned-unchanged (no actionable) must stay invalid.

    Why: trimming it to empty would let it silently apply name/description to an untouched component.
    """
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=2)],
                component_id="1",
                rationale="Real change.",
            ),
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=cluster_id)
                    for cluster_id in (13, 14)
                ],
                component_id="5",
                name="Hijacked",
                description="Update of an untouched component that changed nothing.",
                rationale="Nothing here actually changed.",
            ),
        ]
    )
    actionable = {ClusterRef(language="python", cluster_id=2)}
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        scope_id="root",
        actionable_cluster_refs=actionable,
        owned_cluster_ids_by_component_id={"1": {"2"}, "5": {"13", "14"}},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=actionable,
        existing_component_ids={"1", "5"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert not result.is_valid
    assert [ref.cluster_id for ref in decision.operations[1].cluster_refs] == [13, 14]
    assert "Unexpected cluster_refs" in "\n".join(result.feedback_messages)


def test_repair_leaves_no_change_delete_for_validator_to_reject() -> None:
    """A delete whose refs are all owned-unchanged (no actionable) must stay invalid.

    Why: trimming it to empty would let it silently remove an untouched component.
    """
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=2)],
                component_id="1",
                rationale="Real change.",
            ),
            ScopeOperation(
                action=ScopeOperationAction.DELETE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=cluster_id)
                    for cluster_id in (13, 14)
                ],
                component_id="5",
                rationale="Nothing here actually changed.",
            ),
        ]
    )
    actionable = {ClusterRef(language="python", cluster_id=2)}
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        scope_id="root",
        actionable_cluster_refs=actionable,
        owned_cluster_ids_by_component_id={"1": {"2"}, "5": {"13", "14"}},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=actionable,
        existing_component_ids={"1", "5"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert not result.is_valid
    assert [ref.cluster_id for ref in decision.operations[1].cluster_refs] == [13, 14]
    assert "Unexpected cluster_refs" in "\n".join(result.feedback_messages)


def test_repair_keeps_refs_when_update_moves_another_components_cluster() -> None:
    """An update that lists a changed cluster owned by another component keeps its own owned refs.

    Why: dropping them would let the validator accept moving that cluster off its real owner.
    """
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=3),  # changed, owned by comp 2
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=9),  # owned by comp 1, unchanged
                ],
                component_id="1",
                rationale="Claims a changed cluster that still belongs to another component.",
            )
        ]
    )
    actionable = {ClusterRef(language="python", cluster_id=3)}
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        scope_id="root",
        actionable_cluster_refs=actionable,
        owned_cluster_ids_by_component_id={"1": {"9"}, "2": {"3"}},
        component_ids_by_cluster_ref={ClusterRef(language="python", cluster_id=3): "2"},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=actionable,
        existing_component_ids={"1", "2"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert not result.is_valid
    assert [ref.cluster_id for ref in decision.operations[0].cluster_refs] == [3, 9]
    assert "Unexpected cluster_refs: root:python:9" in "\n".join(result.feedback_messages)


def test_repair_keeps_delete_refs_when_component_still_owns_clusters() -> None:
    """A delete of a component that still owns clusters keeps its refs so the validator rejects it.

    Why: dropping them would let a delete through even though the component still owns clusters.
    """
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.DELETE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=cluster_id)
                    for cluster_id in (2, 13, 14)
                ],
                component_id="5",
                rationale="Deletes a component that still owns live clusters.",
            )
        ]
    )
    actionable = {ClusterRef(language="python", cluster_id=2)}
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        scope_id="root",
        actionable_cluster_refs=actionable,
        owned_cluster_ids_by_component_id={"5": {"2", "13", "14"}},
        component_ids_by_cluster_ref={ClusterRef(language="python", cluster_id=2): "5"},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=actionable,
        existing_component_ids={"5"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert not result.is_valid
    assert [ref.cluster_id for ref in decision.operations[0].cluster_refs] == [2, 13, 14]
    assert "Unexpected cluster_refs" in "\n".join(result.feedback_messages)


def test_validate_rejects_noop_claiming_another_components_cluster() -> None:
    """A noop that claims a changed cluster owned by another component is rejected (would duplicate ownership).

    Why: the noop branch of update_scope unions the ref into its component without removing it from the
    real owner, so the cluster would end up owned by two components.
    """
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.NOOP,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=3)],  # owned by comp 1
                component_id="2",
                rationale="Preserves comp 2 but grabs comp 1's changed cluster.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=3)},
        existing_component_ids={"1", "2"},
        component_ids_by_cluster_ref={ClusterRef(language="python", cluster_id=3): "1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    assert "claims clusters owned by another component" in "\n".join(result.feedback_messages)


def test_validate_rejects_delete_that_still_owns_an_actionable_cluster() -> None:
    """A delete listing an actionable cluster is rejected: a delete discards its clusters, so it cannot cover one.

    Why: otherwise a changed cluster "covered" only by a delete is thrown away on apply and orphaned.
    """
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.DELETE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=5)],
                component_id="2",
                rationale="Deletes a component that still owns a changed cluster.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=5)},
        existing_component_ids={"2"},
        component_ids_by_cluster_ref={ClusterRef(language="python", cluster_id=5): "2"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    assert "Missing cluster_refs: root:python:5" in "\n".join(result.feedback_messages)


def test_repair_trims_noop_that_only_preserves_its_own_clusters() -> None:
    """A noop listing only its own unchanged clusters is trimmed to empty and stays valid."""
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=3)],
                component_id="1",
                rationale="Real change.",
            ),
            ScopeOperation(
                action=ScopeOperationAction.NOOP,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=cluster_id) for cluster_id in (8, 9)
                ],
                component_id="2",
                rationale="Preserve comp 2 unchanged.",
            ),
        ]
    )
    actionable = {ClusterRef(language="python", cluster_id=3)}
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        scope_id="root",
        actionable_cluster_refs=actionable,
        owned_cluster_ids_by_component_id={"1": {"3"}, "2": {"8", "9"}},
        component_ids_by_cluster_ref={ClusterRef(language="python", cluster_id=3): "1"},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs=actionable,
        existing_component_ids={"1", "2"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert result.is_valid
    assert decision.operations[1].cluster_refs == []


def test_operation_source_cluster_ids_treats_blank_scope_as_root() -> None:
    """A ref with a blank scope_id is counted for the root scope, matching the validator's normalization."""
    operation = ScopeOperation(
        action=ScopeOperationAction.CREATE_COMPONENT,
        cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=5)],
        name="New Feature",
        description="Owns a brand-new cluster.",
        rationale="A new cluster appeared.",
    )

    assert _operation_source_cluster_ids(ROOT_SCOPE_ID, operation) == ["5"]


def test_validate_scope_update_decision_keeps_ownerless_update_invalid() -> None:
    ref = ClusterRef(language="python", cluster_id=7)
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=7)],
                name="New Responsibility",
                description="Owns a new cluster.",
                component_id=None,
                rationale="The parser emitted update without an existing target.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ref},
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    assert decision.operations[0].action == ScopeOperationAction.UPDATE_COMPONENT
    assert "component_id=None" in "\n".join(result.feedback_messages)


def test_repair_scope_update_decision_routes_missing_component_id_from_unique_name() -> None:
    ref = ClusterRef(language="python", cluster_id=7)
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=7)],
                name="  API   Gateway ",
                component_id=None,
                rationale="The new cluster belongs to the existing API gateway.",
            )
        ]
    )
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        component_ids_by_name={"api gateway": "1"},
    )
    validation_context = ScopeOperationValidationContext(expected_cluster_refs={ref}, existing_component_ids={"1"})

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert result.is_valid
    assert decision.operations[0].component_id == "1"


def test_validate_scope_update_decision_keeps_ambiguous_missing_owner_invalid() -> None:
    first = ClusterRef(language="python", cluster_id=1)
    second = ClusterRef(language="python", cluster_id=2)
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=1),
                    ScopedClusterRef(scope_id="root", language="python", cluster_id=2),
                ],
                name="Merged Responsibility",
                description="Would span two existing owners.",
                component_id=None,
                rationale="Ambiguous merge.",
            )
        ]
    )
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        component_ids_by_cluster_ref={first: "1", second: "2"},
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs={first, second},
        existing_component_ids={"1", "2"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert not result.is_valid
    assert decision.operations[0].action == ScopeOperationAction.UPDATE_COMPONENT
    assert "component_id=None" in "\n".join(result.feedback_messages)


def test_validate_scope_update_decision_rejects_metadata_changes_on_noop() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.NOOP,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                component_id="1",
                description="This should be an update instead.",
                rationale="No boundary change.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=1)},
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    assert "noop operations must preserve" in result.feedback_messages[0]


def test_repair_scope_update_decision_clears_name_used_to_route_noop() -> None:
    ref = ClusterRef(language="python", cluster_id=1)
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.NOOP,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                name="API Gateway",
                rationale="The component boundary is unchanged.",
            )
        ]
    )
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
        component_ids_by_name={"api gateway": "1"},
    )
    validation_context = ScopeOperationValidationContext(expected_cluster_refs={ref}, existing_component_ids={"1"})

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert result.is_valid
    assert decision.operations[0].component_id == "1"
    assert decision.operations[0].name is None


def test_repair_scope_update_decision_clears_noop_metadata_with_existing_id() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.NOOP,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                component_id="1",
                name="API Gateway",
                description="This should be preserved from the existing component.",
                key_entities=[SourceCodeReference(qualified_name="api.handle")],
                rationale="The component boundary is unchanged.",
            )
        ]
    )
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(),
        allowed_key_entity_qnames=set(),
    )
    validation_context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=1)},
        existing_component_ids={"1"},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)
    result = validate_scope_update_decision(decision, validation_context)

    assert result.is_valid
    assert decision.operations[0].component_id == "1"
    assert decision.operations[0].name is None
    assert decision.operations[0].description is None
    assert decision.operations[0].key_entities == []


def test_repair_scope_update_decision_drops_key_entities_outside_scope() -> None:
    scoped_qname = "nested.worker.run"
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="1", language="python", cluster_id=2)],
                name="Worker",
                description="Runs nested jobs.",
                key_entities=[
                    SourceCodeReference(qualified_name=scoped_qname),
                    SourceCodeReference(qualified_name="sibling.service.run"),
                ],
                rationale="A new nested responsibility was added.",
            )
        ]
    )
    repair_context = ScopeOperationRepairContext(
        reference_resolver=_reference_resolver(scoped_qname, "sibling.service.run"),
        allowed_key_entity_qnames={scoped_qname},
    )

    repair_unambiguous_routing_and_optional_key_entity_metadata(decision, repair_context)

    assert [entity.qualified_name for entity in decision.operations[0].key_entities] == [scoped_qname]


def test_incremental_planning_agent_uses_narrow_diff_aware_toolkit() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    changes = ChangeSet(
        files=[FileChange(status_code="M", file_path="pkg/module.py")],
    )

    with (
        patch("agents.agent.create_agent") as mock_base_create,
        patch("agents.incremental_planning_agent.create_agent") as mock_scoped_create,
    ):
        mock_base_create.return_value = MagicMock()
        mock_scoped_create.return_value = MagicMock()
        agent = IncrementalPlanningAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
            changes=changes,
        )

    tools = mock_scoped_create.call_args.kwargs["tools"]
    assert [type(tool).__name__ for tool in tools] == ["CodeReferenceReader", "ListGitChangesTool"]
    assert agent.toolkit.context.changes is changes


def test_decide_scope_update_passes_structural_diff_to_validator() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    scope = AnalysisInsights(
        description="root",
        components=[
            Component(
                name="API",
                description="Handles requests",
                key_entities=[],
                component_id="1",
                source_cluster_ids=["10", "2", "1.3"],
            )
        ],
        components_relations=[],
    )
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                modified=[
                    ClusterMemberDelta(
                        old_cluster=ClusterRef(language="python", cluster_id=1),
                        new_cluster=ClusterRef(language="python", cluster_id=1),
                        added_methods={"api.new"},
                    )
                ],
            )
        }
    )
    expected = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                component_id="1",
                rationale="API gained a method.",
            )
        ]
    )

    with (
        patch("agents.agent.create_agent", return_value=MagicMock()),
        patch("agents.incremental_planning_agent.create_agent", return_value=MagicMock()),
    ):
        agent = IncrementalPlanningAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )
    agent._invoke_repair_validate = MagicMock(return_value=expected)

    cluster_results = {"python": ClusterResult(clusters={1: {"api.new"}})}
    result = agent.decide_scope_update("root", scope, structural, cluster_results)

    assert result is expected
    prompt = agent._invoke_repair_validate.call_args.args[0]
    repair_context = agent._invoke_repair_validate.call_args.kwargs["repair_context"]
    validation_context = agent._invoke_repair_validate.call_args.kwargs["validation_context"]
    assert "Existing components in this scope" in prompt
    assert '1 "API" clusters=[2, 10, 1.3]' in prompt
    assert "api.new" in prompt
    assert "Do not define component relations" in prompt
    assert repair_context.component_ids_by_name == {"api": "1"}
    assert repair_context.allowed_key_entity_qnames == {"api.new"}
    assert validation_context.expected_cluster_refs == {ClusterRef(language="python", cluster_id=1)}
    assert validation_context.existing_component_ids == {"1"}


def test_decide_scope_update_runs_repair_before_final_validation() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    scope = AnalysisInsights(
        description="root",
        components=[
            Component(
                name="API",
                description="Handles requests",
                key_entities=[],
                component_id="1",
                source_cluster_ids=["1"],
            )
        ],
        components_relations=[],
    )
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                modified=[
                    ClusterMemberDelta(
                        old_cluster=ClusterRef(language="python", cluster_id=1),
                        new_cluster=ClusterRef(language="python", cluster_id=1),
                    )
                ],
            )
        }
    )
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.NOOP,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                component_id="1",
                name="API",
                description="Handles requests",
                key_entities=[SourceCodeReference(qualified_name="api.handle")],
                rationale="The component boundary is unchanged.",
            )
        ]
    )

    with (
        patch("agents.agent.create_agent", return_value=MagicMock()),
        patch("agents.incremental_planning_agent.create_agent", return_value=MagicMock()),
    ):
        agent = IncrementalPlanningAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )
    agent._parse_invoke = MagicMock(return_value=decision)

    result = agent.decide_scope_update(
        "root",
        scope,
        structural,
        {"python": ClusterResult(clusters={1: {"api.handle"}})},
    )

    assert result.operations[0].name is None
    assert result.operations[0].description is None
    assert result.operations[0].key_entities == []


def test_decide_scope_update_tracks_invalid_decision_after_retries() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    scope = AnalysisInsights(
        description="root",
        components=[Component(name="API", description="Handles requests", key_entities=[], component_id="1")],
        components_relations=[],
    )
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                new=[ClusterRef(language="python", cluster_id=2)],
            )
        }
    )
    invalid = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.NOOP,
                cluster_refs=[],
                component_id="1",
                rationale="Missed the new cluster.",
            )
        ]
    )

    with (
        patch("agents.agent.create_agent", return_value=MagicMock()),
        patch("agents.incremental_planning_agent.create_agent", return_value=MagicMock()),
    ):
        agent = IncrementalPlanningAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )
    agent._invoke_repair_validate = MagicMock(return_value=invalid)

    with patch("agents.incremental_planning_agent.telemetry") as mock_telemetry:
        with pytest.raises(InvalidIncrementalPlanError, match="Missing cluster_refs"):
            agent.decide_scope_update("root", scope, structural, {})

    mock_telemetry.capture_exception.assert_called_once()
    exc = mock_telemetry.capture_exception.call_args.args[0]
    properties = mock_telemetry.capture_exception.call_args.kwargs["properties"]
    assert isinstance(exc, RuntimeError)
    assert properties["error_type"] == "incremental_planning_invalid_decision"
    assert properties["scope_id"] == "root"
    assert properties["issue_count"] == 1
    assert "Missing cluster_refs" in properties["issues"][0]
    mock_telemetry.flush.assert_called_once()


def test_scope_operation_rejects_regenerate_scope() -> None:
    with pytest.raises(ValidationError):
        ScopeOperation.model_validate(
            {
                "action": "regenerate_scope",
                "cluster_refs": [],
                "rationale": "Reparenting is not a valid incremental operation.",
            }
        )


def _ref(cid: int) -> ClusterRef:
    return ClusterRef(language="python", cluster_id=cid)


def _components_owning(mapping: dict[str, list[str]]) -> list[Component]:
    return [
        Component(name=f"C{cid}", description="", component_id=cid, source_cluster_ids=list(clusters), key_entities=[])
        for cid, clusters in mapping.items()
    ]


def test_majority_prior_owner_keeps_a_reshaped_cluster() -> None:
    # New cluster 7 draws 3 retained members from component A's old cluster and 1
    # from component B's. A holds the majority, so 7 stays with A rather than going
    # to whichever component the planner picks.
    components = _components_owning({"1": ["1"], "2": ["2"]})
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                reshaped=[
                    ClusterReshape(
                        old_clusters=[_ref(1), _ref(2)],
                        new_clusters=[_ref(7)],
                        overlap_counts={(_ref(1), _ref(7)): 3, (_ref(2), _ref(7)): 1},
                    )
                ],
            )
        }
    )
    resolved = _component_ids_by_cluster_ref(ROOT_SCOPE_ID, components, structural)
    assert resolved[_ref(7)] == "1"


def test_a_genuine_merge_with_no_majority_is_left_to_the_planner() -> None:
    # New cluster 7 is an even split between two prior owners: no majority, so the
    # planner decides rather than a coin-flip forcing one.
    components = _components_owning({"1": ["1"], "2": ["2"]})
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                reshaped=[
                    ClusterReshape(
                        old_clusters=[_ref(1), _ref(2)],
                        new_clusters=[_ref(7)],
                        overlap_counts={(_ref(1), _ref(7)): 2, (_ref(2), _ref(7)): 2},
                    )
                ],
            )
        }
    )
    resolved = _component_ids_by_cluster_ref(ROOT_SCOPE_ID, components, structural)
    assert _ref(7) not in resolved


def test_a_grown_but_stable_cluster_keeps_its_owner() -> None:
    # A modified cluster that only gained members keeps its owner: every retained
    # member is still that owner's.
    components = _components_owning({"1": ["5"]})
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                modified=[
                    ClusterMemberDelta(
                        old_cluster=_ref(5),
                        new_cluster=_ref(5),
                        unchanged_methods={"pkg.a", "pkg.b", "pkg.c"},
                        added_methods={"pkg.new"},
                    )
                ],
            )
        }
    )
    resolved = _component_ids_by_cluster_ref(ROOT_SCOPE_ID, components, structural)
    assert resolved[_ref(5)] == "1"
