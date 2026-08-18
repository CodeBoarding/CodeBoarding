"""Failures the analyzer refuses to paper over.

Both are raised where the alternative is a graph that looks complete and is not:
the caller decides whether to retry, fall back to a full analysis, or surface the
problem, and that decision needs the failure to reach it.
"""


class EdgeResolutionError(RuntimeError):
    """A language-server query needed to build edges failed.

    Continuing would persist a graph missing every edge the failed batch covered,
    with nothing recording which.
    """


class IncrementalAnalysisError(RuntimeError):
    """An incremental update could not reproduce what a full rebuild would produce.

    Raised rather than returning a partially-updated graph: an incremental result
    the user believes is complete is worse than a clear instruction to run a full
    analysis.
    """
