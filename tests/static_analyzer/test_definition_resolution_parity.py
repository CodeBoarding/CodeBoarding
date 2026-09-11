"""The engine and the merged graph must name the same declaration for a definition result.

The full rebuild resolves against the symbol table and the warm start against the merged
graph; a position the two answer differently is an edge that appears or disappears on an
edit alone, so every rule is exercised here through both.
"""

from pathlib import Path

import pytest

from static_analyzer.cfg import CallGraph
from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.edge_builder import SymbolIndex
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.graph_definitions import CALL, GraphIndex, targets_for
from static_analyzer.node import Node

SOURCE = """def head(
    payload,
):
    return payload


class Box:
    def hold(self, item):
        return item


def solo(): ...
def solo():
    return 1
"""

# (qualified name, kind, name line, name column, declaration end line)
DECLARATIONS = [
    ("m.head", NodeType.FUNCTION, 0, 4, 3),
    ("m.Box", NodeType.CLASS, 6, 6, 8),
    ("m.Box.hold", NodeType.METHOD, 7, 8, 8),
    ("m.solo", NodeType.FUNCTION, 12, 4, 13),
]

# Positions a server can answer with, the declaration each must name, and the nodes a call
# there reaches -- the declaration plus, by the definitions convention, the class holding it.
CASES = [
    ((0, 4), "m.head", ["m.head"]),  # exact
    ((7, 8), "m.Box.hold", ["m.Box.hold", "m.Box"]),  # exact, inside a class
    ((7, 20), "m.Box.hold", ["m.Box.hold", "m.Box"]),  # on the declaration's line, past its name
    ((6, 10), "m.Box", ["m.Box"]),  # on the class line, past its name
    ((1, 4), "", []),  # a parameter line
    ((3, 11), "", []),  # a body line
    ((11, 4), "m.solo", ["m.solo"]),  # the overload signature line, by the name written there
    ((6, 0), "", []),  # before every declaration on the line
    ((5, 0), "", []),  # a blank line
]


@pytest.fixture
def module(tmp_path: Path) -> Path:
    path = tmp_path / "m.py"
    path.write_text(SOURCE)
    return path


def _symbol_index(module: Path) -> SymbolIndex:
    table = SymbolTable(PythonAdapter())
    for qualified_name, kind, line, column, end_line in DECLARATIONS:
        table.symbols[qualified_name] = SymbolInfo(
            name=qualified_name.rsplit(".", 1)[-1],
            qualified_name=qualified_name,
            kind=kind,
            file_path=module,
            start_line=line,
            start_char=column,
            end_line=end_line,
            end_char=0,
        )
    return SymbolIndex(table, SourceInspector())


def _graph_index(module: Path) -> GraphIndex:
    graph = CallGraph(language="python")
    for qualified_name, kind, line, column, end_line in DECLARATIONS:
        graph.add_node(Node(qualified_name, kind, str(module), line + 1, end_line + 1, col_start=column))
    return GraphIndex(graph, SourceInspector())


@pytest.mark.parametrize("position,declaration,targets", CASES)
def test_both_resolvers_name_the_same_declaration(
    module: Path, position: tuple[int, int], declaration: str, targets: list[str]
) -> None:
    line, character = position
    definition = {"uri": module.as_uri(), "range": {"start": {"line": line, "character": character}}}

    symbol = _symbol_index(module).resolve(definition)
    nodes = targets_for(_graph_index(module), str(module), line, character, CALL, PythonAdapter())

    assert (symbol.qualified_name if symbol else "") == declaration
    assert [node.fully_qualified_name for node in nodes] == targets
