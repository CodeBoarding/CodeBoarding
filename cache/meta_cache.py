from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from cache._paths import get_meta_cache_db_path
from cache._sqlite_store import SQLiteCacheStore
from repo_utils.ignore import RepoIgnoreManager
from repo_utils.project_manifests import (
    COMMON_DEPENDENCY_FILES,
    COMMON_DEPENDENCY_GLOBS,
    COMMON_DEPENDENCY_SUBDIRS,
)
from utils import safe_read_text, sha256_hexdigest

logger = logging.getLogger(__name__)

META_CACHE_TABLE_NAME = "meta_cache"
DOCS_MANIFEST_SCHEMA_VERSION = 5
DOC_EXTENSIONS = {".md", ".rst", ".html"}
META_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class MetaCacheIdentity:
    """
    Snapshot of metadata inputs used to decide whether meta-analysis can be reused.

    Identity keys:
    - scope: isolates cache by repository path.
    - model_id: guarantees cache does not cross LLM model changes.
    - prompt_version: guarantees cache does not cross prompt-template changes.
    - deps_hash/tree_hash: strict invalidation for dependency/layout changes.
    - docs_manifest: strict per-file docs digest.
    """

    scope: str
    deps_hash: str
    tree_hash: str
    model_id: str
    prompt_version: str
    docs_manifest: dict[str, object]

    @property
    def cache_key(self) -> str:
        """Return the cache key for this snapshot."""
        docs_fingerprint = sha256_hexdigest(
            json.dumps(self.docs_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
        raw = "|".join(
            [
                self.scope,
                self.deps_hash,
                self.tree_hash,
                self.model_id,
                self.prompt_version,
                docs_fingerprint,
            ]
        )
        return sha256_hexdigest(raw)

    @classmethod
    def from_repo(
        cls,
        repo_dir: Path,
        agent_llm: BaseChatModel,
        ignore_manager: RepoIgnoreManager,
        prompt_version: str,
    ) -> "MetaCacheIdentity":
        """Build a metadata snapshot from repository and LLM context."""
        repo_root = repo_dir.resolve()
        all_files = _collect_all_repo_files(repo_root, ignore_manager)
        doc_paths = _collect_doc_paths(repo_root, all_files)
        docs_manifest = _build_docs_manifest(repo_root, doc_paths)
        return cls(
            scope=str(repo_root),
            deps_hash=_hash_dependency_inputs(repo_root, ignore_manager),
            tree_hash=_hash_repo_tree(repo_root, all_files),
            model_id=_normalize_model_id(agent_llm),
            prompt_version=prompt_version,
            docs_manifest=docs_manifest,
        )

    def matches_cached_metadata(
        self,
        cached_meta: dict[str, object],
    ) -> bool:
        """Return whether cached metadata can be safely reused."""
        if cached_meta.get("model_id") != self.model_id:
            return False
        if cached_meta.get("prompt_version") != self.prompt_version:
            return False
        if cached_meta.get("deps_hash") != self.deps_hash:
            return False
        if cached_meta.get("tree_hash") != self.tree_hash:
            return False

        match_result = _docs_manifest_match(
            cached_meta.get("docs_manifest"),
            self.docs_manifest,
        )
        if match_result is None:
            logger.debug("Docs digest mismatch: signature_version_mismatch_or_invalid")
            return False
        return match_result == 1.0

    def to_cache_metadata(self) -> dict[str, object]:
        """Build metadata payload stored with cached meta analysis output."""
        return {
            "deps_hash": self.deps_hash,
            "tree_hash": self.tree_hash,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "docs_manifest_schema_version": DOCS_MANIFEST_SCHEMA_VERSION,
            "docs_manifest": self.docs_manifest,
        }


def _normalize_model_id(agent_llm: BaseChatModel) -> str:
    """Extract a stable model identifier from the configured LLM."""
    for attr in ("model_name", "model", "model_id"):
        value = getattr(agent_llm, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(agent_llm).__name__


def _collect_all_repo_files(repo_dir: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
    """Walk the repository once and return all non-ignored file paths."""
    result: list[Path] = []
    for path in repo_dir.rglob("*"):
        if ignore_manager.should_ignore(path):
            continue
        if path.is_file():
            result.append(path)
    return result


def _collect_dependency_paths(repo_dir: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
    """Collect dependency-related files used for cache invalidation."""
    found: list[Path] = []
    for dep_file in COMMON_DEPENDENCY_FILES:
        candidate = repo_dir / dep_file
        if candidate.exists() and candidate.is_file() and not ignore_manager.should_ignore(candidate):
            found.append(candidate)

    for subdir in COMMON_DEPENDENCY_SUBDIRS:
        subdir_path = repo_dir / subdir
        if not subdir_path.exists() or not subdir_path.is_dir() or ignore_manager.should_ignore(subdir_path):
            continue
        for pattern in COMMON_DEPENDENCY_GLOBS:
            for file_path in subdir_path.glob(pattern):
                if file_path.is_file() and not ignore_manager.should_ignore(file_path):
                    found.append(file_path)

    return sorted(set(found))


def _hash_dependency_inputs(repo_dir: Path, ignore_manager: RepoIgnoreManager) -> str:
    """Compute a content hash across discovered dependency files."""
    payload_parts: list[str] = []
    for dep_path in _collect_dependency_paths(repo_dir, ignore_manager):
        rel = dep_path.relative_to(repo_dir).as_posix()
        payload_parts.append(f"{rel}\n{safe_read_text(dep_path)}")
    return sha256_hexdigest("\n---\n".join(payload_parts))


def _hash_repo_tree(repo_dir: Path, all_files: list[Path]) -> str:
    """Compute a hash of the repository file tree from pre-collected files."""
    rel_paths = sorted(path.relative_to(repo_dir).as_posix() for path in all_files)
    return sha256_hexdigest("\n".join(rel_paths))


def _collect_doc_paths(repo_dir: Path, all_files: list[Path]) -> list[Path]:
    """Filter documentation files from pre-collected file list."""
    docs: list[Path] = []
    for path in all_files:
        if path.suffix.lower() not in DOC_EXTENSIONS:
            continue
        rel_path = path.relative_to(repo_dir)
        lower_parts = {part.lower() for part in rel_path.parts}
        if "tests" in lower_parts or "test" in rel_path.name.lower():
            continue
        docs.append(path)
    docs.sort(key=lambda p: p.relative_to(repo_dir).as_posix())
    return docs


def _build_docs_manifest(repo_dir: Path, doc_paths: list[Path]) -> dict[str, object]:
    """Build strict per-file documentation signature metadata."""
    file_hashes: dict[str, str] = {}
    priority_files: list[str] = []
    for doc in doc_paths:
        rel = doc.relative_to(repo_dir).as_posix()
        text = safe_read_text(doc)
        file_hashes[rel] = sha256_hexdigest(text)

        rel_lower = rel.lower()
        name = Path(rel_lower).name
        parts = Path(rel_lower).parts
        in_root = len(parts) == 1
        in_docs_dir = len(parts) >= 2 and parts[0] == "docs"
        is_priority_name = name.startswith(("readme", "contributing", "architecture"))
        is_docs_index = in_docs_dir and name.startswith("index.")
        is_priority = (is_priority_name and (in_root or in_docs_dir)) or is_docs_index

        if is_priority:
            priority_files.append(rel)

    priority_files.sort()

    logger.debug("Computed docs digest for %s files in %s", len(file_hashes), repo_dir)
    return {
        "v": DOCS_MANIFEST_SCHEMA_VERSION,
        "files": file_hashes,
        "priority_files": priority_files,
        "doc_extensions": sorted(DOC_EXTENSIONS),
    }


def _normalize_rel_path(path: str) -> str:
    """Normalize a documentation relative path for stable comparisons."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _coerce_digest(signature: object) -> dict[str, str] | None:
    """Validate and normalize a docs signature into path-to-hash mapping."""
    if not isinstance(signature, dict):
        return None
    version = signature.get("v")
    if not isinstance(version, int):
        return None
    if version != DOCS_MANIFEST_SCHEMA_VERSION:
        return None

    files = signature.get("files")
    if not isinstance(files, dict):
        return None

    normalized: dict[str, str] = {}
    for rel_path, file_hash in files.items():
        if not isinstance(rel_path, str) or not isinstance(file_hash, str):
            return None
        norm_path = _normalize_rel_path(rel_path)
        existing = normalized.get(norm_path)
        if existing is not None and existing != file_hash:
            return None
        normalized[norm_path] = file_hash
    return normalized


def _docs_manifest_match(old_manifest: object, new_manifest: object) -> float | None:
    """Return exact-manifest similarity score or None for invalid manifests."""
    old_digest = _coerce_digest(old_manifest)
    new_digest = _coerce_digest(new_manifest)
    if old_digest is None or new_digest is None:
        return None

    old_paths = set(old_digest)
    new_paths = set(new_digest)
    if old_paths != new_paths:
        logger.debug(
            "Docs digest mismatch: file_set_changed old_count=%s new_count=%s",
            len(old_paths),
            len(new_paths),
        )
        return 0.0

    for rel_path, new_hash in new_digest.items():
        old_hash = old_digest.get(rel_path)
        if old_hash != new_hash:
            logger.debug("Docs digest mismatch: file_hash_changed path=%s", rel_path)
            return 0.0

    logger.debug("Docs digest compatible: files_unchanged=%s", len(new_digest))
    return 1.0


class MetaAgentCache:
    def __init__(self, db_path: Path):
        """Initialize the meta-agent cache store with configured retention policy."""
        self._store = SQLiteCacheStore(
            db_path,
            table_name=META_CACHE_TABLE_NAME,
            ttl_seconds=META_CACHE_TTL_SECONDS,
        )

    @classmethod
    def from_repo_dir(cls, repo_dir: Path) -> "MetaAgentCache":
        """Create a meta cache instance rooted in the repository cache directory."""
        return cls(get_meta_cache_db_path(repo_dir))

    def load_if_valid(self, snapshot: MetaCacheIdentity) -> str | None:
        """Load cached meta insights JSON when the current snapshot is valid."""
        latest = self._store.load_latest(snapshot.scope)
        if latest is None:
            return None
        cache_key, payload_json, cached_meta = latest
        if not snapshot.matches_cached_metadata(cached_meta):
            return None
        self._store.touch(snapshot.scope, cache_key)
        return payload_json

    def save(self, snapshot: MetaCacheIdentity, payload_json: str) -> None:
        """Persist meta insights JSON with snapshot metadata for future reuse."""
        meta_json = snapshot.to_cache_metadata()
        self._store.upsert(snapshot.scope, snapshot.cache_key, payload_json, meta_json)
