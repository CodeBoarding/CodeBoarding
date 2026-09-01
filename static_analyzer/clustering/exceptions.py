"""Exceptions raised while reconstructing persisted clustering."""

from pathlib import Path

from static_analyzer.analysis_cache import STATIC_ANALYSIS_PKL, STATIC_ANALYSIS_SHA


class IncrementalCacheMissingError(RuntimeError):
    """Raised when incremental clustering finds no usable warm cache."""

    def __init__(self, artifact_dir: Path, reason: str = ""):
        pkl_path = artifact_dir / STATIC_ANALYSIS_PKL
        sha_path = artifact_dir / STATIC_ANALYSIS_SHA
        if not reason:
            if not pkl_path.exists():
                reason = f"no {STATIC_ANALYSIS_PKL} at {pkl_path}"
            elif not sha_path.exists():
                reason = (
                    f"{STATIC_ANALYSIS_PKL} at {pkl_path} has no sibling "
                    f"{STATIC_ANALYSIS_SHA}; the warm-start cannot SHA-gate"
                )
            else:
                reason = (
                    f"{STATIC_ANALYSIS_PKL} at {pkl_path} loaded but has no cluster baseline "
                    "(legacy pkl or first-ever incremental run)"
                )
        super().__init__(
            f"Incremental analysis cannot proceed: {reason}. " "Run a full analysis first to seed the cache."
        )
        self.artifact_dir = artifact_dir


class PersistedOwnershipConflictError(RuntimeError):
    """Raised when one persisted scope assigns a live symbol to multiple components."""

    def __init__(self, scope_id: str, language: str, qualified_name: str, owners: set[str]):
        super().__init__(
            f"Persisted scope {scope_id!r} assigns {language} member {qualified_name!r} "
            f"to multiple components: {', '.join(sorted(owners))}"
        )


class NamingModelUnavailableError(RuntimeError):
    """Raised when clustering has no naming model to partition by.

    Components come from what a repo's identifiers say, which is read once per full
    analysis by an LLM. There is no graph-based fallback: an LLM is required for the later
    stages anyway, so a missing model means the run cannot produce an answer, not that it
    should produce a worse one.
    """

    def __init__(self, reason: str = ""):
        super().__init__(
            f"Clustering has no naming model{f': {reason}' if reason else ''}. "
            "Configure an LLM provider and run a full analysis; see "
            "https://github.com/CodeBoarding/CodeBoarding#authentication-and-providers."
        )
