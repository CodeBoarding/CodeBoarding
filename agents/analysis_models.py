"""Runtime/domain models for an analysis result.

These are the in-memory objects the pipeline builds and enriches (component
ids, file/method groups, content hashes, CFG edge counts) and persists. They
are NOT sent to the LLM — see ``agents.agent_responses`` for the LLM-facing
schemas, each of which has a ``from_llm`` promoter here.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

from agents.agent_responses import (
    AnalysisInsightsLLM,
    ComponentLLM,
    RelationLLM,
    SourceCodeReference,
    _analysis_str,
    _component_str,
    _relation_str,
)

logger = logging.getLogger(__name__)


class MethodEntry(BaseModel):
    """A single method/function within a file, with its location and identity."""

    qualified_name: str = Field(description="Fully qualified name of the method or function.")
    start_line: int = Field(description="Starting line number in the file.")
    end_line: int = Field(description="Ending line number in the file.")
    node_type: str = Field(description="Node type name matching NodeType enum (e.g. METHOD, FUNCTION, CLASS).")
    content_hash: str = Field(
        default="",
        description="Truncated SHA-256 of the method's source lines; '' when source was unavailable.",
    )

    def __hash__(self) -> int:
        return hash(self.qualified_name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MethodEntry):
            return NotImplemented
        return self.qualified_name == other.qualified_name

    @classmethod
    def from_node(cls, node) -> MethodEntry:
        """Build from a ``static_analyzer.Node``. Accepts ``Any`` to avoid a hard dep."""
        return cls(
            qualified_name=node.fully_qualified_name,
            start_line=node.line_start,
            end_line=node.line_end,
            node_type=node.type.name,
        )


class FileMethodGroup(BaseModel):
    """All methods/functions belonging to a component within a single file."""

    file_path: str = Field(description="Relative path to the source file.")
    methods: list[MethodEntry] = Field(
        default_factory=list,
        description="Methods and functions in this file that belong to the component, sorted by start_line.",
    )


class FileEntry(BaseModel):
    """Single source of truth for methods in one file."""

    methods: list[MethodEntry] = Field(
        default_factory=list,
        description="Methods and functions in this file, sorted by start line.",
    )
    content_hash: str = Field(
        default="",
        description="Truncated SHA-256 of the entire file's source lines; '' when source was unavailable.",
    )


class Relation(BaseModel):
    """Runtime relation: the LLM's fields plus deterministically-resolved identity and static evidence."""

    relation: str = Field(description="Single phrase used for the relationship of two components.")
    src_name: str = Field(description="Source component name")
    dst_name: str = Field(description="Target component name")
    src_id: str = Field(default="", description="Component ID of the source.")
    dst_id: str = Field(default="", description="Component ID of the destination.")
    edge_count: int = Field(default=0, description="Number of CFG edges backing this relation.")
    is_static: bool = Field(default=False, description="True if derived from static CFG analysis.")

    @classmethod
    def from_llm(cls, llm: RelationLLM) -> Relation:
        return cls(relation=llm.relation, src_name=llm.src_name, dst_name=llm.dst_name)

    def llm_str(self):
        return _relation_str(self)


class Component(BaseModel):
    """Runtime component: the LLM's fields plus fields populated deterministically from cluster results."""

    name: str = Field(description="Name of the component")
    description: str = Field(description="A short description of the component.")
    key_entities: list[SourceCodeReference] = Field(
        description="The most important/critical classes and methods that represent this component's core functionality."
    )
    source_group_names: list[str] = Field(
        default_factory=list,
        description="Names of the cluster groups from the grouping analysis that this component encompasses.",
    )
    source_cluster_ids: list[int] = Field(
        default_factory=list,
        description="Cluster IDs this component encompasses (populated deterministically from source_group_names).",
    )
    file_methods: list[FileMethodGroup] = Field(
        default_factory=list,
        description="All methods/functions belonging to this component, grouped by file (populated deterministically).",
    )
    component_id: str = Field(default="", description="Deterministic unique identifier for this component.")

    @classmethod
    def from_llm(cls, llm: ComponentLLM) -> Component:
        return cls(
            name=llm.name,
            description=llm.description,
            key_entities=llm.key_entities,
            source_group_names=llm.source_group_names,
        )

    def llm_str(self):
        return _component_str(self)


class AnalysisInsights(BaseModel):
    """Runtime analysis insights: enriched components/relations plus the deterministic file index."""

    description: str = Field(description="One paragraph explaining the functionality represented by this graph.")
    components: list[Component] = Field(default_factory=list, description="Identified components, enriched.")
    components_relations: list[Relation] = Field(
        default_factory=list, description="Relations among components, enriched."
    )
    files: dict[str, FileEntry] = Field(
        default_factory=dict,
        description="Top-level file index keyed by relative file path. Contains all methods and statuses.",
    )

    @classmethod
    def from_llm(cls, llm: AnalysisInsightsLLM) -> AnalysisInsights:
        """Promote a parsed LLM result into the runtime model; internal fields default, filled later by enrichment."""
        return cls(
            description=llm.description,
            components=[Component.from_llm(c) for c in llm.components],
            components_relations=[Relation.from_llm(r) for r in llm.components_relations],
        )

    def llm_str(self):
        return _analysis_str(self)

    def file_to_component(self) -> dict[str, str]:
        """Build file path -> component_id mapping from root components."""
        return {str(PurePosixPath(fg.file_path)): c.component_id for c in self.components for fg in c.file_methods}


def assign_component_ids(analysis: AnalysisInsights, parent_id: str = "", only_new: bool = False) -> None:
    """Assign hierarchical component IDs based on sibling index.

    IDs encode structural position in the component tree:
    - Top-level (parent_id=""): "1", "2", "3"
    - Under "1" (parent_id="1"): "1.1", "1.2"
    - Under "1.2" (parent_id="1.2"): "1.2.1", "1.2.2"

    With ``only_new=True`` (incremental path), components that already carry a
    populated ``component_id`` are preserved verbatim and only siblings with an
    empty id are assigned a fresh slot — used when stitching new components into
    an existing tree without renumbering survivors.
    """
    if only_new:
        used_indices: set[int] = set()
        for component in analysis.components:
            if not component.component_id:
                continue
            tail = component.component_id.split(".")[-1]
            if tail.isdigit():
                used_indices.add(int(tail))
        next_idx = max(used_indices, default=0) + 1
        for component in analysis.components:
            if component.component_id:
                continue
            component.component_id = f"{parent_id}.{next_idx}" if parent_id else str(next_idx)
            next_idx += 1
    else:
        for idx, component in enumerate(analysis.components, start=1):
            component.component_id = f"{parent_id}.{idx}" if parent_id else str(idx)

    # Assign relation IDs by looking up component names (first occurrence wins for duplicates)
    name_to_id: dict[str, str] = {}
    for c in analysis.components:
        if c.name in name_to_id:
            logger.warning(
                f"Duplicate component name '{c.name}' found during ID assignment; "
                f"relation lookup will use the first occurrence (ID: {name_to_id[c.name]})"
            )
        else:
            name_to_id[c.name] = c.component_id
    for relation in analysis.components_relations:
        relation.src_id = name_to_id.get(relation.src_name, "")
        relation.dst_id = name_to_id.get(relation.dst_name, "")


def iter_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> list[Component]:
    """Return every component across the root and all sub-analyses, in tree order."""
    components = list(root_analysis.components)
    for sub in sub_analyses.values():
        components.extend(sub.components)
    return components


def index_components_by_id(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, Component]:
    """Build a ``component_id -> Component`` lookup across the full tree.

    Components without a ``component_id`` are skipped. Later occurrences of
    the same id silently override earlier ones (sub-analyses win over root).
    """
    return {c.component_id: c for c in iter_components(root_analysis, sub_analyses) if c.component_id}
