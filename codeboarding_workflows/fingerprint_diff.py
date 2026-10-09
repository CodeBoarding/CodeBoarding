"""Git-free change detection via content-hash fingerprints.

The incremental changed-file set from diffing two ``{posix_path: sha16}`` maps:
the whole-tree ``fingerprint.json`` sidecar vs. a fresh fingerprint of the
working tree. Shared by the CLI (default detection) and the wrapper (which passes
a frozen-copy fingerprint), so neither needs git.
"""

import logging
from pathlib import Path

from agents.content_hash import hash_repo_source_files
from diagram_analysis.io_utils import read_fingerprint
from repo_utils.change_detector import ChangeSet

logger = logging.getLogger(__name__)

FileHashMap = dict[str, str]


class BaselineUnavailableError(RuntimeError):
    """Raised when a workflow needs an existing analysis.json baseline but none is usable.

    Covers the cases where incremental/partial cannot trust its starting state: no
    ``analysis.json`` on disk, or a baseline that predates content versioning (no
    whole-tree fingerprint to diff against, so an "empty" change set is
    indistinguishable from "no changes").

    Callers must surface a "run full analysis" prompt rather than silently degrading
    to an unscoped run or an empty-but-successful update.
    """


def diff_file_maps(old: FileHashMap, new: FileHashMap) -> tuple[list[str], list[str], list[str]]:
    """Return ``(added, modified, deleted)`` repo-relative paths between two maps.

    added = in new not old; deleted = in old not new; modified = in both, hash differs.
    """
    old_keys, new_keys = set(old), set(new)
    added = sorted(new_keys - old_keys)
    deleted = sorted(old_keys - new_keys)
    modified = sorted(p for p in old_keys & new_keys if old[p] != new[p])
    return added, modified, deleted


def detect_changes_from_fingerprints(baseline: FileHashMap, current: FileHashMap) -> ChangeSet:
    """Build the incremental ``ChangeSet`` from two fingerprint maps."""
    added, modified, deleted = diff_file_maps(baseline, current)
    logger.info("fingerprint diff: A=%d M=%d D=%d", len(added), len(modified), len(deleted))
    return ChangeSet.from_changed_files(added=added, modified=modified, deleted=deleted)


def detect_changes_from_fingerprint(repo_path: Path, output_dir: Path) -> ChangeSet:
    """Auto-detect the changed-file set without git.

    Requires the whole-tree ``fingerprint.json`` sidecar as the baseline: it is
    the only record that covers every analyzable file, so a new/added or
    unclustered-file change surfaces. Raise ``BaselineUnavailableError`` when the
    sidecar is missing — the component-only hashes in ``analysis.json`` cover
    only clustered files, so an "empty" diff against them is indistinguishable
    from "no changes" (a legacy baseline would silently no-op). The caller must
    prompt for a full run, which rewrites the sidecar.
    """
    sidecar = read_fingerprint(output_dir)
    if sidecar is None:
        raise BaselineUnavailableError(
            f"No whole-tree fingerprint sidecar in '{output_dir}' (baseline predates content versioning "
            "or was never written). Run a full analysis first to seed it."
        )
    current = hash_repo_source_files(repo_path)
    return detect_changes_from_fingerprints(sidecar, current)
