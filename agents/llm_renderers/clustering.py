"""Render deterministic cluster groups for agent prompts."""

from pathlib import Path

from clustering_ids import ClusterId
from static_analyzer.cluster_helpers import combine_cluster_results, group_symbols
from static_analyzer.clustering import ClusterGroup, ClusterResult


def cluster_group_ids(groups: list[ClusterGroup]) -> dict[str, list[ClusterId]]:
    return {f"Group {index}": group.cluster_ids for index, group in enumerate(groups, start=1)}


def cluster_group_descriptions(
    groups: list[ClusterGroup],
    cluster_results: dict[str, ClusterResult],
) -> dict[str, str]:
    combined = combine_cluster_results(cluster_results)
    descriptions: dict[str, str] = {}
    for name, group in zip(cluster_group_ids(groups), groups, strict=True):
        symbols = sorted(
            group_symbols(group.cluster_ids, combined.clusters),
            key=lambda qualified_name: (qualified_name.count("."), qualified_name),
        )
        files = sorted(
            {path for cluster_id in group.cluster_ids for path in combined.cluster_to_files.get(cluster_id, set())}
        )
        parts = [f"{len(group.cluster_ids)} leaf clusters, {len(symbols)} symbols across {len(files)} files."]
        if files:
            shown = ", ".join(Path(path).name for path in files[:8])
            parts.append(f"Files: {shown}{', ...' if len(files) > 8 else ''}")
        if symbols:
            shown = ", ".join(symbols[:12])
            parts.append(f"Key symbols: {shown}{', ...' if len(symbols) > 12 else ''}")
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
