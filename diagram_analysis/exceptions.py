"""Exceptions raised by diagram_analysis pipelines."""

from pathlib import Path

from static_analyzer.analysis_cache import STATIC_ANALYSIS_PKL, STATIC_ANALYSIS_SHA


class IncrementalCacheMissingError(RuntimeError):
    """Raised when ``generate_analysis_incremental`` finds no usable warm cache.

    Needs a populated ``ClusterCache`` on the cached ``LanguageResults``, from the
    SHA-tagged ``static_analysis.pkl``. Why raise: falling back to a full analysis
    discarded analysis.json's depth and component IDs. The message names which
    piece is missing — pkl, sha, or cluster baseline.
    """

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


class IncrementalClusteringError(RuntimeError):
    """Raised when a scope's clustering comes back empty but it still owns live methods.

    ``_create_strict_component_subgraph`` expands to one cluster per method whenever a
    component has any live callable node, so an empty clustering means none of the methods
    the scope claims exist in the live call graph. Continuing would save the scope's stale
    membership and relations and hide the missed change; per the fail-fast rule the caller
    must learn the incremental run could not represent this scope rather than get a
    plausible-but-wrong result.
    """

    def __init__(self, scope_id: str, component_ids: list[str]):
        super().__init__(
            f"Scope {scope_id!r} produced no clusters but still owns methods in component(s) "
            f"{', '.join(component_ids)}; the incremental clustering could not represent live code."
        )
        self.scope_id = scope_id
        self.component_ids = component_ids


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
