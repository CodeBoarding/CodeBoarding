"""Last-run metadata used by standalone incremental analysis."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_utils import get_repo_state_hash
from repo_utils.change_detector import get_current_commit

METADATA_FILENAME = "incremental_run_metadata.json"


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


def worktree_has_changes(repo_dir: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", ".", ":!.codeboarding"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def write_last_run_metadata(
    output_dir: Path,
    repo_dir: Path,
    *,
    mode: str,
    analysis_path: Path | str | None,
    commit_hash: str | None = None,
    source_identity: str | None = None,
    diff_base_ref: str | None = None,
    use_source_as_diff_base: bool | None = None,
) -> dict[str, Any]:
    """Persist the source identity that produced the latest successful analysis."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = Path(repo_dir)
    current_commit = get_current_commit(repo_dir)

    if commit_hash is None and source_identity is None and diff_base_ref is None:
        if worktree_has_changes(repo_dir):
            source_identity = get_repo_state_hash(repo_dir)
            diff_base_ref = None
            use_source_as_diff_base = False
        else:
            source_identity = current_commit
            diff_base_ref = current_commit
            use_source_as_diff_base = True

    resolved_source_identity = source_identity or commit_hash or current_commit
    resolved_diff_base_ref = diff_base_ref
    if resolved_diff_base_ref is None and use_source_as_diff_base:
        resolved_diff_base_ref = resolved_source_identity

    payload: dict[str, Any] = {
        "lastSuccessfulRun": {
            "commit": commit_hash or current_commit,
            "sourceIdentity": resolved_source_identity,
            "diffBaseRef": resolved_diff_base_ref,
            "analysisPath": None if analysis_path is None else str(analysis_path),
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
        }
    }
    metadata_path(output_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def last_successful_commit(output_dir: Path) -> str | None:
    payload = load_last_run_metadata(output_dir)
    if payload is None:
        analysis_path = Path(output_dir) / "analysis.json"
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
