from static_analyzer.constants import Language, NodeType
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


def graph(annotation: str) -> CallGraph:
    result = CallGraph(language="python")
    result.add_node(Node("app.run", NodeType.FUNCTION, "app.py", 0, 2, annotations=(annotation,)))
    return result


def test_source_fact_only_change_is_detected_in_incremental_delta():
    old = StaticAnalysisResults()
    old.add_cfg(Language.PYTHON, graph("old"))
    new = StaticAnalysisResults()
    new.add_cfg(Language.PYTHON, graph("new"))
    new.incremental_base_results = old
    delta = new.incremental_program_delta(Language.PYTHON)
    assert delta.added_symbols == ()
    assert delta.removed_symbols == ()
    assert delta.changed_symbols == ("app.run",)
    assert delta.added_edges == ()
    assert delta.statistics_changed is False


def test_missing_incremental_baseline_fails_specifically():
    results = StaticAnalysisResults()
    results.add_cfg(Language.PYTHON, graph("fact"))
    try:
        results.incremental_program_delta(Language.PYTHON)
    except ValueError as error:
        assert str(error) == "Program delta requires incremental_base_results"
    else:
        raise AssertionError("missing baseline was accepted")


def test_scoped_delta_detects_only_selected_file_changes():
    old_graph = graph("old")
    old_graph.add_node(Node("stable.keep", NodeType.FUNCTION, "stable.py", 0, 1, annotations=("same",)))
    new_graph = graph("new")
    new_graph.add_node(Node("stable.keep", NodeType.FUNCTION, "stable.py", 0, 1, annotations=("same",)))
    old = StaticAnalysisResults()
    old.add_cfg(Language.PYTHON, old_graph)
    new = StaticAnalysisResults()
    new.add_cfg(Language.PYTHON, new_graph)
    new.incremental_base_results = old
    changed = new.compare_program_scope(Language.PYTHON, files={"app.py"})
    stable = new.compare_program_scope(Language.PYTHON, files={"stable.py"})
    assert changed.changed_symbols == ("app.run",)
    assert stable.is_empty is True
