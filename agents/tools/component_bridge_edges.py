import logging

from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field

from agents.tools.base import BaseRepoTool

logger = logging.getLogger(__name__)


class ComponentBridgeEdgesInput(BaseModel):
    source_group_names: list[str] = Field(
        description="source_group_names for the source component whose outgoing calls should be checked"
    )
    destination_group_names: list[str] = Field(
        description="source_group_names for the destination component whose incoming calls should be checked"
    )


class ComponentBridgeEdgesTool(BaseRepoTool):
    name: str = "getComponentBridgeEdges"
    description: str = (
        "Returns directed static CFG method/function calls between two candidate components. "
        "Use this before adding a static relationship: pass each component's source_group_names. "
        "If it returns no bridge edges, only add the relation when you found concrete runtime evidence."
    )
    args_schema: ArgsSchema = ComponentBridgeEdgesInput

    def _run(self, source_group_names: list[str], destination_group_names: list[str]) -> str:
        if not self.context.cluster_analysis.cluster_components:
            return "No grouped cluster context available."

        source_clusters = self._cluster_ids_for_groups(source_group_names)
        destination_clusters = self._cluster_ids_for_groups(destination_group_names)
        if not source_clusters:
            return f"No source clusters found for groups: {source_group_names}"
        if not destination_clusters:
            return f"No destination clusters found for groups: {destination_group_names}"

        cfg_graphs = self.context.cfg_graphs
        if not cfg_graphs:
            if not self.static_analysis:
                return "No static analysis data available."
            cfg_graphs = self.static_analysis.available_program_graphs()

        source_nodes = self._nodes_for_clusters(source_clusters)
        destination_nodes = self._nodes_for_clusters(destination_clusters)
        all_edges: list[str] = []

        for language, cfg in cfg_graphs.items():
            for edge in cfg.call_edges():
                src_name = edge.source
                dst_name = edge.target
                if src_name in source_nodes and dst_name in destination_nodes:
                    source = cfg.nodes[src_name]
                    target = cfg.nodes[dst_name]
                    all_edges.append(
                        f"{language}: {src_name} ({source.file_path}:{source.line_start}) "
                        f"-> {dst_name} ({target.file_path}:{target.line_start})"
                    )

        if not all_edges:
            logger.info(
                "[ComponentBridgeEdgesTool] No bridge edges found for %s -> %s",
                source_group_names,
                destination_group_names,
            )
            return "No directed static bridge edges found between these component groups."

        header = f"Directed static bridge edges ({len(all_edges)}):"
        return "\n".join([header, *sorted(all_edges)])

    def _cluster_ids_for_groups(self, group_names: list[str]) -> set[int]:
        requested = set(group_names)
        cluster_ids: set[int] = set()
        for group in self.context.cluster_analysis.cluster_components:
            if group.name in requested:
                cluster_ids.update(group.cluster_ids)
        return cluster_ids

    def _nodes_for_clusters(self, cluster_ids: set[int]) -> set[str]:
        nodes: set[str] = set()
        for cluster_result in self.context.cluster_results.values():
            for cluster_id in cluster_ids:
                nodes.update(cluster_result.get_nodes_for_cluster(cluster_id))
        return nodes
