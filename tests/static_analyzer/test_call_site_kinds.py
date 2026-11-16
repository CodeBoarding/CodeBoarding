"""Every call site an edge holds says what the engine decided it is, through to the stored graph."""

import pickle
from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.cfg import CallGraph, Edge
from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.java_adapter import JavaAdapter
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.adapters.typescript_adapter import TypeScriptAdapter
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.edge_builder import CallEdgeSink, DispatchIndex, EdgeMap, build_edges_via_definitions
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import CallFlowGraph, CallSite, LanguageAnalysisResult, SymbolInfo
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.graph_definitions import (
    CALL,
    CONSTRUCT,
    IMPLEMENTATION,
    METHOD_GROUP,
    OVERRIDE,
    OWNER,
    RUNTIME_REFERENCE,
)
from static_analyzer.node import Node

# (qualified name, kind, file, line, column, end line, owning class) -- zero-based, as the server reports.
Declaration = tuple[str, int, Path, int, int, int, str]


def _symbol(qualified_name: str, kind: int, path: Path, line: int, column: int, end_line: int, owner: str):
    return SymbolInfo(
        name=qualified_name.rsplit(".", 1)[-1],
        qualified_name=qualified_name,
        kind=kind,
        file_path=path,
        start_line=line,
        start_char=column,
        end_line=end_line,
        end_char=100,
        parent_chain=[(owner, NodeType.CLASS)] if owner else [],
    )


def _build(
    adapter: LanguageAdapter,
    declarations: list[Declaration],
    answers: dict[tuple[int, int], tuple[Path, int, int]],
    source: Path,
) -> EdgeMap:
    """Build the edges of *source*, the server answering each queried position from *answers*."""
    lsp = MagicMock()
    lsp.send_definition_batch.side_effect = lambda queries: [
        (
            [{"uri": at[0].as_uri(), "range": {"start": {"line": at[1], "character": at[2]}}}]
            if (at := answers.get((line, column))) is not None
            else []
        )
        for _, line, column in queries
    ]
    lsp.send_implementation_batch.side_effect = lambda queries, **_: [[] for _ in queries]
    table = SymbolTable(adapter)
    for qualified_name, kind, path, line, column, end_line, owner in declarations:
        symbol = _symbol(qualified_name, kind, path, line, column, end_line, owner)
        table._symbols[qualified_name] = symbol
        table._file_symbols.setdefault(str(path), []).append(symbol)
        table._primary_file_symbols.setdefault(str(path), []).append(symbol)
    table.build_indices()
    return build_edges_via_definitions(adapter, EdgeBuildContext(lsp, table, SourceInspector()), [source])


def _kinds(edges: EdgeMap, source: str, destination: str) -> dict[tuple[int, int], set[str]]:
    """The kinds each one-based ``(line, column)`` holds on one edge."""
    found: dict[tuple[int, int], set[str]] = {}
    for site in edges[(source, destination)]:
        found.setdefault((site.line, site.column), set()).add(site.kind)
    return found


class TestPython:
    SOURCE = (
        "class LlmError(Exception):\n"
        "    pass\n"
        "\n"
        "\n"
        "class Client:\n"
        "    def shutdown(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "def run(client):\n"
        "    try:\n"
        "        client.shutdown()\n"
        "    except LlmError:\n"
        "        pass\n"
        "    if isinstance(client, Client):\n"
        "        Client()\n"
    )

    def _edges(self, tmp_path: Path) -> EdgeMap:
        source = tmp_path / "app.py"
        source.write_text(self.SOURCE)
        declarations: list[Declaration] = [
            ("app.LlmError", NodeType.CLASS, source, 0, 6, 1, ""),
            ("app.Client", NodeType.CLASS, source, 4, 6, 6, ""),
            ("app.Client.shutdown", NodeType.METHOD, source, 5, 8, 6, "Client"),
            ("app.run", NodeType.FUNCTION, source, 9, 4, 15, ""),
        ]
        answers = {
            (11, 15): (source, 5, 8),  # client.shutdown()
            (12, 11): (source, 0, 6),  # except LlmError
            (14, 26): (source, 4, 6),  # isinstance(client, Client)
            (15, 8): (source, 4, 6),  # Client()
        }
        return _build(PythonAdapter(), declarations, answers, source)

    def test_a_method_call_is_a_call_and_its_class_only_the_owner(self, tmp_path: Path):
        edges = self._edges(tmp_path)

        assert _kinds(edges, "app.run", "app.Client.shutdown") == {(12, 16): {CALL}}
        assert _kinds(edges, "app.run", "app.Client")[(12, 16)] == {OWNER}

    def test_an_except_clause_is_a_runtime_reference(self, tmp_path: Path):
        assert _kinds(self._edges(tmp_path), "app.run", "app.LlmError") == {(13, 12): {RUNTIME_REFERENCE}}

    def test_a_class_passed_to_isinstance_is_a_method_group(self, tmp_path: Path):
        assert _kinds(self._edges(tmp_path), "app.run", "app.Client")[(15, 27)] == {METHOD_GROUP}

    def test_calling_a_class_constructs_it(self, tmp_path: Path):
        assert _kinds(self._edges(tmp_path), "app.run", "app.Client")[(16, 9)] == {CONSTRUCT}


class TestTypeScript:
    SOURCE = (
        "class GhError extends Error {}\n"
        "\n"
        "class Client {\n"
        "  shutdown(): void {}\n"
        "}\n"
        "\n"
        "function run(client: Client): void {\n"
        "  try {\n"
        "    client.shutdown();\n"
        "  } catch (e) {\n"
        "    if (e instanceof GhError) {\n"
        "      new Client();\n"
        "    }\n"
        "  }\n"
        "}\n"
    )

    def _edges(self, tmp_path: Path) -> EdgeMap:
        source = tmp_path / "app.ts"
        source.write_text(self.SOURCE)
        declarations: list[Declaration] = [
            ("app.GhError", NodeType.CLASS, source, 0, 6, 0, ""),
            ("app.Client", NodeType.CLASS, source, 2, 6, 4, ""),
            ("app.Client.shutdown", NodeType.METHOD, source, 3, 2, 3, "Client"),
            ("app.run", NodeType.FUNCTION, source, 6, 9, 14, ""),
        ]
        answers = {
            (8, 11): (source, 3, 2),  # client.shutdown()
            (10, 21): (source, 0, 6),  # e instanceof GhError
            (11, 10): (source, 2, 6),  # new Client()
        }
        return _build(TypeScriptAdapter(), declarations, answers, source)

    def test_a_method_call_is_a_call_and_its_class_only_the_owner(self, tmp_path: Path):
        edges = self._edges(tmp_path)

        assert _kinds(edges, "app.run", "app.Client.shutdown") == {(9, 12): {CALL}}
        assert _kinds(edges, "app.run", "app.Client")[(9, 12)] == {OWNER}

    def test_instanceof_is_a_runtime_reference(self, tmp_path: Path):
        assert _kinds(self._edges(tmp_path), "app.run", "app.GhError") == {(11, 22): {RUNTIME_REFERENCE}}

    def test_new_constructs(self, tmp_path: Path):
        assert _kinds(self._edges(tmp_path), "app.run", "app.Client")[(12, 11)] == {CONSTRUCT}


class TestJava:
    def _edges(self, tmp_path: Path) -> EdgeMap:
        source = tmp_path / "App.java"
        source.write_text(
            "class App {\n"
            "  void run(Client client) {\n"
            "    try {\n"
            "      client.shutdown();\n"
            "    } catch (GhError e) {\n"
            "      if (e instanceof GhError) {\n"
            "        new Client();\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
        client = tmp_path / "Client.java"
        client.write_text("class Client {\n  void shutdown() {}\n}\n")
        error = tmp_path / "GhError.java"
        error.write_text("class GhError extends Exception {}\n")
        declarations: list[Declaration] = [
            ("App", NodeType.CLASS, source, 0, 6, 10, ""),
            ("App.run", NodeType.METHOD, source, 1, 7, 9, "App"),
            ("Client", NodeType.CLASS, client, 0, 6, 2, ""),
            ("Client.shutdown", NodeType.METHOD, client, 1, 7, 1, "Client"),
            ("GhError", NodeType.CLASS, error, 0, 6, 0, ""),
        ]
        answers = {
            (3, 13): (client, 1, 7),  # client.shutdown()
            (4, 13): (error, 0, 6),  # catch (GhError e)
            (5, 23): (error, 0, 6),  # e instanceof GhError
            (6, 12): (client, 0, 6),  # new Client()
        }
        return _build(JavaAdapter(), declarations, answers, source)

    def test_a_method_call_is_a_call_and_its_class_only_the_owner(self, tmp_path: Path):
        edges = self._edges(tmp_path)

        assert _kinds(edges, "App.run", "Client.shutdown") == {(4, 14): {CALL}}
        assert _kinds(edges, "App.run", "Client")[(4, 14)] == {OWNER}

    def test_catch_and_instanceof_are_runtime_references(self, tmp_path: Path):
        assert _kinds(self._edges(tmp_path), "App.run", "GhError") == {
            (5, 14): {RUNTIME_REFERENCE},
            (6, 24): {RUNTIME_REFERENCE},
        }

    def test_new_constructs(self, tmp_path: Path):
        assert _kinds(self._edges(tmp_path), "App.run", "Client")[(7, 13)] == {CONSTRUCT}


class TestDispatch:
    SOURCE = (
        "class Base:\n"
        "    def speak(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "class Dog(Base):\n"
        "    def speak(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "def main(pet):\n"
        "    pet.speak()\n"
    )

    def test_a_dispatched_call_reaches_its_override_as_an_override(self, tmp_path: Path):
        source = tmp_path / "app.py"
        source.write_text(self.SOURCE)
        adapter = PythonAdapter()
        table = SymbolTable(adapter)
        base = _symbol("app.Base", NodeType.CLASS, source, 0, 6, 2, "")
        dog = _symbol("app.Dog", NodeType.CLASS, source, 5, 6, 7, "")
        for symbol in (
            base,
            _symbol("app.Base.speak", NodeType.METHOD, source, 1, 8, 2, "Base"),
            dog,
            _symbol("app.Dog.speak", NodeType.METHOD, source, 6, 8, 7, "Dog"),
            _symbol("app.main", NodeType.FUNCTION, source, 10, 4, 11, ""),
        ):
            table._symbols[symbol.qualified_name] = symbol
        sink = CallEdgeSink(adapter, table, SourceInspector(), DispatchIndex({"Base": [dog]}, {}, set()))

        sink.add(table.symbols["app.main"], table.symbols["app.Base.speak"], CallSite(str(source), 12, 9), CALL)

        assert _kinds(sink.edges, "app.main", "app.Base.speak") == {(12, 9): {CALL}}
        assert _kinds(sink.edges, "app.main", "app.Dog.speak") == {(12, 9): {OVERRIDE}}
        assert _kinds(sink.edges, "app.main", "app.Base") == {(12, 9): {OWNER}}

    def test_an_implementation_answer_is_an_implementation(self, tmp_path: Path):
        source = tmp_path / "app.py"
        source.write_text("def main():\n    speak()\n\ndef speak():\n    pass\n\ndef dog_speak():\n    pass\n")
        lsp = MagicMock()
        lsp.send_definition_batch.side_effect = lambda queries: [
            [{"uri": source.as_uri(), "range": {"start": {"line": 3, "character": 4}}}] if (line, col) == (1, 4) else []
            for _, line, col in queries
        ]
        lsp.send_implementation_batch.side_effect = lambda queries, **_: [
            [{"uri": source.as_uri(), "range": {"start": {"line": 6, "character": 4}}}] for _ in queries
        ]
        adapter = PythonAdapter()
        table = SymbolTable(adapter)
        symbols = [
            _symbol("app.main", NodeType.FUNCTION, source, 0, 4, 1, ""),
            _symbol("app.speak", NodeType.METHOD, source, 3, 4, 4, ""),
            _symbol("app.dog_speak", NodeType.METHOD, source, 6, 4, 7, ""),
        ]
        for symbol in symbols:
            table._symbols[symbol.qualified_name] = symbol
        table._file_symbols[str(source)] = symbols
        table._primary_file_symbols[str(source)] = symbols
        table.build_indices()

        edges = build_edges_via_definitions(adapter, EdgeBuildContext(lsp, table, SourceInspector()), [source])

        assert _kinds(edges, "app.main", "app.speak") == {(2, 5): {CALL}}
        assert _kinds(edges, "app.main", "app.dog_speak") == {(2, 5): {IMPLEMENTATION}}


class TestStoredEdge:
    def _graph(self) -> CallGraph:
        graph = CallGraph(language="python")
        graph.add_node(Node("app.run", NodeType.FUNCTION, "/r/app.py", 10, 16))
        graph.add_node(Node("app.Client", NodeType.CLASS, "/r/app.py", 5, 7))
        return graph

    def test_two_kinds_at_one_site_are_one_site_holding_both(self):
        graph = self._graph()

        graph.add_edge("app.run", "app.Client", [{"file": "/r/app.py", "line": 16, "column": 9, "kinds": (CALL,)}])
        graph.add_edge("app.run", "app.Client", [{"file": "/r/app.py", "line": 16, "column": 9, "kinds": (OWNER,)}])

        assert graph.edges[0].call_sites == [{"file": "/r/app.py", "line": 16, "column": 9, "kinds": (CALL, OWNER)}]

    def test_a_site_from_an_older_cache_has_no_kind_until_one_is_known(self):
        graph = self._graph()

        graph.add_edge("app.run", "app.Client", [{"file": "/r/app.py", "line": 16, "column": 9}])
        assert graph.edges[0].call_sites == [{"file": "/r/app.py", "line": 16, "column": 9}]

        graph.add_edge("app.run", "app.Client", [{"file": "/r/app.py", "line": 16, "column": 9, "kinds": (CALL,)}])
        assert graph.edges[0].call_sites == [{"file": "/r/app.py", "line": 16, "column": 9, "kinds": (CALL,)}]

    def test_an_edge_pickled_before_kinds_still_takes_new_sites(self):
        graph = self._graph()
        old = Edge.__new__(Edge)
        old.src_node, old.dst_node = graph.nodes["app.run"], graph.nodes["app.Client"]
        old._call_sites = [{"file": "/r/app.py", "line": 16, "column": 9}]
        old._call_site_keys = {(("column", 9), ("file", "/r/app.py"), ("line", 16))}
        restored: Edge = pickle.loads(pickle.dumps(old))

        restored.add_call_site({"file": "/r/app.py", "line": 16, "column": 9, "kinds": (CONSTRUCT,)})
        restored.add_call_site({"file": "/r/app.py", "line": 20, "column": 5})

        assert restored.call_sites == [
            {"file": "/r/app.py", "line": 16, "column": 9, "kinds": (CONSTRUCT,)},
            {"file": "/r/app.py", "line": 20, "column": 5},
        ]

    def test_the_converter_stores_each_engine_kind_on_one_site(self):
        adapter = PythonAdapter()
        table = SymbolTable(adapter)
        for symbol in (
            _symbol("app.run", NodeType.FUNCTION, Path("/r/app.py"), 9, 4, 15, ""),
            _symbol("app.Client", NodeType.CLASS, Path("/r/app.py"), 4, 6, 6, ""),
        ):
            table._symbols[symbol.qualified_name] = symbol
        sites = [CallSite("/r/app.py", 16, 9, CONSTRUCT), CallSite("/r/app.py", 16, 9, METHOD_GROUP)]
        result = LanguageAnalysisResult(
            cfg=CallFlowGraph.from_edge_set({("app.run", "app.Client"): sites}), source_files=["/r/app.py"]
        )

        graph = convert_to_codeboarding_format(table, result, adapter)["call_graph"]
        (site,) = graph.edges[0].call_sites

        assert site == {"file": "/r/app.py", "line": 16, "column": 9, "kinds": (CONSTRUCT, METHOD_GROUP)}
        # What an external reader of the pickle takes from a site is still there.
        assert vars(graph.edges[0])["_call_sites"][0]["line"] == 16
