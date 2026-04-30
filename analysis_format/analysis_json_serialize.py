"""Serialize ``AnalysisInsights`` (+ sub-analyses) to the unified JSON format."""

import json
import logging
from datetime import datetime, timezone

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    Relation,
)
from analysis_format.analysis_json_models import (
    AnalysisMetadata,
    ComponentFileMethodGroupJson,
    ComponentJson,
    FileCoverageSummary,
    FileEntryJson,
    MethodIndexEntry,
    RelationJson,
    UnifiedAnalysisJson,
    method_key,
)
from analysis_format.analysis_json_parse import compute_depth_level

logger = logging.getLogger(__name__)


def _build_files_index_from_analysis(analysis: AnalysisInsights) -> dict[str, FileEntry]:
    return {file_path: entry.model_copy(deep=True) for file_path, entry in analysis.files.items()}


def _to_component_file_method_refs(file_methods: list[FileMethodGroup]) -> list[ComponentFileMethodGroupJson]:
    refs: list[ComponentFileMethodGroupJson] = []
    for group in file_methods:
        qnames: list[str] = []
        seen: set[str] = set()
        for method in group.methods:
            qname = method.qualified_name
            if qname in seen:
                continue
            seen.add(qname)
            qnames.append(qname)
        refs.append(ComponentFileMethodGroupJson(file_path=group.file_path, methods=qnames))
    return refs


def _build_methods_index_from_files(files_index: dict[str, FileEntry]) -> dict[str, MethodIndexEntry]:
    methods_index: dict[str, MethodIndexEntry] = {}
    for file_path, entry in files_index.items():
        for method in entry.methods:
            methods_index[method_key(file_path, method.qualified_name)] = MethodIndexEntry(
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
            method_keys=[method_key(file_path, m.qualified_name) for m in entry.methods],
        )
        for file_path, entry in files_index.items()
    }


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


def build_unified_analysis_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    repo_name: str,
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
    file_coverage_summary: FileCoverageSummary | None = None,
    commit_hash: str = "",
) -> str:
    """Build the full unified analysis JSON with metadata and nested sub-analyses.

    The depth_level metadata is computed automatically from the sub_analyses structure.
    """
    components_json = [
        from_component_to_json_component(c, expandable_components, sub_analyses, None) for c in analysis.components
    ]
    files_index = _build_files_index_from_analysis(analysis)
    methods_index = _build_methods_index_from_files(files_index)

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
            depth_level=compute_depth_level(sub_analyses),
            file_coverage_summary=summary,
        ),
        description=analysis.description,
        files=_build_file_entry_json_from_files(files_index),
        methods_index=methods_index,
        components=components_json,
        components_relations=relations_json,
    )
    return unified.model_dump_json(indent=2, exclude_none=True)
