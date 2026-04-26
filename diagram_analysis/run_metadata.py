"""Last-run metadata used by standalone incremental analysis."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from repo_utils import get_repo_state_hash
from repo_utils.git_ops import get_current_commit, worktree_has_changes
from utils import ANALYSIS_FILENAME, CODEBOARDING_DIR_NAME

METADATA_FILENAME = "incremental_run_metadata.json"


class RunMode(StrEnum):
    """Mode written into ``lastSuccessfulRun.mode`` and wire payloads.

    Only scopes that persist metadata are represented. ``partial`` doesn't
    write metadata today, so there is no ``PARTIAL`` value.
    """

    FULL = "full"
    INCREMENTAL = "incremental"


def metadata_path(output_dir: Path) -> Path:
    return Path(output_dir) / METADATA_FILENAME


def load_last_run_metadata(output_dir: Path) -> dict[str, Any] | None:
    path = metadata_path(output_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _build_payload(
    *,
    mode: RunMode,
    commit: str | None,
    source_identity: str | None,
    diff_base_ref: str | None,
    analysis_path: Path,
) -> dict[str, Any]:
    """Build the wire-format metadata payload.

    The shape is identical for both modes — readers (``last_successful_commit``)
    branch on ``diffBaseRef`` presence/value, not on ``mode``. ``mode`` is
    informational on the wire.
    """
    return {
        "lastSuccessfulRun": {
            "commit": commit,
            "sourceIdentity": source_identity,
            "diffBaseRef": diff_base_ref,
            "analysisPath": str(analysis_path),  # Path -> str at the JSON boundary
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
        }
    }


def _write_metadata(output_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Serialize *payload* to ``METADATA_FILENAME`` under *output_dir*."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path(output_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def write_full_run_metadata(
    output_dir: Path,
    repo_dir: Path,
    *,
    analysis_path: Path,
) -> dict[str, Any]:
    """Persist metadata after a successful FULL analysis."""
    repo_dir = Path(repo_dir)
    current_commit = get_current_commit(repo_dir)

    if worktree_has_changes(repo_dir, exclude_patterns=(CODEBOARDING_DIR_NAME,)):
        source_identity: str | None = get_repo_state_hash(repo_dir)
        diff_base_ref: str | None = None
    else:
        source_identity = current_commit
        diff_base_ref = current_commit

    payload = _build_payload(
        mode=RunMode.FULL,
        commit=current_commit,
        source_identity=source_identity,
        diff_base_ref=diff_base_ref,
        analysis_path=analysis_path,
    )
    return _write_metadata(Path(output_dir), payload)


def write_incremental_run_metadata(
    output_dir: Path,
    repo_dir: Path,
    *,
    analysis_path: Path,
    source_identity: str,
    diff_base_ref: str | None,
) -> dict[str, Any]:
    """Persist metadata after a successful INCREMENTAL analysis."""
    repo_dir = Path(repo_dir)
    payload = _build_payload(
        mode=RunMode.INCREMENTAL,
        commit=get_current_commit(repo_dir),
        source_identity=source_identity,
        diff_base_ref=diff_base_ref,
        analysis_path=analysis_path,
    )
    return _write_metadata(Path(output_dir), payload)


def last_successful_commit(output_dir: Path) -> str | None:
    payload = load_last_run_metadata(output_dir)
    if payload is None:
        analysis_path = Path(output_dir) / ANALYSIS_FILENAME
        if not analysis_path.exists():
            return None
        try:
            payload = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    last_run = payload.get("lastSuccessfulRun")
    if not isinstance(last_run, dict):
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return None
        commit = metadata.get("commit_hash")
        return commit if isinstance(commit, str) and commit else None
    if "diffBaseRef" in last_run:
        diff_base_ref = last_run.get("diffBaseRef")
        return diff_base_ref if isinstance(diff_base_ref, str) and diff_base_ref else None
    commit = last_run.get("commit")
    return commit if isinstance(commit, str) and commit else None
