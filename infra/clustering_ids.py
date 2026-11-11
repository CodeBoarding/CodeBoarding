"""Identifier types and operations shared across clustering layers."""

type ClusterId = int
type ScopeId = str
type GroupId = str
type ComponentId = str

ROOT_SCOPE_ID: ScopeId = "root"


class GraphClusterIds:
    @classmethod
    def sort(cls, cluster_ids: set[ClusterId]) -> list[ClusterId]:
        return sorted(cluster_ids)


class CodeBoardingClusterIds:
    @classmethod
    def prefix_for_scope(cls, scope_id: ScopeId) -> ComponentId:
        return "" if scope_id == ROOT_SCOPE_ID else scope_id

    @classmethod
    def sort(cls, cluster_ids: set[ComponentId]) -> list[ComponentId]:
        return sorted(cluster_ids, key=_cluster_id_sort_key)

    @classmethod
    def from_graph_id(cls, cluster_id: ClusterId) -> ComponentId:
        return str(cluster_id)

    @classmethod
    def from_graph_ids(cls, cluster_ids: set[ClusterId]) -> list[ComponentId]:
        return [cls.from_graph_id(cluster_id) for cluster_id in GraphClusterIds.sort(cluster_ids)]

    @classmethod
    def qualify_local_id(cls, local_cluster_id: ComponentId, source_cluster_id_prefix: str = "") -> ComponentId:
        if not source_cluster_id_prefix:
            return local_cluster_id
        return f"{source_cluster_id_prefix}.{local_cluster_id}"

    @classmethod
    def qualify_local_ids(
        cls, local_cluster_ids: list[ComponentId], source_cluster_id_prefix: str = ""
    ) -> list[ComponentId]:
        return [cls.qualify_local_id(cluster_id, source_cluster_id_prefix) for cluster_id in local_cluster_ids]


def _cluster_id_sort_key(cluster_id: ComponentId) -> tuple[tuple[int, int | str], ...]:
    parts = tuple((0, int(part)) if part.isdigit() else (1, part) for part in cluster_id.split("."))
    return ((0, len(parts)), *parts)


def is_self_or_descendant(component_id: ComponentId, ancestor_id: ComponentId) -> bool:
    """True when component_id is ancestor_id or one of its dotted descendants."""
    return component_id == ancestor_id or component_id.startswith(f"{ancestor_id}.")
