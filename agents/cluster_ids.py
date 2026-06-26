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
