from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    MethodEntry,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.scoped_incremental_agent import (
    ScopeOperationValidationContext,
    ScopedIncrementalAgent,
    format_structural_diff,
    validate_scope_update_decision,
)
from diagram_analysis.cluster_delta import (
    ClusterMemberDelta,
    ClusterRef,
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


def test_validate_scope_update_decision_enforces_cluster_coverage_and_component_ids() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.ASSIGN_TO_EXISTING,
                cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=1)],
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


def test_validate_scope_update_decision_accepts_root_scope_alias() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                component_id="1",
                rationale="Root alias should match the empty root scope.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=1, scope_id="")},
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
                    ScopedClusterRef(scope_id="", language="python", cluster_id=1),
                    ScopedClusterRef(scope_id="", language="python", cluster_id=1),
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


def test_validate_scope_update_decision_requires_create_for_new_package_roots() -> None:
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=5)],
                component_id="1",
                rationale="Absorbed into existing services.",
            )
        ]
    )
    context = ScopeOperationValidationContext(
        expected_cluster_refs={ClusterRef(language="python", cluster_id=5)},
        existing_component_ids={"1"},
        required_create_cluster_refs={ClusterRef(language="python", cluster_id=5)},
    )

    result = validate_scope_update_decision(decision, context)

    assert not result.is_valid
    assert "must create components" in "\n".join(result.feedback_messages)


def test_new_package_root_is_marked_required_create_in_prompt_and_context() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    scope = AnalysisInsights(
        description="root",
        components=[
            Component(
                name="Core",
                description="Core package",
                key_entities=[],
                component_id="1",
                file_methods=[
                    FileMethodGroup(
                        file_path="packages/markitdown/src/markitdown/_markitdown.py",
                        methods=[
                            MethodEntry(
                                qualified_name="packages.markitdown.src.markitdown.MarkItDown",
                                start_line=1,
                                end_line=2,
                                node_type="CLASS",
                            )
                        ],
                    )
                ],
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
                        old_cluster=ClusterRef(language="python", cluster_id=5),
                        new_cluster=ClusterRef(language="python", cluster_id=5),
                        added_methods={
                            "packages.markitdown-mcp.src.markitdown_mcp.__main__.create_starlette_app",
                            "packages.markitdown-ocr.src.markitdown_ocr._ocr_service.LLMVisionOCRService",
                        },
                    )
                ],
            )
        }
    )

    with (
        patch("agents.agent.create_agent", return_value=MagicMock()),
        patch("agents.scoped_incremental_agent.create_agent", return_value=MagicMock()),
    ):
        agent = ScopedIncrementalAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )
    agent._validation_invoke = MagicMock(return_value=ScopeUpdateDecision(operations=[]))

    agent.decide_scope_update("", scope, structural)

    prompt = agent._validation_invoke.call_args.args[0]
    context = agent._validation_invoke.call_args.kwargs["context"]
    assert "New package-root clusters that must create components" in prompt
    assert "root:python:5" in prompt
    assert context.required_create_cluster_refs == {ClusterRef(language="python", cluster_id=5)}


def test_new_cluster_package_root_is_marked_required_create_in_prompt_and_context() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    scope = AnalysisInsights(
        description="root",
        components=[
            Component(
                name="Core",
                description="Core package",
                key_entities=[],
                component_id="1",
                file_methods=[
                    FileMethodGroup(
                        file_path="packages/markitdown/src/markitdown/_markitdown.py",
                        methods=[],
                    )
                ],
            )
        ],
        components_relations=[],
    )
    structural = StructuralClusterDiff(
        by_language={
            "python": LanguageStructuralDiff(
                language="python",
                new=[ClusterRef(language="python", cluster_id=12)],
                new_details=[
                    ClusterMemberDelta(
                        old_cluster=ClusterRef(language="python", cluster_id=12),
                        new_cluster=ClusterRef(language="python", cluster_id=12),
                        added_methods={"packages.markitdown-mcp.src.markitdown_mcp.__main__.main"},
                        dirty_files={"packages/markitdown-mcp/src/markitdown_mcp/__main__.py"},
                    )
                ],
            )
        }
    )

    with (
        patch("agents.agent.create_agent", return_value=MagicMock()),
        patch("agents.scoped_incremental_agent.create_agent", return_value=MagicMock()),
    ):
        agent = ScopedIncrementalAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )
    agent._validation_invoke = MagicMock(return_value=ScopeUpdateDecision(operations=[]))

    agent.decide_scope_update("", scope, structural)

    prompt = agent._validation_invoke.call_args.args[0]
    context = agent._validation_invoke.call_args.kwargs["context"]
    assert "packages/markitdown-mcp/src/markitdown_mcp/__main__.py" in prompt
    assert "root:python:12" in prompt
    assert context.required_create_cluster_refs == {ClusterRef(language="python", cluster_id=12)}


def test_scoped_incremental_agent_uses_narrow_diff_aware_toolkit() -> None:
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    changes = ChangeSet(
        base_ref="base",
        target_ref="head",
        files=[FileChange(status_code="M", file_path="pkg/module.py")],
    )

    with (
        patch("agents.agent.create_agent") as mock_base_create,
        patch("agents.scoped_incremental_agent.create_agent") as mock_scoped_create,
    ):
        mock_base_create.return_value = MagicMock()
        mock_scoped_create.return_value = MagicMock()
        agent = ScopedIncrementalAgent(
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
    assert agent.toolkit.context.diff_base_ref == "base"
    assert agent.toolkit.context.diff_target_ref == "head"


def test_decide_scope_update_passes_structural_diff_to_validator() -> None:
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
                cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=1)],
                component_id="1",
                rationale="API gained a method.",
            )
        ]
    )

    with (
        patch("agents.agent.create_agent", return_value=MagicMock()),
        patch("agents.scoped_incremental_agent.create_agent", return_value=MagicMock()),
    ):
        agent = ScopedIncrementalAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )
    agent._validation_invoke = MagicMock(return_value=expected)

    result = agent.decide_scope_update("", scope, structural)

    assert result is expected
    prompt = agent._validation_invoke.call_args.args[0]
    context = agent._validation_invoke.call_args.kwargs["context"]
    assert "Existing components in this scope" in prompt
    assert '1 "API"' in prompt
    assert "api.new" in prompt
    assert context.expected_cluster_refs == {ClusterRef(language="python", cluster_id=1)}
    assert context.existing_component_ids == {"1"}


def test_decide_scope_update_regenerates_scope_when_best_decision_is_invalid() -> None:
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
        patch("agents.scoped_incremental_agent.create_agent", return_value=MagicMock()),
    ):
        agent = ScopedIncrementalAgent(
            repo_dir=Path("/tmp/fake-repo"),
            static_analysis=static_analysis,
            project_name="Test",
            meta_context=None,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )
    agent._validation_invoke = MagicMock(return_value=invalid)

    result = agent.decide_scope_update("", scope, structural)

    assert len(result.operations) == 1
    assert result.operations[0].action == ScopeOperationAction.REGENERATE_SCOPE
