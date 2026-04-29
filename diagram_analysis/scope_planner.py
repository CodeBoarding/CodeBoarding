"""Pure helpers for routing trace results into ``PatchScope`` lists."""

from collections import defaultdict
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis.analysis_patcher import PatchScope, patch_analysis_scope
from diagram_analysis.incremental_models import TraceResult, TraceStopReason


def build_ownership_index(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, dict]:
    """Build maps used for routing files/methods to their owning components."""
    component_to_scope: dict[str, str | None] = {}
    file_to_components: dict[str, set[str]] = defaultdict(set)
    component_to_files: dict[str, set[str]] = defaultdict(set)
    method_to_component: dict[str, str] = {}

    for component in root_analysis.components:
        component_to_scope[component.component_id] = None
        for group in component.file_methods:
            file_to_components[group.file_path].add(component.component_id)
            component_to_files[component.component_id].add(group.file_path)
            for method in group.methods:
                existing = method_to_component.get(method.qualified_name)
                if existing is None or component.component_id.count(".") >= existing.count("."):
                    method_to_component[method.qualified_name] = component.component_id

    for scope_id, analysis in sorted(sub_analyses.items(), key=lambda item: item[0].count(".")):
        # Route subtree patching to the nested scope when a component has a dedicated sub-analysis.
        component_to_scope[scope_id] = scope_id
        for component in analysis.components:
            component_to_scope[component.component_id] = scope_id
            for group in component.file_methods:
                file_to_components[group.file_path].add(component.component_id)
                component_to_files[component.component_id].add(group.file_path)
                for method in group.methods:
                    existing = method_to_component.get(method.qualified_name)
                    if existing is None or component.component_id.count(".") >= existing.count("."):
                        method_to_component[method.qualified_name] = component.component_id

    component_to_descendants: dict[str, set[str]] = {}
    for component_id in component_to_scope:
        component_to_descendants[component_id] = {
            other_id
            for other_id in component_to_scope
            if other_id == component_id or other_id.startswith(component_id + ".")
        }

    return {
        "component_to_scope": component_to_scope,
        "file_to_components": file_to_components,
        "component_to_files": component_to_files,
        "method_to_component": method_to_component,
        "component_to_descendants": component_to_descendants,
    }


def _scope_component_ids(analysis: AnalysisInsights) -> set[str]:
    return {component.component_id for component in analysis.components}


def lowest_common_ancestor(component_ids: list[str]) -> str | None:
    if not component_ids:
        return None
    split_ids = [component_id.split(".") for component_id in component_ids]
    prefix: list[str] = []
    for parts in zip(*split_ids):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    if not prefix:
        return None
    return ".".join(prefix)


def directory_distance(left_path: str, right_path: str) -> int:
    left_parts = Path(left_path).parts[:-1]
    right_parts = Path(right_path).parts[:-1]
    common = 0
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part != right_part:
            break
        common += 1
    return (len(left_parts) - common) + (len(right_parts) - common)


def pick_component_for_file(
    file_path: str,
    ownership_index: dict[str, dict],
    rename_map: dict[str, str] | None = None,
) -> str | None:
    file_to_components: dict[str, set[str]] = ownership_index["file_to_components"]

    if file_path in file_to_components:
        candidates = sorted(file_to_components[file_path], key=lambda component_id: component_id.count("."))
        return candidates[-1] if candidates else None

    inverse_renames = {new_path: old_path for old_path, new_path in (rename_map or {}).items()}
    old_path = inverse_renames.get(file_path)
    if old_path and old_path in file_to_components:
        candidates = sorted(file_to_components[old_path], key=lambda component_id: component_id.count("."))
        return candidates[-1] if candidates else None

    if not file_to_components:
        return None

    distances = {candidate: directory_distance(file_path, candidate) for candidate in file_to_components}
    nearest_distance = min(distances.values())
    nearest_files = [
        candidate
        for candidate, distance in distances.items()
        if distance == nearest_distance and candidate != file_path
    ]
    nearest_components = sorted(
        {component_id for candidate in nearest_files for component_id in file_to_components.get(candidate, set())}
    )
    if not nearest_components:
        return None
    if len(nearest_components) == 1:
        return nearest_components[0]
    return lowest_common_ancestor(nearest_components)


def normalize_changes_for_delta(changes) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    added_files = set(changes.added_files)
    modified_files = set(changes.modified_files)
    deleted_files = set(changes.deleted_files)
    rename_map: dict[str, str] = {}

    for change in changes.changes:
        if change.change_type.value == "R":
            if change.old_path:
                deleted_files.add(change.old_path)
                rename_map[change.old_path] = change.file_path
            added_files.add(change.file_path)
        elif change.change_type.value == "C":
            added_files.add(change.file_path)

    return sorted(added_files), sorted(modified_files), sorted(deleted_files), rename_map


def derive_patch_scopes(
    trace_result: TraceResult,
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    ownership_index: dict[str, dict],
    rename_map: dict[str, str] | None = None,
) -> list[PatchScope]:
    component_to_scope: dict[str, str | None] = ownership_index["component_to_scope"]
    component_to_descendants: dict[str, set[str]] = ownership_index["component_to_descendants"]
    method_to_component: dict[str, str] = ownership_index["method_to_component"]

    target_component_ids: set[str] = {
        method_to_component[method] for method in trace_result.visited_methods if method in method_to_component
    }

    for file_path in trace_result.non_traceable_files + trace_result.disconnected_files:
        component_id = pick_component_for_file(file_path, ownership_index, rename_map)
        if component_id:
            target_component_ids.add(component_id)

    if not target_component_ids and trace_result.unresolved_frontier:
        target_component_ids = {component.component_id for component in root_analysis.components}

    if not target_component_ids:
        return []

    target_by_scope: dict[str | None, set[str]] = defaultdict(set)
    for component_id in target_component_ids:
        target_by_scope[component_to_scope.get(component_id)].add(component_id)

    should_widen = trace_result.stop_reason in {
        TraceStopReason.UNCERTAIN,
        TraceStopReason.BUDGET_EXHAUSTED,
        TraceStopReason.FRONTIER_EXHAUSTED,
    } or bool(trace_result.non_traceable_files)

    patch_scopes: list[PatchScope] = []
    for scope_id, component_ids in target_by_scope.items():
        scope_analysis = root_analysis if scope_id is None else sub_analyses.get(scope_id)
        if scope_analysis is None:
            continue

        selected_ids = set(component_ids)
        scope_component_ids = _scope_component_ids(scope_analysis)
        if should_widen:
            lca = lowest_common_ancestor(sorted(component_ids))
            if lca:
                descendant_ids = {
                    component_id
                    for component_id in component_to_descendants.get(lca, {lca})
                    if component_id in scope_component_ids
                }
                if descendant_ids:
                    selected_ids = descendant_ids
                elif lca in scope_component_ids:
                    selected_ids = {lca}
                else:
                    selected_ids = scope_component_ids
            else:
                selected_ids = scope_component_ids

        patch_scopes.append(
            PatchScope(
                scope_id=scope_id,
                target_component_ids=sorted(selected_ids),
                visited_methods=[
                    method for method in trace_result.visited_methods if method_to_component.get(method) in selected_ids
                ],
                impacted_methods=[
                    method
                    for method in trace_result.impacted_methods
                    if method_to_component.get(method) in selected_ids
                ],
                synthetic_files=[
                    file_path
                    for file_path in trace_result.non_traceable_files + trace_result.disconnected_files
                    if pick_component_for_file(file_path, ownership_index, rename_map) in selected_ids
                ],
                semantic_impact_summary=trace_result.semantic_impact_summary,
            )
        )

    return patch_scopes


def apply_patch_scopes(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    patch_scopes: list[PatchScope],
    agent_llm,
    callbacks: list | None = None,
) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
    patched_root = root_analysis
    patched_sub_analyses = dict(sub_analyses)

    for patch_scope in patch_scopes:
        current_scope = patched_root if patch_scope.scope_id is None else patched_sub_analyses.get(patch_scope.scope_id)
        if current_scope is None:
            raise RuntimeError(f"Patch scope '{patch_scope.scope_id}' could not be resolved")
        patched_scope = patch_analysis_scope(current_scope, patch_scope, agent_llm, callbacks)
        if patched_scope is None:
            raise RuntimeError(f"Patch generation failed for scope '{patch_scope.scope_id or 'root'}' after 3 attempts")
        if patch_scope.scope_id is None:
            patched_root = patched_scope
        else:
            patched_sub_analyses[patch_scope.scope_id] = patched_scope

    return patched_root, patched_sub_analyses
