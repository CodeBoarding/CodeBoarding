"""Turn a finished analysis into one self-describing clustering payload.

Reads only run artifacts — ``static_analysis.pkl`` (call graph + the scoped
cluster id every method carries) and ``analysis.json`` (the component tree that
claimed those clusters) — so it can be pointed at any completed run without
re-analyzing anything.
"""

import json
import logging
import os
import pickle
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

from cluster_viz.decision import GroupingDecision, modularity_of, replay_grouping
from cluster_viz.hierarchy import (
    ComponentNode,
    Scope,
    build_scopes,
    flatten_components,
    lineage_path,
    path_conflicts,
    split_cluster_id,
)
from cluster_viz.layout import Bucket, layout_graph, layout_hierarchy
from constants import STATIC_ANALYSIS_PKL
from clustering.cluster_helpers import (
    SUBCOMPONENTS_MAX,
    SUBCOMPONENTS_MIN,
    TOP_LEVEL_COMPONENTS_MAX,
    TOP_LEVEL_COMPONENTS_MIN,
    _RESOLUTION_LADDER,
)
from static_analyzer.constants import ClusteringConfig
from static_analyzer.graph import CallGraph, ClusterResult

logger = logging.getLogger(__name__)

ANALYSIS_FILENAME = "analysis.json"
EDGE_KINDS = ["call", "contains", "inherits", "typeref", "import"]
#: Node keys used in the payload, spelled out for whoever reads the JSON by hand.
NODE_SCHEMA = {
    "q": "fully qualified name",
    "n": "short name",
    "f": "file path, repo-relative",
    "s": "first line",
    "e": "last line",
    "t": "symbol kind",
    "lang": "language",
    "path": "cluster id per level, index 0 = level 1, empty where never clustered that deep",
    "comp": "deepest component that owns the method",
}
#: How many concrete call sites to keep per cross-boundary component pair.
_BOUNDARY_EXAMPLES = 8


def _relative(path: str, repo_dir: Path) -> str:
    """Repo-relative form of a stored file path, whichever way it was persisted."""
    if not path:
        return ""
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.relative_to(repo_dir).as_posix()
        except ValueError:
            return candidate.as_posix()
    return candidate.as_posix()


def _load_static_analysis(artifacts_dir: Path):
    with open(artifacts_dir / STATIC_ANALYSIS_PKL, "rb") as handle:
        return pickle.load(handle)


def _collect_nodes(static_analysis, repo_dir: Path) -> tuple[list[dict], dict[str, int], list[str]]:
    """Flatten every language's call-graph nodes into one indexed table."""
    nodes: list[dict] = []
    index_of: dict[str, int] = {}
    warnings: list[str] = []
    for language in static_analysis.get_languages():
        cfg: CallGraph = static_analysis.get_cfg(language)
        for qname, node in sorted(cfg.nodes.items()):
            if qname in index_of:
                warnings.append(f"qualified name '{qname}' exists in more than one language; keeping the first")
                continue
            index_of[qname] = len(nodes)
            nodes.append(
                {
                    "q": qname,
                    "n": qname.rsplit(".", 1)[-1],
                    "f": _relative(node.file_path, repo_dir),
                    "s": node.line_start,
                    "e": node.line_end,
                    "t": node.type.name,
                    "lang": str(language),
                    "path": [],
                    "comp": "",
                }
            )
    return nodes, index_of, warnings


def _collect_edges(static_analysis, index_of: dict[str, int]) -> list[list[int]]:
    """Call edges plus the reference edges clustering runs on, as index pairs."""
    weights: dict[tuple[int, int, int], int] = defaultdict(int)
    for language in static_analysis.get_languages():
        cfg: CallGraph = static_analysis.get_cfg(language)
        for edge in cfg.edges:
            source, target = index_of.get(edge.get_source()), index_of.get(edge.get_destination())
            if source is not None and target is not None and source != target:
                weights[(source, target, 0)] += max(len(edge.call_sites), 1)
        for source_name, target_name, kind in getattr(cfg, "reference_edges", ()):
            if kind not in EDGE_KINDS:
                continue
            source, target = index_of.get(source_name), index_of.get(target_name)
            if source is not None and target is not None and source != target:
                weights[(source, target, EDGE_KINDS.index(kind))] += 1
    return [[source, target, kind, weight] for (source, target, kind), weight in sorted(weights.items())]


def _collect_lineage(static_analysis) -> dict[str, set[str]]:
    lineage: dict[str, set[str]] = {}
    for language in static_analysis.get_languages():
        cfg: CallGraph = static_analysis.get_cfg(language)
        for qname, cluster_ids in cfg.method_cluster_paths_snapshot():
            lineage.setdefault(qname, set()).update(cluster_ids)
    return lineage


def _clustering_graph(static_analysis) -> nx.DiGraph:
    """The reference-augmented graph the pipeline clusters on, across all languages."""
    graphs = [static_analysis.get_cfg(language).clustering_networkx() for language in static_analysis.get_languages()]
    return nx.compose_all(graphs) if graphs else nx.DiGraph()


def _cluster_result_of(scope: Scope, files_of: dict[str, str]) -> tuple[ClusterResult, dict[int, str]]:
    """A ``ClusterResult`` over local integer ids, plus the map back to scoped ids."""
    clusters: dict[int, set[str]] = {}
    cluster_to_files: dict[int, set[str]] = {}
    file_to_clusters: dict[str, set[int]] = defaultdict(set)
    local_to_scoped: dict[int, str] = {}
    for cluster_id, members in sorted(scope.clusters.items(), key=lambda item: int(split_cluster_id(item[0])[1])):
        local_id = int(split_cluster_id(cluster_id)[1])
        local_to_scoped[local_id] = cluster_id
        clusters[local_id] = set(members)
        files = {files_of.get(qname, "") for qname in members} - {""}
        cluster_to_files[local_id] = files
        for file_path in files:
            file_to_clusters[file_path].add(local_id)
    result = ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=dict(file_to_clusters),
        strategy="recorded_lineage",
    )
    return result, local_to_scoped


def _edge_counts(
    keys: list[str],
    edges: list[list[int]],
    owner_of_node: dict[int, str],
) -> tuple[dict[str, int], dict[str, int], dict[tuple[str, str], int]]:
    """Call-weighted internal and external edge totals per group, plus what crosses each pair.

    A node whose cluster no component claimed lands under the empty-string key;
    callers render it as unclaimed rather than folding it into a real group.
    """
    internal: dict[str, int] = defaultdict(int, {key: 0 for key in keys})
    external: dict[str, int] = defaultdict(int, {key: 0 for key in keys})
    crossing: dict[tuple[str, str], int] = defaultdict(int)
    for source, target, _kind, weight in edges:
        source_owner, target_owner = owner_of_node.get(source), owner_of_node.get(target)
        if source_owner is None or target_owner is None:
            continue
        if source_owner == target_owner:
            internal[source_owner] += weight
            continue
        external[source_owner] += weight
        external[target_owner] += weight
        crossing[(source_owner, target_owner)] += weight
    return internal, external, dict(crossing)


def _cohesion(internal: int, external: int) -> float:
    total = internal + external
    return round(internal / total, 4) if total else 0.0


def _packages(members: set[int], nodes: list[dict]) -> dict[str, int]:
    counter = Counter(os.path.dirname(nodes[index]["f"]) or "." for index in members)
    return dict(counter.most_common(6))


def _label(members: set[int], nodes: list[dict], limit: int = 5) -> list[str]:
    """The most top-level symbols in a cluster — a name-rich stand-in for a title."""
    names = sorted((nodes[index]["q"] for index in members), key=lambda qname: (qname.count("."), qname))
    return names[:limit]


def _boundary_examples(
    edges: list[list[int]],
    owner_of_node: dict[int, str],
    nodes: list[dict],
) -> dict[tuple[str, str], list[list[str]]]:
    examples: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    for source, target, kind, _weight in edges:
        source_owner, target_owner = owner_of_node.get(source), owner_of_node.get(target)
        if not source_owner or not target_owner or source_owner == target_owner:
            continue
        pair = (source_owner, target_owner)
        if len(examples[pair]) < _BOUNDARY_EXAMPLES:
            examples[pair].append([nodes[source]["q"], nodes[target]["q"], EDGE_KINDS[kind]])
    return examples


def _scope_payload(
    scope: Scope,
    components: dict[str, ComponentNode],
    nodes: list[dict],
    index_of: dict[str, int],
    edges: list[list[int]],
    clustering_graph: nx.DiGraph,
) -> dict:
    """Everything the viewer needs about one clustering run, decision trace included."""
    members_by_cluster = {
        cluster_id: {index_of[qname] for qname in qnames if qname in index_of}
        for cluster_id, qnames in scope.clusters.items()
    }
    scope_members = {index for members in members_by_cluster.values() for index in members}
    scope_edges = [edge for edge in edges if edge[0] in scope_members and edge[1] in scope_members]

    cluster_of_node = {index: cluster_id for cluster_id, members in members_by_cluster.items() for index in members}
    cluster_internal, cluster_external, _ = _edge_counts(list(members_by_cluster), scope_edges, cluster_of_node)

    owner_of_cluster = scope.cluster_owner()
    component_of_node = {index: owner_of_cluster.get(cluster_id, "") for index, cluster_id in cluster_of_node.items()}
    grouped: dict[str, set[int]] = {component_id: set() for component_id in scope.groups}
    for index, component_id in component_of_node.items():
        if component_id:
            grouped[component_id].add(index)
    group_internal, group_external, group_crossing = _edge_counts(list(grouped), scope_edges, component_of_node)
    boundary = _boundary_examples(scope_edges, component_of_node, nodes)

    files_of = {node["q"]: node["f"] for node in nodes}
    cluster_result, scoped = _cluster_result_of(scope, files_of)
    subgraph = clustering_graph.subgraph({nodes[index]["q"] for index in scope_members})
    low, high = (
        (TOP_LEVEL_COMPONENTS_MIN, TOP_LEVEL_COMPONENTS_MAX)
        if scope.scope_id == ""
        else (SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX)
    )
    decision, meta_graph = replay_grouping(cluster_result, nx.DiGraph(subgraph), low, high)
    # The meta-graph the decision was scored on is the one to show: one unit of weight
    # per graph edge between two leaf clusters, which is what modularity saw.
    meta_edges = [
        (scoped[source], scoped[target], float(data["weight"])) for source, target, data in meta_graph.edges(data=True)
    ]
    shipped_groups = [
        {int(split_cluster_id(cid)[1]) for cid in cluster_ids} for cluster_ids in scope.groups.values() if cluster_ids
    ]

    replay_group_of: dict[str, int] = {}
    for position, group in enumerate(decision.groups):
        for local_id in group:
            replay_group_of[scoped[local_id]] = position
    absorbed = {scoped[item.cluster_id]: item for item in decision.absorptions}
    promoted = {scoped[local_id] for local_id in decision.promoted}

    clusters_payload = {}
    for cluster_id, members in sorted(members_by_cluster.items(), key=lambda item: int(split_cluster_id(item[0])[1])):
        absorption = absorbed.get(cluster_id)
        clusters_payload[cluster_id] = {
            "members": sorted(members),
            "size": len(members),
            "files": sorted({nodes[index]["f"] for index in members if nodes[index]["f"]}),
            "packages": _packages(members, nodes),
            "symbols": _label(members, nodes),
            "component": owner_of_cluster.get(cluster_id, ""),
            "internal_edges": cluster_internal.get(cluster_id, 0),
            "external_edges": cluster_external.get(cluster_id, 0),
            "cohesion": _cohesion(cluster_internal.get(cluster_id, 0), cluster_external.get(cluster_id, 0)),
            "replay_group": replay_group_of.get(cluster_id, -1),
            "role": ("absorbed" if absorption else "promoted_seed" if cluster_id in promoted else "seed"),
            "absorbed_by": (
                {"reason": absorption.reason, "hops": absorption.hops, "package_affinity": absorption.package_affinity}
                if absorption
                else {}
            ),
        }

    groups_payload = []
    for component_id, cluster_ids in scope.groups.items():
        component = components.get(component_id)
        groups_payload.append(
            {
                "component_id": component_id,
                "name": component.name if component else component_id,
                "description": component.description if component else "",
                "cluster_ids": cluster_ids,
                "size": len(grouped.get(component_id, set())),
                "internal_edges": group_internal.get(component_id, 0),
                "external_edges": group_external.get(component_id, 0),
                "cohesion": _cohesion(group_internal.get(component_id, 0), group_external.get(component_id, 0)),
                "packages": _packages(grouped.get(component_id, set()), nodes),
            }
        )

    meta_layout = layout_graph(sorted(members_by_cluster), meta_edges)

    return {
        "scope_id": scope.scope_id,
        "level": scope.level,
        "size": len(scope_members),
        "cluster_count": len(members_by_cluster),
        "method_level_expansion": bool(members_by_cluster) and all(len(m) == 1 for m in members_by_cluster.values()),
        "clusters": clusters_payload,
        "groups": groups_payload,
        "meta_graph": sorted([source, target, weight] for source, target, weight in meta_edges),
        "meta_layout": {cluster_id: [round(x, 4), round(y, 4)] for cluster_id, (x, y) in meta_layout.items()},
        "boundaries": [
            {
                "from": pair[0],
                "to": pair[1],
                "weight": weight,
                "examples": boundary.get(pair, []),
            }
            for pair, weight in sorted(group_crossing.items(), key=lambda item: -item[1])
            if pair[0] and pair[1]
        ],
        "decision": _decision_payload(decision, scoped, meta_graph, shipped_groups),
    }


def _decision_payload(
    decision: GroupingDecision,
    scoped: dict[int, str],
    meta_graph: nx.DiGraph,
    shipped_groups: list[set[int]],
) -> dict:
    """The grouping trace, with the replayed and shipped partitions scored side by side."""
    payload = asdict(decision)
    payload["seeds"] = [[scoped.get(cid, str(cid)) for cid in group] for group in decision.seeds]
    payload["groups"] = [[scoped.get(cid, str(cid)) for cid in group] for group in decision.groups]
    payload["promoted"] = [scoped.get(cid, str(cid)) for cid in decision.promoted]
    for absorption in payload["absorptions"]:
        absorption["cluster_id"] = scoped.get(absorption["cluster_id"], str(absorption["cluster_id"]))
    live = set(meta_graph.nodes)
    claimed = [group & live for group in shipped_groups]
    unclaimed = live - {cid for group in claimed for cid in group}
    # Modularity is only defined over a full partition, so clusters no component claimed
    # (an incremental run regrouped since) are scored as singletons rather than dropped.
    payload["shipped_modularity"] = modularity_of(
        meta_graph, [group for group in claimed if group] + [{cid} for cid in unclaimed]
    )
    payload["shipped_unclaimed"] = sorted(scoped.get(cid, str(cid)) for cid in unclaimed)
    payload["shipped_matches_replay"] = not unclaimed and sorted(sorted(group) for group in claimed if group) == sorted(
        sorted(group) for group in decision.groups
    )
    return payload


def _components_payload(components: dict[str, ComponentNode], nodes: list[dict]) -> list[dict]:
    """The component tree, each entry carrying how much code it owns."""
    own: Counter[str] = Counter(node["comp"] for node in nodes if node["comp"])
    payload = []
    for component_id, component in sorted(components.items()):
        entry = asdict(component)
        entry["own_methods"] = own[component_id]
        entry["subtree_methods"] = sum(
            count for other, count in own.items() if other == component_id or other.startswith(f"{component_id}.")
        )
        payload.append(entry)
    return payload


def _build_layout(
    nodes: list[dict],
    edges: list[list[int]],
    scopes: dict[str, Scope],
    components: dict[str, ComponentNode],
) -> dict:
    """Nest every method inside its component chain, deepest cluster innermost."""
    owner_of_cluster: dict[str, str] = {}
    for scope in scopes.values():
        owner_of_cluster.update(scope.cluster_owner())

    chains: dict[int, list[str]] = {}
    for index, node in enumerate(nodes):
        chain: list[str] = []
        finest_cluster = ""
        for cluster_id in node["path"]:
            if not cluster_id:
                continue
            finest_cluster = cluster_id
            component_id = owner_of_cluster.get(cluster_id, "")
            if component_id and component_id not in chain:
                chain.append(component_id)
        chains[index] = [*chain, f"c:{finest_cluster}"] if finest_cluster else ["c:unclustered"]

    root = Bucket(key="")
    by_key: dict[str, Bucket] = {"": root}
    for index, chain in chains.items():
        parent = root
        for position, key in enumerate(chain):
            bucket = by_key.get(key)
            if bucket is None:
                bucket = Bucket(key=key)
                by_key[key] = bucket
                parent.children.append(bucket)
            parent = bucket
            if position == len(chain) - 1:
                bucket.members.append(index)

    placement = layout_hierarchy(root, [(source, target, float(weight)) for source, target, _kind, weight in edges])
    positions = [[0.0, 0.0] for _ in nodes]
    for index, (x, y) in placement.nodes.items():
        positions[index] = [round(x, 3), round(y, 3)]
    circles = {
        key: [round(x, 3), round(y, 3), round(radius, 3)]
        for key, (x, y, radius) in placement.circles.items()
        if key in by_key
    }
    labels = {key: components[key].name for key in circles if key in components}
    return {"nodes": positions, "circles": circles, "labels": labels}


def export_clustering(artifacts_dir: Path, repo_dir: Path) -> dict:
    """Build the clustering payload for one finished analysis directory."""
    artifacts_dir = artifacts_dir.resolve()
    repo_dir = repo_dir.resolve()
    analysis = json.loads((artifacts_dir / ANALYSIS_FILENAME).read_text(encoding="utf-8"))
    static_analysis = _load_static_analysis(artifacts_dir)

    nodes, index_of, warnings = _collect_nodes(static_analysis, repo_dir)
    edges = _collect_edges(static_analysis, index_of)
    lineage = _collect_lineage(static_analysis)
    components = flatten_components(analysis.get("components", []))
    scopes = build_scopes(lineage, components)

    max_level = max((scope.level for scope in scopes.values()), default=0)
    owner_of_cluster: dict[str, str] = {}
    for scope in scopes.values():
        owner_of_cluster.update(scope.cluster_owner())
    conflicted = 0
    for node in nodes:
        cluster_ids = lineage.get(node["q"], set())
        node["path"] = lineage_path(cluster_ids, max_level)
        conflicted += 1 if path_conflicts(cluster_ids) else 0
        deepest = [cid for cid in node["path"] if cid]
        node["comp"] = owner_of_cluster.get(deepest[-1], "") if deepest else ""
    if conflicted:
        warnings.append(
            f"{conflicted} method(s) carry more than one cluster id at some level; "
            "the lowest id was used for the layout path"
        )
    unclustered = sum(1 for node in nodes if not any(node["path"]))
    if unclustered:
        warnings.append(f"{unclustered} method(s) carry no cluster lineage and are drawn outside every component")

    clustering_graph = _clustering_graph(static_analysis)
    scope_payloads = [
        _scope_payload(scope, components, nodes, index_of, edges, clustering_graph)
        for _, scope in sorted(scopes.items(), key=lambda item: (item[1].level, item[0]))
    ]
    for payload in scope_payloads:
        if not payload["decision"]["matches_pipeline"]:
            warnings.append(f"scope '{payload['scope_id']}': grouping replay diverged from the pipeline helper")
        if not payload["decision"]["shipped_matches_replay"]:
            warnings.append(
                f"scope '{payload['scope_id']}': the shipped grouping differs from a from-scratch replay "
                "(expected when the run was incremental or the LLM regrouped)"
            )

    metadata = analysis.get("metadata", {})
    levels = [
        {
            "level": level,
            "scopes": [payload["scope_id"] for payload in scope_payloads if payload["level"] == level],
            "clusters": sum(payload["cluster_count"] for payload in scope_payloads if payload["level"] == level),
            "components": sum(1 for component in components.values() if component.level == level),
        }
        for level in range(1, max_level + 1)
    ]

    return {
        "meta": {
            "project": metadata.get("repo_name", repo_dir.name),
            "repo_dir": str(repo_dir),
            "artifacts_dir": str(artifacts_dir),
            "analysis_generated_at": metadata.get("generated_at", ""),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "description": analysis.get("description", ""),
            "languages": [str(language) for language in static_analysis.get_languages()],
            "depth_level": metadata.get("depth_level", 0),
            "file_coverage": metadata.get("file_coverage_summary", {}),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "levels": max_level,
            "clustering": {
                "seed": ClusteringConfig.CLUSTERING_SEED,
                "resolution_ladder": list(_RESOLUTION_LADDER),
                "top_level_range": [TOP_LEVEL_COMPONENTS_MIN, TOP_LEVEL_COMPONENTS_MAX],
                "subcomponent_range": [SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX],
                "edge_kinds_clustered": sorted(ClusteringConfig.CLUSTERING_EDGE_KINDS),
                "root_strategy": {
                    str(language): getattr(static_analysis.get_cfg(language)._cluster_cache, "strategy", "")
                    for language in static_analysis.get_languages()
                },
            },
            "warnings": warnings,
        },
        "schema": {"nodes": NODE_SCHEMA, "edges": "[source index, target index, edge kind index, weight]"},
        "edge_kinds": EDGE_KINDS,
        "nodes": nodes,
        "edges": edges,
        "components": _components_payload(components, nodes),
        "levels": levels,
        "scopes": scope_payloads,
        "layout": _build_layout(nodes, edges, scopes, components),
    }
