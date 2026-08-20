"""A call is credited to the innermost declaration a reader would name."""

import unittest
from pathlib import Path

from static_analyzer.constants import NodeType
from static_analyzer.engine.adapters.typescript_adapter import TypeScriptAdapter
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.symbol_table import SymbolTable


def sym(qname, start, end, kind=NodeType.FUNCTION, name=None, promoted=False):
    return SymbolInfo(
        name=name if name is not None else qname.split(".")[-1],
        qualified_name=qname,
        kind=kind,
        file_path=Path("src/github.ts"),
        start_line=start,
        start_char=0,
        end_line=end,
        end_char=0,
        promoted_from_variable=promoted,
    )


class TestAttributionSymbol(unittest.TestCase):
    def setUp(self):
        self.st = SymbolTable(TypeScriptAdapter())

    def _load(self, symbols):
        self.st.file_symbols["src/github.ts"] = symbols

    def test_a_named_symbol_credits_itself(self):
        named = sym("src.github.listReviewComments", 10, 30)
        self._load([named])
        self.assertIs(self.st.attribution_symbol(named), named)

    def test_a_callback_credits_its_enclosing_function(self):
        outer = sym("src.github.listReviewComments", 10, 30)
        callback = sym("src.github.listReviewComments.raw.ghPaginate() callback", 12, 14, name="ghPaginate() callback")
        self._load([outer, callback])
        self.assertEqual(self.st.attribution_symbol(callback).qualified_name, outer.qualified_name)

    def test_a_promoted_variable_wrapper_is_stepped_past(self):
        # `const raw = await ghPaginate(...)` is promoted to class-like at registration.
        outer = sym("src.github.listReviewComments", 10, 30)
        wrapper = sym("src.github.listReviewComments.raw", 12, 16, kind=NodeType.CLASS, promoted=True)
        self._load([outer, wrapper])
        self.assertEqual(self.st.attribution_symbol(wrapper).qualified_name, outer.qualified_name)

    def test_the_innermost_named_enclosure_wins(self):
        file_level = sym("src.github.module", 1, 100, kind=NodeType.CLASS)
        outer = sym("src.github.listReviewComments", 10, 30)
        callback = sym("src.github.listReviewComments.map() callback", 12, 14, name="map() callback")
        self._load([file_level, outer, callback])
        self.assertEqual(self.st.attribution_symbol(callback).qualified_name, outer.qualified_name)

    def test_a_callback_inside_a_callback_reaches_the_named_function(self):
        outer = sym("src.github.listRepos", 10, 40)
        mid = sym("src.github.listRepos.then() callback", 12, 30, name="then() callback")
        inner = sym("src.github.listRepos.then() callback.map() callback", 14, 20, name="map() callback")
        self._load([outer, mid, inner])
        self.assertEqual(self.st.attribution_symbol(inner).qualified_name, outer.qualified_name)

    def test_a_module_level_anonymous_function_credits_itself(self):
        # Nothing encloses it, so there is no better name to use.
        lone = sym("src.github.<function>", 5, 9, name="<function>")
        self._load([lone])
        self.assertIs(self.st.attribution_symbol(lone), lone)


if __name__ == "__main__":
    unittest.main()
