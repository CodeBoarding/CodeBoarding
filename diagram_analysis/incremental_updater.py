"""Incremental analysis updater using a simple file-first model.

Flow:
1. Collect changed files from the monitor.
2. Compare current file symbols with methods stored in analysis.files.
3. Use git diff line ranges to mark methods as modified (via repo_utils.method_diff).
4. Apply updates to the file index, then reflect them in component file lists.

NOTE: Method status assignment is delegated to repo_utils.method_diff to ensure
consistent behavior between the main analysis pipeline and incremental updates.
"""

import logging
import time
from pathlib import Path
from typing import Callable

from agents.agent_responses import AnalysisInsights, Component, FileEntry, FileMethodGroup, MethodEntry
from agents.change_status import ChangeStatus
from diagram_analysis.incremental_types import FileDelta, IncrementalDelta, MethodChange
from diagram_analysis.manifest import AnalysisManifest
from repo_utils.change_detector import ChangeSet
from repo_utils.method_diff import get_method_statuses_for_file

logger = logging.getLogger(__name__)

# Callable that resolves a repo-relative file path to its current MethodEntry list.
SymbolResolver = Callable[[str], list[MethodEntry]]
ComponentResolver = Callable[[str, AnalysisManifest], str | None]


class IncrementalUpdater:
    """Computes file-level incremental deltas from file changes.

    Delegates method status assignment to repo_utils.method_diff.get_method_statuses_for_file
    to ensure consistent behavior with the main analysis pipeline.
    """

    def __init__(
        self,
        analysis: AnalysisInsights,
        manifest: AnalysisManifest,
        symbol_resolver: SymbolResolver,
        repo_dir: Path,
        component_resolver: ComponentResolver | None = None,
    ):
        self.analysis = analysis
        self.manifest = manifest
        self._symbol_resolver = symbol_resolver
        self._repo_dir = repo_dir
        self._component_resolver = component_resolver

    def _get_current_methods(self, file_path: str) -> list[MethodEntry]:
        return self._symbol_resolver(file_path)

    def _get_previous_methods(self, file_path: str) -> dict[str, MethodEntry]:
        entry = self.analysis.files.get(file_path)
        if entry is None:
            return {}
        return {m.qualified_name: m for m in entry.methods}

    def _get_previous_active_methods(self, file_path: str) -> dict[str, MethodEntry]:
        return {qn: m for qn, m in self._get_previous_methods(file_path).items() if m.status != ChangeStatus.DELETED}

    def _resolve_component(self, file_path: str, *, register_file: bool = False) -> tuple[str | None, bool]:
        component_id = self.manifest.get_component_for_file(file_path)
        if component_id is not None:
            if register_file:
                self.manifest.add_file(file_path, component_id)
            return component_id, False

        if register_file and self._component_resolver is not None:
            component_id = self._component_resolver(file_path, self.manifest)
            if component_id is not None:
                self.manifest.add_file(file_path, component_id)
                return component_id, False

        return None, True

    def _apply_method_diff_statuses(
        self, file_path: str, current_methods: list[MethodEntry], changes: ChangeSet
    ) -> None:
        get_method_statuses_for_file(current_methods, file_path, changes, self._repo_dir)

    @staticmethod
    def _to_method_change(
        file_path: str,
        method: MethodEntry,
        *,
        change_type: ChangeStatus | None = None,
    ) -> MethodChange:
        return MethodChange(
            qualified_name=method.qualified_name,
            file_path=file_path,
            start_line=method.start_line,
            end_line=method.end_line,
            change_type=change_type or method.status,
            node_type=method.node_type,
        )

    def _compute_file_delta(
        self,
        file_path: str,
        file_status: ChangeStatus,
        register_file: bool,
        changes: ChangeSet,
    ) -> tuple[FileDelta, bool]:
        component_id, missing = self._resolve_component(file_path, register_file=register_file)

        if file_status == ChangeStatus.ADDED:
            current = self._get_current_methods(file_path)
            self._apply_method_diff_statuses(file_path, current, changes)
            return (
                FileDelta(
                    file_path=file_path,
                    file_status=ChangeStatus.ADDED,
                    component_id=component_id,
                    added_methods=[
                        self._to_method_change(file_path, m, change_type=ChangeStatus.ADDED) for m in current
                    ],
                ),
                missing,
            )

        if file_status == ChangeStatus.DELETED:
            prev = self._get_previous_active_methods(file_path)
            return (
                FileDelta(
                    file_path=file_path,
                    file_status=ChangeStatus.DELETED,
                    component_id=component_id,
                    deleted_methods=[
                        self._to_method_change(file_path, m, change_type=ChangeStatus.DELETED)
                        for _, m in sorted(prev.items())
                    ],
                ),
                missing,
            )

        # Modified
        prev_active = self._get_previous_active_methods(file_path)
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
                        self._to_method_change(file_path, m, change_type=ChangeStatus.MODIFIED)
                        for _, m in sorted(prev_active.items())
                    ],
                ),
                missing,
            )
        current_by_name = {m.qualified_name: m for m in current}

        prev_keys = set(prev_active.keys())
        current_keys = set(current_by_name.keys())

        self._apply_method_diff_statuses(file_path, current, changes)

        return (
            FileDelta(
                file_path=file_path,
                file_status=ChangeStatus.MODIFIED,
                component_id=component_id,
                added_methods=[
                    self._to_method_change(file_path, m, change_type=ChangeStatus.ADDED)
                    for m in current
                    if m.qualified_name in current_keys - prev_keys
                ],
                modified_methods=[
                    self._to_method_change(file_path, m, change_type=ChangeStatus.MODIFIED)
                    for m in current
                    if m.qualified_name in current_keys & prev_keys and m.status == ChangeStatus.MODIFIED
                ],
                deleted_methods=[
                    self._to_method_change(file_path, prev_active[qn], change_type=ChangeStatus.DELETED)
                    for qn in sorted(prev_keys - current_keys)
                ],
            ),
            missing,
        )

    def _compute_reset_delta(self, file_path: str) -> tuple[FileDelta, bool]:
        """Compute a reset delta for a file that is now clean relative to HEAD.

        Replaces the stored file state with the current unchanged snapshot,
        removing stale added/deleted/modified statuses from prior incremental updates.
        """
        component_id, missing = self._resolve_component(file_path)
        current = self._get_current_methods(file_path)
        for m in current:
            m.status = ChangeStatus.UNCHANGED

        return (
            FileDelta(
                file_path=file_path,
                file_status=ChangeStatus.UNCHANGED,
                component_id=component_id,
                is_reset=True,
                reset_methods=[
                    self._to_method_change(file_path, m, change_type=ChangeStatus.UNCHANGED) for m in current
                ],
            ),
            missing,
        )

    def compute_delta(
        self,
        added_files: list[str],
        modified_files: list[str],
        deleted_files: list[str],
        changes: ChangeSet,
        reset_files: list[str] | None = None,
    ) -> IncrementalDelta:
        """Compute delta from file changes."""
        needs_reanalysis = False
        file_deltas: list[FileDelta] = []

        for file_path in added_files:
            delta, missing = self._compute_file_delta(file_path, ChangeStatus.ADDED, True, changes)
            file_deltas.append(delta)
            needs_reanalysis = needs_reanalysis or missing

        for file_path in modified_files:
            delta, missing = self._compute_file_delta(file_path, ChangeStatus.MODIFIED, False, changes)
            file_deltas.append(delta)
            needs_reanalysis = needs_reanalysis or missing

        for file_path in deleted_files:
            delta, missing = self._compute_file_delta(file_path, ChangeStatus.DELETED, False, changes)
            file_deltas.append(delta)
            needs_reanalysis = needs_reanalysis or missing

        for file_path in reset_files or []:
            delta, missing = self._compute_reset_delta(file_path)
            file_deltas.append(delta)
            needs_reanalysis = needs_reanalysis or missing

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


def _ensure_file_entry(files: dict[str, FileEntry], file_path: str, file_status: ChangeStatus) -> FileEntry:
    existing = files.get(file_path)
    if existing is None:
        existing = FileEntry(file_status=file_status, methods=[])
        files[file_path] = existing
    return existing


def _apply_method_changes(
    methods_by_name: dict[str, MethodEntry],
    method_changes: list[MethodChange],
    *,
    status_override: ChangeStatus | None = None,
) -> None:
    for method in method_changes:
        methods_by_name[method.qualified_name] = MethodEntry.from_method_change(
            method,
            status_override=status_override,
        )


def _sorted_methods(methods_by_name: dict[str, MethodEntry]) -> list[MethodEntry]:
    return sorted(methods_by_name.values(), key=lambda m: (m.start_line, m.end_line, m.qualified_name))


def _apply_file_delta_to_index(files: dict[str, FileEntry], file_delta: FileDelta) -> None:
    if file_delta.is_reset:
        methods_by_name: dict[str, MethodEntry] = {
            m.qualified_name: MethodEntry.from_method_change(m) for m in (file_delta.reset_methods or [])
        }
        files[file_delta.file_path] = FileEntry(
            file_status=file_delta.file_status,
            methods=_sorted_methods(methods_by_name),
        )
        return

    existing = _ensure_file_entry(files, file_delta.file_path, file_delta.file_status)
    existing.file_status = file_delta.file_status
    methods_by_name = {m.qualified_name: m for m in existing.methods}

    if file_delta.file_status == ChangeStatus.DELETED:
        _apply_method_changes(methods_by_name, file_delta.deleted_methods, status_override=ChangeStatus.DELETED)
        for method_entry in methods_by_name.values():
            method_entry.status = ChangeStatus.DELETED
        existing.methods = _sorted_methods(methods_by_name)
        return

    _apply_method_changes(methods_by_name, file_delta.deleted_methods, status_override=ChangeStatus.DELETED)
    _apply_method_changes(methods_by_name, file_delta.added_methods)
    _apply_method_changes(methods_by_name, file_delta.modified_methods)
    existing.methods = _sorted_methods(methods_by_name)


def _sync_component_methods(
    component: Component,
    files: dict[str, FileEntry],
    deltas_by_path: dict[str, FileDelta],
) -> None:
    """Rebuild component's file_methods, preserving method boundaries between components.

    Strategy:
    1. Collect the qualified names this component originally owned.
    2. For files with a delta, only add newly added methods when the delta
       targets this exact component (``is_primary``).  Child / sibling
       components that merely share the file keep only their originally-owned
       methods so that cluster-level boundaries established during full
       analysis are preserved.
    3. Deleted methods are removed from ``owned_qnames`` so they disappear
       from every component that previously owned them.
    4. Modified methods that the component already owns stay owned; modified
       methods belonging to a sibling component are NOT absorbed.
    5. Filter the global files index to only include methods this component owns.
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

        # Remove deleted methods from this component's ownership.
        for method in delta.deleted_methods:
            owned_qnames.discard(method.qualified_name)

        # Only the component that the manifest mapped the file to (the
        # "primary owner") should absorb newly added methods.  Child /
        # sibling components keep only the methods they already owned so
        # that the cluster-level boundaries from the full analysis are
        # not destroyed.
        is_primary = (delta.component_id or "") == component.component_id

        if is_primary:
            for method in delta.added_methods:
                owned_qnames.add(method.qualified_name)

    component.file_methods = [
        FileMethodGroup(
            file_path=fp,
            file_status=entry.file_status if entry else "unchanged",
            methods=[
                m.model_copy(deep=True) for m in (entry.methods if entry else []) if m.qualified_name in owned_qnames
            ],
        )
        for fp in sorted({g.file_path for g in component.file_methods})
        if (entry := files.get(fp)) is not None
    ]


def apply_delta(
    root: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    delta: IncrementalDelta,
) -> None:
    """Apply IncrementalDelta to analysis structures, mutating in place."""
    if not delta.has_changes:
        return

    files = dict(root.files)
    component_lookup = _component_lookup(root, sub_analyses)

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
        _sync_component_methods(component, files, deltas_by_path)

    for sub in sub_analyses.values():
        sub.files = files
        for component in sub.components:
            _sync_component_methods(component, files, deltas_by_path)
