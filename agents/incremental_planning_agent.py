"""Plan scoped analysis updates from structural cluster diffs."""

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
    ScopeUpdateDecision,
)
from agents.cluster_ids import CodeBoardingClusterIds
from agents.prompts import format_project_system_message, get_planning_message, get_system_message
from agents.repair import (
    ScopeOperationRepairContext,
    repair_unambiguous_routing_and_optional_key_entity_metadata,
)
from agents.scope_ids import ROOT_SCOPE_ID
from agents.scope_operations import cluster_member_qnames, normalize_component_name
from agents.validation import ScopeOperationValidationContext, validate_scope_update_decision
from diagram_analysis.cluster_delta import (
    ClusterMemberDelta,
    ClusterRef,
    ClusterReshape,
    LanguageStructuralDiff,
    StructuralClusterDiff,
)
from diagram_analysis.exceptions import InvalidIncrementalPlanError
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult
from telemetry.service import telemetry


logger = logging.getLogger(__name__)


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
        system_message = format_project_system_message(get_system_message(), project_name, meta_context)
        super().__init__(repo_dir, static_analysis, system_message, agent_llm, parsing_llm)
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
                "scope_id",
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
        cluster_results: dict[str, ClusterResult],
    ) -> ScopeUpdateDecision:
        prompt = self.prompt.format(
            scope_id=scope_id,
            existing_components=_format_scope_components(scope.components),
            changed_files=_format_changed_files(self.toolkit.context.changes),
            structural_diff=format_structural_diff(structural_diff),
        )
        actionable_cluster_refs = _actionable_new_cluster_refs(structural_diff)
        component_ids_by_cluster_ref = _component_ids_by_cluster_ref(scope_id, scope.components, structural_diff)
        repair_context = ScopeOperationRepairContext(
            reference_resolver=self.reference_resolver,
            allowed_key_entity_qnames=cluster_member_qnames(cluster_results),
            component_ids_by_cluster_ref=component_ids_by_cluster_ref,
            component_ids_by_name=_component_ids_by_name(scope.components),
            scope_id=scope_id,
            actionable_cluster_refs=actionable_cluster_refs,
            owned_cluster_ids_by_component_id={
                component.component_id: set(component.source_cluster_ids)
                for component in scope.components
                if component.component_id
            },
        )
        validation_context = ScopeOperationValidationContext(
            expected_cluster_refs=actionable_cluster_refs,
            existing_component_ids={component.component_id for component in scope.components if component.component_id},
            component_ids_by_cluster_ref=component_ids_by_cluster_ref,
        )
        decision = self._invoke_repair_validate(
            prompt,
            ScopeUpdateDecision,
            repairs=[repair_unambiguous_routing_and_optional_key_entity_metadata],
            validators=[validate_scope_update_decision],
            repair_context=repair_context,
            validation_context=validation_context,
            max_validation_attempts=3,
        )
        validation = validate_scope_update_decision(decision, validation_context)
        if not validation.is_valid:
            logger.error(
                "Incremental planning decision remained invalid after retries: %s",
                validation.feedback_messages,
            )
            _track_invalid_planning_decision(scope_id, validation.feedback_messages)
            raise InvalidIncrementalPlanError(scope_id, validation.feedback_messages)
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


def _component_ids_by_cluster_ref(
    scope_id: str,
    components: list[Component],
    structural_diff: StructuralClusterDiff,
) -> dict[ClusterRef, str]:
    """Map new-side cluster refs to a unique existing owner when overlap proves one."""
    cluster_id_prefix = CodeBoardingClusterIds.prefix_for_scope(scope_id)
    owners_by_cluster_id: dict[str, set[str]] = {}
    for component in components:
        if not component.component_id:
            continue
        for cluster_id in component.source_cluster_ids:
            owners_by_cluster_id.setdefault(cluster_id, set()).add(component.component_id)

    def owner_for_old_ref(ref: ClusterRef) -> str | None:
        cluster_id = CodeBoardingClusterIds.qualify_local_id(
            CodeBoardingClusterIds.from_graph_id(ref.cluster_id),
            cluster_id_prefix,
        )
        owners = owners_by_cluster_id.get(cluster_id, set())
        return next(iter(owners)) if len(owners) == 1 else None

    owners_by_new_ref: dict[ClusterRef, set[str]] = {}
    for language_diff in structural_diff.by_language.values():
        for delta in language_diff.modified:
            owner = owner_for_old_ref(delta.old_cluster)
            if owner:
                owners_by_new_ref.setdefault(delta.new_cluster, set()).add(owner)
        for reshape in language_diff.reshaped:
            for (old_ref, new_ref), overlap in reshape.overlap_counts.items():
                if overlap <= 0:
                    continue
                owner = owner_for_old_ref(old_ref)
                if owner:
                    owners_by_new_ref.setdefault(new_ref, set()).add(owner)

    return {ref: next(iter(owner_ids)) for ref, owner_ids in owners_by_new_ref.items() if len(owner_ids) == 1}


def _component_ids_by_name(components: list[Component]) -> dict[str, str]:
    ids_by_name: dict[str, set[str]] = {}
    for component in components:
        if component.component_id:
            ids_by_name.setdefault(normalize_component_name(component.name), set()).add(component.component_id)
    return {name: next(iter(component_ids)) for name, component_ids in ids_by_name.items() if len(component_ids) == 1}


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
    lines: list[str] = []
    for component in components:
        key_entities = ", ".join(entity.qualified_name for entity in component.key_entities) or "None"
        lines.append(
            f'- {component.component_id or "?"} "{component.name}" '
            f"clusters=[{_format_component_cluster_ids(component.source_cluster_ids)}] -- "
            f"{(component.description or '').strip()} -- key_entities=[{key_entities}]"
        )
    return "\n".join(lines)


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
