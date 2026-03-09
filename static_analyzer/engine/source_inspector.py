"""Source code reading and call-site detection utilities."""

from __future__ import annotations

from pathlib import Path


class SourceInspector:
    """Reads source files and determines whether references are call sites.

    Maintains a file content cache to avoid re-reading files.
    """

    def __init__(self) -> None:
        self._file_content_cache: dict[str, list[str]] = {}

    def get_source_line(self, file_path: Path, line: int) -> str | None:
        """Get a source line from cache, loading the file if needed."""
        lines = self.get_file_lines(file_path)
        if lines is None or line >= len(lines):
            return None
        return lines[line]

    def get_file_lines(self, file_path: Path) -> list[str] | None:
        """Get all lines of a file from cache, loading if needed."""
        file_key = str(file_path)
        if file_key not in self._file_content_cache:
            try:
                self._file_content_cache[file_key] = file_path.read_text(errors="replace").splitlines()
            except Exception:
                return None
        return self._file_content_cache[file_key]

    def is_invocation(self, file_path: Path, ref_line: int, ref_end_char: int) -> bool:
        """Check whether a reference is directly invoked (followed by '(').

        Also handles generic instantiation patterns: Name<T>(...).
        """
        line = self.get_source_line(file_path, ref_line)
        if line is None:
            return True  # Conservative: include if we can't read

        rest = line[ref_end_char:]
        stripped = rest.lstrip()
        if stripped:
            if stripped[0] == "(":
                return True
            # Generic instantiation: Name<T>(...) or Name<T, U>(...)
            if stripped[0] == "<":
                close = stripped.find(">")
                if close != -1:
                    after_generic = stripped[close + 1 :].lstrip()
                    return bool(after_generic) and after_generic[0] == "("
            return False

        # If the reference is at end of line, check subsequent lines for '('
        lines = self.get_file_lines(file_path)
        if lines is None:
            return True
        for subsequent_line_idx in range(ref_line + 1, min(ref_line + 3, len(lines))):
            stripped = lines[subsequent_line_idx].lstrip()
            if stripped:
                return stripped[0] == "("

        return False

    def is_callable_usage(self, file_path: Path, ref_line: int, ref_start_char: int, ref_end_char: int) -> bool:
        """Check whether a variable/constant reference is used in a callable context.

        Returns True for:
        - Direct invocation: ``func(args)``
        - Callback argument: ``filter(func)``, ``map(func, ...)``
        - Return value: ``return func``
        - Assignment to another callable context: ``handler = func``
        - Method chaining argument: ``.then(func)``

        This is broader than is_invocation because variables often hold
        arrow functions or closures that are passed around without being
        directly called at the reference site.
        """
        # First check: is it directly invoked?
        if self.is_invocation(file_path, ref_line, ref_end_char):
            return True

        line = self.get_source_line(file_path, ref_line)
        if line is None:
            return True  # Conservative

        # Check if preceded by 'return' (closure/factory pattern)
        prefix = line[:ref_start_char].rstrip()
        if prefix.endswith("return"):
            return True

        # Check if the reference is inside a function call's argument list.
        # Look for an unmatched '(' before the reference on the same line,
        # preceded by an identifier (the function being called).
        # This handles: filter(func), map(func, ...), setTimeout(func, 100)
        before = line[:ref_start_char]
        depth = 0
        for ch in reversed(before):
            if ch == ")":
                depth += 1
            elif ch == "(":
                if depth == 0:
                    # We found an unmatched '(' — this reference is inside a call
                    return True
                depth -= 1

        return False
