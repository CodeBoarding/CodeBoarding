"""Contract and relation reconciliation for affected scopes."""

from collections import defaultdict
import hashlib
import logging

from agents.analysis_result_responses import AnalysisInsights, Component, Relation, RelationEdge, SourceCodeReference
from agents.incremental_agent import IncrementalAgent
from constants import DEFAULT_STATIC_RELATION_LABEL
from diagram_analysis.incremental.models import CatalogEdge
from static_analyzer.cluster_relations import (
    ClusterRelation,
    ComponentRelationCandidate,
    build_component_relation_candidates,
    build_component_relations,
    build_node_to_component_map,
    render_component_relation_candidates,
)
from static_analyzer.program_graph import ProgramGraph

logger = logging.getLogger(__name__)


class IncrementalContractsUpdater:
    """Refresh semantic contracts while keeping concrete edges static-owned."""

    def __init__(self, agent: IncrementalAgent) -> None:
        self.agent = agent

    def update(
        self,
        analysis: AnalysisInsights,
        graphs: dict[str, ProgramGraph],
        affected_ids: set[str],
        deleted_ids: set[str],
    ) -> None:
        live_ids = {component.component_id for component in analysis.components}
        prior_relations = [relation for relation in analysis.components_relations if relation.src_id != relation.dst_id]
        node_to_component = build_node_to_component_map(analysis)
        static_relations = build_component_relations(node_to_component, graphs)
        candidates = build_component_relation_candidates(node_to_component, graphs)
        candidate_by_pair = {
            (candidate.src_cluster_id, candidate.dst_cluster_id): candidate for candidate in candidates
        }
        catalog = self._edge_catalog(static_relations)
        neighboring_ids = {
            component_id
            for relation in candidates
            if affected_ids & {relation.src_cluster_id, relation.dst_cluster_id}
            for component_id in (relation.src_cluster_id, relation.dst_cluster_id)
        }
        contract_ids = affected_ids | neighboring_ids
        component_context = self._component_context(
            [component for component in analysis.components if component.component_id in contract_ids]
        )
        call_catalog_context = self._catalog_context(catalog)
        candidate_context = render_component_relation_candidates(
            candidates,
            {component.component_id: component.name for component in analysis.components},
        )
        static_context = (
            f"Verified CALL edge IDs:\n{call_catalog_context or 'None'}\n\n"
            f"Architectural neighbor evidence (not CALL edge IDs):\n{candidate_context or 'None'}"
        )
        draft_by_pair = {}
        if affected_ids and component_context:
            api_surfaces = self.agent.analyze_api_surfaces(component_context, static_context, affected_ids)
            drafts = self.agent.analyze_relations(
                component_context,
                api_surfaces,
                static_context,
                "\n".join(relation.llm_str() for relation in prior_relations),
                affected_ids,
            )
            draft_by_pair = {(draft.src_id, draft.dst_id): draft for draft in drafts.relations}

        prior_by_pair = {(relation.src_id, relation.dst_id): relation for relation in prior_relations}
        id_to_name = {component.component_id: component.name for component in analysis.components}
        relations: list[Relation] = []
        static_pairs = {(relation.src_cluster_id, relation.dst_cluster_id) for relation in static_relations}
        materialized_pairs = set(static_pairs)
        for static_relation in static_relations:
            pair = (static_relation.src_cluster_id, static_relation.dst_cluster_id)
            prior = prior_by_pair.get(pair)
            draft = draft_by_pair.get(pair) if affected_ids & set(pair) else None
            key_edges = self._selected_key_edges(draft.key_static_edge_ids, pair, catalog) if draft else []
            relations.append(
                Relation(
                    relation=draft.relation if draft else prior.relation if prior else DEFAULT_STATIC_RELATION_LABEL,
                    src_name=id_to_name[pair[0]],
                    dst_name=id_to_name[pair[1]],
                    evidence=draft.evidence if draft else prior.evidence if prior else "",
                    evidence_references=(
                        draft.evidence_references if draft else prior.evidence_references if prior else []
                    ),
                    key_edges=(
                        key_edges
                        if draft
                        else self._verified_prior_key_edges(prior, static_relation.all_edges) if prior else []
                    ),
                    src_id=pair[0],
                    dst_id=pair[1],
                    is_static=True,
                    all_edges=static_relation.all_edges,
                )
            )

        owner_by_method = build_node_to_component_map(analysis)
        for pair, draft in draft_by_pair.items():
            if pair in static_pairs or not (affected_ids & set(pair)):
                continue
            if pair[0] not in live_ids or pair[1] not in live_ids or pair[0] == pair[1]:
                continue
            candidate = candidate_by_pair.get(pair)
            if candidate is not None:
                relations.append(
                    self._candidate_relation(
                        pair,
                        id_to_name,
                        candidate,
                        draft.relation,
                        draft.evidence,
                        draft.evidence_references,
                    )
                )
                materialized_pairs.add(pair)
                continue
            reference_owners = {
                owner_by_method.get(reference.qualified_name) for reference in draft.evidence_references
            }
            if not draft.evidence.strip() or not set(pair) <= reference_owners:
                logger.warning("Discarding unverified non-static relation %s -> %s", *pair)
                continue
            relations.append(
                Relation(
                    relation=draft.relation,
                    src_name=id_to_name[pair[0]],
                    dst_name=id_to_name[pair[1]],
                    evidence=draft.evidence,
                    evidence_references=draft.evidence_references,
                    src_id=pair[0],
                    dst_id=pair[1],
                    is_static=False,
                )
            )

        for pair, prior in prior_by_pair.items():
            if pair in materialized_pairs or deleted_ids & set(pair):
                continue
            if not set(pair) <= live_ids:
                continue
            candidate = candidate_by_pair.get(pair)
            if candidate is not None:
                relations.append(
                    self._candidate_relation(
                        pair,
                        id_to_name,
                        candidate,
                        prior.relation,
                        prior.evidence,
                        prior.evidence_references,
                    )
                )
            elif not affected_ids & set(pair):
                relations.append(prior)
        analysis.components_relations = sorted(relations, key=lambda relation: (relation.src_id, relation.dst_id))
        self.agent.reference_resolver.fix_source_code_reference_lines(analysis)
        analysis.components_relations = [
            relation
            for relation in analysis.components_relations
            if relation.is_static
            or not (affected_ids & {relation.src_id, relation.dst_id})
            or bool(relation.evidence_references)
        ]

    def refresh_static_edges(self, analysis: AnalysisInsights, graphs: dict[str, ProgramGraph]) -> None:
        """Refresh concrete edges without changing surviving semantic labels."""
        node_to_component = build_node_to_component_map(analysis)
        static_relations = build_component_relations(node_to_component, graphs)
        candidates = build_component_relation_candidates(node_to_component, graphs)
        candidate_by_pair = {
            (candidate.src_cluster_id, candidate.dst_cluster_id): candidate for candidate in candidates
        }
        static_by_pair = {(relation.src_cluster_id, relation.dst_cluster_id): relation for relation in static_relations}
        prior_by_pair = {
            (relation.src_id, relation.dst_id): relation
            for relation in analysis.components_relations
            if relation.src_id != relation.dst_id
        }
        id_to_name = {component.component_id: component.name for component in analysis.components}
        refreshed: list[Relation] = []
        for pair, static_relation in sorted(static_by_pair.items()):
            prior = prior_by_pair.get(pair)
            refreshed.append(
                Relation(
                    relation=prior.relation if prior else DEFAULT_STATIC_RELATION_LABEL,
                    src_name=id_to_name[pair[0]],
                    dst_name=id_to_name[pair[1]],
                    evidence=prior.evidence if prior else "",
                    evidence_references=prior.evidence_references if prior else [],
                    key_edges=self._verified_prior_key_edges(prior, static_relation.all_edges) if prior else [],
                    src_id=pair[0],
                    dst_id=pair[1],
                    is_static=True,
                    all_edges=static_relation.all_edges,
                )
            )
        for pair, prior in prior_by_pair.items():
            if pair in static_by_pair or not set(pair) <= set(id_to_name):
                continue
            candidate = candidate_by_pair.get(pair)
            if candidate is not None:
                refreshed.append(
                    self._candidate_relation(
                        pair,
                        id_to_name,
                        candidate,
                        prior.relation,
                        prior.evidence,
                        prior.evidence_references,
                    )
                )
            elif not prior.is_static or (prior.evidence.strip() and prior.evidence_references):
                prior.is_static = False
                prior.key_edges = []
                prior.all_edges = []
                prior.src_name = id_to_name[pair[0]]
                prior.dst_name = id_to_name[pair[1]]
                refreshed.append(prior)
        analysis.components_relations = sorted(refreshed, key=lambda relation: (relation.src_id, relation.dst_id))

    @staticmethod
    def _candidate_relation(
        pair: tuple[str, str],
        component_names: dict[str, str],
        candidate: ComponentRelationCandidate,
        relation: str,
        evidence: str,
        evidence_references: list[SourceCodeReference],
    ) -> Relation:
        evidence_kinds = ", ".join(sorted({item.kind.value for item in candidate.evidence}))
        return Relation(
            relation=relation,
            src_name=component_names[pair[0]],
            dst_name=component_names[pair[1]],
            evidence=evidence or f"ProgramGraph {evidence_kinds} evidence connects these components.",
            evidence_references=evidence_references,
            src_id=pair[0],
            dst_id=pair[1],
            is_static=True,
            key_edges=[],
            all_edges=[],
        )

    @staticmethod
    def _edge_catalog(relations: list[ClusterRelation]) -> dict[str, CatalogEdge]:
        catalog: dict[str, CatalogEdge] = {}
        for relation in relations:
            pair = (relation.src_cluster_id, relation.dst_cluster_id)
            for edge in relation.all_edges:
                digest = hashlib.sha256(repr(edge.identity()).encode("utf-8")).hexdigest()[:12]
                edge_id = f"edge-{digest}"
                catalog[edge_id] = CatalogEdge(edge_id, pair, edge)
        return catalog

    @staticmethod
    def _selected_key_edges(
        edge_ids: list[str],
        pair: tuple[str, str],
        catalog: dict[str, CatalogEdge],
    ) -> list[RelationEdge]:
        return [catalog[edge_id].edge for edge_id in edge_ids if edge_id in catalog and catalog[edge_id].pair == pair]

    @staticmethod
    def _verified_prior_key_edges(prior: Relation, static_edges: list[RelationEdge]) -> list[RelationEdge]:
        identities = {edge.identity() for edge in static_edges}
        return [edge for edge in prior.key_edges if edge.identity() in identities]

    @staticmethod
    def _catalog_context(catalog: dict[str, CatalogEdge]) -> str:
        by_pair: dict[tuple[str, str], list[CatalogEdge]] = defaultdict(list)
        for entry in catalog.values():
            by_pair[entry.pair].append(entry)
        lines: list[str] = []
        for pair, entries in sorted(by_pair.items()):
            for entry in sorted(entries, key=lambda item: item.edge_id)[:20]:
                lines.append(
                    f"{entry.edge_id}: {pair[0]} -> {pair[1]}: "
                    f"{entry.edge.source.qualified_name} -> {entry.edge.target.qualified_name}"
                )
            if len(entries) > 20:
                lines.append(f"{pair[0]} -> {pair[1]}: +{len(entries) - 20} additional verified calls")
        return "\n".join(lines)

    @staticmethod
    def _component_context(components: list[Component]) -> str:
        return "\n".join(
            f"[{component.component_id}] {component.name}: {component.description}; "
            f"clusters={component.source_cluster_ids}; "
            f"key_entities={[reference.qualified_name for reference in component.key_entities]}"
            for component in components
        )
