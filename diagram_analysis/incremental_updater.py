"""Incremental analysis updater using a simple file-first model.

Flow:
1. Collect changed files from the monitor.
2. Compare current file symbols with methods stored in analysis.files.
3. Use git diff line ranges to mark methods as modified (via repo_utils.method_diff).
4. Apply updates to the file index, then reflect them in component file lists.

NOTE: Method status assignment is delegated to repo_utils.method_diff to ensure
consistent behavior between the main analysis pipeline and incremental updates.
"""

import time
from pathlib import Path
from typing import Callable

from agents.agent_responses import AnalysisInsights, Component, FileEntry, FileMethodGroup, MethodEntry
from diagram_analysis.incremental_types import FileDelta, IncrementalDelta, MethodChange
from diagram_analysis.manifest import AnalysisManifest
from repo_utils.change_detector import ChangeSet
from repo_utils.method_diff import get_method_statuses_for_file

# Callable that resolves a repo-relative file path to its current MethodEntry list.
SymbolResolver = Callable[[str], list[MethodEntry]]


class IncrementalUpdater:
    """Computes file-level incremental deltas from file changes.

    Delegates method status assignment to repo_utils.method_diff.get_method_statuses_for_file
    to ensure consistent behavior with the main analysis pipeline.
    """

    def __init__(
        self,
        analysis: AnalysisInsights,
        manifest: AnalysisManifest,
        symbol_resolver: SymbolResolver | None = None,
        repo_dir: Path | None = None,
    ):
        self.analysis = analysis
        self.manifest = manifest
        self._symbol_resolver = symbol_resolver
        self._repo_dir = repo_dir

        self._hydrate_file_index()

    def _hydrate_file_index(self) -> None:
        """Ensure analysis.files exists."""
        if not self.analysis.files:
            self.analysis.files = {}

    def _get_current_methods(self, file_path: str) -> list[MethodEntry]:
        if self._symbol_resolver is None:
            return []
        return self._symbol_resolver(file_path)

    def _get_previous_methods(self, file_path: str) -> dict[str, MethodEntry]:
        entry = self.analysis.files.get(file_path)
        if entry is None:
            return {}
        return {m.qualified_name: m for m in entry.methods}

    def _get_previous_active_methods(self, file_path: str) -> dict[str, MethodEntry]:
        previous_methods = self._get_previous_methods(file_path)
        return {qname: method for qname, method in previous_methods.items() if method.status != "deleted"}

    def _resolve_component(self, file_path: str, *, register_file: bool = False) -> tuple[str | None, bool]:
        component_id = self.manifest.get_component_for_file(file_path)
        if component_id is None:
            return None, True

        if register_file:
            self.manifest.add_file(file_path, component_id)

        return component_id, False

    def _apply_method_diff_statuses(
        self, file_path: str, current_methods: list[MethodEntry], changes: ChangeSet
    ) -> None:
        if self._repo_dir is None:
            return
        get_method_statuses_for_file(current_methods, file_path, changes, self._repo_dir)

    @staticmethod
    def _to_method_change(
        file_path: str,
        method: MethodEntry,
        *,
        change_type: str | None = None,
    ) -> MethodChange:
        return MethodChange(
            qualified_name=method.qualified_name,
            file_path=file_path,
            start_line=method.start_line,
            end_line=method.end_line,
            change_type=change_type or method.status,
            node_type=method.node_type,
        )

    def compute_delta(
        self,
        added_files: list[str],
        modified_files: list[str],
        deleted_files: list[str],
        changes: ChangeSet,
    ) -> IncrementalDelta:
        """Compute delta from file changes.

        Uses the canonical get_method_statuses_for_file from repo_utils.method_diff
        to determine method statuses, ensuring consistency with the main pipeline.
        """
        needs_reanalysis = False
        file_deltas: list[FileDelta] = []

        for file_path in added_files:
            current_methods = self._get_current_methods(file_path)
            component_id, missing_component = self._resolve_component(file_path, register_file=True)
            needs_reanalysis = needs_reanalysis or missing_component

            self._apply_method_diff_statuses(file_path, current_methods, changes)
            added = [self._to_method_change(file_path, method) for method in current_methods]
            file_deltas.append(
                FileDelta(
                    file_path=file_path,
                    file_status="added",
                    component_id=component_id,
                    added_methods=added,
                )
            )

        for file_path in modified_files:
            component_id, missing_component = self._resolve_component(file_path)
            needs_reanalysis = needs_reanalysis or missing_component

            prev_active_methods = self._get_previous_active_methods(file_path)
            current_methods = self._get_current_methods(file_path)
            current_by_name = {m.qualified_name: m for m in current_methods}

            prev_keys = set(prev_active_methods.keys())
            current_keys = set(current_by_name.keys())

            # Determine which methods are truly new vs existing
            added_keys = current_keys - prev_keys
            deleted_keys = prev_keys - current_keys
            existing_keys = current_keys & prev_keys

            self._apply_method_diff_statuses(file_path, current_methods, changes)

            added_method_changes = [
                self._to_method_change(file_path, method)
                for method in current_methods
                if method.qualified_name in added_keys
            ]
            modified_method_changes = [
                self._to_method_change(file_path, method, change_type="modified")
                for method in current_methods
                if method.qualified_name in existing_keys and method.status == "modified"
            ]
            deleted_method_changes = [
                self._to_method_change(file_path, prev_active_methods[qname], change_type="deleted")
                for qname in sorted(deleted_keys)
            ]

            file_deltas.append(
                FileDelta(
                    file_path=file_path,
                    file_status="modified",
                    component_id=component_id,
                    added_methods=added_method_changes,
                    modified_methods=modified_method_changes,
                    deleted_methods=deleted_method_changes,
                )
            )

        for file_path in deleted_files:
            component_id, missing_component = self._resolve_component(file_path)
            needs_reanalysis = needs_reanalysis or missing_component

            prev_active_methods = self._get_previous_active_methods(file_path)
            deleted = [
                self._to_method_change(file_path, method, change_type="deleted")
                for _, method in sorted(prev_active_methods.items())
            ]
            file_deltas.append(
                FileDelta(
                    file_path=file_path,
                    file_status="deleted",
                    component_id=component_id,
                    deleted_methods=deleted,
                )
            )

        return IncrementalDelta(
            file_deltas=file_deltas,
            needs_reanalysis=needs_reanalysis,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )


def create_incremental_updater(
    analysis: AnalysisInsights,
    manifest: AnalysisManifest,
    symbol_resolver: SymbolResolver | None = None,
    repo_dir: Path | None = None,
) -> IncrementalUpdater:
    """Factory function to create an IncrementalUpdater."""
    return IncrementalUpdater(
        analysis=analysis,
        manifest=manifest,
        symbol_resolver=symbol_resolver,
        repo_dir=repo_dir,
    )


def _component_lookup(root: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> dict[str, Component]:
    lookup: dict[str, Component] = {}
    for component in root.components:
        lookup[component.component_id] = component
    for sub in sub_analyses.values():
        for component in sub.components:
            lookup[component.component_id] = component
    return lookup


def _ensure_assigned_files(component: Component) -> list[str]:
    assigned = component.assigned_files
    component.assigned_files = sorted(set(assigned))
    return component.assigned_files


def _ensure_file_entry(files: dict[str, FileEntry], file_path: str, file_status: str) -> FileEntry:
    existing = files.get(file_path)
    if existing is None:
        existing = FileEntry(file_status=file_status, methods=[])
        files[file_path] = existing
    return existing


def _apply_method_changes(
    methods_by_name: dict[str, MethodEntry],
    method_changes: list[MethodChange],
    *,
    status_override: str | None = None,
) -> None:
    for method in method_changes:
        methods_by_name[method.qualified_name] = MethodEntry.from_method_change(
            method,
            status_override=status_override,
        )


def _sorted_methods(methods_by_name: dict[str, MethodEntry]) -> list[MethodEntry]:
    return sorted(methods_by_name.values(), key=lambda m: (m.start_line, m.end_line, m.qualified_name))


def _apply_file_delta_to_index(files: dict[str, FileEntry], file_delta: FileDelta) -> None:
    file_path = file_delta.file_path
    existing = _ensure_file_entry(files, file_path, file_delta.file_status)
    existing.file_status = file_delta.file_status
    methods_by_name: dict[str, MethodEntry] = {m.qualified_name: m for m in existing.methods}

    if file_delta.file_status == "deleted":
        _apply_method_changes(methods_by_name, file_delta.deleted_methods, status_override="deleted")

        for method_entry in methods_by_name.values():
            method_entry.status = "deleted"

        existing.methods = _sorted_methods(methods_by_name)
        return

    _apply_method_changes(methods_by_name, file_delta.deleted_methods, status_override="deleted")
    _apply_method_changes(methods_by_name, file_delta.added_methods)
    _apply_method_changes(methods_by_name, file_delta.modified_methods)

    existing.methods = _sorted_methods(methods_by_name)


def _sync_component_methods(component: Component, files: dict[str, FileEntry]) -> None:
    assigned = _ensure_assigned_files(component)
    groups: list[FileMethodGroup] = []
    kept_assigned: list[str] = []

    for file_path in assigned:
        entry = files.get(file_path)
        if entry is None:
            continue

        kept_assigned.append(file_path)
        groups.append(
            FileMethodGroup(
                file_path=file_path,
                file_status=entry.file_status,
                methods=[m.model_copy(deep=True) for m in entry.methods],
            )
        )

    component.assigned_files = kept_assigned
    component.file_methods = groups


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

    for file_delta in delta.file_deltas:
        _apply_file_delta_to_index(files, file_delta)

        component = component_lookup.get(file_delta.component_id or "")
        if component is None:
            continue

        assigned = _ensure_assigned_files(component)
        if file_delta.file_path not in assigned:
            component.assigned_files = sorted(set([*assigned, file_delta.file_path]))

    root.files = files
    for component in root.components:
        _sync_component_methods(component, files)

    for sub in sub_analyses.values():
        sub.files = files
        for component in sub.components:
            _sync_component_methods(component, files)
