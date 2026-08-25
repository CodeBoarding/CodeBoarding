"""Exceptions raised by diagram_analysis pipelines."""


class IncrementalClusteringError(RuntimeError):
    """Raised when a scope's clustering comes back empty but it still owns live methods.

    Scoped clustering expands to one cluster per method whenever a
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


class ClusteringScopeUnavailableError(RuntimeError):
    """Raised when a persisted component cannot be mapped to a clustering scope."""

    def __init__(self, component_id: str, reason: str):
        super().__init__(f"Clustering scope unavailable for component {component_id!r}: {reason}")
        self.component_id = component_id
