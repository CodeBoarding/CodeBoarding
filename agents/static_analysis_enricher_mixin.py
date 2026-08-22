"""Apply deterministic static-analysis scope data to LLM analysis models."""

import logging
from collections.abc import Callable
from pathlib import Path

from constants import DEFAULT_STATIC_RELATION_LABEL
from agents.agent_responses import AnalysisInsights, Component, ComponentArchitecture, Relation, RelationEdge
from agents.cluster_ids import CodeBoardingClusterIds
from agents.component_ownership import ComponentOwnershipIndex
from agents.content_hash import SourceCache
from agents.llm_renderers import cluster_group_descriptions, cluster_group_ids
from agents.relation_edges import (
    append_or_merge_relation,
    drop_internal_self_relations,
    drop_reverse_duplicates,
    edge_crosses_components,
    ground_relation_edges,
    prune_ungrounded_edges,
)
from diagram_analysis.file_index import build_file_methods_from_nodes, build_files_index
from static_analyzer.cfg import Edge
from static_analyzer.clustering import ClusterScopeResult, GroupConnection

logger = logging.getLogger(__name__)


class StaticAnalysisEnricherMixin:
    """Enrich LLM output from one authoritative clustering scope."""

    repo_dir: Path

    @staticmethod
    def assemble_one_component_per_group(
        architecture: ComponentArchitecture,
        scope: ClusterScopeResult,
    ) -> None:
        """Reconcile LLM metadata to exactly one component per fixed group."""
        rendered_group_ids = cluster_group_ids(scope.groups)
        descriptions = cluster_group_descriptions(scope)

        used: set[int] = set()
        components: list[Component] = []
        for group_name, group in zip(rendered_group_ids, scope.groups, strict=True):
            component = next(
                (
                    candidate
                    for candidate in architecture.components
                    if any(name.lower() == group_name.lower() for name in candidate.source_group_names)
                ),
                None,
            )
            if component is None or id(component) in used:
                symbols = sorted(group.qualified_names)
                component = Component(
                    name=symbols[0].split(".")[-1] if symbols else group_name,
                    description=descriptions[group_name],
                    key_entities=[],
                )
            else:
                used.add(id(component))
                component = component.model_copy(deep=True)
            component.source_group_names = [group_name]
            component.source_cluster_ids = CodeBoardingClusterIds.from_graph_ids(set(group.cluster_ids))
            component.component_id = group.group_id
            components.append(component)

        if len(components) != len(architecture.components):
            logger.info(
                "[StaticAnalysisEnricher] Reconciled %d LLM components to %d fixed groups",
                len(architecture.components),
                len(components),
            )
        architecture.components = components

    def populate_file_methods(
        self,
        analysis: AnalysisInsights,
        scope: ClusterScopeResult,
    ) -> None:
        """Populate component files from authoritative scope membership."""
        source_cache: SourceCache = {}
        assigned = 0
        for group in scope.groups:
            component = analysis.component_by_id(group.group_id)
            if component is None:
                continue
            nodes = [
                scope.graphs_by_language[language].nodes[qualified_name]
                for language, qualified_names in group.symbol_members_by_language.items()
                if language in scope.graphs_by_language
                for qualified_name in sorted(qualified_names)
                if qualified_name in scope.graphs_by_language[language].nodes
            ]
            component.file_methods = build_file_methods_from_nodes(nodes, self.repo_dir, source_cache)
            assigned += sum(len(file_methods.methods) for file_methods in component.file_methods)

        analysis.files = build_files_index(analysis, self.repo_dir, source_cache)
        total = sum(len(group.qualified_names) for group in scope.groups)
        logger.info("Component symbol coverage: %d/%d assigned", assigned, total)

    @staticmethod
    def merge_scope_relations(analysis: AnalysisInsights, scope: ClusterScopeResult) -> None:
        """Merge LLM relations with precomputed scope connection evidence."""
        merged: list[Relation] = []
        matched_pairs: set[tuple[str, str]] = set()
        ownership = ComponentOwnershipIndex.from_analysis(analysis)

        for llm_relation in analysis.components_relations:
            source = analysis.component_by_id(llm_relation.src_id) or analysis.component_by_name(llm_relation.src_name)
            target = analysis.component_by_id(llm_relation.dst_id) or analysis.component_by_name(llm_relation.dst_name)
            src_id = source.component_id if source is not None else ""
            dst_id = target.component_id if target is not None else ""
            connection = scope.connection_between(src_id, dst_id)
            static_edges = StaticAnalysisEnricherMixin._connection_edges(scope, connection)
            key_edges = [
                edge
                for edge in llm_relation.key_edges
                if edge_crosses_components(
                    edge,
                    ownership.owner_of,
                    src_id,
                    dst_id,
                )
            ]
            has_evidence = bool(llm_relation.evidence.strip())
            if not static_edges and not key_edges and not has_evidence:
                continue
            if not static_edges and not key_edges:
                logger.warning(
                    "Keeping LLM-only relation without static or key-edge backing: %s -> %s (%s). Evidence: %s",
                    llm_relation.src_name,
                    llm_relation.dst_name,
                    llm_relation.relation,
                    llm_relation.evidence,
                )

            grounded_key_edges, all_edges = ground_relation_edges(key_edges, static_edges)
            if static_edges:
                matched_pairs.add((src_id, dst_id))
            append_or_merge_relation(
                merged,
                Relation(
                    relation=llm_relation.relation,
                    src_name=llm_relation.src_name,
                    dst_name=llm_relation.dst_name,
                    evidence=llm_relation.evidence,
                    key_edges=grounded_key_edges,
                    src_id=src_id,
                    dst_id=dst_id,
                    is_static=bool(static_edges),
                    all_edges=all_edges,
                ),
            )

        for connection in scope.connections:
            pair = (connection.source_group_id, connection.target_group_id)
            if pair in matched_pairs:
                continue
            edges = StaticAnalysisEnricherMixin._connection_edges(scope, connection)
            if not edges:
                continue
            source = analysis.component_by_id(connection.source_group_id)
            target = analysis.component_by_id(connection.target_group_id)
            append_or_merge_relation(
                merged,
                Relation.from_edges(
                    DEFAULT_STATIC_RELATION_LABEL,
                    source.name if source is not None else connection.source_group_id,
                    target.name if target is not None else connection.target_group_id,
                    connection.source_group_id,
                    connection.target_group_id,
                    edges,
                    True,
                ),
            )

        analysis.components_relations = drop_reverse_duplicates(drop_internal_self_relations(merged))
        logger.info(
            "Merged relations: %d total (%d static-backed, %d LLM-only)",
            len(analysis.components_relations),
            sum(1 for relation in analysis.components_relations if relation.is_static),
            sum(1 for relation in analysis.components_relations if not relation.is_static),
        )

    @staticmethod
    def prune_relations(
        analysis: AnalysisInsights,
        keep_edge: Callable[[RelationEdge], bool],
        changed_members: set[str] | None = None,
    ) -> None:
        """Re-apply static edge filters after incremental relation preservation."""
        ownership = ComponentOwnershipIndex.from_analysis(analysis)
        analysis.components_relations = prune_ungrounded_edges(
            analysis.components_relations,
            ownership.owner_of,
            keep_edge,
            changed_members,
        )

    @staticmethod
    def qualify_source_cluster_ids(analysis: AnalysisInsights, scope_id: str) -> None:
        """Qualify scope-local leaf cluster lineage for persistence."""
        for component in analysis.components:
            component.source_cluster_ids = CodeBoardingClusterIds.qualify_local_ids(
                component.source_cluster_ids,
                CodeBoardingClusterIds.prefix_for_scope(scope_id),
            )

    @staticmethod
    def _connection_edges(scope: ClusterScopeResult, connection: GroupConnection | None) -> list[RelationEdge]:
        """Convert one precomputed connection into source-backed relation edges."""
        if connection is None:
            return []
        edges: list[RelationEdge] = []
        for connection_edge in connection.edges:
            graph = scope.graphs_by_language.get(connection_edge.language)
            if graph is None:
                continue
            source = graph.nodes.get(connection_edge.source_qualified_name)
            target = graph.nodes.get(connection_edge.target_qualified_name)
            if source is None or target is None:
                continue
            edges.append(RelationEdge.from_edge(Edge(source, target, connection_edge.call_sites)))
        return edges
