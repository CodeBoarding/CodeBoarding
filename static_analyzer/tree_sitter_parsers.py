"""Tree-sitter parser cache and per-language tuple normalization."""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from static_analyzer.constants import Language

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LangConfig:
    binary_expr_types: frozenset[str]
    commutative_ops: frozenset[str]
    scope_types: frozenset[str]  # nodes that start a new naming scope (fork rename map)
    local_scope_types: frozenset[str]  # function-like scopes where alpha-renaming is allowed
    identifier_types: frozenset[str]  # leaf nodes that carry local names
    binding_parent_types: frozenset[str]  # parent nodes whose child id is a binding


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


# Parser cache keyed by "language:ext" so .tsx and .ts get distinct parsers.
_parser_cache: dict[str, Any] = {}


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


def _to_structural_tuple(node: Any) -> tuple:
    """Convert a tree-sitter node to a hashable tuple, skipping extras."""
    children = [c for c in node.children if not c.is_extra]
    if not children:
        return (node.type, node.text)
    return (node.type, *(_to_structural_tuple(c) for c in children))


def _to_normalized_tuple(
    node: Any,
    config: _LangConfig,
    rename_map: dict[str, str],
    counter: list[int],
    parent_type: str = "",
    in_scope: bool = False,
) -> tuple:
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
