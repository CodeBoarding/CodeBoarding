"""Apply an ``IncrementalDelta`` to a loaded analysis tree.

These helpers mutate ``AnalysisInsights`` in place: ``apply_method_delta``
patches the file/method index and component ownership;
``prune_empty_components`` removes components that lost all methods; and
``drop_deltas_for_pruned_components`` keeps the wire-format delta in sync
with what was actually applied.
"""

import logging

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
)
from agents.change_status import ChangeStatus
from diagram_analysis.incremental_updater import FileDelta, IncrementalDelta, MethodChange

logger = logging.getLogger(__name__)


def _component_lookup(root: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> dict[str, Component]:
    return {
        c.component_id: c
        for sources in [root.components] + [s.components for s in sub_analyses.values()]
        for c in sources
    }


def _ensure_file_entry(files: dict[str, FileEntry], file_path: str) -> FileEntry:
    existing = files.get(file_path)
    if existing is None:
        existing = FileEntry(methods=[])
        files[file_path] = existing
    return existing


def _apply_method_changes(
    methods_by_name: dict[str, MethodEntry],
    method_changes: list[MethodChange],
) -> None:
    for method in method_changes:
        methods_by_name[method.qualified_name] = MethodEntry.from_method_change(method)


def _sorted_methods(methods_by_name: dict[str, MethodEntry]) -> list[MethodEntry]:
    return sorted(methods_by_name.values(), key=lambda m: (m.start_line, m.end_line, m.qualified_name))


def _apply_file_delta_to_index(files: dict[str, FileEntry], file_delta: FileDelta) -> None:
    if file_delta.file_status == ChangeStatus.DELETED:
        files.pop(file_delta.file_path, None)
        return

    existing = _ensure_file_entry(files, file_delta.file_path)
    methods_by_name: dict[str, MethodEntry] = {m.qualified_name: m for m in existing.methods}

    for method_change in file_delta.deleted_methods:
        methods_by_name.pop(method_change.qualified_name, None)
    _apply_method_changes(methods_by_name, file_delta.added_methods)
    _apply_method_changes(methods_by_name, file_delta.modified_methods)
    existing.methods = _sorted_methods(methods_by_name)


def _sync_component_methods(
    component: Component,
    files: dict[str, FileEntry],
    deltas_by_path: dict[str, FileDelta],
    parent_ids: set[str],
) -> None:
    """Rebuild component's file_methods, preserving method boundaries.

    Strategy:
    1. Collect the qualified names this component originally owned.
    2. Remove deleted methods from ownership.
    3. Absorb newly added methods when the component is the primary owner
       (``delta.component_id``) OR is an intermediate ancestor of the primary
       (its ID is a descendant of the primary AND it has its own children in
       ``parent_ids``).  Leaf components and siblings that don't own the file
       are unaffected.
    4. Filter the global files index to only include owned methods.
    """
    owned_qnames: set[str] = set()

    for group in component.file_methods:
        for method in group.methods:
            owned_qnames.add(method.qualified_name)

    for file_path, delta in deltas_by_path.items():
        if not component.file_methods:
            continue
        if not any(group.file_path == file_path for group in component.file_methods):
            continue

        for method in delta.deleted_methods:
            owned_qnames.discard(method.qualified_name)

        primary_id = delta.component_id or ""
        should_absorb = primary_id == component.component_id or (
            component.component_id.startswith(primary_id + ".") and component.component_id in parent_ids
        )

        # A component that owned every pre-existing method in the file is a
        # superset owner (e.g. a root component whose children partition its
        # methods). It must absorb new methods so it stays a superset.
        if not should_absorb and delta.added_methods:
            file_entry = files.get(file_path)
            if file_entry is not None:
                added_qnames = {m.qualified_name for m in delta.added_methods}
                pre_existing = {m.qualified_name for m in file_entry.methods} - added_qnames
                if pre_existing and pre_existing <= owned_qnames:
                    should_absorb = True

        if should_absorb:
            for method in delta.added_methods:
                owned_qnames.add(method.qualified_name)

    component.file_methods = [
        FileMethodGroup(
            file_path=fp,
            methods=[
                m.model_copy(deep=True) for m in (entry.methods if entry else []) if m.qualified_name in owned_qnames
            ],
        )
        for fp in sorted({g.file_path for g in component.file_methods})
        if (entry := files.get(fp)) is not None
    ]


def apply_method_delta(
    root: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    delta: IncrementalDelta,
) -> None:
    """Apply the method-level portion of ``IncrementalDelta`` in place.

    Only method-level facts are mutated here — added/removed methods and the
    per-file method index. Status tracking is the caller's responsibility.
    """
    if not delta.has_changes:
        return

    files = dict(root.files)
    component_lookup = _component_lookup(root, sub_analyses)
    parent_ids = set(sub_analyses.keys())

    deltas_by_path: dict[str, FileDelta] = {}
    for file_delta in delta.file_deltas:
        deltas_by_path[file_delta.file_path] = file_delta
        _apply_file_delta_to_index(files, file_delta)

        component = component_lookup.get(file_delta.component_id or "")
        if component is None:
            continue

        existing_by_path = {group.file_path: group for group in component.file_methods}
        if file_delta.file_path not in existing_by_path:
            existing_by_path[file_delta.file_path] = FileMethodGroup(file_path=file_delta.file_path, methods=[])
            component.file_methods = [existing_by_path[path] for path in sorted(existing_by_path)]

    root.files = files
    for component in root.components:
        _sync_component_methods(component, files, deltas_by_path, parent_ids)

    for sub in sub_analyses.values():
        sub.files = files
        for component in sub.components:
            _sync_component_methods(component, files, deltas_by_path, parent_ids)


def _component_is_empty(component: Component) -> bool:
    """A component is empty when it owns zero methods across all its files."""
    return not any(group.methods for group in component.file_methods)


def _drop_relations(analysis: AnalysisInsights, removed_ids: set[str], removed_names: set[str]) -> None:
    analysis.components_relations = [
        rel
        for rel in analysis.components_relations
        if rel.src_id not in removed_ids
        and rel.dst_id not in removed_ids
        and rel.src_name not in removed_names
        and rel.dst_name not in removed_names
    ]


def prune_empty_components(
    root: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> set[str]:
    """Remove components whose every file group is empty (no methods left).

    Cascades to descendant sub-analyses and drops relations that reference a
    removed component. Component IDs of survivors are preserved as-is.
    Returns the set of removed component IDs.
    """
    all_components: list[Component] = list(root.components)
    for sub in sub_analyses.values():
        all_components.extend(sub.components)

    candidate_ids = {c.component_id for c in all_components if c.component_id and _component_is_empty(c)}
    if not candidate_ids:
        return set()

    non_empty_ids = {c.component_id for c in all_components if c.component_id and not _component_is_empty(c)}

    removed_ids: set[str] = set()
    for cid in candidate_ids:
        prefix = cid + "."
        if any(other.startswith(prefix) for other in non_empty_ids):
            logger.warning(
                "Skipping prune of empty component %s: has non-empty descendants",
                cid,
            )
            continue
        removed_ids.add(cid)

    if not removed_ids:
        return set()

    removed_names = {c.name for c in all_components if c.component_id in removed_ids}

    root.components = [c for c in root.components if c.component_id not in removed_ids]
    _drop_relations(root, removed_ids, removed_names)

    for sub in sub_analyses.values():
        sub.components = [c for c in sub.components if c.component_id not in removed_ids]
        _drop_relations(sub, removed_ids, removed_names)

    for cid in list(sub_analyses.keys()):
        if cid in removed_ids:
            del sub_analyses[cid]

    return removed_ids


def drop_deltas_for_pruned_components(delta: IncrementalDelta, removed_ids: set[str]) -> None:
    """Strip ``file_deltas`` whose component was deterministically pruned."""
    if not removed_ids:
        return
    delta.file_deltas = [fd for fd in delta.file_deltas if fd.component_id not in removed_ids]
