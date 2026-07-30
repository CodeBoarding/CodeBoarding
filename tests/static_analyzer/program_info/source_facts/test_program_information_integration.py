import networkx as nx
import pytest

from static_analyzer.constants import Language, NodeType
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph, EdgeKind
from static_analyzer.node import Node
from static_analyzer.program_info.errors import ProgramInformationError
from static_analyzer.program_info.projection import from_projection, to_projection
from static_analyzer.program_info.source_facts import (
    AnnotationFact,
    ImportFact,
    SourceEvidence,
    SourceSpan,
    TypeUseFact,
)


def rich_graph() -> CallGraph:
    graph = CallGraph(language="python")
    span = SourceSpan(1, 0, 1, 6, 0, 6)
    annotation = AnnotationFact(SourceEvidence("python", "app.py", span, "@route", "decorator"), "route")
    imported = ImportFact(SourceEvidence("python", "app.py", span, "service", "import_statement"), "service")
    type_use = TypeUseFact(SourceEvidence("python", "app.py", span, "Result", "type"), "Result")
    graph.add_node(
        Node(
            "app.run",
            NodeType.FUNCTION,
            "app.py",
            1,
            5,
            visibility="public",
            modifiers=("async",),
            annotations=(annotation,),
            import_evidence=(imported,),
            type_use_evidence=(type_use,),
        )
    )
    graph.add_node(Node("domain.Result", NodeType.CLASS, "domain.py", 1, 3, visibility="private"))
    graph.add_reference_edge("app.run", "domain.Result", EdgeKind.TYPEREF)
    return graph


def test_symbol_to_program_information_retains_every_source_fact():
    results = StaticAnalysisResults()
    results.add_cfg(Language.PYTHON, rich_graph())
    information = results.program_information(Language.PYTHON)
    fact = information.symbol("app.run")
    assert fact.visibility == "public"
    assert fact.modifiers == ("async",)
    assert fact.annotations == ("route",)
    assert fact.import_evidence == ("service",)
    assert fact.type_use_evidence == ("Result",)
    assert [(edge.source, edge.destination, edge.channel.value) for edge in information.edges] == [
        ("app.run", "domain.Result", "typeref")
    ]


def test_legacy_node_without_new_attributes_uses_concrete_defaults():
    legacy = Node("legacy.run", NodeType.FUNCTION, "legacy.py", 0, 1)
    for attribute in ("visibility", "modifiers", "annotations", "import_evidence", "type_use_evidence"):
        delattr(legacy, attribute)
    graph = CallGraph(nodes={"legacy.run": legacy})
    results = StaticAnalysisResults()
    results.add_cfg(Language.PYTHON, graph)
    fact = results.program_information(Language.PYTHON).symbol("legacy.run")
    assert fact.visibility == "unknown"
    assert fact.modifiers == ()
    assert fact.annotations == ()
    assert fact.import_evidence == ()
    assert fact.type_use_evidence == ()


def test_projection_round_trip_preserves_source_facts_and_fingerprint():
    results = StaticAnalysisResults()
    results.add_cfg(Language.PYTHON, rich_graph())
    information = results.program_information(Language.PYTHON)
    decoded = from_projection(to_projection(information))
    assert decoded == information
    assert decoded.snapshot().fingerprint == information.snapshot().fingerprint
    assert decoded.symbol("app.run").annotations == ("route",)


def test_projection_rejects_bad_version_and_malformed_source_attrs():
    bad_version = nx.DiGraph(program_information_codec=999)
    with pytest.raises(ProgramInformationError, match="Unsupported program-information codec version: 999"):
        from_projection(bad_version)
    malformed = nx.DiGraph(program_information_codec=1)
    malformed.add_node("bad", file_path="bad.py", line_start="not-an-int", line_end=2, kind=12)
    with pytest.raises(ProgramInformationError, match="Symbol 'bad' has malformed projection attrs"):
        from_projection(malformed)


def test_source_fact_profile_is_bounded_and_does_not_change_membership():
    results = StaticAnalysisResults()
    results.add_cfg(Language.PYTHON, rich_graph())
    information = results.program_information(Language.PYTHON)
    before = tuple(fact.qualified_name for fact in information.symbols)
    profile = information.source_fact_profile({"app.run"})
    assert profile.public_count == 1
    assert profile.private_count == 0
    assert profile.annotation_count == 1
    assert profile.import_evidence_count == 1
    assert profile.type_use_evidence_count == 1
    assert profile.unresolved_source_fact_count == 1
    assert tuple(fact.qualified_name for fact in information.symbols) == before
