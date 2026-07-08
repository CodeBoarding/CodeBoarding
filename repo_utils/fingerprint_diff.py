"""Git-free change detection via content-hash fingerprints.

The incremental changed-file set from diffing two ``{posix_path: sha16}`` maps:
the baseline hashes recorded in ``analysis.json`` vs. a fresh fingerprint of the
working tree. Shared by the CLI (default detection) and the wrapper (which passes
a frozen-copy fingerprint), so neither needs git.
"""

import json
import logging
from pathlib import Path

from diagram_analysis.analysis_json import hash_repo_source_files
from diagram_analysis.io_utils import ANALYSIS_FILENAME, read_fingerprint
from repo_utils.change_detector import ChangeSet

logger = logging.getLogger(__name__)

FileHashMap = dict[str, str]


def diff_file_maps(old: FileHashMap, new: FileHashMap) -> tuple[list[str], list[str], list[str]]:
    """Return ``(added, modified, deleted)`` repo-relative paths between two maps.

    added = in new not old; deleted = in old not new; modified = in both, hash differs.
    """
    old_keys, new_keys = set(old), set(new)
    added = sorted(new_keys - old_keys)
    deleted = sorted(old_keys - new_keys)
    modified = sorted(p for p in old_keys & new_keys if old[p] != new[p])
    return added, modified, deleted


def read_baseline_file_hashes(output_dir: Path) -> FileHashMap:
    """Per-file ``content_hash`` map from the baseline ``analysis.json`` ``files`` block.

    Empty when the file is absent/malformed or predates content hashing. Covers only
    component-assigned files, so callers restrict the diff domain to these keys.
    """
    path = Path(output_dir) / ANALYSIS_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    result: FileHashMap = {}
    for fp, entry in files.items():
        digest = entry.get("content_hash") if isinstance(entry, dict) else None
        if isinstance(digest, str) and digest:
            # Normalize to posix so the diff matches the working-tree fingerprint
            # (which emits posix keys) regardless of the OS that wrote the baseline.
            result[fp.replace("\\", "/")] = digest
    return result


def detect_changes_from_fingerprints(baseline: FileHashMap, current: FileHashMap) -> ChangeSet:
    """Build the incremental ``ChangeSet`` from two fingerprint maps."""
    added, modified, deleted = diff_file_maps(baseline, current)
    logger.info("fingerprint diff: A=%d M=%d D=%d", len(added), len(modified), len(deleted))
    return ChangeSet.from_changed_files(added=added, modified=modified, deleted=deleted)


def detect_changes_from_fingerprint(repo_path: Path, output_dir: Path) -> ChangeSet:
    """Auto-detect the changed-file set without git.

    Fingerprints the working tree and diffs it against the recorded baseline.
    Prefers the whole-tree ``fingerprint.json`` sidecar; falls back to the
    component-only hashes in ``analysis.json`` (restricting the current map to
    those keys so non-clustered files don't read as spuriously added).
    """
    current = hash_repo_source_files(repo_path)
    sidecar = read_fingerprint(output_dir)
    if sidecar is not None:
        return detect_changes_from_fingerprints(sidecar, current)
    baseline = read_baseline_file_hashes(output_dir)
    scoped = {k: v for k, v in current.items() if k in baseline}
    return detect_changes_from_fingerprints(baseline, scoped)
