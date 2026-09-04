"""Build diagram scopes from deterministic clustering results."""

import logging
from collections.abc import Callable
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, Relation, RelationEdge
from agents.component_ownership import ComponentOwnershipIndex
from agents.content_hash import SourceCache
from agents.relation_edges import (
    append_or_merge_relation,
    drop_internal_self_relations,
    drop_reverse_duplicates,
    edge_crosses_components,
    ground_relation_edges,
    prune_ungrounded_edges,
)
from agents.scope_analysis_agent import ScopeAnalysisResult
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.scope_plan import deterministic_component_name
from clustering_ids import CodeBoardingClusterIds
from constants import DEFAULT_STATIC_RELATION_LABEL
from diagram_analysis.file_index import build_file_methods_from_nodes, build_files_index
from static_analyzer import StaticAnalysisFatalError
from static_analyzer.cfg import Edge
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult, GroupConnection
from static_analyzer.reference_resolver import StaticReferenceResolver

logger = logging.getLogger(__name__)


class ScopeAssembler:
    """Materialize authoritative component membership and static relations."""

    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def build(self, scope: ClusterScopeResult) -> AnalysisInsights:
        """Build a complete deterministic analysis for one scope."""
        if scope.scope_id == ROOT_SCOPE_ID and not scope.groups:
            raise StaticAnalysisFatalError(
                "No component groups found: static analysis produced no callable structure "
                "to build an architecture from."
            )

        components: list[Component] = []
        taken: set[str] = set()
        for group in scope.groups:
            components.append(
                Component(
                    name=deterministic_component_name(group, taken),
                    description=self._fallback_description(scope, group),
                    key_entities=[],
                    source_cluster_ids=CodeBoardingClusterIds.from_graph_ids(set(group.cluster_ids)),
                    component_id=group.group_id,
                )
            )

        analysis = AnalysisInsights(
            description=f"Deterministic component structure for scope {scope.scope_id}.",
            components=components,
            components_relations=[],
        )
        self.populate_file_methods(analysis, scope)
        self.merge_scope_relations(analysis, scope)
        return analysis

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
                raise RuntimeError(f"Clustering group '{group.group_id}' has no matching component")
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

    def apply_semantics(
        self,
        analysis: AnalysisInsights,
        scope: ClusterScopeResult,
        result: ScopeAnalysisResult,
        editable_group_ids: set[str],
        locked_name_ids: set[str],
        reference_resolver: StaticReferenceResolver,
    ) -> None:
        """Apply valid semantic fields without changing deterministic structure."""
        components = {component.component_id: component for component in analysis.components}
        semantics_by_id = {item.group_id: item for item in result.components if item.group_id in components}
        used_names = {component.name for component in analysis.components}
        updated_ids: set[str] = set()
        # Scope order, not set order: a duplicate name proposed for two groups must resolve the
        # same way on every run.
        for group_id in (group.group_id for group in scope.groups if group.group_id in editable_group_ids):
            component = components.get(group_id)
            component_semantics = semantics_by_id.get(group_id)
            if component is None or component_semantics is None:
                continue
            proposed_name = component_semantics.name.strip()
            if (
                group_id not in locked_name_ids
                and proposed_name
                and (proposed_name == component.name or proposed_name not in used_names)
            ):
                used_names.discard(component.name)
                component.name = proposed_name
                used_names.add(proposed_name)
            if component_semantics.description.strip():
                component.description = component_semantics.description.strip()
            if component_semantics.key_entities:
                component.key_entities = [
                    reference.model_copy(deep=True) for reference in component_semantics.key_entities
                ]
                updated_ids.add(group_id)

        if updated_ids:
            reference_resolver.fix_key_entities_refs(analysis, updated_ids)
        if result.description.strip():
            analysis.description = result.description.strip()

        ownership = ComponentOwnershipIndex.from_analysis(analysis)
        preserved = [
            relation.model_copy(deep=True)
            for relation in analysis.components_relations
            if relation.src_id not in editable_group_ids and relation.dst_id not in editable_group_ids
        ]
        preserved_pairs = {(relation.src_id, relation.dst_id) for relation in preserved}
        existing_by_pair = {(relation.src_id, relation.dst_id): relation for relation in analysis.components_relations}
        for relation in preserved:
            source = components.get(relation.src_id)
            target = components.get(relation.dst_id)
            if source is not None:
                relation.src_name = source.name
            if target is not None:
                relation.dst_name = target.name

        semantic_relations: list[Relation] = []
        seen_pairs: set[tuple[str, str]] = set()
        for relation_semantics in result.relations:
            pair = (relation_semantics.source_group_id, relation_semantics.target_group_id)
            if (
                pair in seen_pairs
                or pair[0] == pair[1]
                or pair[0] not in components
                or pair[1] not in components
                or not editable_group_ids.intersection(pair)
                or not relation_semantics.relation.strip()
            ):
                continue
            seen_pairs.add(pair)
            key_edges = self._resolve_semantic_edges(
                relation_semantics.key_edges,
                components[pair[0]],
                components[pair[1]],
                ownership,
                reference_resolver,
            )
            has_static_connection = scope.connection_between(*pair) is not None
            if not has_static_connection and (not relation_semantics.evidence.strip() or not key_edges):
                logger.warning(
                    "Dropping unsupported semantic relation %s -> %s",
                    pair[0],
                    pair[1],
                )
                continue
            semantic_relations.append(
                Relation(
                    relation=relation_semantics.relation.strip(),
                    src_name=components[pair[0]].name,
                    dst_name=components[pair[1]].name,
                    evidence=relation_semantics.evidence.strip(),
                    key_edges=key_edges,
                    src_id=pair[0],
                    dst_id=pair[1],
                )
            )

        # A group the model was asked about drops every relation it touches. Where the model did
        # not label one back, keep the label the previous run gave it rather than resetting a
        # still-connected edge to the generic default.
        carried = [
            relation.model_copy(deep=True)
            for pair, relation in existing_by_pair.items()
            if pair not in seen_pairs
            and pair not in preserved_pairs
            and relation.relation.strip()
            and relation.relation != DEFAULT_STATIC_RELATION_LABEL
            and scope.connection_between(*pair) is not None
        ]
        for relation in carried:
            source = components.get(relation.src_id)
            target = components.get(relation.dst_id)
            if source is not None:
                relation.src_name = source.name
            if target is not None:
                relation.dst_name = target.name

        analysis.components_relations = [*preserved, *carried, *semantic_relations]
        self.merge_scope_relations(analysis, scope)

    @staticmethod
    def merge_scope_relations(analysis: AnalysisInsights, scope: ClusterScopeResult) -> None:
        """Merge existing relation metadata with precomputed scope connections."""
        merged: list[Relation] = []
        matched_pairs: set[tuple[str, str]] = set()
        ownership = ComponentOwnershipIndex.from_analysis(analysis)

        for llm_relation in analysis.components_relations:
            source = analysis.component_by_id(llm_relation.src_id) or analysis.component_by_name(llm_relation.src_name)
            target = analysis.component_by_id(llm_relation.dst_id) or analysis.component_by_name(llm_relation.dst_name)
            src_id = source.component_id if source is not None else ""
            dst_id = target.component_id if target is not None else ""
            connection = scope.connection_between(src_id, dst_id)
            static_edges = ScopeAssembler._connection_edges(scope, connection)
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
                    "Keeping semantic relation without static or key-edge backing: %s -> %s (%s). Evidence: %s",
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
            edges = ScopeAssembler._connection_edges(scope, connection)
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
            "Merged relations: %d total (%d static-backed, %d semantic-only)",
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
    def _fallback_description(scope: ClusterScopeResult, group: ClusterGroup) -> str:
        files = sorted(
            {
                scope.graphs_by_language[language].nodes[qualified_name].file_path
                for language, qualified_names in group.symbol_members_by_language.items()
                if language in scope.graphs_by_language
                for qualified_name in qualified_names
                if qualified_name in scope.graphs_by_language[language].nodes
            }
        )
        shown = ", ".join(files[:8])
        suffix = ", ..." if len(files) > 8 else ""
        return f"Owns {len(group.qualified_names)} symbols across {len(files)} files: {shown}{suffix}."

    @staticmethod
    def _resolve_semantic_edges(
        edges: list[RelationEdge],
        source_component: Component,
        target_component: Component,
        ownership: ComponentOwnershipIndex,
        reference_resolver: StaticReferenceResolver,
    ) -> list[RelationEdge]:
        """Keep exact, resolvable edges whose endpoints belong to the declared groups."""
        resolved: list[RelationEdge] = []
        for original in edges:
            edge = original.model_copy(deep=True)
            source_ok = reference_resolver.resolve_reference(edge.source, source_component.file_paths())
            target_ok = reference_resolver.resolve_reference(edge.target, target_component.file_paths())
            if not source_ok or not target_ok:
                continue
            if ownership.owner_of(edge.source) != source_component.component_id:
                continue
            if ownership.owner_of(edge.target) != target_component.component_id:
                continue
            reference_resolver.attach_static_call_sites(edge)
            resolved.append(edge)
        return Relation.unique_edges(resolved)

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
