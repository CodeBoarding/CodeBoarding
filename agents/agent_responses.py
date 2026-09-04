from __future__ import annotations

import abc
import logging
from abc import abstractmethod
from collections.abc import Hashable
from enum import StrEnum

from pydantic import BaseModel, Field

from clustering_ids import ComponentId
from agents.file_index_models import FileEntry, FileMethodGroup, MethodIndexEntry
from agents.scope_ids import ROOT_SCOPE_ID

logger = logging.getLogger(__name__)


class LLMBaseModel(BaseModel, abc.ABC):
    """Base model for semantic response types with prompt rendering."""

    @abstractmethod
    def llm_str(self) -> str:
        raise NotImplementedError("LLM String has to be implemented.")


class SourceCodeReference(LLMBaseModel):
    """Reference to source code including qualified name and file location."""

    qualified_name: str = Field(
        description="Qualified name of the source code, e.g., `langchain.tools.tool` or `langchain_core.output_parsers.JsonOutputParser` or `langchain_core.output_parsers.JsonOutputParser:parse`."
    )

    reference_file: str | None = Field(
        default=None,
        description="File path where the source code is located, e.g., `langchain/tools/tool.py` or `langchain_core/output_parsers/json_output_parser.py`.",
    )

    reference_start_line: int | None = Field(
        default=None,
        description="The line number in the source code where the reference starts. Only if you are absolutely sure add this, otherwise None.",
    )
    reference_end_line: int | None = Field(
        default=None,
        description="The line number in the source code where the reference ends. Only if you are absolutely sure add this, otherwise None.",
    )

    def llm_str(self) -> str:
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`, Lines:({self.reference_start_line}:{self.reference_end_line})"

    def __str__(self) -> str:
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"`{self.qualified_name}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"`{self.qualified_name}`"
        return f"`{self.qualified_name}`:{self.reference_start_line}-{self.reference_end_line}"


RelationEdgeIdentity = tuple[
    str, str, str, str, int | None, int | None, int | None, int | None, tuple[tuple[int, int], ...]
]


class RelationCallSite(BaseModel):
    """Source location for a relation edge occurrence."""

    line: int = Field(description="One-based line number of the call site in the source file.")
    column: int = Field(description="One-based column number of the call site in the source file.")


class RelationEdge(LLMBaseModel):
    """A source-to-target code reference that supports a component relation."""

    source: SourceCodeReference = Field(description="Source method/class/config reference for this interaction.")
    target: SourceCodeReference = Field(description="Target method/class/config reference for this interaction.")
    description: str = Field(default="", description="Short explanation of how source reaches or configures target.")
    call_sites: list[RelationCallSite] = Field(
        default_factory=list,
        description="Call-site line and column pairs for this edge.",
        exclude=True,
    )

    @classmethod
    def from_dict(cls, edge: dict, methods_index: dict[str, MethodIndexEntry]) -> RelationEdge:
        source_key = edge.get("source")
        target_key = edge.get("target")
        if not isinstance(source_key, str) or not isinstance(target_key, str):
            raise ValueError("Relation edge endpoints must be method-index keys")
        call_sites = edge.get("call_sites") or []
        return cls(
            source=_relation_endpoint_from_key(source_key, methods_index),
            target=_relation_endpoint_from_key(target_key, methods_index),
            description=edge.get("description", ""),
            call_sites=[RelationCallSite.model_validate(site) for site in call_sites],
        )

    @classmethod
    def from_edge(cls, edge) -> RelationEdge:
        return cls(
            source=SourceCodeReference(
                qualified_name=edge.src_node.fully_qualified_name,
                reference_file=edge.src_node.file_path,
                reference_start_line=edge.src_node.line_start,
                reference_end_line=edge.src_node.line_end,
            ),
            target=SourceCodeReference(
                qualified_name=edge.dst_node.fully_qualified_name,
                reference_file=edge.dst_node.file_path,
                reference_start_line=edge.dst_node.line_start,
                reference_end_line=edge.dst_node.line_end,
            ),
            call_sites=[RelationCallSite.model_validate(call_site) for call_site in edge.call_sites],
        )

    def llm_str(self) -> str:
        return f"{self.source} -> {self.target}: {self.description}"

    def identity(self) -> RelationEdgeIdentity:
        return (
            self.source.qualified_name,
            self.target.qualified_name,
            self.source.reference_file or "",
            self.target.reference_file or "",
            self.source.reference_start_line,
            self.source.reference_end_line,
            self.target.reference_start_line,
            self.target.reference_end_line,
            tuple(sorted((site.line, site.column) for site in self.call_sites)),
        )


def _relation_endpoint_from_key(
    key: str,
    methods_index: dict[str, MethodIndexEntry],
) -> SourceCodeReference:
    indexed = methods_index.get(key)
    if indexed is not None:
        return SourceCodeReference(
            qualified_name=indexed.qualified_name,
            reference_file=indexed.file_path or None,
            reference_start_line=indexed.start_line,
            reference_end_line=indexed.end_line,
        )

    file_path, separator, qualified_name = key.partition("|")
    if not separator or not qualified_name:
        raise ValueError(f"Malformed relation edge endpoint key: {key!r}")
    return SourceCodeReference(
        qualified_name=qualified_name,
        reference_file=file_path or None,
    )


class Relation(LLMBaseModel):
    """A relationship between two components."""

    relation: str = Field(description="Single phrase used for the relationship of two components.")
    src_name: str = Field(description="Source component name")
    dst_name: str = Field(description="Target component name")
    evidence: str = Field(
        default="",
        description=(
            "Optional concrete evidence for relations that are not direct static calls, "
            "such as REST endpoints, queues, plugin registration, subprocesses, reflection, or config-driven wiring."
        ),
    )
    key_edges: list[RelationEdge] = Field(
        default_factory=list,
        description=(
            "Small set of architecturally important source-to-target edges for this relation. "
            "Use SourceCodeReference objects, similar to key_entities, so references can be resolved to real methods."
        ),
    )
    src_id: str = Field(default="", description="Component ID of the source.", exclude=True)
    dst_id: str = Field(default="", description="Component ID of the destination.", exclude=True)
    is_static: bool = Field(default=False, description="True if derived from static CFG analysis.", exclude=True)
    all_edges: list[RelationEdge] = Field(
        default_factory=list,
        description="All known source-to-target edges for this relation, populated deterministically when available.",
        exclude=True,
    )

    @classmethod
    def from_edges(
        cls,
        relation: str,
        src_name: str,
        dst_name: str,
        src_id: str,
        dst_id: str,
        edges: list[RelationEdge],
        is_static: bool,
        evidence: str = "",
    ) -> Relation:
        return cls(
            relation=relation,
            src_name=src_name,
            dst_name=dst_name,
            evidence=evidence,
            key_edges=[],
            src_id=src_id,
            dst_id=dst_id,
            is_static=is_static,
            all_edges=cls.unique_edges(edges),
        )

    def llm_str(self) -> str:
        return f"({self.src_name}, {self.relation}, {self.dst_name})"

    def pair_key(self, include_relation: bool = False) -> tuple[str, str] | tuple[str, str, str]:
        src = self.src_id
        dst = self.dst_id
        if include_relation:
            return (src, dst, self.relation)
        return (src, dst)

    def with_merged_edges(self) -> Relation:
        key_edges, all_edges = self._merge_edges(self.key_edges, self.all_edges)
        return Relation(
            relation=self.relation,
            src_name=self.src_name,
            dst_name=self.dst_name,
            evidence=self.evidence,
            key_edges=key_edges,
            src_id=self.src_id,
            dst_id=self.dst_id,
            is_static=self.is_static,
            all_edges=all_edges,
        )

    def merge_edges_from(self, relation: Relation) -> None:
        self.key_edges, self.all_edges = self._merge_edges(
            [*self.key_edges, *relation.key_edges], [*self.all_edges, *relation.all_edges]
        )
        self.is_static = self.is_static or relation.is_static
        if not self.evidence:
            self.evidence = relation.evidence

    @staticmethod
    def _merge_edges(
        key_edges: list[RelationEdge], all_edges: list[RelationEdge]
    ) -> tuple[list[RelationEdge], list[RelationEdge]]:
        merged_key_edges = Relation.unique_edges(key_edges)
        merged_all_edges = Relation.unique_edges([*all_edges, *merged_key_edges])
        return merged_key_edges, merged_all_edges

    @staticmethod
    def unique_edges(edges: list[RelationEdge]) -> list[RelationEdge]:
        unique_edges: list[RelationEdge] = []
        seen: set[Hashable] = set()
        for edge in edges:
            edge_id = edge.identity()
            if edge_id in seen:
                continue
            unique_edges.append(edge)
            seen.add(edge_id)
        return unique_edges

    @property
    def edge_count(self) -> int:
        return len(self.all_edges)


class Component(LLMBaseModel):
    """A software component with name, description, and key entities."""

    name: str = Field(description="Name of the component")
    description: str = Field(description="A short description of the component.")

    # LLM picks these: The MOST IMPORTANT/critical methods and classes
    key_entities: list[SourceCodeReference] = Field(
        description="The most important/critical classes and methods that represent this component's core functionality. Pick 2-5 key entities."
    )

    source_cluster_ids: list[ComponentId] = Field(
        description="List of cluster IDs from CFG analysis that this component encompasses.",
        default_factory=list,
        exclude=True,
    )

    file_methods: list[FileMethodGroup] = Field(
        description="All methods/functions belonging to this component, grouped by file (populated deterministically from cluster results).",
        default_factory=list,
        exclude=True,
    )

    component_id: str = Field(
        default="",
        description="Deterministic unique identifier for this component.",
        exclude=True,
    )

    def file_paths(self) -> list[str]:
        """File paths this component spans, one per ``file_methods`` group."""
        return [group.file_path for group in self.file_methods]

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.key_entities:
            qn += "   - *Key Entities*: "
            qn += ", ".join(f"`{q.llm_str()}`" for q in self.key_entities)
        return "\n".join([n, d, qn]).strip()


class AnalysisInsights(LLMBaseModel):
    """Project analysis insights including components and their relations."""

    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose."
    )
    files: dict[str, FileEntry] = Field(
        default_factory=dict,
        description="Top-level file index keyed by relative file path. Contains all methods and statuses.",
        exclude=True,
    )
    components: list[Component] = Field(description="List of the components identified in the project.")
    components_relations: list[Relation] = Field(description="List of relations among the components.")

    def component_by_id(self, component_id: str) -> Component | None:
        """Return the component with this stable ID."""
        return next((component for component in self.components if component.component_id == component_id), None)

    def component_by_name(self, name: str) -> Component | None:
        """Return the first component with this displayed name."""
        return next((component for component in self.components if component.name == name), None)

    def node_owners(self) -> dict[str, str]:
        """Map each indexed symbol to its owning component ID."""
        return {
            method.qualified_name: component.component_id
            for component in self.components
            for file_methods in component.file_methods
            for method in file_methods.methods
        }

    def llm_str(self):
        if not self.components:
            return "No abstract components found."
        title = "# Abstract Components Overview\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


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

    assign_relation_ids(analysis)


def assign_relation_ids(analysis: AnalysisInsights) -> None:
    """Assign relation component IDs by name, dropping any relation whose endpoint
    does not name a component in this scope.

    Relation generation can emit an endpoint that is not a sibling — a name from a
    neighbouring scope, or one the wording invented. Such a relation has no valid id and
    would render as a dangling edge (``relations.endpoints_resolve``), so it is removed
    rather than kept with an empty endpoint id.
    """
    name_to_id: dict[str, str] = {}
    for c in analysis.components:
        if c.name in name_to_id:
            logger.warning(
                f"Duplicate component name '{c.name}' found during ID assignment; "
                f"relation lookup will use the first occurrence (ID: {name_to_id[c.name]})"
            )
        else:
            name_to_id[c.name] = c.component_id
    resolved: list[Relation] = []
    for relation in analysis.components_relations:
        src_id = name_to_id.get(relation.src_name, "")
        dst_id = name_to_id.get(relation.dst_name, "")
        if not src_id or not dst_id:
            logger.info(f"Dropping relation with unresolved endpoint: '{relation.src_name}' -> '{relation.dst_name}'")
            continue
        relation.src_id = src_id
        relation.dst_id = dst_id
        resolved.append(relation)
    analysis.components_relations = resolved


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


class PlannedGroup(LLMBaseModel):
    """One component the planner proposes, as the candidate groups it gathers."""

    name: str = Field(description="One responsibility; never two joined by 'and' or '&'")
    members: list[str] = Field(description="Labels of the candidate groups this component gathers, e.g. ['G1', 'G4']")
    owns: list[str] = Field(
        default_factory=list, description="Domain words this component owns, as they appear in identifiers"
    )

    def llm_str(self):
        return f"{self.name}: {', '.join(self.members)}"


class TreePlanInsights(LLMBaseModel):
    """How the candidate groups of one scope fold into components."""

    groups: list[PlannedGroup] = Field(description="The components, each gathering one or more candidate groups")
    notes: str = Field(default="", description="What the names say about this scope's shape")

    def llm_str(self):
        return "# Tree plan\n" + "\n".join(f"- {group.llm_str()}" for group in self.groups)


class ScopeOperationAction(StrEnum):
    CREATE_COMPONENT = "create_component"
    UPDATE_COMPONENT = "update_component"
    DELETE_COMPONENT = "delete_component"
    NOOP = "noop"


class ScopedClusterRef(LLMBaseModel):
    """A cluster reference scoped by component depth and language."""

    scope_id: str = Field(description="Component scope id; use 'root' for the top-level scope.")
    language: str = Field(description="Programming language for this cluster.")
    cluster_id: int = Field(description="Cluster id within the scope/language cluster result.")

    def llm_str(self):
        scope_id = self.scope_id or ROOT_SCOPE_ID
        return f"{scope_id}:{self.language}:{self.cluster_id}"


class ScopeOperation(LLMBaseModel):
    """One diagram update operation for a single scope."""

    action: ScopeOperationAction = Field(description="Operation to apply in this scope.")
    cluster_refs: list[ScopedClusterRef] = Field(description="New-side clusters this operation accounts for.")
    component_id: str | None = Field(
        default=None,
        description="Existing component id for update/delete/noop; the id the tree specification "
        "allocated when creating a component, or null to allocate the next sibling index.",
    )
    name: str | None = Field(default=None, description="Component name for create/update operations.")
    description: str | None = Field(default=None, description="Component description for create/update operations.")
    key_entities: list[SourceCodeReference] = Field(
        default_factory=list,
        description=(
            "Important existing source symbols for a created component or a semantically refreshed component. "
            "Leave empty on updates that preserve the current key entities."
        ),
    )
    recurse: bool = Field(
        default=False, description="Whether this component should be considered for child-scope update."
    )
    rationale: str = Field(description="Short reason for the operation, especially for ambiguous reshapes.")

    def llm_str(self):
        refs = ", ".join(ref.llm_str() for ref in self.cluster_refs) or "no clusters"
        target = self.component_id or self.name or "new component"
        key_entities = ", ".join(entity.qualified_name for entity in self.key_entities) or "unchanged"
        return (
            f"{self.action}: {refs} -> {target}; key_entities=[{key_entities}]; "
            f"recurse={self.recurse}; {self.rationale}"
        )


class ScopeUpdateDecision(LLMBaseModel):
    """Deterministic operations for one incremental scope update."""

    operations: list[ScopeOperation] = Field(description="Operations to apply to the current scope.")

    def llm_str(self):
        if not self.operations:
            return "No scope operations."
        return "\n".join(operation.llm_str() for operation in self.operations)
