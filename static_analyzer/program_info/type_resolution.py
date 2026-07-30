"""Conservative structural type-reference resolution."""

import re
from collections import defaultdict
from dataclasses import dataclass

from static_analyzer.constants import CLASS_TYPES
from static_analyzer.engine.models import SymbolInfo

_IDENTIFIER = re.compile(r"(?<!\w)([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?!\w)")
_IGNORED = {
    "any",
    "bool",
    "boolean",
    "byte",
    "char",
    "class",
    "def",
    "dict",
    "double",
    "dynamic",
    "false",
    "float",
    "function",
    "int",
    "integer",
    "interface",
    "list",
    "long",
    "map",
    "none",
    "null",
    "number",
    "object",
    "optional",
    "return",
    "set",
    "short",
    "str",
    "string",
    "struct",
    "true",
    "tuple",
    "void",
}


@dataclass(frozen=True)
class TypeResolutionDiagnostic:
    source: str
    token: str
    reason: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class TypeResolutionResult:
    edges: tuple[tuple[str, str], ...]
    diagnostics: tuple[TypeResolutionDiagnostic, ...]


def resolve_type_references(symbols: dict[str, SymbolInfo]) -> list[tuple[str, str]]:
    """Resolve unambiguous type names present in LSP symbol details."""
    return list(resolve_type_reference_result(symbols).edges)


def resolve_type_reference_result(symbols: dict[str, SymbolInfo]) -> TypeResolutionResult:
    """Resolve known types and retain deterministic ambiguity diagnostics."""
    canonical = _canonical_symbols(symbols)
    types_by_name: dict[str, list[str]] = defaultdict(list)
    for qualified_name, symbol in canonical.items():
        if symbol.kind in CLASS_TYPES:
            types_by_name[symbol.name].append(qualified_name)

    resolved: set[tuple[str, str]] = set()
    diagnostics: set[TypeResolutionDiagnostic] = set()
    for source, symbol in canonical.items():
        detail = getattr(symbol, "detail", "")
        if not detail:
            continue
        type_parameters = _type_parameters(detail)
        for token in _IDENTIFIER.findall(detail):
            simple = token.rsplit(".", 1)[-1]
            if simple.lower() in _IGNORED or simple in type_parameters or (len(simple) == 1 and simple.isupper()):
                continue
            candidates = _rank_candidates(source, token, types_by_name.get(simple, []), canonical)
            if len(candidates) == 1 and candidates[0] != source:
                resolved.add((source, candidates[0]))
            elif candidates:
                diagnostics.add(TypeResolutionDiagnostic(source, token, "ambiguous", tuple(candidates)))
            elif simple[:1].isupper():
                diagnostics.add(TypeResolutionDiagnostic(source, token, "unresolved"))
    return TypeResolutionResult(
        tuple(sorted(resolved)), tuple(sorted(diagnostics, key=lambda item: (item.source, item.token)))
    )


def _type_parameters(detail: str) -> set[str]:
    declarations = re.findall(r"(?:<|\[)([A-Z](?:\s*,\s*[A-Z])*)(?:>|\])", detail)
    return {name.strip() for declaration in declarations for name in declaration.split(",")}


def _rank_candidates(source: str, token: str, candidates: list[str], symbols: dict[str, SymbolInfo]) -> list[str]:
    if not candidates:
        return []
    source_symbol = symbols[source]
    source_package = str(source_symbol.file_path.parent)
    source_scope = source.rsplit(".", 1)[0] if "." in source else ""
    scored: list[tuple[int, str]] = []
    for candidate in candidates:
        target = symbols[candidate]
        score = 0
        if candidate == token or candidate.endswith(f".{token}"):
            score += 100
        if str(target.file_path.parent) == source_package:
            score += 20
        common = 0
        for left, right in zip(source_scope.split("."), candidate.split(".")):
            if left != right:
                break
            common += 1
        score += common
        scored.append((score, candidate))
    best = max(score for score, _ in scored)
    return sorted(candidate for score, candidate in scored if score == best)


def _canonical_symbols(symbols: dict[str, SymbolInfo]) -> dict[str, SymbolInfo]:
    by_location: dict[tuple[str, int, int], tuple[str, SymbolInfo]] = {}
    for qualified_name, symbol in symbols.items():
        key = symbol.definition_location
        current = by_location.get(key)
        if current is None or (len(qualified_name), qualified_name) > (len(current[0]), current[0]):
            by_location[key] = qualified_name, symbol
    return {qualified_name: symbol for qualified_name, symbol in by_location.values()}
