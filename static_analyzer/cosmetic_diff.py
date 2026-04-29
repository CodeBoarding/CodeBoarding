"""Cosmetic-diff detection: AST equality after stripping comments and (tier 1) alpha-renaming locals."""

import ast
import logging
from pathlib import Path
from typing import Any

from static_analyzer.constants import Language, SOURCE_EXTENSION_TO_LANGUAGE
from static_analyzer.tree_sitter_parsers import (
    _get_new_content,
    _get_old_content,
    _get_parser,
    _LANG_CONFIGS,
    _to_normalized_tuple,
    _to_structural_tuple,
)

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 100_000  # bytes – skip semantic diff for very large files


def _collect_error_positions(node: Any, errors: list[tuple[int, int]], max_errors: int = 3) -> None:
    """Collect (line, column) for ERROR or MISSING tree-sitter nodes."""
    if node.type == "ERROR" or node.is_missing:
        errors.append((node.start_point[0] + 1, node.start_point[1]))
        if len(errors) >= max_errors:
            return
    for child in node.children:
        _collect_error_positions(child, errors, max_errors)
        if len(errors) >= max_errors:
            return


def check_syntax_errors(repo_dir: Path, file_path: str) -> list[tuple[int, int]]:
    """Return positions of syntax errors in *file_path*, empty list if clean.

    Each entry is a ``(line, column)`` tuple with 1-indexed line numbers.
    Returns an empty list for unsupported file extensions, unreadable files,
    or when tree-sitter is unavailable.
    """
    ext = Path(file_path).suffix.lower()
    language = SOURCE_EXTENSION_TO_LANGUAGE.get(ext)
    if language is None:
        return []

    content = _get_new_content(repo_dir, file_path)
    if content is None:
        return []

    parser = _get_parser(language, ext)
    if parser is None:
        return []

    try:
        tree = parser.parse(content.encode("utf-8"))
    except Exception:
        logger.debug("Tree-sitter parse failed for %s", file_path, exc_info=True)
        return [(1, 0)]

    if not tree.root_node.has_error:
        return []

    errors: list[tuple[int, int]] = []
    _collect_error_positions(tree.root_node, errors)
    return errors or [(1, 0)]


def is_file_cosmetic(repo_dir: Path, base_ref: str, file_path: str) -> bool:
    """Return True if the changes to *file_path* are cosmetic-only."""
    ext = Path(file_path).suffix.lower()
    language = SOURCE_EXTENSION_TO_LANGUAGE.get(ext)
    if language is None:
        return False

    config = _LANG_CONFIGS.get(language)
    if config is None:
        return False

    old_content = _get_old_content(repo_dir, base_ref, file_path)
    new_content = _get_new_content(repo_dir, file_path)
    if old_content is None or new_content is None:
        return False

    if old_content == new_content:
        return True

    old_content = strip_comments_from_source(file_path, old_content)
    new_content = strip_comments_from_source(file_path, new_content)

    if old_content == new_content:
        return True

    if len(old_content) > _MAX_FILE_SIZE or len(new_content) > _MAX_FILE_SIZE:
        return False

    parser = _get_parser(language, ext)
    if parser is None:
        return False

    try:
        old_tree = parser.parse(old_content.encode("utf-8"))
        new_tree = parser.parse(new_content.encode("utf-8"))
    except Exception:
        logger.debug("Tree-sitter parse failed for %s", file_path, exc_info=True)
        return False

    if old_tree.root_node.has_error or new_tree.root_node.has_error:
        logger.debug("Parse errors in %s; assuming semantic change", file_path)
        return False

    # Tier 0: structural
    if _to_structural_tuple(old_tree.root_node) == _to_structural_tuple(new_tree.root_node):
        logger.debug("Tier 0 cosmetic: %s", file_path)
        return True

    # Tier 1: normalized
    old_norm = _to_normalized_tuple(old_tree.root_node, config, {}, [0])
    new_norm = _to_normalized_tuple(new_tree.root_node, config, {}, [0])
    if old_norm == new_norm:
        logger.debug("Tier 1 cosmetic: %s", file_path)
        return True

    return False


def _collect_extra_ranges(node: Any, ranges: list[tuple[int, int]]) -> None:
    """Collect byte ranges for tree-sitter ``is_extra`` nodes."""
    for child in node.children:
        if child.is_extra:
            ranges.append((child.start_byte, child.end_byte))
            continue
        _collect_extra_ranges(child, ranges)


def _line_col_to_byte_offset(line_bytes: list[bytes], lineno: int | None, column: int | None) -> int | None:
    """Convert a Python AST line/column position into a UTF-8 byte offset."""
    if lineno is None or column is None or lineno < 1 or lineno > len(line_bytes):
        return None
    return sum(len(line) for line in line_bytes[: lineno - 1]) + column


def _collect_python_docstring_ranges(source: str) -> list[tuple[int, int]]:
    """Return UTF-8 byte ranges for Python module/class/function docstrings."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return []

    line_bytes = source.encode("utf-8").splitlines(keepends=True)
    if not line_bytes:
        line_bytes = [b""]

    ranges: list[tuple[int, int]] = []

    def visit(node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        body = getattr(node, "body", [])
        if body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                start = _line_col_to_byte_offset(
                    line_bytes,
                    getattr(first, "lineno", None),
                    getattr(first, "col_offset", None),
                )
                end = _line_col_to_byte_offset(
                    line_bytes,
                    getattr(first, "end_lineno", None),
                    getattr(first, "end_col_offset", None),
                )
                if start is not None and end is not None and end > start:
                    ranges.append((start, end))

        for child in body:
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child)

    visit(module)
    return ranges


def strip_comments_from_source(file_path: str, source: str) -> str:
    """Return *source* with comments (and Python docstrings) removed; preserves newline count."""
    ext = Path(file_path).suffix.lower()
    language = SOURCE_EXTENSION_TO_LANGUAGE.get(ext)
    if language is None:
        return source

    parser = _get_parser(language, ext)
    if parser is None:
        return source

    source_bytes = source.encode("utf-8")
    try:
        tree = parser.parse(source_bytes)
    except Exception:
        logger.debug("Tree-sitter parse failed while stripping comments for %s", file_path, exc_info=True)
        return source

    if tree.root_node.has_error:
        return source

    ranges: list[tuple[int, int]] = []
    _collect_extra_ranges(tree.root_node, ranges)
    if language == Language.PYTHON:
        ranges.extend(_collect_python_docstring_ranges(source))
    if not ranges:
        return source

    ranges.sort()
    parts: list[bytes] = []
    cursor = 0
    for start, end in ranges:
        if start < cursor:
            start = cursor
        if start >= end:
            continue
        parts.append(source_bytes[cursor:start])
        removed = source_bytes[start:end]
        if removed:
            parts.append(b"\n" * removed.count(b"\n"))
        cursor = end
    parts.append(source_bytes[cursor:])
    return b"".join(parts).decode("utf-8", errors="replace")
