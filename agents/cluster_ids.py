type GraphClusterId = int
type CodeBoardingClusterId = str


class GraphClusterIds:
    @classmethod
    def sort(cls, cluster_ids: set[GraphClusterId]) -> list[GraphClusterId]:
        return sorted(cluster_ids)


class CodeBoardingClusterIds:
    @classmethod
    def sort(cls, cluster_ids: set[CodeBoardingClusterId]) -> list[CodeBoardingClusterId]:
        return sorted(
            cluster_ids,
            key=lambda cluster_id: (
                0 if cluster_id.isdigit() else 1,
                [int(part) if part.isdigit() else part for part in cluster_id.split(".")],
            ),
        )

    @classmethod
    def from_graph_ids(cls, cluster_ids: set[GraphClusterId]) -> list[CodeBoardingClusterId]:
        return [str(cluster_id) for cluster_id in GraphClusterIds.sort(cluster_ids)]

    @classmethod
    def qualify_local_ids(
        cls, cluster_ids: list[CodeBoardingClusterId], source_cluster_id_prefix: str = ""
    ) -> list[CodeBoardingClusterId]:
        if not source_cluster_id_prefix:
            return cluster_ids
        qualified_prefix = f"{source_cluster_id_prefix}."
        return [
            cluster_id if cluster_id.startswith(qualified_prefix) else f"{qualified_prefix}{cluster_id}"
            for cluster_id in cluster_ids
        ]
