type GraphClusterId = int
type CodeBoardingClusterId = str


class GraphClusterIds:
    @classmethod
    def sort(cls, cluster_ids: set[GraphClusterId]) -> list[GraphClusterId]:
        return sorted(cluster_ids)


class CodeBoardingClusterIds:
    @classmethod
    def sort(cls, cluster_ids: set[CodeBoardingClusterId]) -> list[CodeBoardingClusterId]:
        # Sort by depth first, then naturally within a level: ["1", "2", "10", "2.1", "3.4"].
        return sorted(cluster_ids, key=_cluster_id_sort_key)

    @classmethod
    def from_graph_id(cls, cluster_id: GraphClusterId) -> CodeBoardingClusterId:
        return str(cluster_id)

    @classmethod
    def from_graph_ids(cls, cluster_ids: set[GraphClusterId]) -> list[CodeBoardingClusterId]:
        return [cls.from_graph_id(cluster_id) for cluster_id in GraphClusterIds.sort(cluster_ids)]

    @classmethod
    def qualify_local_id(
        cls, local_cluster_id: CodeBoardingClusterId, source_cluster_id_prefix: str = ""
    ) -> CodeBoardingClusterId:
        if not source_cluster_id_prefix:
            return local_cluster_id
        return f"{source_cluster_id_prefix}.{local_cluster_id}"

    @classmethod
    def qualify_local_ids(
        cls, local_cluster_ids: list[CodeBoardingClusterId], source_cluster_id_prefix: str = ""
    ) -> list[CodeBoardingClusterId]:
        return [cls.qualify_local_id(cluster_id, source_cluster_id_prefix) for cluster_id in local_cluster_ids]


def _cluster_id_sort_key(cluster_id: CodeBoardingClusterId) -> tuple[tuple[int, int | str], ...]:
    parts = tuple((0, int(part)) if part.isdigit() else (1, part) for part in cluster_id.split("."))
    return ((0, len(parts)), *parts)
