"""Failures that make a result not worth reporting."""


class StaticAnalysisFatalError(RuntimeError):
    """Raised when continuing would produce misleading cached analysis."""
