from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_meta_information_prompt, get_system_meta_analysis_message
from agents.tools.dependency_patterns import (
    COMMON_DEPENDENCY_FILES,
    COMMON_DEPENDENCY_GLOBS,
    COMMON_DEPENDENCY_SUBDIRS,
)
from cache._paths import get_meta_cache_db_path
from cache._sqlite_store import SQLiteCacheStore
from repo_utils.ignore import RepoIgnoreManager
from utils import sha256_hexdigest

logger = logging.getLogger(__name__)

README_SIMILARITY_MIN = 0.995
META_CACHE_TABLE_NAME = "meta_cache"


@dataclass(frozen=True)
class MetaSnapshot:
    """
    Snapshot of metadata inputs used to decide whether meta-analysis can be reused.

    Identity keys:
    - scope: isolates cache by repository path.
    - model_id: guarantees cache does not cross LLM model changes.
    - prompt_version: guarantees cache does not cross prompt-template changes.
    - deps_hash/tree_hash: strict invalidation for dependency/layout changes.
    - docs_text: fuzzy invalidation target so tiny README edits (typos) do not recompute.
    """

    scope: str
    docs_text: str
    deps_hash: str
    tree_hash: str
    model_id: str
    prompt_version: str

    @property
    def fingerprint(self) -> str:
        raw = "|".join(
            [
                self.scope,
                self.deps_hash,
                self.tree_hash,
                self.model_id,
                self.prompt_version,
                sha256_hexdigest(self.docs_text),
            ]
        )
        return sha256_hexdigest(raw)

    @classmethod
    def from_repo(
        cls,
        repo_dir: Path,
        agent_llm: BaseChatModel,
        ignore_manager: RepoIgnoreManager,
    ) -> "MetaSnapshot":
        """Build snapshot inputs from repository content and model settings."""
        repo_root = repo_dir.resolve()
        return cls(
            scope=str(repo_root),
            docs_text=_compute_docs_text(repo_root, ignore_manager),
            deps_hash=_compute_deps_hash(repo_root, ignore_manager),
            tree_hash=_compute_tree_hash(repo_root, ignore_manager),
            model_id=_normalize_model_id(agent_llm),
            prompt_version=_compute_prompt_version(),
        )

    def is_compatible_with_cached_meta(
        self,
        cached_meta: dict[str, object],
        similarity_threshold: float = README_SIMILARITY_MIN,
    ) -> bool:
        """Return True when cached metadata remains valid for this snapshot."""
        if cached_meta.get("model_id") != self.model_id:
            return False
        if cached_meta.get("prompt_version") != self.prompt_version:
            return False
        if cached_meta.get("deps_hash") != self.deps_hash:
            return False
        if cached_meta.get("tree_hash") != self.tree_hash:
            return False

        old_docs = str(cached_meta.get("docs_text", ""))
        similarity = _token_overlap_similarity(old_docs, self.docs_text)
        return similarity >= similarity_threshold

    def to_cache_meta(self) -> dict[str, object]:
        """Return metadata payload persisted alongside cached analysis output."""
        return {
            "docs_text": self.docs_text,
            "deps_hash": self.deps_hash,
            "tree_hash": self.tree_hash,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
        }


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.debug("Failed to read text for meta cache snapshot from %s: %s", path, e)
        return ""


def _normalize_model_id(agent_llm: BaseChatModel) -> str:
    for attr in ("model_name", "model", "model_id"):
        value = getattr(agent_llm, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(agent_llm).__name__


def _compute_prompt_version() -> str:
    return sha256_hexdigest(get_system_meta_analysis_message() + "\n" + get_meta_information_prompt())


def _collect_dependency_paths(repo_dir: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
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


def _compute_deps_hash(repo_dir: Path, ignore_manager: RepoIgnoreManager) -> str:
    payload_parts: list[str] = []
    for dep_path in _collect_dependency_paths(repo_dir, ignore_manager):
        rel = dep_path.relative_to(repo_dir).as_posix()
        payload_parts.append(f"{rel}\n{_safe_read_text(dep_path)}")
    return sha256_hexdigest("\n---\n".join(payload_parts))


def _compute_tree_hash(repo_dir: Path, ignore_manager: RepoIgnoreManager) -> str:
    rel_paths: list[str] = []
    for path in repo_dir.rglob("*"):
        if ignore_manager.should_ignore(path):
            continue
        if path.is_file():
            rel_paths.append(path.relative_to(repo_dir).as_posix())
    rel_paths.sort()
    return sha256_hexdigest("\n".join(rel_paths))


def _compute_docs_text(repo_dir: Path, ignore_manager: RepoIgnoreManager, max_total_chars: int = 400_000) -> str:
    patterns = {".md", ".rst", ".txt", ".html"}
    docs: list[Path] = []
    for path in repo_dir.rglob("*"):
        if ignore_manager.should_ignore(path) or not path.is_file():
            continue
        if path.suffix.lower() not in patterns:
            continue
        if "tests" in path.parts or "test" in path.name.lower():
            continue
        docs.append(path)

    docs.sort(key=lambda p: (len(p.parts), p.as_posix()))
    chunks: list[str] = []
    used = 0
    for doc in docs:
        if used >= max_total_chars:
            break
        rel = doc.relative_to(repo_dir).as_posix()
        text = _safe_read_text(doc)
        remaining = max_total_chars - used
        clipped = text[:remaining]
        chunks.append(f"## {rel}\n{clipped}")
        used += len(clipped)
    return "\n\n".join(chunks)


def _token_overlap_similarity(left: str, right: str) -> float:
    """
    Fast typo-tolerant similarity over documentation text.

    We intentionally use token overlap instead of SequenceMatcher; SequenceMatcher
    can over-penalize long repeated sequences and incorrectly invalidate cache for
    tiny README edits.
    """
    left_tokens = left.split()
    right_tokens = right.split()

    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0

    left_counter = Counter(left_tokens)
    right_counter = Counter(right_tokens)
    overlap = 0
    for token, left_count in left_counter.items():
        overlap += min(left_count, right_counter.get(token, 0))

    denom = max(len(left_tokens), len(right_tokens))
    return overlap / denom if denom else 0.0


class MetaAgentCache:
    def __init__(self, db_path: Path):
        self._store = SQLiteCacheStore(db_path, table_name=META_CACHE_TABLE_NAME)

    @classmethod
    def from_repo_dir(cls, repo_dir: Path) -> "MetaAgentCache":
        return cls(get_meta_cache_db_path(repo_dir))

    def load_if_valid(self, snapshot: MetaSnapshot) -> MetaAnalysisInsights | None:
        """Load cached meta insights only when snapshot compatibility checks pass."""
        latest = self._store.load_latest(snapshot.scope)
        if latest is None:
            return None
        payload_json, cached_meta = latest
        if not snapshot.is_compatible_with_cached_meta(cached_meta):
            return None
        try:
            return MetaAnalysisInsights.model_validate_json(payload_json)
        except Exception:
            logger.warning("Meta cache payload is not valid MetaAnalysisInsights; treating as cache miss")
            return None

    def save(self, snapshot: MetaSnapshot, result: MetaAnalysisInsights) -> None:
        """Save meta insights together with snapshot metadata for future cache hits."""
        meta_json = snapshot.to_cache_meta()
        payload_json = result.model_dump_json()
        self._store.upsert(snapshot.scope, snapshot.fingerprint, payload_json, meta_json)
