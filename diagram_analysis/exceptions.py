"""Exceptions raised by diagram_analysis pipelines."""

from pathlib import Path

from static_analyzer.analysis_cache import STATIC_ANALYSIS_PKL, STATIC_ANALYSIS_SHA


class IncrementalCacheMissingError(RuntimeError):
    """Raised when ``generate_analysis_incremental`` finds no usable warm cache.

    The incremental path requires a populated ``CallGraph._cluster_cache``
    (sourced from the SHA-tagged ``static_analysis.pkl``). When absent we
    used to silently fall back to a full analysis, which discarded the
    existing analysis.json's depth and component IDs. Callers must
    explicitly opt into a full run instead.

    The constructor inspects ``artifact_dir`` to produce a specific
    diagnostic depending on which piece is missing — pkl absent, sha
    absent (so the warm-start cannot SHA-gate), or pkl present but no
    cluster baseline inside it. Without that distinction, every variant
    of this failure surfaced as "no warm static_analysis.pkl", which
    misled callers when the pkl was actually present.
    """

    def __init__(self, artifact_dir: Path):
        pkl_path = artifact_dir / STATIC_ANALYSIS_PKL
        sha_path = artifact_dir / STATIC_ANALYSIS_SHA
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


class InvalidIncrementalPlanError(RuntimeError):
    """Raised when the planner cannot produce a trustworthy scoped update."""

    def __init__(self, scope_id: str, issues: list[str]):
        issue_summary = "; ".join(issues)
        super().__init__(f"Incremental plan for scope {scope_id!r} is invalid: {issue_summary}")
        self.scope_id = scope_id
        self.issues = issues


class ScopeContainmentError(RuntimeError):
    """Raised when a child scope owns methods its parent component does not.

    Every method must belong to exactly one component per tree level. Because a
    child scope is a separate ``AnalysisInsights``, containment is the one half of
    that invariant no single populate/patch pass can enforce — so it is checked
    once before the save. A violation means the same method renders under two
    components, which is worse to ship than a failed run.
    """

    def __init__(self, violations: list[str]):
        super().__init__("Child scopes own methods outside their parent component: " + "; ".join(violations))
        self.violations = violations
