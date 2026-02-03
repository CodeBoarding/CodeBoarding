"""Centralized constants for the static analyzer module.

This module contains all language and configuration constants used throughout
the static analyzer to avoid hardcoded strings and ensure consistency.
"""

from enum import Enum


class Language(str, Enum):
    """Enumeration of supported programming languages.

    Using Enum ensures type safety and prevents typos in language names.
    The values are the lowercase language identifiers used in LSP and throughout
    the codebase.
    """

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    GO = "go"
    JAVA = "java"
    PHP = "php"
    RUST = "rust"
    RUBY = "ruby"
    SCALA = "scala"
    KOTLIN = "kotlin"
    SWIFT = "swift"
    CPP = "cpp"
    C = "c"
    CSHARP = "csharp"


# For backward compatibility and ease of use, also provide module-level constants
LANG_PYTHON = Language.PYTHON.value
LANG_TYPESCRIPT = Language.TYPESCRIPT.value
LANG_JAVASCRIPT = Language.JAVASCRIPT.value
LANG_GO = Language.GO.value
LANG_JAVA = Language.JAVA.value
LANG_PHP = Language.PHP.value
LANG_RUST = Language.RUST.value
LANG_RUBY = Language.RUBY.value
LANG_SCALA = Language.SCALA.value
LANG_KOTLIN = Language.KOTLIN.value
LANG_SWIFT = Language.SWIFT.value
LANG_CPP = Language.CPP.value
LANG_C = Language.C.value
LANG_CSHARP = Language.CSHARP.value

# Language groups for convenience
LANGUAGES_WEB = {LANG_TYPESCRIPT, LANG_JAVASCRIPT}
LANGUAGES_JVM = {LANG_JAVA, LANG_KOTLIN, LANG_SCALA}
LANGUAGES_NATIVE = {LANG_C, LANG_CPP, LANG_RUST, LANG_GO, LANG_SWIFT}

# All supported languages
ALL_SUPPORTED_LANGUAGES = {lang.value for lang in Language}
