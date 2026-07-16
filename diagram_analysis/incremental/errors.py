"""Incremental analysis failures."""


class IncrementalAnalysisError(RuntimeError):
    """Raised when an update cannot preserve trustworthy lineage."""
