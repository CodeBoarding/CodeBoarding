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

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_status": self.file_status,
            "component_id": self.component_id,
            "added_methods": [m.to_dict() for m in self.added_methods],
            "modified_methods": [m.to_dict() for m in self.modified_methods],
            "deleted_methods": [m.to_dict() for m in self.deleted_methods],
        }


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
