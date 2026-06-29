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
    ScopeOperation,
    ScopeOperationAction,
    ScopeUpdateDecision,
)
from agents.cluster_ids import CodeBoardingClusterIds
from agents.prompts import get_planning_message, get_system_message
from agents.scope_ids import ROOT_SCOPE_ID
from agents.validation import ScopeOperationValidationContext, validate_scope_update_decision
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
            tools=[self.toolkit.read_source_reference, self.toolkit.list_git_changes, self.toolkit.read_git_diff],
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
                "routing_facts",
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
        base_method_qnames = _base_method_qnames(self.static_analysis.incremental_base_results)
        context = ScopeOperationValidationContext(
            expected_cluster_refs=_actionable_new_cluster_refs(structural_diff),
            code_added_cluster_refs=_code_added_cluster_refs(structural_diff, base_method_qnames),
            enforce_code_added_creates=bool(base_method_qnames),
            existing_component_ids={component.component_id for component in scope.components if component.component_id},
            existing_cluster_owners=_existing_cluster_owners(scope_id, scope.components),
            scope_id=scope_id,
        )
        prompt = self.prompt.format(
            project_name=self.project_name,
            scope_id=scope_id,
            project_type=project_type,
            meta_context=meta_context_str,
            existing_components=_format_scope_components(scope.components),
            routing_facts=_format_routing_facts(context),
            changed_files=_format_changed_files(self.toolkit.context.changes),
            structural_diff=format_structural_diff(structural_diff, base_method_qnames),
        )
        _log_planning_inputs(scope_id, scope, structural_diff, context)
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
                "Incremental planning decision remained invalid after retries; filtering invalid operations. Issues: %s",
                validation.feedback_messages,
            )
            _track_invalid_planning_decision(scope_id, validation.feedback_messages)
            decision = _filter_valid_scope_operations(decision, context)
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


def _filter_valid_scope_operations(
    decision: ScopeUpdateDecision,
    context: ScopeOperationValidationContext,
) -> ScopeUpdateDecision:
    kept: list[ScopeOperation] = []
    used_refs: set[ClusterRef] = set()
    dropped: list[str] = []
    for index, operation in enumerate(decision.operations, start=1):
        reason = _invalid_operation_reason(operation, context, used_refs)
        if reason is not None:
            dropped.append(f"#{index} {operation.action}: {reason}")
            continue
        kept.append(operation)
        used_refs.update(_operation_cluster_refs(operation))
    if dropped:
        logger.error("Filtered invalid incremental planning operations: %s", dropped)
    return ScopeUpdateDecision(operations=kept)


def _invalid_operation_reason(
    operation: ScopeOperation,
    context: ScopeOperationValidationContext,
    used_refs: set[ClusterRef],
) -> str | None:
    refs = _operation_cluster_refs(operation)
    if operation.action == ScopeOperationAction.NOOP and context.expected_cluster_refs:
        return "noop misses actionable cluster_refs"
    if (
        operation.action
        in {
            ScopeOperationAction.UPDATE_COMPONENT,
            ScopeOperationAction.DELETE_COMPONENT,
            ScopeOperationAction.NOOP,
        }
        and operation.component_id not in context.existing_component_ids
    ):
        return f"unknown component_id={operation.component_id!r}"
    if operation.action == ScopeOperationAction.CREATE_COMPONENT:
        if not refs:
            return "create_component has no cluster_refs"
        if not operation.name or not operation.description:
            return "create_component missing name or description"
        if context.enforce_code_added_creates and not (refs & context.code_added_cluster_refs):
            return "create_component has no code-added cluster_refs"
    unexpected = refs - context.expected_cluster_refs
    if unexpected:
        return f"unexpected cluster_refs={_format_cluster_ref_list(unexpected)}"
    duplicate = refs & used_refs
    if duplicate:
        return f"duplicate cluster_refs={_format_cluster_ref_list(duplicate)}"
    for ref in refs:
        owner = context.existing_cluster_owners.get((ref.scope_id, ref.cluster_id))
        if owner is None:
            continue
        if operation.action == ScopeOperationAction.UPDATE_COMPONENT and owner == operation.component_id:
            continue
        return f"{_format_cluster_ref(ref)} already owned by {owner!r}"
    return None


def _operation_cluster_refs(operation: ScopeOperation) -> set[ClusterRef]:
    return {ClusterRef(ref.language, ref.cluster_id, ref.scope_id) for ref in operation.cluster_refs}


def format_structural_diff(
    structural_diff: StructuralClusterDiff,
    base_method_qnames: set[str] | None = None,
) -> str:
    sections: list[str] = []
    for language in sorted(structural_diff.by_language):
        sections.append(_format_language_diff(structural_diff.by_language[language], base_method_qnames))
    return "\n".join(section for section in sections if section) or "No structural changes."


def _format_language_diff(diff: LanguageStructuralDiff, base_method_qnames: set[str] | None) -> str:
    lines = [f"## {diff.language}"]
    if diff.modified:
        lines.append("### Modified clusters")
        lines.extend(_format_member_delta(delta, base_method_qnames) for delta in diff.modified)
    if diff.new:
        lines.append("### New clusters")
        if diff.new_details:
            lines.extend(_format_new_cluster(delta, base_method_qnames) for delta in diff.new_details)
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


def _format_member_delta(delta: ClusterMemberDelta, base_method_qnames: set[str] | None) -> str:
    parts = [f"- {_format_cluster_ref(delta.old_cluster)} -> {_format_cluster_ref(delta.new_cluster)}"]
    if delta.added_methods:
        parts.append(f"added_to_scope={sorted(delta.added_methods)}")
        parts.extend(_format_code_status_parts(delta.added_methods, base_method_qnames))
    if delta.removed_methods:
        parts.append(f"removed={sorted(delta.removed_methods)}")
    if delta.dirty_files:
        parts.append(f"dirty_files={sorted(delta.dirty_files)}")
    return "; ".join(parts)


def _format_new_cluster(delta: ClusterMemberDelta, base_method_qnames: set[str] | None) -> str:
    parts = [f"- {_format_cluster_ref(delta.new_cluster)}"]
    if delta.added_methods:
        parts.append(f"methods={sorted(delta.added_methods)}")
        parts.extend(_format_code_status_parts(delta.added_methods, base_method_qnames))
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


def _code_added_cluster_refs(structural_diff: StructuralClusterDiff, base_method_qnames: set[str]) -> set[ClusterRef]:
    if not base_method_qnames:
        return set()
    refs: set[ClusterRef] = set()
    for diff in structural_diff.by_language.values():
        for delta in [*diff.modified, *diff.new_details]:
            if delta.added_methods - base_method_qnames:
                refs.add(delta.new_cluster)
    return refs


def _base_method_qnames(static_analysis: StaticAnalysisResults | None) -> set[str]:
    if static_analysis is None or not isinstance(static_analysis, StaticAnalysisResults):
        return set()
    qnames: set[str] = set()
    for language in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_cfg(language)
        except (KeyError, ValueError):
            continue
        qnames.update(str(qname) for qname in cfg.nodes)
    return qnames


def _format_code_status_parts(methods: set[str], base_method_qnames: set[str] | None) -> list[str]:
    if base_method_qnames is None:
        return []
    code_added = methods - base_method_qnames
    existing_in_base = methods & base_method_qnames
    parts: list[str] = []
    if code_added:
        parts.append(f"code_added={sorted(code_added)}")
    if existing_in_base:
        parts.append(f"existing_in_base={sorted(existing_in_base)}")
    return parts


def _existing_cluster_owners(scope_id: str, components: list[Component]) -> dict[tuple[str, int], str]:
    owners: dict[tuple[str, int], str] = {}
    for component in components:
        if not component.component_id:
            continue
        for source_cluster_id in component.source_cluster_ids:
            local_cluster_id = _local_cluster_id(scope_id, source_cluster_id)
            if local_cluster_id is not None:
                owners[(scope_id, local_cluster_id)] = component.component_id
    return owners


def _local_cluster_id(scope_id: str, source_cluster_id: str) -> int | None:
    if scope_id == ROOT_SCOPE_ID:
        return int(source_cluster_id) if source_cluster_id.isdigit() else None
    prefix = f"{scope_id}."
    if not source_cluster_id.startswith(prefix):
        return None
    suffix = source_cluster_id[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


def _log_planning_inputs(
    scope_id: str,
    scope: AnalysisInsights,
    structural_diff: StructuralClusterDiff,
    context: ScopeOperationValidationContext,
) -> None:
    owner_counts: dict[str, int] = {}
    for owner in context.existing_cluster_owners.values():
        owner_counts[owner] = owner_counts.get(owner, 0) + 1
    lines = [
        "[incremental_planning] scope "
        f"{scope_id}: components={len(scope.components)} expected_refs={len(context.expected_cluster_refs)} "
        f"owned_clusters={len(context.existing_cluster_owners)} owner_counts={dict(sorted(owner_counts.items()))}"
    ]
    for component in scope.components:
        lines.append(
            "  component "
            f"{component.component_id or '?'} {component.name!r}: clusters={CodeBoardingClusterIds.sort(set(component.source_cluster_ids))} "
            f"files={sum(len(group.methods) for group in component.file_methods)}"
        )
    lines.extend(_planning_diff_lines(structural_diff, context))
    logger.info("\n".join(lines))


def _planning_diff_lines(
    structural_diff: StructuralClusterDiff,
    context: ScopeOperationValidationContext,
) -> list[str]:
    lines: list[str] = []
    for language in sorted(structural_diff.by_language):
        diff = structural_diff.by_language[language]
        lines.append(
            f"  diff {language}: modified={len(diff.modified)} new={len(diff.new)} "
            f"new_details={len(diff.new_details)} removed={len(diff.removed)} reshaped={len(diff.reshaped)}"
        )
        for delta in diff.modified:
            lines.append(_member_delta_log_line("modified", delta, context))
        for delta in diff.new_details:
            lines.append(_member_delta_log_line("new", delta, context))
        for ref in diff.removed:
            lines.append(f"    removed {_format_cluster_ref(ref)} owner={_cluster_owner(ref, context)}")
        for reshape in diff.reshaped:
            lines.append(
                "    reshaped "
                f"old={[ _format_cluster_ref(ref) for ref in _sort_cluster_refs(reshape.old_clusters) ]} "
                f"new={[ _format_cluster_ref(ref) for ref in _sort_cluster_refs(reshape.new_clusters) ]} "
                f"dirty_files={_sample_strings(reshape.dirty_files)}"
            )
    return lines


def _member_delta_log_line(
    label: str,
    delta: ClusterMemberDelta,
    context: ScopeOperationValidationContext,
) -> str:
    return (
        f"    {label} {_format_cluster_ref(delta.old_cluster)} -> {_format_cluster_ref(delta.new_cluster)} "
        f"owner={_cluster_owner(delta.new_cluster, context)} "
        f"unchanged={len(delta.unchanged_methods)} added={len(delta.added_methods)} "
        f"removed={len(delta.removed_methods)} dirty_files={_sample_strings(delta.dirty_files)} "
        f"added_sample={_sample_strings(delta.added_methods)} removed_sample={_sample_strings(delta.removed_methods)}"
    )


def _cluster_owner(ref: ClusterRef, context: ScopeOperationValidationContext) -> str:
    return context.existing_cluster_owners.get((ref.scope_id, ref.cluster_id), "new")


def _sample_strings(values: Iterable[str], limit: int = 8) -> list[str]:
    sorted_values = sorted(values)
    if len(sorted_values) <= limit:
        return sorted_values
    return [*sorted_values[:limit], f"...(+{len(sorted_values) - limit})"]


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


def _format_routing_facts(context: ScopeOperationValidationContext) -> str:
    lines = [
        "Actionable cluster_refs — use ONLY these refs in operations:",
        _format_cluster_ref_list(context.expected_cluster_refs),
        "",
        "Code-added refs — safe candidates for create_component when they form a new responsibility:",
        _format_cluster_ref_list(context.code_added_cluster_refs),
        "",
        "Existing ownership — these refs are already owned; update that exact owner or omit the ref:",
    ]
    if not context.existing_cluster_owners:
        lines.append("None")
    else:
        for (scope_id, cluster_id), owner in sorted(context.existing_cluster_owners.items()):
            lines.append(f"- {scope_id}:python:{cluster_id} -> {owner}")
    lines.extend(
        [
            "",
            "Decision rule:",
            "- Structural added/modified refs can be only cluster-local movement. Check code_added and existing_in_base before creating components.",
            "- Do not create a component from refs that contain only methods already present in the baseline; update/keep the existing owner instead.",
            "- If a changed ref is already owned and the owner still matches the responsibility, use update_component for that owner or omit it if only cluster shape changed.",
            "- Create components only for unowned actionable refs that form a new architectural responsibility.",
            "- Never include refs outside the actionable list.",
        ]
    )
    return "\n".join(lines)


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
