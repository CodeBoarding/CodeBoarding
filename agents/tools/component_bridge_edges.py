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
        if not self.context.group_ids_by_name:
            return "No grouped cluster context available."

        source_groups = self._group_ids_for_names(source_group_names)
        destination_groups = self._group_ids_for_names(destination_group_names)
        if not source_groups:
            return f"No source groups found for names: {source_group_names}"
        if not destination_groups:
            return f"No destination groups found for names: {destination_group_names}"

        all_edges: list[str] = []
        clustering = self.context.clustering
        for connection in clustering.connections:
            if connection.source_group_id not in source_groups or connection.target_group_id not in destination_groups:
                continue
            for edge in connection.edges:
                graph = clustering.graphs_by_language.get(edge.language)
                source = graph.nodes.get(edge.source_qualified_name) if graph is not None else None
                target = graph.nodes.get(edge.target_qualified_name) if graph is not None else None
                source_location = f" ({source.file_path}:{source.line_start})" if source is not None else ""
                target_location = f" ({target.file_path}:{target.line_start})" if target is not None else ""
                all_edges.append(
                    f"{edge.language}: {edge.source_qualified_name}{source_location} "
                    f"-> {edge.target_qualified_name}{target_location}"
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

    def _group_ids_for_names(self, group_names: list[str]) -> set[str]:
        return {self.context.group_ids_by_name[name] for name in group_names if name in self.context.group_ids_by_name}
