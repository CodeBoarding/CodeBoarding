"""File content hashing utilities for iterative analysis.

This module provides functions to compute normalized content hashes
for source files, enabling detection of meaningful changes while
ignoring whitespace-only or formatting-only modifications.
"""

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def normalize_content(content: str) -> str:
    """Normalize file content to ignore cosmetic differences.

    Normalizations applied:
    1. Convert all line endings to Unix-style (LF)
    2. Strip trailing whitespace from each line
    3. Remove trailing blank lines
    4. Normalize multiple consecutive blank lines to single blank line

    Args:
        content: Raw file content

    Returns:
        Normalized content string
    """
    # Normalize line endings
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    # Strip trailing whitespace from each line
    lines = [line.rstrip() for line in content.split("\n")]

    # Remove trailing blank lines
    while lines and not lines[-1]:
        lines.pop()

    # Normalize multiple consecutive blank lines to single
    normalized_lines = []
    prev_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank:
            if not prev_blank:
                normalized_lines.append(line)
            prev_blank = True
        else:
            normalized_lines.append(line)
            prev_blank = False

    return "\n".join(normalized_lines)


def compute_hash(content: str, normalize: bool = True) -> str:
    """Compute SHA256 hash of file content.

    Args:
        content: File content as string
        normalize: If True, normalize content before hashing (default True)

    Returns:
        Hexadecimal SHA256 hash string
    """
    if normalize:
        content = normalize_content(content)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_file_hash(file_path: Path, normalize: bool = True) -> str | None:
    """Compute SHA256 hash of a file.

    Args:
        file_path: Path to the file
        normalize: If True, normalize content before hashing (default True)

    Returns:
        Hexadecimal SHA256 hash string, or None if file cannot be read
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return compute_hash(content, normalize)
    except Exception as e:
        logger.warning(f"Failed to hash file {file_path}: {e}")
        return None


def compute_hashes_for_files(file_paths: list[Path], normalize: bool = True) -> dict[str, str]:
    """Compute hashes for multiple files.

    Args:
        file_paths: List of file paths to hash
        normalize: If True, normalize content before hashing (default True)

    Returns:
        Dictionary mapping file path (as string) to hash
    """
    hashes = {}
    for file_path in file_paths:
        file_hash = compute_file_hash(file_path, normalize)
        if file_hash is not None:
            hashes[str(file_path)] = file_hash
    return hashes


def detect_moves(
    deleted_files: list[str],
    added_files: list[str],
    old_hashes: dict[str, str],
    new_hashes: dict[str, str],
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Detect file moves/renames by comparing content hashes.

    A file is considered "moved" if:
    1. Its content hash matches between a deleted and added file
    2. The file extension is the same

    Args:
        deleted_files: List of deleted file paths
        added_files: List of added file paths
        old_hashes: Hash cache for old files
        new_hashes: Hash cache for new files

    Returns:
        Tuple of:
        - moves: List of (old_path, new_path) tuples
        - unmatched_deleted: Deleted files that weren't moved
        - unmatched_added: Added files that weren't moved
    """
    moves = []
    unmatched_deleted = deleted_files.copy()
    unmatched_added = added_files.copy()

    # Build hash -> deleted_path lookup (only for files with known hashes)
    deleted_by_hash: dict[str, str] = {}
    for deleted_path in deleted_files:
        if deleted_path in old_hashes:
            old_hash = old_hashes[deleted_path]
            # Store first occurrence (in case of duplicates)
            if old_hash not in deleted_by_hash:
                deleted_by_hash[old_hash] = deleted_path

    # Try to match added files to deleted files by hash
    for added_path in added_files:
        if added_path not in new_hashes:
            continue

        new_hash = new_hashes[added_path]
        if new_hash in deleted_by_hash:
            old_path = deleted_by_hash[new_hash]

            # Verify same extension
            old_ext = Path(old_path).suffix.lower()
            new_ext = Path(added_path).suffix.lower()

            if old_ext == new_ext:
                moves.append((old_path, added_path))
                if old_path in unmatched_deleted:
                    unmatched_deleted.remove(old_path)
                if added_path in unmatched_added:
                    unmatched_added.remove(added_path)
                # Remove from lookup to prevent double-matching
                del deleted_by_hash[new_hash]

    return moves, unmatched_deleted, unmatched_added


def compute_similarity_hash(content: str) -> str:
    """Compute a similarity-preserving hash for fuzzy matching.

    This creates a hash based on the structural content of the file,
    ignoring comments and string literals. Useful for detecting
    files that have been moved AND slightly modified.

    Args:
        content: File content

    Returns:
        Similarity hash string
    """
    # Remove single-line comments (// and #)
    content = re.sub(r"(//|#).*$", "", content, flags=re.MULTILINE)

    # Remove multi-line comments (/* */ and ''' ''' and \"\"\" \"\"\")
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"'''.*?'''", "", content, flags=re.DOTALL)
    content = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)

    # Remove string literals
    content = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', content)
    content = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", content)

    # Normalize whitespace
    content = normalize_content(content)

    # Remove all remaining whitespace for structural comparison
    content = re.sub(r"\s+", "", content)

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
