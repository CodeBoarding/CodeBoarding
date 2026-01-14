"""Constants for agents, including language support configuration."""

# Fallback extensions for supported languages if static analysis is unavailable
# IMPORTANT: Update this constant when adding new language support
# See test_diff_analyzer_language_sync for validation
SUPPORTED_EXTENSIONS_FALLBACK = {
    ".py",
    ".pyi",  # Python
    ".ts",
    ".tsx",  # TypeScript
    ".js",
    ".jsx",  # JavaScript
    ".go",  # Go
    ".php",  # PHP
}
