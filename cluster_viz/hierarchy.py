"""Rebuild the clustering hierarchy from recorded cluster lineage.

A *scope* is one clustering run. The root scope (id ``""``) partitions every
method in the repo into leaf clusters ``"1"``, ``"2"``, ... and groups them into
the top-level components; each expanded component re-clusters its own methods
under its component id, producing ``"1.4"``, ``"1.1.3"`` and so on. So a scoped
cluster id is ``<owning component id>.<local cluster id>``, and its level is its
segment count.
"""

from dataclasses import dataclass, field


def level_of(cluster_id: str) -> int:
    """Clustering level a scoped cluster id belongs to (root clusters are level 1)."""
    return cluster_id.count(".") + 1


def split_cluster_id(cluster_id: str) -> tuple[str, str]:
    """Split ``"1.1.3"`` into its scope (``"1.1"``) and local id (``"3"``)."""
    scope_id, _, local_id = cluster_id.rpartition(".")
    return scope_id, local_id


def is_cluster_id(cluster_id: str) -> bool:
    """True when the trailing segment is a numeric local cluster id."""
    return split_cluster_id(cluster_id)[1].isdigit()


@dataclass
class ComponentNode:
    """One component of the final analysis, flattened out of the nested tree."""

    component_id: str
    name: str
    description: str
    parent_id: str
    level: int
    cluster_ids: list[str]
    can_expand: bool
    key_entities: list[str] = field(default_factory=list)


@dataclass
class Scope:
    """One clustering run: leaf clusters plus the components they were grouped into."""

    scope_id: str
    level: int
    clusters: dict[str, set[str]] = field(default_factory=dict)
    #: child component id -> the cluster ids it claimed, in analysis order.
    groups: dict[str, list[str]] = field(default_factory=dict)

    def members(self) -> set[str]:
        return {qname for members in self.clusters.values() for qname in members}

    def cluster_owner(self) -> dict[str, str]:
        """cluster id -> component id that claimed it."""
        return {cluster_id: component_id for component_id, ids in self.groups.items() for cluster_id in ids}


def flatten_components(components: list[dict]) -> dict[str, ComponentNode]:
    """Walk the nested ``analysis.json`` component tree into a flat id -> node map."""
    flat: dict[str, ComponentNode] = {}

    def walk(nodes: list[dict], parent_id: str) -> None:
        for node in nodes:
            component_id = str(node.get("component_id", ""))
            if not component_id:
                continue
            flat[component_id] = ComponentNode(
                component_id=component_id,
                name=str(node.get("name", component_id)),
                description=str(node.get("description", "")),
                parent_id=parent_id,
                level=component_id.count(".") + 1,
                cluster_ids=[str(cid) for cid in node.get("source_cluster_ids", [])],
                can_expand=bool(node.get("can_expand", False)),
                key_entities=[str(entity.get("qualified_name", "")) for entity in node.get("key_entities", [])],
            )
            walk(node.get("components") or [], component_id)

    walk(components, "")
    return flat


def build_scopes(lineage: dict[str, set[str]], components: dict[str, ComponentNode]) -> dict[str, Scope]:
    """Group recorded cluster ids into one ``Scope`` per clustering run.

    ``lineage`` maps a qualified name to every scoped cluster id it carries. The
    grouping of clusters into components is read back from each component's
    ``source_cluster_ids``, which name clusters in the *parent* scope.
    """
    scopes: dict[str, Scope] = {}
    for qname, cluster_ids in lineage.items():
        for cluster_id in cluster_ids:
            if not is_cluster_id(cluster_id):
                continue
            scope_id, _ = split_cluster_id(cluster_id)
            scope = scopes.setdefault(scope_id, Scope(scope_id=scope_id, level=level_of(cluster_id)))
            scope.clusters.setdefault(cluster_id, set()).add(qname)

    for component in components.values():
        scope = scopes.get(component.parent_id)
        if scope is None:
            continue
        scope.groups[component.component_id] = [cid for cid in component.cluster_ids if cid in scope.clusters]

    return scopes


def lineage_path(cluster_ids: set[str], max_level: int) -> list[str]:
    """The one cluster id per level a method carries, indexed by level - 1.

    Empty where the method was never clustered at that level (its component was
    a leaf). A level holding more than one id means the lineage disagrees with
    itself; the lowest id wins so the path stays deterministic, and the caller
    reports the conflict.
    """
    by_level: dict[int, list[str]] = {}
    for cluster_id in cluster_ids:
        if is_cluster_id(cluster_id):
            by_level.setdefault(level_of(cluster_id), []).append(cluster_id)
    return ["" if level not in by_level else min(by_level[level]) for level in range(1, max_level + 1)]


def path_conflicts(cluster_ids: set[str]) -> list[int]:
    """Levels at which a method carries more than one cluster id."""
    by_level: dict[int, int] = {}
    for cluster_id in cluster_ids:
        if is_cluster_id(cluster_id):
            by_level[level_of(cluster_id)] = by_level.get(level_of(cluster_id), 0) + 1
    return sorted(level for level, count in by_level.items() if count > 1)
