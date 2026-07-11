from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    SourceCodeReference,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from diagram_analysis.exceptions import InvalidIncrementalPlanError, IncrementalScopeRegenerationRequiredError
from agents.incremental_planning_agent import (
    ScopeOperationValidationContext,
    IncrementalPlanningAgent,
    format_structural_diff,
    validate_scope_update_decision,
)
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
        known_qnames={"docs.render"},
    )

    result = validate_scope_update_decision(decision, context)

    assert result.is_valid


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
    agent._validation_invoke = MagicMock(return_value=expected)

    result = agent.decide_scope_update("root", scope, structural)

    assert result is expected
    prompt = agent._validation_invoke.call_args.args[0]
    context = agent._validation_invoke.call_args.kwargs["context"]
    assert "Existing components in this scope" in prompt
    assert '1 "API" clusters=[2, 10, 1.3]' in prompt
    assert "api.new" in prompt
    assert "Do not define component relations" in prompt
    assert context.expected_cluster_refs == {ClusterRef(language="python", cluster_id=1)}
    assert context.existing_component_ids == {"1"}


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
    agent._validation_invoke = MagicMock(return_value=invalid)

    with patch("agents.incremental_planning_agent.telemetry") as mock_telemetry:
        with pytest.raises(InvalidIncrementalPlanError, match="Missing cluster_refs"):
            agent.decide_scope_update("root", scope, structural)

    mock_telemetry.capture_exception.assert_called_once()
    exc = mock_telemetry.capture_exception.call_args.args[0]
    properties = mock_telemetry.capture_exception.call_args.kwargs["properties"]
    assert isinstance(exc, RuntimeError)
    assert properties["error_type"] == "incremental_planning_invalid_decision"
    assert properties["scope_id"] == "root"
    assert properties["issue_count"] == 1
    assert "Missing cluster_refs" in properties["issues"][0]
    mock_telemetry.flush.assert_called_once()


def test_decide_scope_update_fails_when_scope_regeneration_is_required() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    static_analysis.get_languages.return_value = []
    scope = AnalysisInsights(description="root", components=[], components_relations=[])
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.REGENERATE_SCOPE,
                cluster_refs=[],
                rationale="The component must move to another parent.",
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
    agent._validation_invoke = MagicMock(return_value=decision)

    with pytest.raises(IncrementalScopeRegenerationRequiredError, match="Run a full analysis explicitly"):
        agent.decide_scope_update("root", scope, StructuralClusterDiff())
