"""Exceptions raised by diagram_analysis pipelines."""

from pathlib import Path


class IncrementalCacheMissingError(RuntimeError):
    """Raised when ``generate_analysis_incremental`` finds no warm pkl.

    The incremental path requires a populated ``CallGraph._cluster_cache``
    (sourced from the SHA-tagged ``static_analysis.pkl``). When absent we
    used to silently fall back to a full analysis, which discarded the
    existing analysis.json's depth and component IDs. Callers must
    explicitly opt into a full run instead.
    """

    def __init__(self, artifact_dir: Path):
        super().__init__(
            f"Incremental analysis requires a warm static_analysis.pkl at "
            f"{artifact_dir / 'static_analysis.pkl'}; none was found. Run a "
            f"full analysis first to seed the cache."
        )
        self.artifact_dir = artifact_dir
