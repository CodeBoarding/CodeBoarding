"""Persistent component-ID allocation state."""

from dataclasses import dataclass, field
import json
from pathlib import Path

from diagram_analysis.io_utils import write_text_atomic

INCREMENTAL_STATE_FILENAME = "incremental_analysis_state.json"


@dataclass
class IncrementalIdState:
    next_child_indexes: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, output_dir: Path) -> "IncrementalIdState":
        path = output_dir / INCREMENTAL_STATE_FILENAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        raw = data.get("next_child_indexes", {}) if isinstance(data, dict) else {}
        return cls({str(parent): int(index) for parent, index in raw.items() if int(index) > 0})

    def save(self, output_dir: Path) -> None:
        payload = json.dumps({"next_child_indexes": self.next_child_indexes}, indent=2, sort_keys=True)
        write_text_atomic(output_dir / INCREMENTAL_STATE_FILENAME, payload)

    def allocate(self, parent_id: str, sibling_ids: set[str]) -> str:
        prefix = f"{parent_id}." if parent_id else ""
        existing_indexes = {
            int(component_id.removeprefix(prefix))
            for component_id in sibling_ids
            if component_id.startswith(prefix) and component_id.removeprefix(prefix).isdigit()
        }
        next_index = max(self.next_child_indexes.get(parent_id, 1), max(existing_indexes, default=0) + 1)
        self.next_child_indexes[parent_id] = next_index + 1
        return f"{prefix}{next_index}"
