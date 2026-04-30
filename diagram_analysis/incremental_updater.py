"""Incremental analysis updater: file changes -> ``IncrementalDelta``.

Flow:
1. Collect changed files from the monitor.
2. Compare current file symbols with methods stored in analysis.files.
3. Use git diff line ranges to determine per-method statuses (via
   ``ChangeSet.classify_method_statuses`` from the modern parsing layer).
4. Return an ``IncrementalDelta``. Status storage is the caller's responsibility.

The structural-mutation side (apply, prune, drop) lives in
``diagram_analysis.delta_application``.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from agents.agent_responses import AnalysisInsights, MethodEntry
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "change_type": self.change_type.value,
            "node_type": self.node_type,
        }


@dataclass
class FileDelta:
    file_path: str
    file_status: ChangeStatus
    component_id: str | None = None
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
