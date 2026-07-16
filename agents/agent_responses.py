from __future__ import annotations

import abc
import logging
from abc import abstractmethod
from collections.abc import Hashable, Mapping
from pathlib import PurePosixPath
from typing import get_origin, Optional, Protocol

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from agents.cluster_ids import CodeBoardingClusterId, GraphClusterId
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry

logger = logging.getLogger(__name__)


class MethodIndexRecord(Protocol):
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int


class LLMBaseModel(BaseModel, abc.ABC):
    """Base model for LLM-parseable response types."""

    @abstractmethod
    def llm_str(self) -> str:
        raise NotImplementedError("LLM String has to be implemented.")

    @classmethod
    def _is_field_hidden(cls, fvalue: FieldInfo) -> bool:
        if fvalue.exclude:
            return True
        extra = fvalue.json_schema_extra
        if isinstance(extra, dict):
            return bool(extra.get("hidden"))
        return False

    @classmethod
    def _excluded_fields(cls, include_hidden: bool = False) -> set[str]:
        if include_hidden:
            return set()
        names: set[str] = set()
        for klass in cls.__mro__:
            if hasattr(klass, "model_fields"):
                for fname, finfo in klass.model_fields.items():
                    if cls._is_field_hidden(finfo):
                        names.add(fname)
        return names

    @classmethod
    def _resolve_excluded_by_title(cls, include_hidden: bool = False) -> dict[str, set[str]]:
        seen: set[type] = set()
        result: dict[str, set[str]] = {}

        def walk(model: type) -> None:
            if model in seen or not hasattr(model, "model_fields"):
                return
            seen.add(model)
            title = getattr(model, "__name__", "")
            excluded = model._excluded_fields(include_hidden)  # type: ignore[attr-defined]
            if excluded:
                result[title] = excluded
            for finfo in getattr(model, "model_fields", {}).values():
                ann = finfo.annotation
                for candidate in getattr(ann, "__args__", [ann]):
                    if isinstance(candidate, type) and issubclass(candidate, LLMBaseModel):
                        walk(candidate)  # type: ignore[arg-type]

        walk(cls)
        return result

    @classmethod
    def _extractor_fields(cls, indent: str = "  ", include_hidden: bool = False) -> str:
        parts: list[str] = []
        for fname, fvalue in cls.model_fields.items():
            if cls._is_field_hidden(fvalue) and not include_hidden:
                continue
            ftype = fvalue.annotation
            if get_origin(ftype) is list:
                if ftype is not None and hasattr(ftype, "__args__"):
                    inner = ftype.__args__[0]
                    if isinstance(inner, type) and issubclass(inner, LLMBaseModel):
                        parts.append(
                            f"{indent}- {fname}: a list, where each item has:\n{inner._extractor_fields(indent + '  ', include_hidden)}"
                        )
                        continue
                parts.append(f"{indent}- {fname}: {fvalue.description}")
            elif isinstance(ftype, type) and issubclass(ftype, LLMBaseModel):
                parts.append(ftype._extractor_fields(indent, include_hidden))
            else:
                parts.append(f"{indent}- {fname}: {fvalue.description}")
        return "\n".join(parts)

    @classmethod
    def extractor_str(cls, include_hidden: bool = False) -> str:
        title = cls.__name__
        fields = cls._extractor_fields(include_hidden=include_hidden)
        return (
            f"You are a JSON extraction expert. "
            f"Extract a valid JSON object of type `{title}` from the text below.\n"
            f"The JSON must have these fields:\n{fields}\n\n"
        )

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type | None = None,
        mode: str = "validation",
        include_hidden: bool = False,
        **kwargs,
    ) -> dict:
        call_kwargs: dict = {"by_alias": by_alias, "ref_template": ref_template, "mode": mode}
        if schema_generator is not None:
            call_kwargs["schema_generator"] = schema_generator
        call_kwargs.update(kwargs)
        schema = super().model_json_schema(**call_kwargs)
        excluded_by_title = cls._resolve_excluded_by_title(include_hidden)
        for title, excluded in excluded_by_title.items():
            defn = schema.get("$defs", {}).get(title)
            if isinstance(defn, dict) and "properties" in defn:
                defn["properties"] = {k: v for k, v in defn["properties"].items() if k not in excluded}
        own_excluded = cls._excluded_fields(include_hidden)
        if "properties" in schema:
            schema["properties"] = {k: v for k, v in schema["properties"].items() if k not in own_excluded}
        return schema


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
        json_schema_extra={"hidden": True},
    )

    @classmethod
    def from_dict(cls, edge: dict, methods_index: Mapping[str, MethodIndexRecord]) -> RelationEdge:
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
    def from_program_edge(cls, edge, graph) -> RelationEdge:
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]
        return cls(
            source=SourceCodeReference(
                qualified_name=source.id,
                reference_file=source.file_path,
                reference_start_line=source.line_start,
                reference_end_line=source.line_end,
            ),
            target=SourceCodeReference(
                qualified_name=target.id,
                reference_file=target.file_path,
                reference_start_line=target.line_start,
                reference_end_line=target.line_end,
            ),
            call_sites=[RelationCallSite.model_validate(call_site) for call_site in edge.occurrence_dicts()],
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
    methods_index: Mapping[str, MethodIndexRecord],
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
        json_schema_extra={"hidden": True},
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
            all_edges=cls._unique_edges(edges),
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
        merged_key_edges = Relation._unique_edges(key_edges)
        merged_all_edges = Relation._unique_edges([*all_edges, *merged_key_edges])
        return merged_key_edges, merged_all_edges

    @staticmethod
    def _unique_edges(edges: list[RelationEdge]) -> list[RelationEdge]:
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

    def analysis_dump(self) -> dict:
        data = self.model_dump(exclude_none=True)
        data["src_id"] = self.src_id
        data["dst_id"] = self.dst_id
        data["edge_count"] = self.edge_count
        data["is_static"] = self.is_static
        return data


class ClustersComponent(LLMBaseModel):
    """A grouped component from cluster analysis - may contain multiple clusters."""

    name: str = Field(
        description="Short, descriptive name for this cluster group (e.g., 'Authentication', 'Data Pipeline', 'Request Handling')"
    )
    cluster_ids: list[GraphClusterId] = Field(
        description="List of cluster IDs from the CFG analysis that are grouped together (e.g., [1, 3, 5])"
    )
    description: str = Field(
        description="Explanation of what this component does, its main flow, WHY these clusters are grouped together, how it interacts with other cluster groups, and the most important classes/methods (by their exact qualified names from the clusters)"
    )

    def llm_str(self):
        ids_str = ", ".join(str(cid) for cid in self.cluster_ids)
        return f"**{self.name}** (cluster_ids: [{ids_str}])\n   {self.description}"


class ClusterAnalysis(LLMBaseModel):
    """Analysis results containing grouped cluster components."""

    cluster_components: list[ClustersComponent] = Field(
        description="Grouped clusters into logical components. Multiple cluster IDs can be grouped together if they work as a cohesive unit."
    )

    def llm_str(self):
        if not self.cluster_components:
            return "No clusters analyzed."
        title = "# Grouped Cluster Components\n"
        body = "\n".join(cc.llm_str() for cc in self.cluster_components)
        return title + body


class Component(LLMBaseModel):
    """A software component with name, description, and key entities."""

    name: str = Field(description="Name of the component")
    description: str = Field(description="A short description of the component.")

    # LLM picks these: The MOST IMPORTANT/critical methods and classes
    key_entities: list[SourceCodeReference] = Field(
        description="The most important/critical classes and methods that represent this component's core functionality. Pick 2-5 key entities."
    )

    source_group_names: list[str] = Field(
        description="Names of the cluster groups from the grouping analysis that this component encompasses.",
        default_factory=list,
    )

    source_cluster_ids: list[CodeBoardingClusterId] = Field(
        description="List of cluster IDs from CFG analysis that this component encompasses (populated deterministically from source_group_names).",
        default_factory=list,
        exclude=True,
        json_schema_extra={"hidden": True},
    )

    file_methods: list[FileMethodGroup] = Field(
        description="All methods/functions belonging to this component, grouped by file (populated deterministically from cluster results).",
        default_factory=list,
        exclude=True,
        json_schema_extra={"hidden": True},
    )

    component_id: str = Field(
        default="",
        description="Deterministic unique identifier for this component.",
        exclude=True,
        json_schema_extra={"hidden": True},
    )

    def file_paths(self) -> list[str]:
        """File paths this component spans, one per ``file_methods`` group."""
        return [group.file_path for group in self.file_methods]

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        sg = ""
        if self.source_group_names:
            sg = f"   - *Source Group Names*: {', '.join(self.source_group_names)}"
        qn = ""
        if self.key_entities:
            qn += "   - *Key Entities*: "
            qn += ", ".join(f"`{q.llm_str()}`" for q in self.key_entities)
        return "\n".join([n, d, sg, qn]).strip()


class AnalysisInsights(LLMBaseModel):
    """Project analysis insights including components and their relations."""

    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose."
    )
    files: dict[str, FileEntry] = Field(
        default_factory=dict,
        description="Top-level file index keyed by relative file path. Contains all methods and statuses.",
        exclude=True,
        json_schema_extra={"hidden": True},
    )
    components: list[Component] = Field(description="List of the components identified in the project.")
    components_relations: list[Relation] = Field(description="List of relations among the components.")

    def llm_str(self):
        if not self.components:
            return "No abstract components found."
        title = "# Abstract Components Overview\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations

    def file_to_component(self) -> dict[str, str]:
        """Build file path -> component_id mapping from root components."""
        return {str(PurePosixPath(fg.file_path)): c.component_id for c in self.components for fg in c.file_methods}


class ComponentArchitecture(LLMBaseModel):
    """Component-only architecture before relation discovery."""

    description: str = Field(
        description="One paragraph explaining the functionality represented by this graph, its main flow, and purpose."
    )
    components: list[Component] = Field(description="List of the components identified in the project.")

    def llm_str(self):
        if not self.components:
            return "No abstract components found."
        title = "# Abstract Components Overview\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        return title + body


class ComponentApiSurface(LLMBaseModel):
    """The provided and consumed APIs for one component."""

    component_name: str = Field(description="Exact component name this API surface describes.")
    provided_interfaces: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Methods/classes/config symbols this component exposes or uses as entrypoints.",
    )
    consumed_interfaces: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Methods/classes/config symbols this component calls, configures, imports, or expects from others.",
    )
    incoming_api_paths: list[str] = Field(
        default_factory=list,
        description="How other components enter this component's API, such as direct calls, registry dispatch, REST, queues, plugins, files, or config.",
    )
    outgoing_api_paths: list[str] = Field(
        default_factory=list,
        description="How this component reaches other components' APIs, such as direct calls, registry dispatch, REST, queues, plugins, files, or config.",
    )
    notes: str = Field(default="", description="Short notes about the component's API role.")

    def llm_str(self):
        provided = ", ".join(ref.llm_str() for ref in self.provided_interfaces) or "none"
        consumed = ", ".join(ref.llm_str() for ref in self.consumed_interfaces) or "none"
        incoming = ", ".join(self.incoming_api_paths) or "none"
        outgoing = ", ".join(self.outgoing_api_paths) or "none"
        return (
            f"**{self.component_name}**\n"
            f"  Provided: {provided}\n"
            f"  Consumed: {consumed}\n"
            f"  Incoming API paths: {incoming}\n"
            f"  Outgoing API paths: {outgoing}\n"
            f"  Notes: {self.notes}"
        )


class ComponentApiSurfaces(LLMBaseModel):
    """API surfaces for all components in a scope."""

    api_surfaces: list[ComponentApiSurface] = Field(description="API surface for each component in this scope.")

    def llm_str(self):
        if not self.api_surfaces:
            return "No component API surfaces found."
        return "\n".join(surface.llm_str() for surface in self.api_surfaces)


class ComponentRelations(LLMBaseModel):
    """Relations discovered from component API surfaces."""

    components_relations: list[Relation] = Field(description="List of relations among the components.")

    def llm_str(self):
        if not self.components_relations:
            return "No component relations found."
        return "\n".join(relation.llm_str() for relation in self.components_relations)


def assign_component_ids(analysis: AnalysisInsights, parent_id: str = "") -> None:
    """Assign hierarchical component IDs based on sibling index.

    IDs encode structural position in the component tree:
    - Top-level (parent_id=""): "1", "2", "3"
    - Under "1" (parent_id="1"): "1.1", "1.2"
    - Under "1.2" (parent_id="1.2"): "1.2.1", "1.2.2"
    """
    for idx, component in enumerate(analysis.components, start=1):
        component.component_id = f"{parent_id}.{idx}" if parent_id else str(idx)

    assign_relation_ids(analysis)


def assign_relation_ids(analysis: AnalysisInsights) -> None:
    """Assign relation component IDs by looking up component names."""
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


class CFGComponent(LLMBaseModel):
    """A component derived from control flow graph analysis."""

    name: str = Field(description="Name of the abstract component")
    description: str = Field(description="One paragraph explaining the component.")
    referenced_source: list[str] = Field(
        description="List of the qualified names of the methods and classes that are within this component."
    )

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.referenced_source:
            qn += "   - *Related Classes/Methods*: "
            qn += ", ".join(f"`{q}`" for q in self.referenced_source)
        return "\n".join([n, d, qn]).strip()


class CFGAnalysisInsights(LLMBaseModel):
    """Insights from control flow graph analysis including components and relations."""

    components: list[CFGComponent] = Field(description="List of components identified in the CFG.")
    components_relations: list[Relation] = Field(description="List of relations among the components in the CFG.")

    def llm_str(self):
        if not self.components:
            return "No abstract components found in the CFG."
        title = "# Abstract Components Overview from CFG\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class ExpandComponent(LLMBaseModel):
    """Decision on whether to expand a component with reasoning."""

    should_expand: bool = Field(description="Whether the component should be expanded in detail or not.")
    reason: str = Field(description="Reasoning behind the decision to expand or not.")

    def llm_str(self):
        return f"- *Should Expand:* {self.should_expand}\n- *Reason:* {self.reason}"


class ValidationInsights(LLMBaseModel):
    """Validation results with status and additional information."""

    is_valid: bool = Field(description="Indicates whether the validation results in valid or not.")
    additional_info: str | None = Field(
        default=None,
        description="Any additional information or context related to the validation.",
    )

    def llm_str(self):
        return f"**Feedback Information:**\n{self.additional_info}"


class UpdateAnalysis(LLMBaseModel):
    """Feedback on how much a diagram needs updating."""

    update_degree: int = Field(
        description="Degree to which the diagram needs update. 0 means no update, 10 means complete update."
    )
    feedback: str = Field(description="Feedback provided on the analysis.")

    def llm_str(self):
        return f"**Feedback:**\n{self.feedback}"


class MetaAnalysisInsights(LLMBaseModel):
    """Insights from analyzing project metadata including type, domain, and architecture."""

    project_type: str = Field(
        description="Type/category of the project (e.g., web framework, data processing, ML library, etc.)"
    )
    domain: str = Field(
        description="Domain or field the project belongs to (e.g., web development, data science, DevOps, etc.)"
    )
    architectural_patterns: list[str] = Field(description="Main architectural patterns typically used in such projects")
    expected_components: list[str] = Field(description="Expected high-level components/modules based on project type")
    technology_stack: list[str] = Field(description="Main technologies, frameworks, and libraries used")
    architectural_bias: str = Field(
        description="Guidance on how to interpret and organize components for this project type"
    )

    def llm_str(self):
        title = "# Project Metadata Analysis\n"
        content = f"""
**Project Type:** {self.project_type}
**Domain:** {self.domain}
**Technology Stack:** {", ".join(self.technology_stack)}
**Architectural Patterns:** {", ".join(self.architectural_patterns)}
**Expected Components:** {", ".join(self.expected_components)}
**Architectural Bias:** {self.architectural_bias}
"""
        return title + content


class FileClassification(LLMBaseModel):
    """Classification of a file to a component."""

    component_name: str = Field(description="Name of the component or module")
    file_path: str = Field(description="Path to the file")

    def llm_str(self):
        return f"`{self.file_path}` -> Component: `{self.component_name}`"


class ComponentFiles(LLMBaseModel):
    """Collection of file classifications for components."""

    file_paths: list[FileClassification] = Field(
        description="All files with their classifications for each of the files assigned to a component."
    )

    def llm_str(self):
        if not self.file_paths:
            return "No files classified."
        title = "# Component File Classifications\n"
        body = "\n".join(f"- `{fc.file_path}` -> Component: `{fc.component_name}`" for fc in self.file_paths)
        return title + body


class FilePath(LLMBaseModel):
    """File path with optional line range reference."""

    file_path: str = Field(description="Full file path for the reference")
    start_line: int | None = Field(
        default=None,
        description="Starting line number in the file for the reference (if applicable).",
    )
    end_line: int | None = Field(
        default=None,
        description="Ending line number in the file for the reference (if applicable).",
    )

    def llm_str(self):
        return f"`{self.file_path}`: ({self.start_line}:{self.end_line})"
