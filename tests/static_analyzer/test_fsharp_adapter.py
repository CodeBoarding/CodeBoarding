"""Tests for the F# language adapter."""

from pathlib import Path

from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter
from static_analyzer.engine.adapters.fsharp_adapter import FSharpAdapter, _bound, _encloses


def _symbol(name: str, kind: NodeType, start: tuple[int, int], end: tuple[int, int]) -> dict:
    return {
        "name": name,
        "kind": kind,
        "range": {
            "start": {"line": start[0], "character": start[1]},
            "end": {"line": end[0], "character": end[1]},
        },
    }


class TestFSharpAdapterProperties:
    """Basic adapter property tests."""

    def test_language(self):
        assert FSharpAdapter().language == "FSharp"

    def test_language_enum(self):
        assert FSharpAdapter().language_enum is Language.FSHARP

    def test_file_extensions(self):
        assert FSharpAdapter().file_extensions == (".fs",)

    def test_lsp_command(self):
        assert FSharpAdapter().lsp_command == ["fsautocomplete"]

    def test_language_id(self):
        assert FSharpAdapter().language_id == "fsharp"

    def test_registry_returns_fsharp_adapter(self):
        assert isinstance(get_adapter("FSharp"), FSharpAdapter)

    def test_interleaves_did_open_with_symbols(self):
        """FsAutoComplete type-checks lazily, so each file needs its own barrier."""
        assert FSharpAdapter().interleave_did_open_with_symbols is True

    def test_probes_before_open(self):
        assert FSharpAdapter().probe_before_open is True

    def test_workspace_owns_documents(self):
        assert FSharpAdapter().workspace_owns_documents is True

    def test_fails_on_empty_symbols(self):
        assert FSharpAdapter().fail_on_empty_symbols is True

    def test_modules_are_reference_worthy(self):
        """A module, not a class, is F#'s usual unit of code."""
        assert FSharpAdapter().is_reference_worthy(NodeType.MODULE) is True

    def test_init_options_request_automatic_workspace_init(self):
        """Without this, every documentSymbol fails with 'not in LoadedProjects'."""
        assert FSharpAdapter().get_lsp_init_options() == {"AutomaticWorkspaceInit": True}

    def test_workspace_settings_repeat_the_workspace_init_opt_in(self):
        assert FSharpAdapter().get_workspace_settings() == {"FSharp": {"automaticWorkspaceInit": True}}


class TestRangeHelpers:
    """Tests for the range helpers behind the nesting reconstruction."""

    def test_bound_reads_a_range_edge(self):
        symbol = _symbol("M", NodeType.MODULE, (3, 4), (9, 1))
        assert _bound(symbol, "start") == (3, 4)
        assert _bound(symbol, "end") == (9, 1)

    def test_bound_defaults_to_origin_when_the_range_is_absent(self):
        assert _bound({"name": "M"}, "start") == (0, 0)

    def test_encloses_detects_containment(self):
        outer = _symbol("Outer", NodeType.MODULE, (0, 0), (10, 0))
        inner = _symbol("Inner", NodeType.FUNCTION, (2, 4), (3, 0))
        assert _encloses(outer, inner) is True
        assert _encloses(inner, outer) is False

    def test_identical_ranges_do_not_enclose_each_other(self):
        """Otherwise a symbol reported twice at one range would parent itself."""
        first = _symbol("A", NodeType.MODULE, (1, 0), (2, 0))
        second = _symbol("B", NodeType.FUNCTION, (1, 0), (2, 0))
        assert _encloses(first, second) is False

    def test_partial_overlap_does_not_enclose(self):
        first = _symbol("A", NodeType.MODULE, (0, 0), (5, 0))
        second = _symbol("B", NodeType.MODULE, (3, 0), (8, 0))
        assert _encloses(first, second) is False


class TestNormalizeSymbols:
    """FsAutoComplete answers with a flat list, so the adapter rebuilds the tree."""

    def test_empty_input(self):
        assert FSharpAdapter().normalize_symbols([]) == []

    def test_nesting_is_rebuilt_from_ranges(self):
        flat = [
            _symbol("isEligible", NodeType.FIELD, (7, 8), (7, 76)),
            _symbol("Probe.Domain", NodeType.MODULE, (0, 0), (10, 60)),
            _symbol("Rules", NodeType.MODULE, (4, 0), (7, 76)),
        ]

        roots = FSharpAdapter().normalize_symbols(flat)

        assert [root["name"] for root in roots] == ["Probe.Domain"]
        rules = roots[0]["children"][0]
        assert rules["name"] == "Rules"
        assert [child["name"] for child in rules["children"]] == ["isEligible"]

    def test_a_sibling_after_a_nested_module_stays_at_module_level(self):
        """``describe`` follows ``Rules`` in the file but is not inside it."""
        flat = [
            _symbol("Probe.Domain", NodeType.MODULE, (0, 0), (10, 60)),
            _symbol("Rules", NodeType.MODULE, (4, 0), (7, 76)),
            _symbol("describe", NodeType.FIELD, (9, 4), (10, 60)),
        ]

        roots = FSharpAdapter().normalize_symbols(flat)

        assert [child["name"] for child in roots[0]["children"]] == ["Rules", "describe"]

    def test_module_level_let_becomes_a_function(self):
        """Nothing downstream treats a field as callable, so a module-level
        ``let`` — how F# declares a function — has to be named one."""
        flat = [
            _symbol("Probe.Domain", NodeType.MODULE, (0, 0), (5, 0)),
            _symbol("describe", NodeType.FIELD, (2, 4), (3, 0)),
        ]

        roots = FSharpAdapter().normalize_symbols(flat)

        assert roots[0]["children"][0]["kind"] == NodeType.FUNCTION

    def test_let_inside_a_type_stays_a_field(self):
        flat = [
            _symbol("Probe.Domain", NodeType.MODULE, (0, 0), (5, 0)),
            _symbol("Customer", NodeType.CLASS, (2, 5), (2, 46)),
            _symbol("Name", NodeType.FIELD, (2, 18), (2, 30)),
        ]

        roots = FSharpAdapter().normalize_symbols(flat)

        customer = roots[0]["children"][0]
        assert customer["name"] == "Customer"
        assert customer["children"][0]["kind"] == NodeType.FIELD

    def test_the_input_symbols_are_not_mutated(self):
        flat = [_symbol("describe", NodeType.FIELD, (0, 0), (1, 0))]

        FSharpAdapter().normalize_symbols(flat)

        assert flat[0]["kind"] == NodeType.FIELD
        assert "children" not in flat[0]

    def test_the_hook_is_opt_in_for_other_languages(self):
        symbols = [_symbol("Program", NodeType.CLASS, (0, 0), (1, 0))]
        assert CSharpAdapter().normalize_symbols(symbols) == symbols


class TestBuildQualifiedName:
    """F# declares its module path in source, so that path wins over the file path."""

    def test_leading_module_is_its_own_qualified_name(self):
        name = FSharpAdapter().build_qualified_name(
            Path("/repo/Domain.fs"), "Probe.Domain", NodeType.MODULE, [], Path("/repo")
        )
        assert name == "Probe.Domain"

    def test_nested_module_is_prefixed_with_its_scope(self):
        """The server reports a nested module by its short name only."""
        name = FSharpAdapter().build_qualified_name(
            Path("/repo/Domain.fs"), "Rules", NodeType.MODULE, [("Probe.Domain", NodeType.MODULE)], Path("/repo")
        )
        assert name == "Probe.Domain.Rules"

    def test_function_inside_nested_modules(self):
        name = FSharpAdapter().build_qualified_name(
            Path("/repo/Domain.fs"),
            "isEligible",
            NodeType.FUNCTION,
            [("Probe.Domain", NodeType.MODULE), ("Rules", NodeType.MODULE)],
            Path("/repo"),
        )
        assert name == "Probe.Domain.Rules.isEligible"

    def test_type_member_keeps_its_containing_type(self):
        name = FSharpAdapter().build_qualified_name(
            Path("/repo/Domain.fs"),
            "Name",
            NodeType.FIELD,
            [("Probe.Domain", NodeType.MODULE), ("Customer", NodeType.CLASS)],
            Path("/repo"),
        )
        assert name == "Probe.Domain.Customer.Name"

    def test_falls_back_to_the_file_path_without_a_declared_scope(self):
        name = FSharpAdapter().build_qualified_name(
            Path("/repo/src/Helpers.fs"), "trim", NodeType.FUNCTION, [], Path("/repo")
        )
        assert name == "src.Helpers.trim"

    def test_extract_package_drops_the_symbol_and_its_container(self):
        assert FSharpAdapter().extract_package("Layer2.Common.String.trim") == "Layer2.Common"
