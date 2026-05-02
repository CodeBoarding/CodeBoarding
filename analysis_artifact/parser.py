"""Parse the unified ``analysis.json`` back into ``AnalysisInsights``.

Also exposes ``build_id_to_name_map`` (used by the rendering layer) and
``compute_depth_level`` (used by the serializer to populate metadata).
"""

import logging

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    Relation,
    SourceCodeReference,
)
from analysis_artifact.schema import MethodIndexEntry

logger = logging.getLogger(__name__)


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
                qname = method.qualified_name
                indexed = methods_index.get(f"{file_path}|{qname}")
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


def _extract_analysis_recursive(
    data: dict,
    sub_analyses: dict[str, AnalysisInsights],
    parent_component_id: str = "",
) -> AnalysisInsights:
    """Recursively extract AnalysisInsights from data dict, collecting all sub-analyses."""
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


def build_id_to_name_map(root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> dict[str, str]:
    """Build a mapping from component_id to component name across all analysis levels."""
    id_to_name: dict[str, str] = {c.component_id: c.name for c in root_analysis.components}
    for sub_analysis in sub_analyses.values():
        for comp in sub_analysis.components:
            id_to_name[comp.component_id] = comp.name
    return id_to_name


def compute_depth_level(
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None,
) -> int:
    """Compute the maximum depth level from the sub_analyses structure.

    Returns 1 if there are no sub-analyses (root only), 2 if there is one level of
    sub-analyses, etc. Recursively traverses nested sub-analyses to find true max depth.
    """
    if not sub_analyses:
        return 1

    def get_depth(analysis: AnalysisInsights, visited: set[str]) -> int:
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
        # Only compute depth for root-level sub-analyses (not referenced by others).
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
