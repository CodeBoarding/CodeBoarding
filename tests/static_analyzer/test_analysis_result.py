import pytest

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.program_graph import ProgramGraph, ProgramNode, ProgramNodeKind


def _graph(*symbol_ids: str, language: Language = Language.PYTHON) -> ProgramGraph:
    lang = str(language)
    graph = ProgramGraph(language=lang)
    for symbol_id in symbol_ids:
        line_start = sum(ord(character) for character in symbol_id)
        graph.add_node(
            ProgramNode(
                node_id=symbol_id,
                kind=ProgramNodeKind.SYMBOL,
                language=lang,
                name=symbol_id.rsplit(".", 1)[-1],
                file_path=f"{symbol_id.split('.')[0]}.py",
                symbol_type=NodeType.FUNCTION,
                line_start=line_start,
                line_end=line_start + 1,
                reference_worthy=True,
            )
        )
    return graph


def test_program_graphs_merge_per_language() -> None:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, _graph("pkg.first"))
    results.add_program_graph(Language.PYTHON, _graph("pkg.second"))

    assert set(results.get_program_graph(Language.PYTHON).symbols) == {"pkg.first", "pkg.second"}
    assert results.get_languages() == [Language.PYTHON]


def test_reference_lookup_is_case_insensitive_and_returns_program_node() -> None:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, _graph("MyClass.method", "utils.helper"))

    assert results.get_reference(Language.PYTHON, "myclass.method").id == "MyClass.method"
    assert results.get_reference(Language.PYTHON, "UTILS.HELPER").id == "utils.helper"


def test_loose_reference_requires_unique_match() -> None:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, _graph("pkg.unique_function"))

    message, node = results.get_loose_reference(Language.PYTHON, "unique")

    assert message == "pkg.unique_function"
    assert node is not None and node.id == "pkg.unique_function"


def test_source_files_merge_and_rewrite() -> None:
    results = StaticAnalysisResults()
    results.add_source_files(Language.PYTHON, ["src/a.py"])
    results.add_source_files(Language.PYTHON, ["src/b.py"])

    assert results.get_source_files(Language.PYTHON) == ["src/a.py", "src/b.py"]
    assert results.get_all_source_files() == ["src/a.py", "src/b.py"]


def test_available_program_graphs_excludes_empty_languages() -> None:
    results = StaticAnalysisResults()
    results.add_source_files(Language.GO, ["main.go"])
    results.add_program_graph(Language.PYTHON, _graph("pkg.run"))

    assert set(results.available_program_graphs()) == {"python"}


def test_missing_language_queries_fail_without_creating_a_bucket() -> None:
    results = StaticAnalysisResults()

    with pytest.raises(ValueError, match="Program graph"):
        results.get_program_graph(Language.PYTHON)
    with pytest.raises(ValueError, match="Class hierarchy"):
        results.get_hierarchy(Language.PYTHON)
    with pytest.raises(ValueError, match="Package dependencies"):
        results.get_package_dependencies(Language.PYTHON)
    assert results.get_source_files(Language.PYTHON) == []
    assert results.get_languages() == []


def test_reference_lookup_normalizes_generic_signatures_and_go_receivers() -> None:
    results = StaticAnalysisResults()
    results.add_program_graph(
        Language.JAVA,
        _graph(
            "pkg.Service.convert(List<Animal>, T) <T>",
            "pkg.Model.(Entity).GetType",
            language=Language.JAVA,
        ),
    )

    assert (
        results.get_reference(Language.JAVA, "pkg.service.convert(List, Object)").id
        == "pkg.Service.convert(List<Animal>, T) <T>"
    )
    assert results.get_reference(Language.JAVA, "pkg.model.(Entity).gettype").id == "pkg.Model.(Entity).GetType"


def test_cross_language_resolution_uses_exact_then_unique_loose_matches() -> None:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, _graph("pkg.alpha.run", "pkg.beta.run"))
    results.add_program_graph(Language.GO, _graph("service.unique_handler", language=Language.GO))

    message, node = results.get_loose_reference(Language.GO, "unique_handler")
    assert message == "Found a loose match with a fully quantified name: service.unique_handler"
    assert node is not None
    assert results.get_loose_reference(Language.PYTHON, "pkg") == (None, None)
    resolved = results.resolve_across_languages("unique_handler")
    assert resolved is not None and resolved.id == "service.unique_handler"
    assert results.resolve_across_languages("missing") is None
    assert {node.id for node in results.iter_reference_nodes()} == {
        "pkg.alpha.run",
        "pkg.beta.run",
        "service.unique_handler",
    }
