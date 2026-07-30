import pytest

from static_analyzer.program_info import Channel, EdgeEvidence, ProgramInformation, SymbolFact


def info() -> ProgramInformation:
    symbols = tuple(
        SymbolFact(name, 12, path, 1, 1) for name, path in (("a", "one/a.py"), ("b", "one/b.py"), ("c", "two/c.py"))
    )
    edges = tuple(
        sorted(
            (
                EdgeEvidence("a", "b", Channel.CALL, 2, 2),
                EdgeEvidence("a", "b", Channel.TYPEREF),
                EdgeEvidence("c", "b", Channel.IMPORT),
            )
        )
    )
    return ProgramInformation(symbols, edges)


def test_symbol_and_typed_evidence_queries() -> None:
    data = info()
    assert data.symbol("a").file_path == "one/a.py"
    assert data.outgoing_evidence("a", {Channel.CALL})[0].count == 2
    assert tuple(edge.channel for edge in data.incoming_evidence("b")) == (
        Channel.CALL,
        Channel.TYPEREF,
        Channel.IMPORT,
    )
    assert data.neighbors("b", {Channel.CALL}) == ("a",)
    with pytest.raises(KeyError, match="Unknown program symbol"):
        data.symbol("missing")


def test_subgraphs_by_symbol_file_and_package() -> None:
    data = info()
    assert tuple(symbol.qualified_name for symbol in data.subgraph_by_symbols({"a", "b"}).symbols) == ("a", "b")
    assert len(data.subgraph_by_files({"one/a.py", "one/b.py"}).edges) == 2
    assert tuple(symbol.qualified_name for symbol in data.subgraph_by_packages({"two"}).symbols) == ("c",)
    with pytest.raises(KeyError, match="missing"):
        data.subgraph_by_symbols({"missing"})


def test_projection_preserves_every_channel_on_shared_pair() -> None:
    graph = info().projection()
    attrs = graph["a"]["b"]
    assert attrs == {
        "call": 2.0,
        "call_count": 2,
        "weight": 2.5,
        "typeref": 0.5,
        "typeref_count": 1,
        "evidence": (("call", 2, 2), ("typeref", 1, 1.0)),
    }
    assert graph.nodes["a"]["file_path"] == "one/a.py"


def test_projection_channel_filter_never_filters_calls() -> None:
    graph = info().projection({Channel.IMPORT.value})
    assert graph.has_edge("a", "b")
    assert graph["a"]["b"]["weight"] == 2
    assert graph.has_edge("c", "b")
