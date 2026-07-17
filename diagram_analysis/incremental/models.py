"""Typed state shared by incremental analysis stages."""

from dataclasses import dataclass, field

from pydantic import Field

from agents.analysis_result_responses import LLMBaseModel, RelationEdge
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


class PartitionGroup(LLMBaseModel):
    component_id: str = Field(
        default="",
        description="Existing direct-child component ID to retain, or an empty string for a new component.",
    )
    cluster_ids: list[str] = Field(description="Current mutable cluster IDs assigned to this component.")

    def llm_str(self) -> str:
        target = self.component_id or "new"
        return f"{target}: {', '.join(self.cluster_ids)}"


class ScopePartition(LLMBaseModel):
    groups: dict[str, PartitionGroup] = Field(
        default_factory=dict,
        description="Resulting affected component groups keyed by stable working-document keys.",
    )

    def llm_str(self) -> str:
        return "\n".join(f"{key}: {group.llm_str()}" for key, group in sorted(self.groups.items())) or "Empty"


@dataclass(frozen=True)
class ScopePatchContext:
    architecture_outline: str
    parent: str
    immutable_components: str
    mutable_components: str
    current_modules: str
    method_changes: str
    call_changes: str
    expected_cluster_ids: set[str]


class ComponentContent(LLMBaseModel):
    name: str = Field(description="Short name for the component's current responsibility.")
    description: str = Field(description="Current responsibility and main flow of this component.")
    key_entity_qualified_names: list[str] = Field(
        default_factory=list,
        description="Two to five exact qualified symbol names from the assigned clusters.",
    )

    def llm_str(self) -> str:
        return f"{self.name}: {self.description}"


class ScopeDescription(LLMBaseModel):
    description: str = Field(description="Current purpose and main flow represented by this scope.")

    def llm_str(self) -> str:
        return self.description


@dataclass(frozen=True)
class ComponentContentContext:
    architecture_outline: str
    parent: str
    component_id: str
    current_clusters: str
    method_changes: str
    call_changes: str
    allowed_qualified_names: set[str]
    is_new: bool


@dataclass
class ScopeImpact:
    affected_methods: set[str]
    mutable_component_ids: set[str]
    deleted_component_ids: set[str]
    mutable_cluster_ids: set[str]
    immutable_cluster_assignments: dict[str, list[str]]
    proposed_partition: ScopePartition


@dataclass(frozen=True)
class ScopeTask:
    component_id: str
    depth: int
    is_new: bool = False


@dataclass(frozen=True)
class CatalogEdge:
    edge_id: str
    pair: tuple[str, str]
    edge: RelationEdge


@dataclass
class ScopeUpdate:
    affected_component_ids: set[str] = field(default_factory=set)
    new_component_ids: set[str] = field(default_factory=set)
