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
from repo_utils.change_detector import ChangeSet, ChangeType, DetectedChange
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

    def _build_change_set(
        self,
        added_files: list[str],
        modified_files: list[str],
        deleted_files: list[str],
    ) -> ChangeSet:
        """Build a ChangeSet from file lists for use with method_diff functions."""
        changes: list[DetectedChange] = []
        for fp in added_files:
            changes.append(DetectedChange(change_type=ChangeType.ADDED, file_path=fp))
        for fp in modified_files:
            changes.append(DetectedChange(change_type=ChangeType.MODIFIED, file_path=fp))
        for fp in deleted_files:
            changes.append(DetectedChange(change_type=ChangeType.DELETED, file_path=fp))

        return ChangeSet(
            changes=changes,
            base_ref=self.manifest.base_commit or "",
            target_ref="",
        )

    def compute_delta(
        self,
        added_files: list[str],
        modified_files: list[str],
        deleted_files: list[str],
    ) -> IncrementalDelta:
        """Compute delta from file changes.

        Uses the canonical get_method_statuses_for_file from repo_utils.method_diff
        to determine method statuses, ensuring consistency with the main pipeline.
        """
        needs_reanalysis = False
        file_deltas: list[FileDelta] = []

        # Build a ChangeSet for use with the canonical method_diff logic
        change_set = self._build_change_set(added_files, modified_files, deleted_files)

        for file_path in added_files:
            current_methods = self._get_current_methods(file_path)
            component_id = self.manifest.get_component_for_file(file_path)
            if component_id is None:
                needs_reanalysis = True
            else:
                self.manifest.add_file(file_path, component_id)

            # Use canonical method_diff logic to assign statuses
            # For added files, all methods get status="added"
            if self._repo_dir is not None:
                get_method_statuses_for_file(current_methods, file_path, change_set, self._repo_dir)

            added = [
                MethodChange(
                    qualified_name=m.qualified_name,
                    file_path=file_path,
                    start_line=m.start_line,
                    end_line=m.end_line,
                    change_type=m.status,  # Use the status assigned by method_diff
                    node_type=m.node_type,
                )
                for m in current_methods
            ]
            file_deltas.append(
                FileDelta(
                    file_path=file_path,
                    file_status="added",
                    component_id=component_id,
                    added_methods=added,
                )
            )

        for file_path in modified_files:
            component_id = self.manifest.get_component_for_file(file_path)
            if component_id is None:
                needs_reanalysis = True

            prev_methods = self._get_previous_methods(file_path)
            current_methods = self._get_current_methods(file_path)
            current_by_name = {m.qualified_name: m for m in current_methods}

            prev_keys = set(prev_methods.keys())
            current_keys = set(current_by_name.keys())

            # Determine which methods are truly new vs existing
            added_keys = current_keys - prev_keys
            deleted_keys = prev_keys - current_keys
            existing_keys = current_keys & prev_keys

            # Use canonical method_diff logic to assign statuses to ALL current methods
            # This correctly determines which methods in "modified" file are actually
            # modified (their lines overlap git diff ranges) vs unchanged
            if self._repo_dir is not None:
                get_method_statuses_for_file(current_methods, file_path, change_set, self._repo_dir)

            # Build MethodChange lists based on the canonical statuses
            added_method_changes: list[MethodChange] = []
            modified_method_changes: list[MethodChange] = []

            for m in current_methods:
                if m.qualified_name in added_keys:
                    # Truly new method - status should be "added" or "modified" based on diff overlap
                    added_method_changes.append(
                        MethodChange(
                            qualified_name=m.qualified_name,
                            file_path=file_path,
                            start_line=m.start_line,
                            end_line=m.end_line,
                            change_type=m.status,
                            node_type=m.node_type,
                        )
                    )
                elif m.qualified_name in existing_keys:
                    # Existing method - only include if actually modified
                    if m.status == "modified":
                        modified_method_changes.append(
                            MethodChange(
                                qualified_name=m.qualified_name,
                                file_path=file_path,
                                start_line=m.start_line,
                                end_line=m.end_line,
                                change_type="modified",
                                node_type=m.node_type,
                            )
                        )

            deleted_method_changes = [
                MethodChange(
                    qualified_name=qname,
                    file_path=file_path,
                    start_line=prev_methods[qname].start_line,
                    end_line=prev_methods[qname].end_line,
                    change_type="deleted",
                    node_type=prev_methods[qname].node_type,
                )
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
            component_id = self.manifest.get_component_for_file(file_path)
            if component_id is None:
                needs_reanalysis = True
            self.manifest.remove_file(file_path)

            prev_methods = self._get_previous_methods(file_path)
            deleted = [
                MethodChange(
                    qualified_name=qname,
                    file_path=file_path,
                    start_line=method.start_line,
                    end_line=method.end_line,
                    change_type="deleted",
                    node_type=method.node_type,
                )
                for qname, method in sorted(prev_methods.items())
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


def _apply_file_delta_to_index(files: dict[str, FileEntry], file_delta: FileDelta) -> None:
    file_path = file_delta.file_path

    if file_delta.file_status == "deleted":
        files.pop(file_path, None)
        return

    existing = files.get(file_path)
    if existing is None:
        existing = FileEntry(file_status=file_delta.file_status, methods=[])
        files[file_path] = existing

    existing.file_status = file_delta.file_status
    by_name: dict[str, MethodEntry] = {m.qualified_name: m for m in existing.methods}

    for method in file_delta.deleted_methods:
        by_name.pop(method.qualified_name, None)

    for method in file_delta.added_methods:
        by_name[method.qualified_name] = MethodEntry(
            qualified_name=method.qualified_name,
            start_line=method.start_line,
            end_line=method.end_line,
            node_type=method.node_type,
            status=method.change_type,  # Use the status computed by method_diff
        )

    for method in file_delta.modified_methods:
        by_name[method.qualified_name] = MethodEntry(
            qualified_name=method.qualified_name,
            start_line=method.start_line,
            end_line=method.end_line,
            node_type=method.node_type,
            status=method.change_type,  # Use the status computed by method_diff
        )

    existing.methods = sorted(by_name.values(), key=lambda m: (m.start_line, m.end_line, m.qualified_name))


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
        if file_delta.file_status == "deleted":
            component.assigned_files = [fp for fp in assigned if fp != file_delta.file_path]
        elif file_delta.file_path not in assigned:
            component.assigned_files = sorted(set([*assigned, file_delta.file_path]))

    root.files = files
    for component in root.components:
        _sync_component_methods(component, files)

    for sub in sub_analyses.values():
        sub.files = files
        for component in sub.components:
            _sync_component_methods(component, files)
