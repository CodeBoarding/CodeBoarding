from pathlib import Path

from agents.agent_responses import AnalysisInsights, Relation, RelationEdge, SourceCodeReference
from agents.file_index_models import FileEntry, MethodEntry
from agents.relation_edges import index_relation_endpoints
from static_analyzer.constants import NodeType


def test_index_relation_endpoints_keeps_analyzed_kind_and_merges_endpoint_spans() -> None:
    analyzed = MethodEntry(
        qualified_name="pkg.source",
        start_line=2,
        end_line=6,
        node_type=NodeType.FUNCTION.name,
        content_hash="source123",
    )
    target = MethodEntry(
        qualified_name="pkg.target",
        start_line=0,
        end_line=0,
        node_type=NodeType.CLASS.name,
    )
    target_start = SourceCodeReference(
        qualified_name="pkg.target",
        reference_file="target.py",
        reference_start_line=12,
    )
    target_end = SourceCodeReference(
        qualified_name="pkg.target",
        reference_file="target.py",
        reference_end_line=18,
    )
    analysis = AnalysisInsights(
        description="",
        components=[],
        components_relations=[
            Relation(
                relation="calls",
                src_name="Source",
                dst_name="Target",
                key_edges=[
                    RelationEdge(
                        source=SourceCodeReference(
                            qualified_name="pkg.source",
                            reference_file="source.py",
                            reference_start_line=1,
                            reference_end_line=20,
                        ),
                        target=target_start,
                    )
                ],
                all_edges=[
                    RelationEdge(
                        source=SourceCodeReference(qualified_name="pkg.source", reference_file="source.py"),
                        target=target_end,
                    )
                ],
            )
        ],
        files={
            "source.py": FileEntry(methods=[analyzed]),
            "target.py": FileEntry(methods=[target]),
        },
    )

    index_relation_endpoints(analysis, Path("/repo"))

    indexed_source = analysis.files["source.py"].methods[0]
    indexed_target = analysis.files["target.py"].methods[0]
    assert indexed_source.node_type == NodeType.FUNCTION.name
    assert (indexed_source.start_line, indexed_source.end_line) == (2, 6)
    assert indexed_source.content_hash == "source123"
    assert indexed_target.node_type == NodeType.CLASS.name
    assert (indexed_target.start_line, indexed_target.end_line) == (12, 18)


def test_index_relation_endpoints_does_not_invent_unknown_method_kinds() -> None:
    analysis = AnalysisInsights(
        description="",
        components=[],
        components_relations=[
            Relation(
                relation="calls",
                src_name="Source",
                dst_name="Target",
                key_edges=[
                    RelationEdge(
                        source=SourceCodeReference(
                            qualified_name="pkg.unknown",
                            reference_file="unknown.py",
                        ),
                        target=SourceCodeReference(qualified_name="external.call"),
                    )
                ],
            )
        ],
    )

    index_relation_endpoints(analysis, Path("/repo"))

    assert analysis.files == {}
