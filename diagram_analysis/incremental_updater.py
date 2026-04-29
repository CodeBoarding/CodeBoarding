"""Incremental analysis updater: file changes -> ``IncrementalDelta``.

Flow:
1. Collect changed files from the monitor.
2. Compare current file symbols with methods stored in analysis.files.
3. Use git diff line ranges to determine per-method statuses (via
   ``ChangeSet.classify_method_statuses`` from the modern parsing layer).
4. Return an ``IncrementalDelta``. Status storage is the caller's responsibility.

Also exposes structural-mutation helpers used by the orchestrator after delta
construction: :func:`apply_method_delta`, :func:`prune_empty_components`,
:func:`drop_deltas_for_pruned_components`.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
)
from agents.change_status import ChangeStatus
from repo_utils.change_detector import ChangeSet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delta types
# ---------------------------------------------------------------------------
@dataclass
class MethodChange:
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    change_type: ChangeStatus
    node_type: str
    old_start_line: int | None = None
    old_end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "change_type": self.change_type.value,
            "node_type": self.node_type,
            "old_start_line": self.old_start_line,
            "old_end_line": self.old_end_line,
        }


@dataclass
class FileDelta:
    file_path: str
    file_status: ChangeStatus
    component_id: str | None = None
    old_file_path: str | None = None
    added_methods: list[MethodChange] = field(default_factory=list)
    modified_methods: list[MethodChange] = field(default_factory=list)
    deleted_methods: list[MethodChange] = field(default_factory=list)
    renamed_qualified_names: dict[str, str] = field(default_factory=dict)
    # Why: produced by the wrapper on revert so the IDE can drop stale overlays.
    reset_methods: list[MethodChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_status": self.file_status.value,
            "component_id": self.component_id,
            "old_file_path": self.old_file_path,
            "added_methods": [m.to_dict() for m in self.added_methods],
            "modified_methods": [m.to_dict() for m in self.modified_methods],
            "deleted_methods": [m.to_dict() for m in self.deleted_methods],
            "renamed_qualified_names": self.renamed_qualified_names,
            "reset_methods": [m.to_dict() for m in self.reset_methods],
        }


@dataclass
class IncrementalDelta:
    file_deltas: list[FileDelta] = field(default_factory=list)
    needs_reanalysis: bool = False
    timestamp: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.file_deltas)

    @property
    def is_purely_additive(self) -> bool:
        """True when all changes are new files/methods only."""
        return all(
            fd.file_status != ChangeStatus.DELETED and not fd.modified_methods and not fd.deleted_methods
            for fd in self.file_deltas
        )

    @property
    def needs_semantic_trace(self) -> bool:
        """True when modifications or additions remain."""
        return any(fd.modified_methods or fd.added_methods for fd in self.file_deltas)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_deltas": [fd.to_dict() for fd in self.file_deltas],
            "needs_reanalysis": self.needs_reanalysis,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Resolver typedefs
# ---------------------------------------------------------------------------
SymbolResolver = Callable[[str], list[MethodEntry]]
MethodStatusLookup = Callable[[str, list[MethodEntry], list[MethodEntry]], dict[str, ChangeStatus]]
ComponentResolver = Callable[[str], str | None]


def resolve_component_id_by_path_prefix(file_path: str, file_to_component: dict[str, str]) -> str | None:
    """Pick the component whose tracked files share the longest path prefix."""
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


# ---------------------------------------------------------------------------
# IncrementalUpdater
# ---------------------------------------------------------------------------
class IncrementalUpdater:
    """Computes file-level incremental deltas from file changes.

    Per-method status defaults to ``ChangeSet.get_file().classify_method_statuses``;
    a custom *method_status_lookup* may override.
    """

    def __init__(
        self,
        analysis: AnalysisInsights,
        symbol_resolver: SymbolResolver,
        repo_dir: Path,
        method_status_lookup: MethodStatusLookup | None = None,
        component_resolver: ComponentResolver | None = None,
    ):
        self.analysis = analysis
        self._symbol_resolver = symbol_resolver
        self._repo_dir = repo_dir
        self._file_to_component = analysis.file_to_component()
        self._component_resolver = component_resolver
        self._method_status_lookup = method_status_lookup

    def _get_current_methods(self, file_path: str) -> list[MethodEntry]:
        return self._symbol_resolver(file_path)

    def _get_previous_methods(self, file_path: str) -> dict[str, MethodEntry]:
        entry = self.analysis.files.get(file_path)
        if entry is None:
            return {}
        return {m.qualified_name: m for m in entry.methods}

    def _resolve_component_id(self, file_path: str, register_file: bool) -> str | None:
        component_id = self._file_to_component.get(file_path)
        if component_id is None and self._component_resolver is not None and register_file:
            component_id = self._component_resolver(file_path)
        if component_id is None and register_file:
            component_id = resolve_component_id_by_path_prefix(file_path, self._file_to_component)
        if component_id is not None and register_file:
            self._file_to_component[file_path] = component_id
        return component_id

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

    def _classify_method_statuses(
        self,
        file_path: str,
        current: list[MethodEntry],
        previous: list[MethodEntry],
        changes: ChangeSet,
    ) -> dict[str, ChangeStatus]:
        if self._method_status_lookup is not None:
            return self._method_status_lookup(file_path, current, previous)
        file_change = changes.get_file(file_path)
        if file_change is None:
            return {}
        return file_change.classify_method_statuses(current, previous)

    def _compute_file_delta(
        self,
        file_path: str,
        file_status: ChangeStatus,
        register_file: bool,
        changes: ChangeSet,
    ) -> tuple[FileDelta, bool]:
        component_id = self._resolve_component_id(file_path, register_file)
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

        method_statuses = self._classify_method_statuses(file_path, current, list(prev_active.values()), changes)

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

    def compute_delta(
        self,
        added_files: list[str],
        modified_files: list[str],
        deleted_files: list[str],
        changes: ChangeSet,
    ) -> IncrementalDelta:
        """Compute delta from explicit file lists.

        *changes* is the ChangeSet used for per-method status classification.
        """
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


# ---------------------------------------------------------------------------
# Structural mutation helpers
# ---------------------------------------------------------------------------
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
