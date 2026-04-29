"""Incremental analysis updater.

Flow:
1. Collect changed files from the monitor.
2. Compare current file symbols with methods stored in analysis.files.
3. Use git diff line ranges to determine per-method statuses (via ``repo_utils.method_diff``).
4. Return an ``IncrementalDelta``. Status storage is the caller's responsibility.
"""

import logging
import time
from pathlib import PurePosixPath
from typing import Callable

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
)
from agents.change_status import ChangeStatus
from diagram_analysis.incremental.delta import FileDelta, IncrementalDelta, MethodChange
from repo_utils.change_detector import ChangeSet

logger = logging.getLogger(__name__)

# Resolves a repo-relative file path to its current MethodEntry list.
SymbolResolver = Callable[[str], list[MethodEntry]]


def resolve_component_id_by_path_prefix(file_path: str, file_to_component: dict[str, str]) -> str | None:
    """Default component resolver: pick the component whose tracked files share
    the longest path prefix. Used when a newly added file has no direct
    mapping — we assume it belongs to whichever component already owns the
    deepest common directory.
    """
    if file_path in file_to_component:
        return file_to_component[file_path]

    query_parts = PurePosixPath(file_path).parts
    best_component_id: str | None = None
    best_prefix_len = 0

    for existing_path, component_id in file_to_component.items():
        existing_parts = PurePosixPath(existing_path).parts
        prefix_len = 0
        for query_part, existing_part in zip(query_parts, existing_parts, strict=False):
            if query_part != existing_part:
                break
            prefix_len += 1
        if prefix_len > best_prefix_len:
            best_prefix_len = prefix_len
            best_component_id = component_id

    return best_component_id


class IncrementalUpdater:
    """Computes file-level incremental deltas from file changes.

    Delegates per-method status classification to ``FileChange.classify_method_statuses``.
    """

    def __init__(
        self,
        analysis: AnalysisInsights,
        symbol_resolver: SymbolResolver,
        change_set: ChangeSet,
    ):
        self.analysis = analysis
        self._symbol_resolver = symbol_resolver
        self._change_set = change_set
        self._file_to_component = analysis.file_to_component()

    def _get_current_methods(self, file_path: str) -> list[MethodEntry]:
        return self._symbol_resolver(file_path)

    def _get_previous_methods(self, file_path: str) -> dict[str, MethodEntry]:
        entry = self.analysis.files.get(file_path)
        if entry is None:
            return {}
        return {m.qualified_name: m for m in entry.methods}

    @staticmethod
    def _to_method_change(
        file_path: str,
        method: MethodEntry,
        change_type: ChangeStatus,
    ) -> MethodChange:
        return MethodChange(
            qualified_name=method.qualified_name,
            file_path=file_path,
            start_line=method.start_line,
            end_line=method.end_line,
            change_type=change_type,
            node_type=method.node_type,
        )

    def _compute_file_delta(
        self,
        file_path: str,
        file_status: ChangeStatus,
        register_file: bool,
    ) -> tuple[FileDelta, bool]:
        component_id = self._file_to_component.get(file_path)
        if component_id is None and register_file:
            component_id = resolve_component_id_by_path_prefix(file_path, self._file_to_component)
        if component_id is not None and register_file:
            self._file_to_component[file_path] = component_id
        missing = component_id is None

        if file_status == ChangeStatus.ADDED:
            current = self._get_current_methods(file_path)
            return (
                FileDelta(
                    file_path=file_path,
                    file_status=ChangeStatus.ADDED,
                    component_id=component_id,
                    added_methods=[self._to_method_change(file_path, m, ChangeStatus.ADDED) for m in current],
                ),
                missing,
            )

        if file_status == ChangeStatus.DELETED:
            prev = self._get_previous_methods(file_path)
            return (
                FileDelta(
                    file_path=file_path,
                    file_status=ChangeStatus.DELETED,
                    component_id=component_id,
                    deleted_methods=[
                        self._to_method_change(file_path, m, ChangeStatus.DELETED) for _, m in sorted(prev.items())
                    ],
                ),
                missing,
            )

        # Modified
        prev_active = self._get_previous_methods(file_path)
        try:
            current = self._get_current_methods(file_path)
        except Exception as exc:
            logger.warning("Symbol resolution failed for %s: %s", file_path, exc)
            current = []

        if not current and prev_active:
            logger.warning(
                "Symbol resolution returned no methods for %s; marking all existing methods as modified",
                file_path,
            )
            return (
                FileDelta(
                    file_path=file_path,
                    file_status=ChangeStatus.MODIFIED,
                    component_id=component_id,
                    modified_methods=[
                        self._to_method_change(file_path, m, ChangeStatus.MODIFIED)
                        for _, m in sorted(prev_active.items())
                    ],
                ),
                missing,
            )
        current_by_name = {m.qualified_name: m for m in current}

        prev_keys = set(prev_active.keys())
        current_keys = set(current_by_name.keys())

        file_change = self._change_set.get_file(file_path)
        method_statuses = (
            file_change.classify_method_statuses(current, list(prev_active.values())) if file_change else {}
        )

        return (
            FileDelta(
                file_path=file_path,
                file_status=ChangeStatus.MODIFIED,
                component_id=component_id,
                added_methods=[
                    self._to_method_change(file_path, m, ChangeStatus.ADDED)
                    for m in current
                    if m.qualified_name in current_keys - prev_keys
                ],
                modified_methods=[
                    self._to_method_change(file_path, m, ChangeStatus.MODIFIED)
                    for m in current
                    if m.qualified_name in current_keys & prev_keys
                    and method_statuses.get(m.qualified_name) == ChangeStatus.MODIFIED
                ],
                deleted_methods=[
                    self._to_method_change(file_path, prev_active[qn], ChangeStatus.DELETED)
                    for qn in sorted(prev_keys - current_keys)
                ],
            ),
            missing,
        )

    def compute_delta(self) -> IncrementalDelta:
        """Compute delta from file changes in ``self._change_set``."""
        needs_reanalysis = False
        file_deltas: list[FileDelta] = []

        for file_path in self._change_set.added_files:
            delta, missing = self._compute_file_delta(file_path, ChangeStatus.ADDED, register_file=True)
            file_deltas.append(delta)
            needs_reanalysis = needs_reanalysis or missing

        for file_path in self._change_set.modified_files:
            delta, missing = self._compute_file_delta(file_path, ChangeStatus.MODIFIED, register_file=False)
            file_deltas.append(delta)
            needs_reanalysis = needs_reanalysis or missing

        for file_path in self._change_set.deleted_files:
            delta, missing = self._compute_file_delta(file_path, ChangeStatus.DELETED, register_file=False)
            # A deleted file the prior analysis never tracked is a no-op:
            # there are no methods, no component, and no relations to clean
            # up. Skip it instead of forcing full reanalysis.
            if missing:
                continue
            file_deltas.append(delta)

        return IncrementalDelta(
            file_deltas=file_deltas,
            needs_reanalysis=needs_reanalysis,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )


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
       (``delta.component_id``) OR is an intermediate ancestor of the
       primary (its ID is a descendant of the primary AND it has its own
       children in ``parent_ids``).  Leaf components and siblings that
       don't own the file are unaffected.
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
        # methods).  It must absorb new methods so it stays a superset.
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

    Cascades to descendant sub-analyses and drops relations that reference
    a removed component. Component IDs of survivors are preserved as-is —
    re-numbering would invalidate persisted ``component_id`` references in
    sub-analyses, metadata, and external consumers.

    Returns the set of removed component IDs.
    """
    all_components: list[Component] = list(root.components)
    for sub in sub_analyses.values():
        all_components.extend(sub.components)

    candidate_ids = {c.component_id for c in all_components if c.component_id and _component_is_empty(c)}
    if not candidate_ids:
        return set()

    non_empty_ids = {c.component_id for c in all_components if c.component_id and not _component_is_empty(c)}

    # If a parent component goes empty while a descendant still has methods,
    # the parent - children invariant has drifted. Skip the prune so we don't
    # orphan the descendant; the warning surfaces the drift for investigation.
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
    """Strip ``file_deltas`` whose component was deterministically pruned.

    A pruned component is gone from the analysis tree along with its files and
    relations — there is nothing semantic left for the LLM tracer to patch.
    Leaving the delta in place would force a wasted impact trace and
    description rewrite for components that no longer exist.
    """
    if not removed_ids:
        return
    delta.file_deltas = [fd for fd in delta.file_deltas if fd.component_id not in removed_ids]
