"""Internal data structures for method and cluster deltas."""

from dataclasses import dataclass

from agents.analysis_result_responses import RelationEdge
from agents.file_index_models import MethodEntry
from static_analyzer.clustering import ClusterResult
from static_analyzer.program_graph import ProgramGraph


@dataclass(frozen=True)
class MethodRecord:
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    node_type: str
    content_hash: str

    def to_entry(self) -> MethodEntry:
        return MethodEntry(
            qualified_name=self.qualified_name,
            start_line=self.start_line,
            end_line=self.end_line,
            node_type=self.node_type,
            content_hash=self.content_hash,
        )


@dataclass(frozen=True)
class MethodDelta:
    added: set[str]
    deleted: set[str]
    modified: set[str]
    cluster_reassigned: set[str]
    call_boundary_changed: set[str]
    metadata_only: set[str]
    added_calls: set[tuple[str, str]]
    deleted_calls: set[tuple[str, str]]

    @property
    def architecture_methods(self) -> set[str]:
        return self.added | self.deleted | self.modified | self.cluster_reassigned | self.call_boundary_changed


@dataclass
class ScopeGraph:
    cluster_results: dict[str, ClusterResult]
    graphs: dict[str, ProgramGraph]
    cluster_members: dict[str, set[str]]
    method_to_cluster: dict[str, str]


@dataclass(frozen=True)
class CatalogEdge:
    edge_id: str
    pair: tuple[str, str]
    edge: RelationEdge
