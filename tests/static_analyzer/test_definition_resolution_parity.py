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
from static_analyzer.engine.models import CallSite, SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.graph_definitions import (
    CALL,
    containing_source_node,
    MEMBER_READ,
    METHOD_GROUP,
    GraphIndex,
    targets_for,
)
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


LIMIT = 10


def payload():
    return 2
"""

# (qualified name, kind, name line, name column, declaration end line, declaration end column)
DECLARATIONS = [
    ("m.head", NodeType.FUNCTION, 0, 4, 3, 18),
    ("m.Box", NodeType.CLASS, 6, 6, 8, 18),
    ("m.Box.hold", NodeType.METHOD, 7, 8, 8, 18),
    ("m.solo", NodeType.FUNCTION, 12, 4, 13, 12),
    ("m.LIMIT", NodeType.CONSTANT, 16, 0, 16, 5),
    ("m.payload", NodeType.FUNCTION, 19, 4, 20, 12),
]

# Positions a server can answer with, the declaration each must name, and the nodes a call
# there reaches -- the declaration plus, by the definitions convention, the class holding it.
CALL_CASES = [
    ((0, 4), "m.head", ["m.head"]),  # exact
    ((7, 8), "m.Box.hold", ["m.Box.hold", "m.Box"]),  # exact, inside a class
    ((7, 20), "", []),  # a parameter on its declaration's own line
    ((6, 10), "", []),  # past the class name on its line
    ((1, 4), "", []),  # a parameter named like a function the file declares
    ((3, 11), "", []),  # a body line
    ((11, 4), "m.solo", ["m.solo"]),  # the overload signature line, by the name declared there
    ((6, 0), "", []),  # before every declaration on the line
    ((5, 0), "", []),  # a blank line
    ((16, 0), "m.LIMIT", ["m.LIMIT"]),  # a constant is a declaration, exactly matched
]

# A name passed as a value is a call only when it denotes something callable, and the two
# resolvers have to agree on that too.
METHOD_GROUP_CASES = [
    ((0, 4), ["m.head"]),  # exact, callable
    ((7, 20), []),  # a parameter is a value, not a declaration
    ((11, 4), ["m.solo"]),  # the overload signature line still names a callable
    ((16, 0), []),  # a constant passed by name is a value, not a call
]

# A member read is a call only when it reaches a callable: a getter, or a method handed on.
MEMBER_READ_CASES = [
    ((7, 8), ["m.Box.hold", "m.Box"]),
    ((16, 0), []),  # a constant read is a value
]


@pytest.fixture
def module(tmp_path: Path) -> Path:
    path = tmp_path / "m.py"
    path.write_text(SOURCE)
    return path


def _symbol_index(module: Path) -> SymbolIndex:
    table = SymbolTable(PythonAdapter())
    for qualified_name, kind, line, column, end_line, end_char in DECLARATIONS:
        table.symbols[qualified_name] = SymbolInfo(
            name=qualified_name.rsplit(".", 1)[-1],
            qualified_name=qualified_name,
            kind=kind,
            file_path=module,
            start_line=line,
            start_char=column,
            end_line=end_line,
            end_char=end_char,
        )
    return SymbolIndex(table, SourceInspector())


def _graph_index(module: Path) -> GraphIndex:
    graph = CallGraph(language="python")
    for qualified_name, kind, line, column, end_line, _end_char in DECLARATIONS:
        graph.add_node(Node(qualified_name, kind, str(module), line + 1, end_line + 1, col_start=column))
    return GraphIndex(graph, SourceInspector())


def test_a_decoration_is_credited_to_the_member_it_decorates(tmp_path: Path) -> None:
    """The engine's ``_caller_at`` redirects a decoration; the graph side has to as well.

    A warm start that looked the position up as written would credit a class-member
    decorator to its class and a module-level one to nobody.
    """
    module = tmp_path / "m.py"
    module.write_text("def register(f):\n    return f\n\n\n@register\ndef target():\n    return 1\n")
    graph = CallGraph(language="python")
    graph.add_node(Node("m.register", NodeType.FUNCTION, str(module), 1, 2, col_start=4))
    graph.add_node(Node("m.target", NodeType.FUNCTION, str(module), 6, 7, col_start=4))

    node = containing_source_node(GraphIndex(graph, SourceInspector()), str(module), 4, 1)

    assert node is not None and node.fully_qualified_name == "m.target"


@pytest.mark.parametrize("position,declaration,targets", CALL_CASES)
def test_both_resolvers_name_the_same_declaration(
    module: Path, position: tuple[int, int], declaration: str, targets: list[str]
) -> None:
    line, character = position
    definition = {"uri": module.as_uri(), "range": {"start": {"line": line, "character": character}}}

    match = _symbol_index(module).resolve(definition)
    site = CallSite.from_lsp_position(str(module), line, character)
    nodes = targets_for(_graph_index(module), str(module), line, character, CALL, PythonAdapter(), site).nodes

    assert (match.qualified_name if match else "") == declaration
    assert [node.fully_qualified_name for node in nodes] == targets


@pytest.mark.parametrize("position,targets", METHOD_GROUP_CASES)
def test_both_resolvers_agree_on_what_a_name_passed_as_a_value_reaches(
    module: Path, position: tuple[int, int], targets: list[str]
) -> None:
    line, character = position
    definition = {"uri": module.as_uri(), "range": {"start": {"line": line, "character": character}}}
    adapter = PythonAdapter()

    match = _symbol_index(module).resolve(definition)
    engine_target = match is not None and adapter.is_callable(match.kind)
    site = CallSite.from_lsp_position(str(module), line, character)
    nodes = targets_for(_graph_index(module), str(module), line, character, METHOD_GROUP, adapter, site).nodes

    assert engine_target == bool(targets)
    assert [node.fully_qualified_name for node in nodes] == targets


@pytest.mark.parametrize("position,targets", MEMBER_READ_CASES)
def test_both_resolvers_agree_on_what_a_member_read_reaches(
    module: Path, position: tuple[int, int], targets: list[str]
) -> None:
    line, character = position
    definition = {"uri": module.as_uri(), "range": {"start": {"line": line, "character": character}}}
    adapter = PythonAdapter()

    match = _symbol_index(module).resolve(definition)
    engine_target = match is not None and adapter.is_callable(match.kind)
    site = CallSite.from_lsp_position(str(module), line, character)
    nodes = targets_for(_graph_index(module), str(module), line, character, MEMBER_READ, adapter, site).nodes

    assert engine_target == bool(targets)
    assert [node.fully_qualified_name for node in nodes] == targets


def test_both_resolvers_take_an_overload_signature_to_its_own_class_implementation(tmp_path: Path) -> None:
    module = tmp_path / "graph.ts"
    module.write_text(
        "class Node {\n  getModel(): number;\n  getModel(path?: string): number { return 1; }\n}\n"
        "class Edge {\n  getModel(path?: string): number { return 2; }\n}\n"
    )
    table = SymbolTable(PythonAdapter())
    graph = CallGraph(language="typescript")
    for qualified_name, line in (("graph.Node.getModel", 2), ("graph.Edge.getModel", 5)):
        table.symbols[qualified_name] = SymbolInfo(
            name="getModel",
            qualified_name=qualified_name,
            kind=NodeType.METHOD,
            file_path=module,
            start_line=line,
            start_char=2,
            end_line=line,
            end_char=46,
        )
        graph.add_node(Node(qualified_name, NodeType.METHOD, str(module), line + 1, line + 1, col_start=2))
    definition = {"uri": module.as_uri(), "range": {"start": {"line": 1, "character": 2}}}

    match = SymbolIndex(table, SourceInspector()).resolve(definition)
    node = GraphIndex(graph, SourceInspector()).declaration_at(str(module), 1, 2)

    assert match is not None and match.qualified_name == "graph.Node.getModel"
    assert node is not None and node.fully_qualified_name == "graph.Node.getModel"


def test_a_member_named_on_the_base_is_owed_no_implementation_query(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    module.write_text(
        "class Base:\n    def describe(self):\n        return 1\n\n\n"
        "class Child(Base):\n    def describe(self):\n        return super().describe()\n\n"
        "    def show(self):\n        return self.describe()\n"
    )
    graph = CallGraph(language="python")
    graph.add_node(Node("m.Base", NodeType.CLASS, str(module), 1, 3, col_start=6))
    graph.add_node(Node("m.Base.describe", NodeType.METHOD, str(module), 2, 3, col_start=8))
    index = GraphIndex(graph, SourceInspector())
    adapter = PythonAdapter()

    through_super = CallSite.from_lsp_position(str(module), 7, 23)
    through_self = CallSite.from_lsp_position(str(module), 10, 20)

    assert targets_for(index, str(module), 1, 8, CALL, adapter, through_super).implementations == []
    assert targets_for(index, str(module), 1, 8, CALL, adapter, through_self).implementations == [(str(module), 1, 8)]
