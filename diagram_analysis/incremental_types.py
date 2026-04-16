"""Types for incremental analysis updates."""

from dataclasses import dataclass, field
from typing import Any

from agents.change_status import ChangeStatus


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
            "change_type": self.change_type,
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
    is_reset: bool = False
    reset_methods: list[MethodChange] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "file_path": self.file_path,
            "file_status": self.file_status,
            "component_id": self.component_id,
            "added_methods": [m.to_dict() for m in self.added_methods],
            "modified_methods": [m.to_dict() for m in self.modified_methods],
            "deleted_methods": [m.to_dict() for m in self.deleted_methods],
        }
        if self.is_reset:
            d["is_reset"] = True
            d["reset_methods"] = [m.to_dict() for m in self.reset_methods] if self.reset_methods else []
        return d


@dataclass
class IncrementalDelta:
    file_deltas: list[FileDelta] = field(default_factory=list)
    needs_reanalysis: bool = False
    timestamp: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.file_deltas)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_deltas": [fd.to_dict() for fd in self.file_deltas],
            "needs_reanalysis": self.needs_reanalysis,
            "timestamp": self.timestamp,
        }


@dataclass
class StatusIndex:
    """In-memory status cache owned by the wrapper session.

    Status is never persisted to analysis.json — it lives only in this index
    (wrapper side) and its wire-format twin ``IncrementalDelta`` (sent to the
    extension). Both ``.file_statuses`` and ``.method_statuses`` default to
    ``UNCHANGED`` on miss.
    """

    file_statuses: dict[str, ChangeStatus] = field(default_factory=dict)
    method_statuses: dict[str, ChangeStatus] = field(default_factory=dict)

    @staticmethod
    def method_key(file_path: str, qualified_name: str) -> str:
        return f"{file_path}|{qualified_name}"

    def get_file_status(self, file_path: str) -> ChangeStatus:
        return self.file_statuses.get(file_path, ChangeStatus.UNCHANGED)

    def get_method_status(self, file_path: str, qualified_name: str) -> ChangeStatus:
        return self.method_statuses.get(self.method_key(file_path, qualified_name), ChangeStatus.UNCHANGED)

    def set_file_status(self, file_path: str, status: ChangeStatus) -> None:
        if status == ChangeStatus.UNCHANGED:
            self.file_statuses.pop(file_path, None)
        else:
            self.file_statuses[file_path] = status

    def set_method_status(self, file_path: str, qualified_name: str, status: ChangeStatus) -> None:
        key = self.method_key(file_path, qualified_name)
        if status == ChangeStatus.UNCHANGED:
            self.method_statuses.pop(key, None)
        else:
            self.method_statuses[key] = status

    def clear_file(self, file_path: str) -> None:
        self.file_statuses.pop(file_path, None)
        prefix = f"{file_path}|"
        for key in [k for k in self.method_statuses if k.startswith(prefix)]:
            del self.method_statuses[key]

    def apply_delta(self, delta: "IncrementalDelta") -> None:
        """Merge a delta into this index in place."""
        for fd in delta.file_deltas:
            if fd.is_reset:
                self.clear_file(fd.file_path)
                continue
            if fd.file_status == ChangeStatus.DELETED:
                self.clear_file(fd.file_path)
                self.set_file_status(fd.file_path, ChangeStatus.DELETED)
                for m in fd.deleted_methods:
                    self.set_method_status(fd.file_path, m.qualified_name, ChangeStatus.DELETED)
                continue
            self.set_file_status(fd.file_path, fd.file_status)
            for m in fd.added_methods:
                self.set_method_status(fd.file_path, m.qualified_name, ChangeStatus.ADDED)
            for m in fd.modified_methods:
                self.set_method_status(fd.file_path, m.qualified_name, ChangeStatus.MODIFIED)
            for m in fd.deleted_methods:
                # File-level modified with a deleted method — record the deletion under that file.
                self.set_method_status(fd.file_path, m.qualified_name, ChangeStatus.DELETED)
