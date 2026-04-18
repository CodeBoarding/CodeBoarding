import logging
import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agents.agent_responses import (
    Component,
    Relation,
    AnalysisInsights,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    SourceCodeReference,
)

logger = logging.getLogger(__name__)


class RelationJson(Relation):
    """Relation subclass that includes src_id/dst_id and static analysis evidence in JSON serialization."""

    src_id: str = Field(default="", description="Component ID of the source.")
    dst_id: str = Field(default="", description="Component ID of the destination.")
    edge_count: int = Field(default=0, description="Number of CFG edges backing this relation.")
    is_static: bool = Field(default=False, description="True if derived from static CFG analysis.")


class ComponentJson(Component):
    # Override to include in JSON serialization (parent has exclude=True)
    component_id: str = Field(description="Deterministic unique identifier for this component.")
    source_cluster_ids: list[int] = Field(
        description="List of cluster IDs from CFG analysis that this component encompasses.",
        default_factory=list,
    )
    can_expand: bool = Field(
        description="Whether the component can be expanded in detail or not.",
        default=False,
    )
    file_methods: list["ComponentFileMethodGroupJson"] = Field(
        description="Component method references grouped by file. Each methods entry stores only qualified_name.",
        default_factory=list,
    )
    # Exclude intermediate field from JSON output
    source_group_names: list[str] = Field(default_factory=list, exclude=True)
    # Nested sub-analysis for expanded components
    components: list["ComponentJson"] | None = Field(
        description="Sub-components if expanded, None otherwise.", default=None
    )
    components_relations: list[RelationJson] | None = Field(
        description="Relations among sub-components if expanded, None otherwise.",
        default=None,
    )


class NotAnalyzedFile(BaseModel):
    path: str = Field(description="Relative path of the file.")
    reason: str = Field(description="Exclusion reason for the file.")


class FileCoverageSummary(BaseModel):
    total_files: int = Field(description="Total number of text files in the repository.")
    analyzed: int = Field(description="Number of files included in the analysis.")
    not_analyzed: int = Field(description="Number of files excluded from the analysis.")
    not_analyzed_by_reason: dict[str, int] = Field(
        default_factory=dict, description="Count of excluded files grouped by reason."
    )


class FileCoverageReport(BaseModel):
    version: int = Field(default=1, description="Schema version of the file coverage report.")
    generated_at: str = Field(description="ISO timestamp of when the report was generated.")
    analyzed_files: list[str] = Field(description="List of analyzed file paths.")
    not_analyzed_files: list[NotAnalyzedFile] = Field(description="List of excluded files with optional reasons.")
    summary: FileCoverageSummary = Field(description="Aggregated coverage counts.")


class AnalysisMetadata(BaseModel):
    generated_at: str = Field(description="ISO timestamp of when the analysis was generated.")
    commit_hash: str = Field(default="", description="Git commit hash at which the analysis was generated.")
    repo_name: str = Field(description="Name of the analyzed repository.")
    depth_level: int = Field(description="Maximum depth level of the analysis.")
    file_coverage_summary: FileCoverageSummary = Field(
        default_factory=lambda: FileCoverageSummary(
            total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={}
        ),
        description="Lightweight file coverage counts.",
    )


class MethodIndexEntry(BaseModel):
    file_path: str = Field(description="Relative path to the source file.")
    qualified_name: str = Field(description="Fully qualified method/function name.")
    start_line: int = Field(description="Starting line number in the file.")
    end_line: int = Field(description="Ending line number in the file.")
    type: str = Field(description="Node type name (METHOD, FUNCTION, CLASS, ...).")


class ComponentFileMethodGroupJson(BaseModel):
    file_path: str = Field(description="Relative path to the source file.")
    methods: list[str] = Field(
        default_factory=list,
        description="Qualified method/function names assigned to this component in this file.",
    )


class FileEntryJson(BaseModel):
    """Persisted file entry — stores only method-index keys.

    Full method metadata lives in ``methods_index``; this avoids duplication.
    """

    method_keys: list[str] = Field(
        default_factory=list,
        description="Keys into ``methods_index`` ('<file_path>|<qualified_name>'), in declaration order.",
    )


class UnifiedAnalysisJson(BaseModel):
    metadata: AnalysisMetadata = Field(description="Metadata about the analysis run.")
    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph."
    )
    files: dict[str, FileEntryJson] = Field(
        default_factory=dict,
        description="Top-level file index keyed by relative file path.",
    )
    methods_index: dict[str, MethodIndexEntry] = Field(
        default_factory=dict,
        description="Canonical method metadata keyed by '<file_path>|<qualified_name>'.",
    )
    components: list[ComponentJson] = Field(description="List of the components identified in the project.")
    components_relations: list[RelationJson] = Field(description="List of relations among the components.")


def _build_files_index_from_analysis(analysis: AnalysisInsights) -> dict[str, FileEntry]:
    """Build a top-level files index from analysis."""
    return {file_path: entry.model_copy(deep=True) for file_path, entry in analysis.files.items()}


def _method_key(file_path: str, qualified_name: str) -> str:
    return f"{file_path}|{qualified_name}"


def _to_method_qualified_name(method: MethodEntry) -> str:
    return method.qualified_name


def _to_component_file_method_refs(file_methods: list[FileMethodGroup]) -> list[ComponentFileMethodGroupJson]:
    refs: list[ComponentFileMethodGroupJson] = []
    for group in file_methods:
        qnames: list[str] = []
        seen: set[str] = set()
        for method in group.methods:
            qname = _to_method_qualified_name(method)
            if qname in seen:
                continue
            seen.add(qname)
            qnames.append(qname)
        refs.append(ComponentFileMethodGroupJson(file_path=group.file_path, methods=qnames))
    return refs


def _method_refs_to_placeholders(method_names: list[str]) -> list[MethodEntry]:
    return [
        MethodEntry(
            qualified_name=method_name,
            start_line=0,
            end_line=0,
            node_type="METHOD",
        )
        for method_name in method_names
    ]


def _build_methods_index_from_files(files_index: dict[str, FileEntry]) -> dict[str, MethodIndexEntry]:
    methods_index: dict[str, MethodIndexEntry] = {}
    for file_path, entry in files_index.items():
        for method in entry.methods:
            methods_index[_method_key(file_path, method.qualified_name)] = MethodIndexEntry(
                file_path=file_path,
                qualified_name=method.qualified_name,
                start_line=method.start_line,
                end_line=method.end_line,
                type=method.node_type,
            )
    return methods_index


def _build_file_entry_json_from_files(files_index: dict[str, FileEntry]) -> dict[str, FileEntryJson]:
    return {
        file_path: FileEntryJson(
            method_keys=[_method_key(file_path, m.qualified_name) for m in entry.methods],
        )
        for file_path, entry in files_index.items()
    }


def _hydrate_component_methods_from_refs(
    analysis: AnalysisInsights,
    methods_index: dict[str, MethodIndexEntry],
) -> None:
    missing: list[str] = []
    for component in analysis.components:
        rebuilt: list[FileMethodGroup] = []
        for group in component.file_methods:
            file_path = group.file_path
            methods: list[MethodEntry] = []
            for method in group.methods:
                qname = _to_method_qualified_name(method)
                indexed = methods_index.get(_method_key(file_path, qname))
                if indexed is None:
                    missing.append(f"{file_path}|{qname}")
                    continue
                methods.append(
                    MethodEntry(
                        qualified_name=indexed.qualified_name,
                        start_line=indexed.start_line,
                        end_line=indexed.end_line,
                        node_type=indexed.type,
                    )
                )

            methods = sorted(methods, key=lambda m: (m.start_line, m.end_line, m.qualified_name))
            rebuilt.append(FileMethodGroup(file_path=file_path, methods=methods))

        component.file_methods = rebuilt

    if missing:
        logger.warning("Missing method index entry for %d ref(s): %s", len(missing), missing)


def _relation_to_json(r: Relation) -> RelationJson:
    """Convert a Relation to RelationJson, preserving all fields including static analysis evidence."""
    return RelationJson(
        relation=r.relation,
        src_name=r.src_name,
        dst_name=r.dst_name,
        src_id=r.src_id,
        dst_id=r.dst_id,
        edge_count=r.edge_count,
        is_static=r.is_static,
    )


def from_component_to_json_component(
    component: Component,
    expandable_components: list[Component],
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
    processed_ids: set[str] | None = None,
) -> ComponentJson:
    """Convert a Component to a ComponentJson, optionally nesting sub-analysis data."""
    if processed_ids is None:
        processed_ids = set()

    component_id_val: str = component.component_id
    if component_id_val in processed_ids:
        logger.warning(f"Component {component.name} (ID: {component_id_val}) already processed, skipping expansion")
        can_expand = False
    else:
        processed_ids.add(component_id_val)
        can_expand = any(c.component_id == component.component_id for c in expandable_components)

    nested_components: list[ComponentJson] | None = None
    nested_relations: list[RelationJson] | None = None

    if can_expand and sub_analyses and component.component_id in sub_analyses:
        sub_analysis, sub_expandable = sub_analyses[component.component_id]
        nested_components = [
            from_component_to_json_component(c, sub_expandable, sub_analyses, processed_ids)
            for c in sub_analysis.components
        ]
        nested_relations = [_relation_to_json(r) for r in sub_analysis.components_relations]

    return ComponentJson(
        name=component.name,
        component_id=component.component_id,
        description=component.description,
        key_entities=component.key_entities,
        source_cluster_ids=component.source_cluster_ids,
        file_methods=_to_component_file_method_refs(component.file_methods),
        can_expand=can_expand,
        components=nested_components,
        components_relations=nested_relations,
    )


def from_analysis_to_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
) -> str:
    """Convert an AnalysisInsights to a flat JSON string (no metadata wrapper)."""
    components_json = [
        from_component_to_json_component(c, expandable_components, sub_analyses, None) for c in analysis.components
    ]
    # Build a dict matching the old AnalysisInsightsJson shape but with nested components
    relations_json = [_relation_to_json(r) for r in analysis.components_relations]
    files_index = _build_files_index_from_analysis(analysis)
    methods_index = _build_methods_index_from_files(files_index)
    files_json = _build_file_entry_json_from_files(files_index)
    data = {
        "description": analysis.description,
        "files": {fp: entry.model_dump() for fp, entry in files_json.items()},
        "methods_index": {k: v.model_dump() for k, v in methods_index.items()},
        "components": [c.model_dump(exclude_none=True) for c in components_json],
        "components_relations": [r.model_dump() for r in relations_json],
    }

    return json.dumps(data, indent=2)


def _compute_depth_level(
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None,
) -> int:
    """Compute the maximum depth level from the sub_analyses structure.

    Returns 1 if there are no sub-analyses (root only), 2 if there is one level of
    sub-analyses, etc. Recursively traverses nested sub-analyses to find true max depth.
    """
    if not sub_analyses:
        return 1

    def get_depth(analysis: AnalysisInsights, visited: set[str]) -> int:
        """Recursively compute depth for a sub-analysis."""
        max_depth = 1
        for comp in analysis.components:
            if comp.component_id in sub_analyses and comp.component_id not in visited:
                visited.add(comp.component_id)
                sub_analysis, _ = sub_analyses[comp.component_id]
                child_depth = 1 + get_depth(sub_analysis, visited)
                max_depth = max(max_depth, child_depth)
                visited.remove(comp.component_id)
        return max_depth

    max_depth = 1
    for cid, (sub_analysis, _) in sub_analyses.items():
        # Only compute depth for root-level sub-analyses (not referenced by others)
        is_root_level = True
        for other_cid, (other_analysis, _) in sub_analyses.items():
            if other_cid != cid:
                for comp in other_analysis.components:
                    if comp.component_id == cid:
                        is_root_level = False
                        break
            if not is_root_level:
                break

        if is_root_level:
            visited = {cid}
            depth = 1 + get_depth(sub_analysis, visited)
            max_depth = max(max_depth, depth)

    return max_depth


def build_unified_analysis_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    repo_name: str,
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
    file_coverage_summary: FileCoverageSummary | None = None,
    commit_hash: str = "",
) -> str:
    """Build the full unified analysis JSON with metadata and nested sub-analyses.

    The depth_level metadata is computed automatically from the sub_analyses structure
    if not provided explicitly.
    """
    components_json = [
        from_component_to_json_component(c, expandable_components, sub_analyses, None) for c in analysis.components
    ]
    files_index = _build_files_index_from_analysis(analysis)
    methods_index = _build_methods_index_from_files(files_index)

    # Use default summary if none provided
    if file_coverage_summary is None:
        summary = FileCoverageSummary(total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={})
    else:
        summary = file_coverage_summary

    relations_json = [_relation_to_json(r) for r in analysis.components_relations]
    unified = UnifiedAnalysisJson(
        metadata=AnalysisMetadata(
            generated_at=datetime.now(timezone.utc).isoformat(),
            commit_hash=commit_hash,
            repo_name=repo_name,
            depth_level=_compute_depth_level(sub_analyses),
            file_coverage_summary=summary,
        ),
        description=analysis.description,
        files=_build_file_entry_json_from_files(files_index),
        methods_index=methods_index,
        components=components_json,
        components_relations=relations_json,
    )
    return unified.model_dump_json(indent=2, exclude_none=True)


def parse_unified_analysis(
    data: dict,
) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
    """Parse a unified analysis JSON dict into root AnalysisInsights and sub-analyses.

    Returns:
        (root_analysis, sub_analyses_dict) where sub_analyses_dict maps component_id
        to its nested AnalysisInsights.
    """
    sub_analyses: dict[str, AnalysisInsights] = {}
    root_analysis = _extract_analysis_recursive(data, sub_analyses)

    methods_index_raw = data.get("methods_index", {})
    methods_index: dict[str, MethodIndexEntry] = {
        key: MethodIndexEntry(**entry) for key, entry in methods_index_raw.items()
    }

    files_raw = data.get("files", {})
    files_index = _reconstruct_files_index(files_raw, methods_index)

    root_analysis.files = {path: entry.model_copy(deep=True) for path, entry in files_index.items()}
    _hydrate_component_methods_from_refs(root_analysis, methods_index)
    for sub in sub_analyses.values():
        sub.files = {path: entry.model_copy(deep=True) for path, entry in files_index.items()}
        _hydrate_component_methods_from_refs(sub, methods_index)

    return root_analysis, sub_analyses


def _reconstruct_files_index(
    files_raw: dict,
    methods_index: dict[str, MethodIndexEntry],
) -> dict[str, FileEntry]:
    """Rebuild in-memory ``FileEntry`` objects from persisted ``method_keys``."""
    files_index: dict[str, FileEntry] = {}
    for file_path, entry_raw in files_raw.items():
        methods: list[MethodEntry] = []
        for key in entry_raw["method_keys"]:
            indexed = methods_index.get(key)
            if indexed is None:
                logger.warning("Missing methods_index entry for key %s (file %s)", key, file_path)
                continue
            methods.append(
                MethodEntry(
                    qualified_name=indexed.qualified_name,
                    start_line=indexed.start_line,
                    end_line=indexed.end_line,
                    node_type=indexed.type,
                )
            )
        files_index[file_path] = FileEntry(methods=methods)
    return files_index


def build_id_to_name_map(root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> dict[str, str]:
    """Build a mapping from component_id to component name across all analysis levels."""
    id_to_name: dict[str, str] = {c.component_id: c.name for c in root_analysis.components}
    for sub_analysis in sub_analyses.values():
        for comp in sub_analysis.components:
            id_to_name[comp.component_id] = comp.name
    return id_to_name


def _extract_analysis_recursive(
    data: dict,
    sub_analyses: dict[str, AnalysisInsights],
    parent_component_id: str = "",
) -> AnalysisInsights:
    """Recursively extract AnalysisInsights from data dict, collecting all sub-analyses.

    Args:
        data: The analysis data dict containing components, description, etc.
        sub_analyses: Dict to populate with component_id -> AnalysisInsights mappings.

    Returns:
        AnalysisInsights for this level (components are non-nested at this level).
    """
    components: list[Component] = []

    for index, comp_data in enumerate(data.get("components", []), start=1):
        file_methods = [
            FileMethodGroup(
                file_path=group["file_path"],
                methods=_method_refs_to_placeholders([str(m) for m in group.get("methods", [])]),
            )
            for group in comp_data.get("file_methods", [])
        ]
        key_entities = [
            SourceCodeReference(
                qualified_name=ke["qualified_name"],
                reference_file=ke.get("reference_file"),
                reference_start_line=ke.get("reference_start_line", 0),
                reference_end_line=ke.get("reference_end_line", 0),
            )
            for ke in comp_data.get("key_entities", [])
        ]

        # Create the component for this level (non-nested)
        legacy_prefix = f"{parent_component_id}_" if parent_component_id else ""
        fallback_component_id = f"legacy_component_{legacy_prefix}{index}"
        component = Component(
            name=comp_data.get("name", fallback_component_id),
            component_id=comp_data.get("component_id") or fallback_component_id,
            description=comp_data.get("description", ""),
            key_entities=key_entities,
            file_methods=file_methods,
            source_cluster_ids=comp_data.get("source_cluster_ids", []),
        )
        components.append(component)

        # Recursively process nested components if they exist
        nested_components = comp_data.get("components")
        if isinstance(nested_components, list) and nested_components:
            nested_data = {
                "description": comp_data.get("description", ""),
                "components": nested_components,
                "components_relations": comp_data.get("components_relations", []),
            }
            sub_analysis = _extract_analysis_recursive(nested_data, sub_analyses, component.component_id)
            sub_analyses[component.component_id] = sub_analysis

    return AnalysisInsights(
        description=data.get("description", ""),
        components=components,
        components_relations=[
            Relation(
                relation=r["relation"],
                src_name=r["src_name"],
                dst_name=r["dst_name"],
                src_id=r.get("src_id", ""),
                dst_id=r.get("dst_id", ""),
                edge_count=r.get("edge_count", 0),
                is_static=r.get("is_static", False),
            )
            for r in data.get("components_relations", [])
        ],
    )
