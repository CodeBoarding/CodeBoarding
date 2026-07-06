from __future__ import annotations

import abc
import logging
from abc import abstractmethod
from enum import StrEnum
from pathlib import PurePosixPath
from typing import get_origin, Optional

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from agents.cluster_ids import CodeBoardingClusterId, GraphClusterId
from agents.scope_ids import ROOT_SCOPE_ID

logger = logging.getLogger(__name__)


class LLMBaseModel(BaseModel, abc.ABC):
    """Base model for LLM-parseable response types."""

    @abstractmethod
    def llm_str(self):
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

    def llm_str(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`, Lines:({self.reference_start_line}:{self.reference_end_line})"

    def __str__(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"`{self.qualified_name}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"`{self.qualified_name}`"
        return f"`{self.qualified_name}`:{self.reference_start_line}-{self.reference_end_line}"


class RelationEdge(LLMBaseModel):
    """A source-to-target code reference that supports a component relation."""

    source: SourceCodeReference = Field(description="Source method/class/config reference for this interaction.")
    target: SourceCodeReference = Field(description="Target method/class/config reference for this interaction.")
    description: str = Field(default="", description="Short explanation of how source reaches or configures target.")
    call_sites: list[dict[str, int]] = Field(
        default_factory=list,
        description="Call-site line and column pairs for this edge.",
        exclude=True,
        json_schema_extra={"hidden": True},
    )

    def llm_str(self):
        return f"{self.source} -> {self.target}: {self.description}"


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

    def llm_str(self):
        return f"({self.src_name}, {self.relation}, {self.dst_name})"

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
    existing_component_id: str | None = Field(
        default=None,
        description=(
            "Incremental routing: the exact component_id of the existing component "
            "this entry is routing clusters into (e.g. '1.3'). Set to null to create "
            "a brand-new component. Identity is by ID, not name — leaving this null "
            "while reusing an existing component's name forks a duplicate component. "
            "Ignored by the full-analysis flow."
        ),
        json_schema_extra={"hidden": True},
    )
    parent_id: str | None = Field(
        default=None,
        description=(
            "Incremental routing: when ``existing_component_id`` is null (brand-new "
            "component), the existing component_id under which the new component "
            "should attach (or null to attach at root). Ignored when "
            "``existing_component_id`` is set, and ignored by the full-analysis flow."
        ),
        json_schema_extra={"hidden": True},
    )
    redetail_needed: bool = Field(
        default=True,
        description=(
            "Incremental routing only: when routing clusters into an existing component "
            "(``existing_component_id`` is set), set False if the cluster delta is "
            "cosmetic (refactor, internal rename, small bug fix) and the component's "
            "high-level purpose is unchanged — the existing description stays. Default "
            "True forces a full redetail. Ignored for brand-new components (always "
            "redetailed) and by the full-analysis flow."
        ),
        json_schema_extra={"hidden": True},
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


class MethodEntry(BaseModel):
    """A single method/function within a file, with its location and identity."""

    qualified_name: str = Field(description="Fully qualified name of the method or function.")
    start_line: int = Field(description="Starting line number in the file.")
    end_line: int = Field(description="Ending line number in the file.")
    node_type: str = Field(description="Node type name matching NodeType enum (e.g. METHOD, FUNCTION, CLASS).")

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
    """The public/consumed API surface and communication mechanisms for one component."""

    component_name: str = Field(description="Exact component name this API surface describes.")
    provided_interfaces: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Methods/classes/config symbols this component exposes or uses as entrypoints.",
    )
    consumed_interfaces: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Methods/classes/config symbols this component calls, configures, imports, or expects from others.",
    )
    incoming_mechanisms: list[str] = Field(
        default_factory=list,
        description="How other components communicate with this component, such as direct calls, registry dispatch, REST, queues, plugins, files, or config.",
    )
    outgoing_mechanisms: list[str] = Field(
        default_factory=list,
        description="How this component communicates with others, such as direct calls, registry dispatch, REST, queues, plugins, files, or config.",
    )
    notes: str = Field(default="", description="Short notes about the component's communication role.")

    def llm_str(self):
        provided = ", ".join(ref.llm_str() for ref in self.provided_interfaces) or "none"
        consumed = ", ".join(ref.llm_str() for ref in self.consumed_interfaces) or "none"
        incoming = ", ".join(self.incoming_mechanisms) or "none"
        outgoing = ", ".join(self.outgoing_mechanisms) or "none"
        return (
            f"**{self.component_name}**\n"
            f"  Provided: {provided}\n"
            f"  Consumed: {consumed}\n"
            f"  Incoming mechanisms: {incoming}\n"
            f"  Outgoing mechanisms: {outgoing}\n"
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


class ScopeRelations(LLMBaseModel):
    """Relations between components within a single scope."""

    components_relations: list[Relation] = Field(description="Inter-component relationships within this scope.")

    def llm_str(self):
        if not self.components_relations:
            return "No relations found."
        return "\n".join(r.llm_str() for r in self.components_relations)


class ScopeOperationAction(StrEnum):
    CREATE_COMPONENT = "create_component"
    UPDATE_COMPONENT = "update_component"
    DELETE_COMPONENT = "delete_component"
    NOOP = "noop"
    REGENERATE_SCOPE = "regenerate_scope"


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
        description="Existing component id for update/delete/noop; null when creating a component.",
    )
    name: str | None = Field(default=None, description="Component name for create/update operations.")
    description: str | None = Field(default=None, description="Component description for create/update operations.")
    recurse: bool = Field(
        default=False, description="Whether this component should be considered for child-scope update."
    )
    rationale: str = Field(description="Short reason for the operation, especially for ambiguous reshapes.")

    def llm_str(self):
        refs = ", ".join(ref.llm_str() for ref in self.cluster_refs) or "no clusters"
        target = self.component_id or self.name or "new component"
        return f"{self.action}: {refs} -> {target}; recurse={self.recurse}; {self.rationale}"


class ScopeUpdateDecision(LLMBaseModel):
    """LLM-selected operations for one incremental scope update."""

    operations: list[ScopeOperation] = Field(description="Operations to apply to the current scope.")

    def llm_str(self):
        if not self.operations:
            return "No scope operations."
        return "\n".join(operation.llm_str() for operation in self.operations)


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
