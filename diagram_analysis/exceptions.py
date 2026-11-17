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
    """Raised when a child scope's members are not exactly its parent component's.

    Every method must belong to exactly one component per tree level. Because a
    child scope is a separate ``AnalysisInsights``, that invariant spans two objects
    no single populate/patch pass touches together, so it is checked before the save.
    A child method outside its parent renders under two components; a parent method
    in no child belongs to no leaf, and analysis.json, which lists only leaves'
    members, would drop it.
    """

    def __init__(self, violations: list[str]):
        super().__init__("Child scopes do not hold exactly their parent's methods: " + "; ".join(violations))
        self.violations = violations


class ClusteringScopeUnavailableError(RuntimeError):
    """Raised when a persisted component cannot be mapped to a clustering scope."""

    def __init__(self, component_id: str, reason: str):
        super().__init__(f"Clustering scope unavailable for component {component_id!r}: {reason}")
        self.component_id = component_id


class ScopeSemanticsError(RuntimeError):
    """Raised when the LLM fails to name a scope.

    Why: deterministic, folder-named components look like a finished diagram, so the run stops instead.
    """

    def __init__(self, scope_id: str, *, telemetry_properties: dict):
        super().__init__(f"Semantic analysis failed for scope {scope_id!r}")
        self.scope_id = scope_id
        self.telemetry_properties = telemetry_properties


class SymbolIdCollisionError(RuntimeError):
    """Raised when two indexed methods hash to the same analysis.json id."""

    def __init__(self, symbol_id: str, first_key: str, second_key: str):
        super().__init__(f"Methods {first_key!r} and {second_key!r} share the analysis.json id {symbol_id!r}")
        self.symbol_id = symbol_id
        self.keys = (first_key, second_key)


class AnalysisInvariantError(RuntimeError):
    """Raised when an analysis breaks a rule analysis.json relies on to state each fact once.

    Why: the document leaves out what a reader rebuilds, so writing such an analysis would lose
    or invent facts on the next read.
    """


class UnreadableAnalysisError(ValueError):
    """Raised when an analysis.json exists but this Core cannot read it."""


class AnalysisFormatError(UnreadableAnalysisError):
    """Raised when an analysis.json document is not in the format this Core reads."""

    def __init__(self, found: str | None, expected: str):
        written = "predates format versioning" if found is None else f"is format {found!r}"
        super().__init__(f"analysis.json {written}, not {expected!r}; run a full analysis to rewrite it")
        self.found = found
        self.expected = expected
