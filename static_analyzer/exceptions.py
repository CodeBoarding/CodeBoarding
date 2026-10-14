"""Failures that make a result not worth reporting."""


class StaticAnalysisFatalError(RuntimeError):
    """Raised when continuing would produce misleading cached analysis."""


class LanguageServerStalledError(StaticAnalysisFatalError):
    """Raised when a language server stays up but the backend that answers for it has died."""
