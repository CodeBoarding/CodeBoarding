from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.incremental_planning_agent import (
    IncrementalPlanningAgent,
    format_structural_diff,
)
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


def test_format_structural_diff_includes_modified_new_and_dirty_files() -> None:
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                modified=[
                    ClusterMemberDelta(
                        old_cluster=ClusterRef(language="python", cluster_id=1),
                        new_cluster=ClusterRef(language="python", cluster_id=1),
                        added_methods={"pkg.new"},
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
    assert "pkg/module.py" in rendered
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
    assert "must set component_id to one exact existing id" in feedback
    assert "Missing cluster_refs" in feedback
    assert "Duplicate cluster_refs" in feedback


def test_validate_scope_update_decision_explains_ids_misplaced_in_name() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="2", language="python", cluster_id=1)],
                name='2.1 "Strategy Registry & Built-in Factory"',
                rationale="The id was incorrectly emitted as part of the name field.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=1, scope_id="2")},
        existing_component_ids={"2.1", "2.2"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    feedback = "\n".join(result.feedback_messages)
    assert "component_id=None" in feedback
    assert "name='2.1" in feedback
    assert "Move '2.1' from name into component_id" in feedback


def test_validate_scope_update_decision_rejects_claiming_cluster_owned_by_sibling() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="2", language="python", cluster_id=24)],
                component_id="2.3",
                rationale="Incorrectly moves an already-owned cluster to another component.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=24, scope_id="2")},
        existing_component_ids={"2.1", "2.3"},
        existing_cluster_owners={("2", 24): "2.1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    feedback = "\n".join(result.feedback_messages)
    assert "already owned by existing component_id='2.1'" in feedback
    assert "Preserve existing ownership" in feedback


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


def test_validate_scope_update_decision_rejects_create_without_cluster_refs() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[],
                name="Ghost Component",
                description="Should not be materialized without source clusters.",
                rationale="LLM invented a component without assigning changed clusters.",
            )
        ]
    )
    context = ScopeOperationValidationContext(expected_cluster_refs=set(), existing_component_ids={"1"})

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    feedback = "\n".join(result.feedback_messages)
    assert "has no cluster_refs" in feedback
    assert "Move one or more changed clusters" in feedback


def test_validate_scope_update_decision_rejects_create_from_baseline_only_refs() -> None:
    ref = ClusterRef(language="python", cluster_id=7)
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=7)],
                name="Moved Existing Code",
                description="Should not become a new component when only scope ownership changed.",
                rationale="The cluster is new to this scope but not new to the codebase.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ref},
        code_added_cluster_refs=set(),
        enforce_code_added_creates=True,
        existing_component_ids={"1"},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    assert "already present in the baseline" in "\n".join(result.feedback_messages)


def test_incremental_planning_agent_uses_narrow_diff_aware_toolkit() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    changes = ChangeSet(
        base_ref="base",
        target_ref="head",
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
    assert [type(tool).__name__ for tool in tools] == ["CodeReferenceReader", "ListGitChangesTool", "ReadGitDiffTool"]
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
    agent._validation_invoke = MagicMock(return_value=expected)

    result = agent.decide_scope_update("root", scope, structural)

    assert result is expected
    prompt = agent._validation_invoke.call_args.args[0]
    context = agent._validation_invoke.call_args.kwargs["context"]
    assert "Existing components in this scope" in prompt
    assert '1 "API" clusters=[2, 10, 1.3]' in prompt
    assert "api.new" in prompt
    assert context.expected_cluster_refs == {ClusterRef(language="python", cluster_id=1)}
    assert context.existing_component_ids == {"1"}
    assert context.existing_cluster_owners == {("root", 10): "1", ("root", 2): "1"}


def test_decide_scope_update_filters_invalid_decision_after_retries() -> None:
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
    agent._validation_invoke = MagicMock(return_value=invalid)

    with patch("agents.incremental_planning_agent.telemetry") as mock_telemetry:
        result = agent.decide_scope_update("root", scope, structural)

    assert result.operations == []
    mock_telemetry.capture_exception.assert_called_once()
    exc = mock_telemetry.capture_exception.call_args.args[0]
    properties = mock_telemetry.capture_exception.call_args.kwargs["properties"]
    assert isinstance(exc, RuntimeError)
    assert properties["error_type"] == "incremental_planning_invalid_decision"
    assert properties["scope_id"] == "root"
    assert properties["issue_count"] == 1
    assert "Missing cluster_refs" in properties["issues"][0]
    mock_telemetry.flush.assert_called_once()


def test_decide_scope_update_keeps_valid_operations_when_filtering_invalid_ones() -> None:
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
                new=[
                    ClusterRef(language="python", cluster_id=2),
                    ClusterRef(language="python", cluster_id=3),
                ],
            )
        }
    )
    valid_create = ScopeOperation(
        action=ScopeOperationAction.CREATE_COMPONENT,
        cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=2)],
        name="Worker",
        description="Processes queued jobs.",
        rationale="Cluster 2 is a new responsibility.",
    )
    invalid_create = ScopeOperation(
        action=ScopeOperationAction.CREATE_COMPONENT,
        cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=99)],
        name="Invented",
        description="Claims a cluster outside the scoped diff.",
        rationale="Invalid ref should be dropped instead of applied.",
    )
    invalid = ScopeUpdateDecision(operations=[valid_create, invalid_create])

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
    agent._validation_invoke = MagicMock(return_value=invalid)

    with patch("agents.incremental_planning_agent.telemetry"):
        result = agent.decide_scope_update("root", scope, structural)

    assert result.operations == [valid_create]


def test_decide_scope_update_prompt_includes_routing_facts() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    scope = AnalysisInsights(
        description="root",
        components=[
            Component(
                name="API",
                description="Handles requests",
                key_entities=[],
                component_id="1",
                source_cluster_ids=["4"],
            )
        ],
        components_relations=[],
    )
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                new=[ClusterRef(language="python", cluster_id=5)],
            )
        }
    )
    expected = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=5)],
                name="Worker",
                description="Processes jobs.",
                rationale="New cluster.",
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
    agent._validation_invoke = MagicMock(return_value=expected)

    agent.decide_scope_update("root", scope, structural)

    prompt = agent._validation_invoke.call_args.args[0]
    assert "Routing facts" in prompt
    assert "Actionable cluster_refs" in prompt
    assert "root:python:5" in prompt
    assert "root:python:4 -> 1" in prompt


def test_format_structural_diff_distinguishes_code_added_from_scope_added() -> None:
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                new_details=[
                    ClusterMemberDelta(
                        old_cluster=ClusterRef(language="python", cluster_id=1),
                        new_cluster=ClusterRef(language="python", cluster_id=1),
                        added_methods={"pkg.existing", "pkg.new"},
                    )
                ],
                new=[ClusterRef(language="python", cluster_id=1)],
            )
        }
    )

    rendered = format_structural_diff(structural, base_method_qnames={"pkg.existing"})

    assert "code_added=['pkg.new']" in rendered
    assert "existing_in_base=['pkg.existing']" in rendered
