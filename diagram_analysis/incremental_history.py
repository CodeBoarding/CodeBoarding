from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from diagram_analysis.incremental_models import IncrementalRunStats, IncrementalSummary

logger = logging.getLogger(__name__)

INCREMENTAL_HISTORY_FILENAME = "incremental_history.jsonl"


def incremental_history_path(output_dir: Path) -> Path:
    return output_dir / INCREMENTAL_HISTORY_FILENAME


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class IncrementalHistoryEvent:
    version: int
    event_id: str
    timestamp: str
    run_id: str
    event_type: str
    status: str
    message: str
    project_name: str
    repo_commit: str | None = None
    baseline_checkpoint_id: str | None = None
    result_checkpoint_id: str | None = None
    summary: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "eventId": self.event_id,
            "timestamp": self.timestamp,
            "runId": self.run_id,
            "eventType": self.event_type,
            "status": self.status,
            "message": self.message,
            "projectName": self.project_name,
        }
        if self.repo_commit is not None:
            payload["repoCommit"] = self.repo_commit
        if self.baseline_checkpoint_id is not None:
            payload["baselineCheckpointId"] = self.baseline_checkpoint_id
        if self.result_checkpoint_id is not None:
            payload["resultCheckpointId"] = self.result_checkpoint_id
        if self.summary is not None:
            payload["summary"] = self.summary
        if self.stats is not None:
            payload["stats"] = self.stats
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IncrementalHistoryEvent":
        return cls(
            version=int(payload.get("version", 1)),
            event_id=str(payload["eventId"]),
            timestamp=str(payload["timestamp"]),
            run_id=str(payload["runId"]),
            event_type=str(payload["eventType"]),
            status=str(payload["status"]),
            message=str(payload["message"]),
            project_name=str(payload["projectName"]),
            repo_commit=payload.get("repoCommit"),
            baseline_checkpoint_id=payload.get("baselineCheckpointId"),
            result_checkpoint_id=payload.get("resultCheckpointId"),
            summary=payload.get("summary"),
            stats=payload.get("stats"),
        )


def build_incremental_history_event(
    *,
    run_id: str,
    event_type: str,
    status: str,
    message: str,
    project_name: str,
    summary: IncrementalSummary | None = None,
    stats: IncrementalRunStats | None = None,
    timestamp: str | None = None,
) -> IncrementalHistoryEvent:
    event_timestamp = timestamp or _utc_timestamp()
    return IncrementalHistoryEvent(
        version=1,
        event_id=f"{event_timestamp}_{uuid.uuid4().hex[:10]}",
        timestamp=event_timestamp,
        run_id=run_id,
        event_type=event_type,
        status=status,
        message=message,
        project_name=project_name,
        repo_commit=stats.repo_commit if stats is not None else None,
        baseline_checkpoint_id=stats.baseline_checkpoint_id if stats is not None else None,
        result_checkpoint_id=stats.result_checkpoint_id if stats is not None else None,
        summary=summary.to_dict() if summary is not None else None,
        stats=stats.to_dict() if stats is not None else None,
    )


def append_incremental_history_event(output_dir: Path, event: IncrementalHistoryEvent) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    history_file = incremental_history_path(output_dir)
    with open(history_file, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.to_dict(), sort_keys=True))
        handle.write("\n")
    return history_file


def load_incremental_history(output_dir: Path) -> list[IncrementalHistoryEvent]:
    history_file = incremental_history_path(output_dir)
    if not history_file.is_file():
        return []

    events: list[IncrementalHistoryEvent] = []
    with open(history_file, "r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("history line is not a JSON object")
                events.append(IncrementalHistoryEvent.from_dict(payload))
            except (ValueError, json.JSONDecodeError, KeyError) as exc:
                logger.warning("Skipping invalid incremental history line %s in %s: %s", line_number, history_file, exc)
    return events
