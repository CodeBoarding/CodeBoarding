"""Plan scoped analysis updates from structural cluster diffs."""

from dataclasses import dataclass, field
from collections.abc import Iterable
import logging
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    MetaAnalysisInsights,
    ScopeOperationAction,
    ScopeUpdateDecision,
    ScopedClusterRef,
)
from agents.cluster_ids import CodeBoardingClusterIds
from agents.prompts import get_planning_message, get_system_message
from agents.scope_ids import ROOT_SCOPE_ID
from agents.validation import ValidationResult
from diagram_analysis.cluster_delta import (
    ClusterMemberDelta,
    ClusterRef,
    ClusterReshape,
    LanguageStructuralDiff,
    StructuralClusterDiff,
)
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from telemetry.service import telemetry


logger = logging.getLogger(__name__)


@dataclass
class ScopeOperationValidationContext:
    expected_cluster_refs: set[ClusterRef] = field(default_factory=set)
    existing_component_ids: set[str] = field(default_factory=set)
    scope_id: str = ROOT_SCOPE_ID


class IncrementalPlanningAgent(CodeBoardingAgent):
    """Decides diagram operations for one structural-diff scope."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights | None,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        changes: ChangeSet | None = None,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message(), agent_llm, parsing_llm)
        if changes is not None:
            self.toolkit.context.changes = changes
        self.agent = create_agent(
            model=agent_llm,
            tools=[self.toolkit.read_source_reference, self.toolkit.list_git_changes],
        )
        self.project_name = project_name
        self.meta_context = meta_context
        self.prompt = PromptTemplate(
            template=get_planning_message(),
            input_variables=[
                "project_name",
                "scope_id",
                "project_type",
                "meta_context",
                "existing_components",
                "changed_files",
                "structural_diff",
            ],
        )

    def decide_scope_update(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        structural_diff: StructuralClusterDiff,
    ) -> ScopeUpdateDecision:
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"
        prompt = self.prompt.format(
            project_name=self.project_name,
            scope_id=scope_id,
            project_type=project_type,
            meta_context=meta_context_str,
            existing_components=_format_scope_components(scope.components),
            changed_files=_format_changed_files(self.toolkit.context.changes),
            structural_diff=format_structural_diff(structural_diff),
        )
        context = ScopeOperationValidationContext(
            expected_cluster_refs=_actionable_new_cluster_refs(structural_diff),
            existing_component_ids={component.component_id for component in scope.components if component.component_id},
            scope_id=scope_id,
        )
        decision = self._validation_invoke(
            prompt,
            ScopeUpdateDecision,
            validators=[validate_scope_update_decision],
            context=context,
            max_validation_attempts=3,
        )
        validation = validate_scope_update_decision(decision, context)
        if not validation.is_valid:
            logger.error(
                "Incremental planning decision remained invalid after retries; will still pass it on. Issues: %s",
                validation.feedback_messages,
            )
            _track_invalid_planning_decision(scope_id, validation.feedback_messages)
        return decision


def _track_invalid_planning_decision(scope_id: str, feedback_messages: list[str]) -> None:
    telemetry.capture_exception(
        RuntimeError("Incremental planning decision remained invalid after retries"),
        properties={
            "error_type": "incremental_planning_invalid_decision",
            "scope_id": scope_id,
            "issue_count": len(feedback_messages),
            "issues": feedback_messages[:10],
        },
    )
    telemetry.flush()


def validate_scope_update_decision(
    decision: ScopeUpdateDecision,
    context: ScopeOperationValidationContext,
) -> ValidationResult:
    errors: list[str] = []
    seen_refs: list[ClusterRef] = []
    for operation in decision.operations:
        refs = [_cluster_ref_from_scoped_ref(ref) for ref in operation.cluster_refs]
        seen_refs.extend(refs)
        if operation.action in {
            ScopeOperationAction.UPDATE_COMPONENT,
            ScopeOperationAction.DELETE_COMPONENT,
            ScopeOperationAction.NOOP,
        }:
            if operation.component_id not in context.existing_component_ids:
                errors.append(
                    f"Operation {operation.action} references unknown component_id={operation.component_id!r}."
                )
        if operation.action == ScopeOperationAction.CREATE_COMPONENT:
            if not operation.name or not operation.description:
                errors.append("create_component operations must include name and description.")

    seen_set = set(seen_refs)
    missing = context.expected_cluster_refs - seen_set
    extra = seen_set - context.expected_cluster_refs
    duplicates = {ref for ref in seen_set if seen_refs.count(ref) > 1}
    if missing:
        errors.append(f"Missing cluster_refs: {_format_cluster_ref_list(missing)}")
    if extra:
        errors.append(f"Unexpected cluster_refs: {_format_cluster_ref_list(extra)}")
    if duplicates:
        errors.append(f"Duplicate cluster_refs: {_format_cluster_ref_list(duplicates)}")
    return ValidationResult(is_valid=not errors, feedback_messages=errors)


def _cluster_ref_from_scoped_ref(ref: ScopedClusterRef) -> ClusterRef:
    return ClusterRef(ref.language, ref.cluster_id, ref.scope_id)


def format_structural_diff(structural_diff: StructuralClusterDiff) -> str:
    sections: list[str] = []
    for language in sorted(structural_diff.by_language):
        sections.append(_format_language_diff(structural_diff.by_language[language]))
    return "\n".join(section for section in sections if section) or "No structural changes."


def _format_language_diff(diff: LanguageStructuralDiff) -> str:
    lines = [f"## {diff.language}"]
    if diff.modified:
        lines.append("### Modified clusters")
        lines.extend(_format_member_delta(delta) for delta in diff.modified)
    if diff.new:
        lines.append("### New clusters")
        if diff.new_details:
            lines.extend(_format_new_cluster(delta) for delta in diff.new_details)
        else:
            lines.extend(f"- {_format_cluster_ref(ref)}" for ref in _sort_cluster_refs(diff.new))
    if diff.removed:
        lines.append("### Removed clusters")
        lines.extend(f"- {_format_cluster_ref(ref)}" for ref in _sort_cluster_refs(diff.removed))
    if diff.reshaped:
        lines.append("### Reshaped clusters")
        lines.extend(_format_reshape(reshape) for reshape in diff.reshaped)
    if len(lines) == 1:
        lines.append("No structural changes.")
    return "\n".join(lines)


def _format_member_delta(delta: ClusterMemberDelta) -> str:
    parts = [f"- {_format_cluster_ref(delta.old_cluster)} -> {_format_cluster_ref(delta.new_cluster)}"]
    if delta.added_methods:
        parts.append(f"added={sorted(delta.added_methods)}")
    if delta.removed_methods:
        parts.append(f"removed={sorted(delta.removed_methods)}")
    if delta.dirty_files:
        parts.append(f"dirty_files={sorted(delta.dirty_files)}")
    return "; ".join(parts)


def _format_new_cluster(delta: ClusterMemberDelta) -> str:
    parts = [f"- {_format_cluster_ref(delta.new_cluster)}"]
    if delta.added_methods:
        parts.append(f"methods={sorted(delta.added_methods)}")
    if delta.dirty_files:
        parts.append(f"files={sorted(delta.dirty_files)}")
    return "; ".join(parts)


def _format_reshape(reshape: ClusterReshape) -> str:
    old_refs = ", ".join(_format_cluster_ref(ref) for ref in _sort_cluster_refs(reshape.old_clusters))
    new_refs = ", ".join(_format_cluster_ref(ref) for ref in _sort_cluster_refs(reshape.new_clusters))
    overlaps = ", ".join(
        f"{_format_cluster_ref(old_ref)}->{_format_cluster_ref(new_ref)}:{count}"
        for (old_ref, new_ref), count in sorted(
            reshape.overlap_counts.items(),
            key=lambda item: (
                item[0][0].scope_id,
                item[0][0].language,
                item[0][0].cluster_id,
                item[0][1].scope_id,
                item[0][1].language,
                item[0][1].cluster_id,
            ),
        )
    )
    suffix = f"; dirty_files={sorted(reshape.dirty_files)}" if reshape.dirty_files else ""
    return f"- old=[{old_refs}] new=[{new_refs}] overlaps=[{overlaps}]{suffix}"


def _actionable_new_cluster_refs(structural_diff: StructuralClusterDiff) -> set[ClusterRef]:
    refs: set[ClusterRef] = set()
    for diff in structural_diff.by_language.values():
        refs.update(delta.new_cluster for delta in diff.modified)
        refs.update(diff.new)
        for reshape in diff.reshaped:
            refs.update(reshape.new_clusters)
    return refs


def _format_scope_components(components: list[Component]) -> str:
    if not components:
        return "No existing components in this scope."
    return "\n".join(
        f'- {component.component_id or "?"} "{component.name}" '
        f"clusters=[{_format_component_cluster_ids(component.source_cluster_ids)}] -- "
        f"{(component.description or '').strip()}"
        for component in components
    )


def _format_component_cluster_ids(cluster_ids: list[str]) -> str:
    return ", ".join(CodeBoardingClusterIds.sort(set(cluster_ids))) or "None"


def _format_changed_files(changes: ChangeSet | None) -> str:
    if changes is None or not changes.files:
        return "No changed-file summary available."
    lines = []
    for file_change in changes.files:
        if file_change.old_path:
            lines.append(f"- {file_change.status_code} {file_change.old_path} -> {file_change.file_path}")
        else:
            lines.append(f"- {file_change.status_code} {file_change.file_path}")
    return "\n".join(lines)


def _format_cluster_ref(ref: ClusterRef) -> str:
    return f"{ref.scope_id}:{ref.language}:{ref.cluster_id}"


def _sort_cluster_refs(refs: Iterable[ClusterRef]) -> list[ClusterRef]:
    return sorted(refs, key=lambda ref: (ref.scope_id, ref.language, ref.cluster_id))


def _format_cluster_ref_list(refs: set[ClusterRef]) -> str:
    if not refs:
        return "None"
    return ", ".join(_format_cluster_ref(ref) for ref in _sort_cluster_refs(refs))
