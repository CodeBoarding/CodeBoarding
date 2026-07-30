from pathlib import Path

from static_analyzer.constants import NodeType
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.program_info.type_resolution import resolve_type_references


def symbol(name: str, qualified_name: str, kind: NodeType, line: int, detail: str = "") -> SymbolInfo:
    return SymbolInfo(name, qualified_name, kind, Path("module.py"), line, 0, line + 1, 0, detail=detail)


def test_resolves_unambiguous_types_from_lsp_detail():
    symbols = {
        "pkg.Widget": symbol("Widget", "pkg.Widget", NodeType.CLASS, 1),
        "pkg.build": symbol("build", "pkg.build", NodeType.FUNCTION, 5, "(item: Widget) -> Widget"),
    }
    assert resolve_type_references(symbols) == [("pkg.build", "pkg.Widget")]


def test_skips_ambiguous_unknown_self_and_empty_type_details():
    symbols = {
        "a.Widget": symbol("Widget", "a.Widget", NodeType.CLASS, 1, "Widget"),
        "b.Widget": symbol("Widget", "b.Widget", NodeType.CLASS, 3),
        "pkg.build": symbol("build", "pkg.build", NodeType.FUNCTION, 5, "Widget Missing"),
        "pkg.empty": symbol("empty", "pkg.empty", NodeType.FUNCTION, 7),
    }
    assert resolve_type_references(symbols) == []


def test_aliases_at_same_definition_are_canonicalized():
    primary = symbol("Widget", "pkg.Container.Widget", NodeType.CLASS, 1)
    alias = symbol("Widget", "pkg.Widget", NodeType.CLASS, 1)
    caller = symbol("make", "pkg.make", NodeType.FUNCTION, 5, "() -> Widget")
    assert resolve_type_references(
        {primary.qualified_name: primary, alias.qualified_name: alias, caller.qualified_name: caller}
    ) == [("pkg.make", "pkg.Container.Widget")]
