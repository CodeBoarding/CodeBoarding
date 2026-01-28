"""Symbol-level diff analysis using LSP.

This module provides functionality to extract and compare symbols between
two versions of a file, enabling detection of API changes vs implementation-only changes.
"""

import logging
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class SymbolKind(Enum):
    """LSP Symbol kinds we care about for diffing."""

    FILE = 1
    MODULE = 2
    NAMESPACE = 3
    PACKAGE = 4
    CLASS = 5
    METHOD = 6
    PROPERTY = 7
    FIELD = 8
    CONSTRUCTOR = 9
    ENUM = 10
    INTERFACE = 11
    FUNCTION = 12
    VARIABLE = 13
    CONSTANT = 14
    STRING = 15
    NUMBER = 16
    BOOLEAN = 17
    ARRAY = 18
    OBJECT = 19
    KEY = 20
    NULL = 21
    ENUM_MEMBER = 22
    STRUCT = 23
    EVENT = 24
    OPERATOR = 25
    TYPE_PARAMETER = 26

    @classmethod
    def is_callable(cls, kind: int) -> bool:
        """Check if symbol kind represents a callable (function/method)."""
        return kind in {cls.FUNCTION.value, cls.METHOD.value, cls.CONSTRUCTOR.value}

    @classmethod
    def is_type(cls, kind: int) -> bool:
        """Check if symbol kind represents a type definition."""
        return kind in {cls.CLASS.value, cls.INTERFACE.value, cls.ENUM.value, cls.STRUCT.value}


@dataclass
class SymbolInfo:
    """Represents a symbol extracted via LSP or AST parsing."""

    qualified_name: str
    name: str
    kind: int  # LSP SymbolKind value
    file_path: str
    start_line: int
    end_line: int
    signature: str = ""  # For functions/methods: parameter signature
    parent_name: str | None = None  # For methods: the class name

    @property
    def is_callable(self) -> bool:
        """Check if this symbol is a callable (function/method)."""
        return SymbolKind.is_callable(self.kind)

    @property
    def is_type(self) -> bool:
        """Check if this symbol is a type definition."""
        return SymbolKind.is_type(self.kind)

    @property
    def body_size(self) -> int:
        """Approximate body size in lines."""
        return self.end_line - self.start_line


@dataclass
class SymbolDiff:
    """Differences between two versions of a file's symbols."""

    file_path: str
    added_symbols: list[SymbolInfo] = field(default_factory=list)
    removed_symbols: list[SymbolInfo] = field(default_factory=list)
    modified_signatures: list[tuple[SymbolInfo, SymbolInfo]] = field(default_factory=list)  # (old, new)
    implementation_only: list[SymbolInfo] = field(default_factory=list)  # Same signature, different body

    @property
    def has_api_changes(self) -> bool:
        """Check if there are any API-level changes (not just implementation)."""
        return bool(self.added_symbols or self.removed_symbols or self.modified_signatures)

    @property
    def has_changes(self) -> bool:
        """Check if there are any changes at all."""
        return self.has_api_changes or bool(self.implementation_only)

    def summary(self) -> str:
        """Generate a human-readable summary of changes."""
        parts = []
        if self.added_symbols:
            parts.append(f"+{len(self.added_symbols)} symbols")
        if self.removed_symbols:
            parts.append(f"-{len(self.removed_symbols)} symbols")
        if self.modified_signatures:
            parts.append(f"~{len(self.modified_signatures)} signatures")
        if self.implementation_only:
            parts.append(f"impl:{len(self.implementation_only)}")
        return ", ".join(parts) if parts else "no changes"


class SymbolExtractor:
    """Extracts symbols from source code using regex-based parsing.

    This is a lightweight alternative to full LSP when we just need
    symbol signatures for comparison. Supports Python, TypeScript,
    Go, PHP, and Java.
    """

    # Regex patterns for different languages
    PATTERNS = {
        ".py": {
            "class": re.compile(r"^(\s*)class\s+(\w+)(?:\s*\(([^)]*)\))?\s*:", re.MULTILINE),
            "function": re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE),
        },
        ".ts": {
            "class": re.compile(r"^(\s*)(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
            "function": re.compile(
                r"^(\s*)(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)", re.MULTILINE
            ),
            "method": re.compile(
                r"^(\s*)(?:public|private|protected)?\s*(?:async\s+)?(\w+)\s*\(([^)]*)\)", re.MULTILINE
            ),
        },
        ".js": {
            "class": re.compile(r"^(\s*)(?:export\s+)?class\s+(\w+)", re.MULTILINE),
            "function": re.compile(r"^(\s*)(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE),
        },
        ".go": {
            "struct": re.compile(r"^type\s+(\w+)\s+struct\s*\{", re.MULTILINE),
            "function": re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(([^)]*)\)", re.MULTILINE),
        },
        ".php": {
            "class": re.compile(r"^(\s*)(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
            "function": re.compile(
                r"^(\s*)(?:public|private|protected)?\s*function\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE
            ),
        },
        ".java": {
            "class": re.compile(r"^(\s*)(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
            "method": re.compile(
                r"^(\s*)(?:public|private|protected)?\s*(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[^{]+)?{",
                re.MULTILINE,
            ),
        },
    }

    def __init__(self):
        self._indent_width = 4  # Default indent width

    def extract_symbols(self, file_path: str, content: str) -> list[SymbolInfo]:
        """Extract symbols from file content.

        Args:
            file_path: Path to the file (used to determine language)
            content: File content

        Returns:
            List of SymbolInfo objects
        """
        suffix = Path(file_path).suffix.lower()
        patterns = self.PATTERNS.get(suffix)

        if not patterns:
            logger.debug(f"No patterns for suffix {suffix}, skipping symbol extraction")
            return []

        symbols = []
        lines = content.split("\n")

        # Extract classes/structs
        for pattern_name, pattern in patterns.items():
            for match in pattern.finditer(content):
                start_pos = match.start()
                start_line = content[:start_pos].count("\n")

                # Determine symbol name and signature
                groups = match.groups()
                indent = groups[0] if groups[0] else ""
                name = groups[1] if len(groups) > 1 else groups[0]
                signature = groups[2] if len(groups) > 2 else ""

                # Find end line by tracking indentation (for Python) or braces (for others)
                end_line = self._find_symbol_end(lines, start_line, suffix, len(indent))

                kind = self._pattern_to_kind(pattern_name)
                qualified_name = self._create_qualified_name(file_path, name)

                symbols.append(
                    SymbolInfo(
                        qualified_name=qualified_name,
                        name=name,
                        kind=kind,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        signature=self._normalize_signature(signature or ""),
                    )
                )

        return symbols

    def _pattern_to_kind(self, pattern_name: str) -> int:
        """Convert pattern name to LSP SymbolKind."""
        mapping = {
            "class": SymbolKind.CLASS.value,
            "struct": SymbolKind.STRUCT.value,
            "function": SymbolKind.FUNCTION.value,
            "method": SymbolKind.METHOD.value,
        }
        return mapping.get(pattern_name, SymbolKind.FUNCTION.value)

    def _find_symbol_end(self, lines: list[str], start_line: int, suffix: str, base_indent: int) -> int:
        """Find the end line of a symbol definition."""
        if suffix == ".py":
            # Python uses indentation
            for i in range(start_line + 1, len(lines)):
                line = lines[i]
                if line.strip() and not line.startswith(" " * (base_indent + 1)) and not line.strip().startswith("#"):
                    return i - 1
            return len(lines) - 1
        else:
            # Other languages use braces
            brace_count = 0
            started = False
            for i in range(start_line, len(lines)):
                line = lines[i]
                brace_count += line.count("{") - line.count("}")
                if "{" in line:
                    started = True
                if started and brace_count == 0:
                    return i
            return len(lines) - 1

    def _normalize_signature(self, signature: str) -> str:
        """Normalize function signature for comparison."""
        # Remove whitespace variations
        signature = re.sub(r"\s+", " ", signature.strip())
        # Remove default values for comparison
        signature = re.sub(r"\s*=\s*[^,)]+", "", signature)
        return signature

    def _create_qualified_name(self, file_path: str, name: str) -> str:
        """Create a qualified name from file path and symbol name."""
        # Convert file path to module-like qualified name
        path = Path(file_path)
        parts = list(path.with_suffix("").parts)
        # Remove common prefixes like 'src', 'lib'
        while parts and parts[0] in {"src", "lib", "app", "pkg"}:
            parts.pop(0)
        parts.append(name)
        return ".".join(parts)


class SymbolDiffAnalyzer:
    """Analyzes symbol-level differences between file versions.

    This class compares symbols extracted from two versions of a file
    to determine what kind of changes were made (API vs implementation).
    """

    def __init__(self):
        self.extractor = SymbolExtractor()

    def diff_symbols(self, file_path: str, old_content: str, new_content: str) -> SymbolDiff:
        """Compare symbols between old and new file content.

        Args:
            file_path: Path to the file
            old_content: Content of the file before changes
            new_content: Content of the file after changes

        Returns:
            SymbolDiff describing the differences
        """
        old_symbols = self.extractor.extract_symbols(file_path, old_content)
        new_symbols = self.extractor.extract_symbols(file_path, new_content)

        # Build lookup maps
        old_by_name = {s.qualified_name: s for s in old_symbols}
        new_by_name = {s.qualified_name: s for s in new_symbols}

        # Find added and removed symbols
        added = [s for name, s in new_by_name.items() if name not in old_by_name]
        removed = [s for name, s in old_by_name.items() if name not in new_by_name]

        # For symbols that exist in both, check for changes
        modified_signatures = []
        implementation_only = []

        for name in set(old_by_name) & set(new_by_name):
            old_sym = old_by_name[name]
            new_sym = new_by_name[name]

            # Check signature changes
            if old_sym.signature != new_sym.signature:
                modified_signatures.append((old_sym, new_sym))
            # Check implementation changes (same signature, different body size)
            elif old_sym.body_size != new_sym.body_size:
                implementation_only.append(new_sym)

        return SymbolDiff(
            file_path=file_path,
            added_symbols=added,
            removed_symbols=removed,
            modified_signatures=modified_signatures,
            implementation_only=implementation_only,
        )

    def diff_files(
        self, file_paths: list[str], old_contents: dict[str, str], new_contents: dict[str, str]
    ) -> dict[str, SymbolDiff]:
        """Diff multiple files at once.

        Args:
            file_paths: List of file paths to diff
            old_contents: Mapping of file_path -> old content
            new_contents: Mapping of file_path -> new content

        Returns:
            Dictionary mapping file_path -> SymbolDiff
        """
        results = {}
        for file_path in file_paths:
            old_content = old_contents.get(file_path, "")
            new_content = new_contents.get(file_path, "")

            if old_content or new_content:
                results[file_path] = self.diff_symbols(file_path, old_content, new_content)

        return results


def extract_function_signatures_from_content(content: str, language_suffix: str) -> dict[str, str]:
    """Quick extraction of function signatures for comparison.

    This is a lightweight function for fast signature comparison
    without creating full SymbolInfo objects.

    Args:
        content: Source file content
        language_suffix: File extension (e.g., '.py', '.ts')

    Returns:
        Dictionary mapping function name -> normalized signature
    """
    extractor = SymbolExtractor()
    patterns = extractor.PATTERNS.get(language_suffix, {})

    signatures = {}
    for pattern_name, pattern in patterns.items():
        if pattern_name in {"function", "method"}:
            for match in pattern.finditer(content):
                groups = match.groups()
                name = groups[1] if len(groups) > 1 else groups[0]
                signature = groups[2] if len(groups) > 2 else ""
                signatures[name] = extractor._normalize_signature(signature or "")

    return signatures
