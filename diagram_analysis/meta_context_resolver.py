from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agents.meta_agent import MetaAgent
from cache.meta_cache import MetaCache, build_meta_snapshot

logger = logging.getLogger(__name__)


def resolve_meta_context(repo_dir: Path, meta_agent: MetaAgent, agent_llm: object) -> Any:
    """
    Resolve metadata context using cache-first policy.

    This keeps cache policy reusable while leaving orchestration control in callers.
    """
    meta_cache = MetaCache.from_repo_dir(repo_dir)
    meta_snapshot = build_meta_snapshot(repo_dir, agent_llm)

    cached_meta = meta_cache.load_if_valid(meta_snapshot)
    if cached_meta is not None:
        logger.info("Meta cache hit; reusing metadata analysis")
        return cached_meta

    logger.info("Meta cache miss/invalid; recomputing metadata analysis")
    computed_meta = meta_agent.analyze_project_metadata()
    meta_cache.save(meta_snapshot, computed_meta)
    return computed_meta
