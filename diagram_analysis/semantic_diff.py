"""Tree-sitter based semantic diff for cosmetic change detection.

Compares old (from git) vs new (from working tree) file ASTs to detect
changes that don't affect program semantics.  Used by the incremental
tracer to skip unnecessary LLM calls.

Two tiers:
  Tier 0 – structural: strips comments and whitespace, compares ASTs.
  Tier 1 – normalized: alpha-renames locals, sorts commutative operands.
"""

import hashlib
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from static_analyzer.constants import Language

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 100_000  # bytes – skip semantic diff for very large files
_WHITESPACE_RE = re.compile(r"\s+")

# ---------------------------------------------------------------------------
# Per-language tree-sitter configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LangConfig:
    binary_expr_types: frozenset[str]
    commutative_ops: frozenset[str]
    scope_types: frozenset[str]  # nodes that start a new naming scope (fork rename map)
    local_scope_types: frozenset[str]  # function-like scopes where alpha-renaming is allowed
    identifier_types: frozenset[str]  # leaf nodes that carry local names
    binding_parent_types: frozenset[str]  # parent nodes whose child id is a binding


EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".go": Language.GO,
    ".java": Language.JAVA,
    ".php": Language.PHP,
}

_LANG_CONFIGS: dict[Language, _LangConfig] = {
    Language.PYTHON: _LangConfig(
        binary_expr_types=frozenset({"binary_operator", "boolean_operator", "comparison_operator"}),
        commutative_ops=frozenset({"*", "==", "!=", "is"}),
        scope_types=frozenset({"function_definition", "class_definition", "lambda"}),
        local_scope_types=frozenset({"function_definition", "lambda"}),
        identifier_types=frozenset({"identifier"}),
        binding_parent_types=frozenset(
            {
                "assignment",
                "augmented_assignment",
                "for_statement",
                "with_item",
                "parameters",
                "default_parameter",
                "typed_parameter",
                "typed_default_parameter",
                "list_splat_pattern",
                "dictionary_splat_pattern",
                "as_pattern",
                "pattern_list",
                "tuple_pattern",
                "list_pattern",
                "for_in_clause",
            }
        ),
    ),
    Language.TYPESCRIPT: _LangConfig(
        binary_expr_types=frozenset({"binary_expression"}),
        commutative_ops=frozenset({"*", "===", "!==", "==", "!="}),
        scope_types=frozenset({"function_declaration", "arrow_function", "method_definition", "class_declaration"}),
        local_scope_types=frozenset({"function_declaration", "arrow_function", "method_definition"}),
        identifier_types=frozenset({"identifier"}),
        binding_parent_types=frozenset(
            {"variable_declarator", "formal_parameters", "required_parameter", "optional_parameter", "rest_parameter"}
        ),
    ),
    Language.JAVASCRIPT: _LangConfig(
        binary_expr_types=frozenset({"binary_expression"}),
        commutative_ops=frozenset({"*", "===", "!==", "==", "!="}),
        scope_types=frozenset({"function_declaration", "arrow_function", "method_definition", "class_declaration"}),
        local_scope_types=frozenset({"function_declaration", "arrow_function", "method_definition"}),
        identifier_types=frozenset({"identifier"}),
        binding_parent_types=frozenset(
            {"variable_declarator", "formal_parameters", "required_parameter", "optional_parameter", "rest_parameter"}
        ),
    ),
    Language.GO: _LangConfig(
        binary_expr_types=frozenset({"binary_expression"}),
        commutative_ops=frozenset({"*", "==", "!="}),
        scope_types=frozenset({"function_declaration", "method_declaration", "func_literal"}),
        local_scope_types=frozenset({"function_declaration", "method_declaration", "func_literal"}),
        identifier_types=frozenset({"identifier"}),
        binding_parent_types=frozenset({"short_var_declaration", "var_spec", "parameter_declaration", "range_clause"}),
    ),
    Language.JAVA: _LangConfig(
        binary_expr_types=frozenset({"binary_expression"}),
        commutative_ops=frozenset({"*", "==", "!="}),
        scope_types=frozenset(
            {"method_declaration", "constructor_declaration", "class_declaration", "lambda_expression"}
        ),
        local_scope_types=frozenset({"method_declaration", "constructor_declaration", "lambda_expression"}),
        identifier_types=frozenset({"identifier"}),
        binding_parent_types=frozenset(
            {
                "variable_declarator",
                "formal_parameter",
                "catch_formal_parameter",
                "enhanced_for_statement",
                "local_variable_declaration",
            }
        ),
    ),
    Language.PHP: _LangConfig(
        binary_expr_types=frozenset({"binary_expression"}),
        commutative_ops=frozenset({"*", "===", "!==", "==", "!="}),
        scope_types=frozenset(
            {"function_definition", "method_declaration", "class_declaration", "anonymous_function_creation_expression"}
        ),
        local_scope_types=frozenset(
            {"function_definition", "method_declaration", "anonymous_function_creation_expression"}
        ),
        identifier_types=frozenset({"variable_name", "name"}),
        binding_parent_types=frozenset(
            {
                "assignment_expression",
                "simple_parameter",
                "foreach_statement",
                "static_variable_declaration",
                "variable_name",
            }
        ),
    ),
}


# ---------------------------------------------------------------------------
# Parser cache – lazy-loaded per language
# ---------------------------------------------------------------------------
_parser_cache: dict[str, Any] = {}  # keyed by "language:ext" for tsx distinction


def _get_parser(language: Language, file_ext: str) -> Any | None:
    """Return a cached tree-sitter Parser for *language*, or None on failure."""
    cache_key = f"{language}:{file_ext}" if language == Language.TYPESCRIPT else str(language)
    if cache_key in _parser_cache:
        return _parser_cache[cache_key]

    try:
        from tree_sitter import Language as TSLanguage, Parser

        lang_obj = _load_ts_language(language, file_ext)
        if lang_obj is None:
            return None
        parser = Parser(TSLanguage(lang_obj))
        _parser_cache[cache_key] = parser
        return parser
    except Exception:
        logger.debug("Failed to create tree-sitter parser for %s", language, exc_info=True)
        _parser_cache[cache_key] = None  # type: ignore[assignment]
        return None


def _load_ts_language(language: Language, file_ext: str) -> Any | None:
    """Dynamically import the tree-sitter grammar for *language*."""
    try:
        if language == Language.PYTHON:
            import tree_sitter_python

            return tree_sitter_python.language()
        if language == Language.JAVASCRIPT:
            import tree_sitter_javascript

            return tree_sitter_javascript.language()
        if language == Language.TYPESCRIPT:
            import tree_sitter_typescript

            if file_ext in (".tsx", ".jsx"):
                return tree_sitter_typescript.language_tsx()
            return tree_sitter_typescript.language_typescript()
        if language == Language.GO:
            import tree_sitter_go

            return tree_sitter_go.language()
        if language == Language.JAVA:
            import tree_sitter_java

            return tree_sitter_java.language()
        if language == Language.PHP:
            import tree_sitter_php

            return tree_sitter_php.language_php()
    except Exception:
        logger.debug("Could not load tree-sitter grammar for %s", language, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# File content retrieval
# ---------------------------------------------------------------------------


def _get_old_content(repo_dir: Path, base_ref: str, file_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "show", f"{base_ref}:{file_path}"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="replace")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_new_content(repo_dir: Path, file_path: str) -> str | None:
    full = repo_dir / file_path
    if not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Tier 0: structural comparison (ignore comments + extra nodes)
# ---------------------------------------------------------------------------
# We convert each AST into a nested tuple and compare.  Tuples are hashable
# so this is effectively an O(N) tree equality check.


def _to_structural_tuple(node: Any) -> tuple:
    """Convert a tree-sitter node to a hashable tuple, skipping extras."""
    children = [c for c in node.children if not c.is_extra]
    if not children:
        return (node.type, node.text)
    return (node.type, *(_to_structural_tuple(c) for c in children))


# ---------------------------------------------------------------------------
# Tier 1: normalized comparison (alpha-rename + commutative sort)
# ---------------------------------------------------------------------------


def _to_normalized_tuple(
    node: Any,
    config: _LangConfig,
    rename_map: dict[str, str],
    counter: list[int],
    parent_type: str = "",
    in_scope: bool = False,
) -> tuple:
    """Convert a tree-sitter node to a normalized hashable tuple.

    Normalizations applied:
      - Skip ``is_extra`` nodes (comments).
      - Alpha-rename local identifiers by order of first binding (only
        inside function/method/lambda scopes — module-level and class-level
        names are compared literally).
      - Sort operands of commutative binary expressions.
    """
    ntype: str = node.type
    children = [c for c in node.children if not c.is_extra]

    # Enter new scope – fork the rename map
    if ntype in config.scope_types:
        rename_map = dict(rename_map)
        counter = [counter[0]]
        # Only enable alpha-renaming inside function-like scopes, not classes
        if ntype in config.local_scope_types:
            in_scope = True

    # Leaf node
    if not children:
        text: bytes = node.text
        if ntype in config.identifier_types:
            text_str = text.decode("utf-8", errors="replace")
            # Register new binding only inside a scope (function/method/lambda)
            if in_scope and parent_type in config.binding_parent_types and text_str not in rename_map:
                rename_map[text_str] = f"_v{counter[0]}"
                counter[0] += 1
            # Return renamed version if known
            renamed = rename_map.get(text_str, text_str)
            return (ntype, renamed.encode("utf-8"))
        return (ntype, text)

    # Recurse into children
    child_tuples = tuple(
        _to_normalized_tuple(c, config, rename_map, counter, parent_type=ntype, in_scope=in_scope) for c in children
    )

    # Sort commutative binary expressions
    if ntype in config.binary_expr_types and len(child_tuples) == 3:
        # child_tuples = (left_operand, operator, right_operand)
        op_tuple = child_tuples[1]
        if len(op_tuple) >= 2 and isinstance(op_tuple[1], bytes):
            op_text = op_tuple[1].decode("utf-8", errors="replace")
            if op_text in config.commutative_ops:
                left, right = child_tuples[0], child_tuples[2]
                if right < left:
                    child_tuples = (right, op_tuple, left)

    return (ntype, *child_tuples)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
    language = EXTENSION_TO_LANGUAGE.get(ext)
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
    """Return True if the changes to *file_path* are cosmetic-only.

    Cosmetic means the AST is structurally equivalent (tier 0) or
    normalized-equivalent after alpha-renaming and commutative sorting
    (tier 1).  Returns False (assume semantic) on any error.
    """
    ext = Path(file_path).suffix.lower()
    language = EXTENSION_TO_LANGUAGE.get(ext)
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


def strip_comments_from_source(file_path: str, source: str) -> str:
    """Return *source* with tree-sitter comment/extra nodes removed when supported.

    Falls back to the original source for unsupported languages or parse errors.
    Newlines from removed ranges are preserved to keep the remaining text readable.
    """
    ext = Path(file_path).suffix.lower()
    language = EXTENSION_TO_LANGUAGE.get(ext)
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


def fingerprint_source_text(file_path: str, source: str) -> str:
    """Return a stable fingerprint for comment-stripped, whitespace-normalized source."""
    normalized = _WHITESPACE_RE.sub(" ", strip_comments_from_source(file_path, source)).strip()
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()


def fingerprint_method_signature(file_path: str, source: str) -> str | None:
    """Return a stable fingerprint for the signature/header portion of a method source slice.

    Returns ``None`` when a safe signature/header boundary cannot be determined.
    """
    ext = Path(file_path).suffix.lower()
    stripped = strip_comments_from_source(file_path, source)
    signature: str | None = None

    if ext == ".py":
        collected: list[str] = []
        for line in stripped.splitlines():
            if not collected and not line.strip():
                continue
            collected.append(line)
            line_text = line.strip()
            if line_text.startswith(("def ", "async def ", "class ")) and line_text.endswith(":"):
                signature = "\n".join(collected)
                break
            if (
                collected
                and line_text.endswith(":")
                and any(token in "\n".join(collected) for token in ("def ", "async def ", "class "))
            ):
                signature = "\n".join(collected)
                break
    else:
        brace_index = stripped.find("{")
        if brace_index != -1:
            signature = stripped[:brace_index]

    if signature is None:
        return None

    normalized = _WHITESPACE_RE.sub(" ", signature).strip()
    if not normalized:
        return None
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()
