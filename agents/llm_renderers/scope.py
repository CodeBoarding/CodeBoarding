"""Render one deterministic component scope for semantic analysis."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.cfg.edge import EdgeKind
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult


def render_scope_context(
    scope: ClusterScopeResult,
    analysis: AnalysisInsights,
    repo_dir: Path,
    editable_group_ids: set[str] | frozenset[str],
    locked_name_ids: set[str] | frozenset[str],
    changed_files: set[str] | frozenset[str],
    incremental: bool,
) -> str:
    """Return complete group files, boundary candidates, and known calls as JSON."""
    boundary_reasons = _boundary_reasons(scope, repo_dir)
    components = {component.component_id: component for component in analysis.components}
    groups = []
    for group in scope.groups:
        component = components.get(group.group_id)
        file_reasons = _group_file_reasons(group, scope, repo_dir)
        groups.append(
            {
                "group_id": group.group_id,
                "status": "changed" if group.group_id in editable_group_ids else "unchanged",
                "name_locked": group.group_id in locked_name_ids,
                "files": [
                    {
                        "path": file_path,
                        "grouping_reason": reason,
                        "changed": file_path in changed_files,
                    }
                    for file_path, reason in file_reasons.items()
                ],
                "bordering_files": [
                    {"path": file_path, "reasons": sorted(reasons)}
                    for file_path, reasons in sorted(boundary_reasons.get(group.group_id, {}).items())
                ],
                "existing": (
                    {
                        "name": component.name,
                        "description": component.description,
                        "key_entities": [entity.qualified_name for entity in component.key_entities],
                    }
                    if incremental and component is not None
                    else None
                ),
            }
        )

    payload = {
        "scope_id": scope.scope_id,
        "mode": "incremental" if incremental else "full",
        "existing_description": analysis.description if incremental else None,
        "groups": groups,
        "known_connections": _known_connections(scope, repo_dir),
        "existing_relations": [
            {
                "source_group_id": relation.src_id,
                "target_group_id": relation.dst_id,
                "relation": relation.relation,
                "evidence": relation.evidence,
            }
            for relation in analysis.components_relations
            if relation.src_id and relation.dst_id
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def scope_file_paths(scope: ClusterScopeResult, repo_dir: Path) -> frozenset[str]:
    """Return every analyzed file available to this scope's tools."""
    return frozenset(
        normalize_repo_path(node.file_path, repo_dir)
        for graph in scope.graphs_by_language.values()
        for node in graph.nodes.values()
        if node.file_path
    )


def scope_method_names(scope: ClusterScopeResult) -> frozenset[str]:
    """Return every analyzed symbol available to this scope's call tool."""
    return frozenset(name for graph in scope.graphs_by_language.values() for name in graph.nodes)


def _group_file_reasons(
    group: ClusterGroup,
    scope: ClusterScopeResult,
    repo_dir: Path,
) -> dict[str, str]:
    reasons: dict[str, str] = {
        normalize_repo_path(file_path, repo_dir): reason for file_path, reason in group.file_reasons.items()
    }
    files = {
        normalize_repo_path(scope.graphs_by_language[language].nodes[name].file_path, repo_dir)
        for language, names in group.symbol_members_by_language.items()
        if language in scope.graphs_by_language
        for name in names
        if name in scope.graphs_by_language[language].nodes
    }
    for file_path in files:
        reasons.setdefault(file_path, "member of the deterministic group")
    return dict(sorted(reasons.items()))


def _known_connections(scope: ClusterScopeResult, repo_dir: Path) -> list[dict[str, str | int]]:
    connections: list[dict[str, str | int]] = []
    seen: set[tuple[str, str, str]] = set()
    for connection in scope.connections:
        for edge in connection.edges:
            key = (edge.language, edge.source_qualified_name, edge.target_qualified_name)
            if key in seen:
                continue
            seen.add(key)
            graph = scope.graphs_by_language.get(edge.language)
            source = graph.nodes.get(edge.source_qualified_name) if graph is not None else None
            target = graph.nodes.get(edge.target_qualified_name) if graph is not None else None
            connections.append(
                {
                    "source_group_id": connection.source_group_id,
                    "target_group_id": connection.target_group_id,
                    "language": edge.language,
                    "source_method": edge.source_qualified_name,
                    "source_file": normalize_repo_path(source.file_path, repo_dir) if source is not None else "",
                    "source_line": source.line_start if source is not None else 0,
                    "target_method": edge.target_qualified_name,
                    "target_file": normalize_repo_path(target.file_path, repo_dir) if target is not None else "",
                    "target_line": target.line_start if target is not None else 0,
                }
            )
    return sorted(
        connections,
        key=lambda item: (
            str(item["source_group_id"]),
            str(item["target_group_id"]),
            str(item["source_method"]),
            str(item["target_method"]),
        ),
    )


def _boundary_reasons(
    scope: ClusterScopeResult,
    repo_dir: Path,
) -> dict[str, dict[str, set[str]]]:
    reasons: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    owners = {
        (language, name): group.group_id
        for group in scope.groups
        for language, names in group.symbol_members_by_language.items()
        for name in names
    }
    for connection in scope.connections:
        for edge in connection.edges:
            graph = scope.graphs_by_language.get(edge.language)
            if graph is None:
                continue
            source = graph.nodes.get(edge.source_qualified_name)
            target = graph.nodes.get(edge.target_qualified_name)
            if source is not None:
                path = normalize_repo_path(source.file_path, repo_dir)
                reasons[connection.source_group_id][path].add(f"calls group {connection.target_group_id}")
            if target is not None:
                path = normalize_repo_path(target.file_path, repo_dir)
                reasons[connection.target_group_id][path].add(f"called by group {connection.source_group_id}")

    for language, graph in scope.graphs_by_language.items():
        for reference in graph.reference_edges:
            if reference.kind is EdgeKind.CONTAINS:
                continue
            source_group = owners.get((language, reference.src))
            target_group = owners.get((language, reference.dst))
            if not source_group or not target_group or source_group == target_group:
                continue
            source = graph.nodes.get(reference.src)
            target = graph.nodes.get(reference.dst)
            if source is not None:
                path = normalize_repo_path(source.file_path, repo_dir)
                reasons[source_group][path].add(f"{reference.kind.value} reference to group {target_group}")
            if target is not None:
                path = normalize_repo_path(target.file_path, repo_dir)
                reasons[target_group][path].add(f"{reference.kind.value} reference from group {source_group}")
    return reasons
