"""Render deterministic cluster groups for agent prompts."""

from pathlib import Path

from clustering_ids import ClusterId
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult

MAX_GROUP_FILES = 8
MAX_GROUP_SYMBOLS = 12
MAX_CONNECTION_EDGES = 10


def cluster_group_ids(groups: list[ClusterGroup]) -> dict[str, list[ClusterId]]:
    return {f"Group {index}": group.cluster_ids for index, group in enumerate(groups, start=1)}


def cluster_group_descriptions(scope: ClusterScopeResult) -> dict[str, str]:
    """Describe each fixed group from its authoritative symbol membership."""
    descriptions: dict[str, str] = {}
    for name, group in zip(cluster_group_ids(scope.groups), scope.groups, strict=True):
        symbols = sorted(
            group.qualified_names,
            key=lambda qualified_name: (qualified_name.count("."), qualified_name),
        )
        files = sorted(
            {
                scope.graphs_by_language[language].nodes[qualified_name].file_path
                for language, qualified_names in group.symbol_members_by_language.items()
                if language in scope.graphs_by_language
                for qualified_name in qualified_names
                if qualified_name in scope.graphs_by_language[language].nodes
            }
        )
        parts = [f"{len(group.cluster_ids)} leaf clusters, {len(symbols)} symbols across {len(files)} files."]
        if files:
            shown = ", ".join(Path(path).name for path in files[:MAX_GROUP_FILES])
            parts.append(f"Files: {shown}{', ...' if len(files) > MAX_GROUP_FILES else ''}")
        if symbols:
            shown = ", ".join(symbols[:MAX_GROUP_SYMBOLS])
            parts.append(f"Key symbols: {shown}{', ...' if len(symbols) > MAX_GROUP_SYMBOLS else ''}")
        descriptions[name] = " ".join(parts)
    return descriptions


def render_cluster_groups(group_ids: dict[str, list[ClusterId]], group_descriptions: dict[str, str]) -> str:
    if not group_ids:
        return "No clusters analyzed."
    body = "\n".join(
        f"**{name}** (cluster_ids: [{', '.join(str(cluster_id) for cluster_id in cluster_ids)}])\n"
        f"   {group_descriptions[name]}"
        for name, cluster_ids in group_ids.items()
    )
    return f"# Grouped Cluster Components\n{body}"


def render_scope_connections(scope: ClusterScopeResult, group_names: dict[str, str]) -> str:
    """Render precomputed cross-group communication for an agent prompt."""
    if not scope.connections:
        return "No cross-component communication edges found."

    lines: list[str] = []
    for connection in scope.connections:
        source = group_names.get(connection.source_group_id, connection.source_group_id)
        target = group_names.get(connection.target_group_id, connection.target_group_id)
        edge_count = len(connection.edges)
        lines.append(f"\n{source} -> {target} ({edge_count} edge{'s' if edge_count != 1 else ''}):")
        for edge in connection.edges[:MAX_CONNECTION_EDGES]:
            short_source = edge.source_qualified_name.split(".")[-1]
            short_target = edge.target_qualified_name.split(".")[-1]
            lines.append(f"  {short_source} -> {short_target}")
        if edge_count > MAX_CONNECTION_EDGES:
            lines.append(f"  ... and {edge_count - MAX_CONNECTION_EDGES} more")
    return "\n".join(lines)
