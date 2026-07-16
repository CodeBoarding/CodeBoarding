"""Deterministic hierarchical Infomap clustering."""

from __future__ import annotations

from collections import defaultdict
from math import log1p

from infomap import Infomap

from static_analyzer.clustering import (
    ClusterResult,
    InfomapClusterSnapshot,
    ModuleMembers,
    ModuleSignature,
    OverlapCandidate,
)
from static_analyzer.constants import InfomapConfig
from static_analyzer.program_graph import ProgramEdgeKind, ProgramGraph, ProgramNodeKind


class HierarchicalInfomapClusterer:
    """Cluster a ProgramGraph and update its stable lineage snapshot."""

    def __init__(
        self,
        *,
        seed: int = InfomapConfig.SEED,
        num_trials: int = InfomapConfig.NUM_TRIALS,
    ) -> None:
        self.seed = seed
        self.num_trials = num_trials

    def cluster(self, program_graph: ProgramGraph) -> ClusterResult:
        previous = program_graph.cluster_snapshot
        node_names = sorted(
            node.id for node in program_graph.nodes.values() if node.kind != ProgramNodeKind.EXTERNAL_PACKAGE
        )
        if not node_names:
            snapshot = InfomapClusterSnapshot(cluster_result=ClusterResult(strategy=InfomapConfig.EMPTY_STRATEGY))
            program_graph.cluster_snapshot = snapshot
            return snapshot.cluster_result

        numeric_id = {node_id: index for index, node_id in enumerate(node_names, start=1)}
        infomap = Infomap(directed=True, silent=True, seed=self.seed, num_trials=self.num_trials)
        for node_id in node_names:
            infomap.add_node(numeric_id[node_id], node_id)
        for source, target, weight in self._weighted_edges(program_graph, set(node_names)):
            infomap.add_link(numeric_id[source], numeric_id[target], weight)

        initial_partition = self._initial_partition(node_names, numeric_id, previous)
        if initial_partition:
            infomap.run(initial_partition=initial_partition)
        else:
            infomap.run()

        raw_paths_by_numeric = infomap.get_multilevel_modules()
        raw_paths = {node_id: tuple(raw_paths_by_numeric[numeric_id[node_id]]) for node_id in node_names}
        candidates: ModuleMembers = {}
        for node_id in node_names:
            path = raw_paths[node_id]
            raw_top = path[0] if path else numeric_id[node_id]
            candidates.setdefault(raw_top, set()).add(node_id)

        candidate_to_stable, next_cluster_id = self._reconcile(candidates, previous)
        snapshot = self._build_snapshot(
            program_graph,
            raw_paths,
            candidates,
            candidate_to_stable,
            next_cluster_id,
            float(infomap.codelength),
        )
        program_graph.cluster_snapshot = snapshot
        return snapshot.cluster_result

    @staticmethod
    def _weighted_edges(
        program_graph: ProgramGraph,
        included_nodes: set[str],
    ) -> list[tuple[str, str, float]]:
        weights: defaultdict[tuple[str, str], float] = defaultdict(float)
        for edge in program_graph.edges:
            if edge.source not in included_nodes or edge.target not in included_nodes:
                continue
            if edge.kind == ProgramEdgeKind.CALL:
                weights[(edge.source, edge.target)] += InfomapConfig.CALL_WEIGHT * log1p(edge.occurrence_count)
            elif edge.kind == ProgramEdgeKind.CONTAINS:
                weights[(edge.source, edge.target)] += InfomapConfig.CONTAINMENT_WEIGHT
                weights[(edge.target, edge.source)] += InfomapConfig.CONTAINMENT_WEIGHT
            elif edge.kind == ProgramEdgeKind.IMPORTS:
                weights[(edge.source, edge.target)] += InfomapConfig.IMPORT_WEIGHT
            elif edge.kind == ProgramEdgeKind.INHERITS:
                weights[(edge.source, edge.target)] += InfomapConfig.INHERITANCE_WEIGHT
        return [
            (source, target, round(weight, 12)) for (source, target), weight in sorted(weights.items()) if weight > 0
        ]

    @staticmethod
    def _build_snapshot(
        program_graph: ProgramGraph,
        raw_paths: dict[str, tuple[int, ...]],
        candidates: ModuleMembers,
        candidate_to_stable: dict[int, int],
        next_cluster_id: int,
        codelength: float,
    ) -> InfomapClusterSnapshot:
        module_members: ModuleMembers = {}
        node_paths: dict[str, tuple[int, ...]] = {}
        for raw_top, members in sorted(candidates.items(), key=lambda item: tuple(sorted(item[1]))):
            stable_id = candidate_to_stable[raw_top]
            module_members.setdefault(stable_id, set()).update(members)
            for node_id in sorted(members):
                raw_path = raw_paths[node_id]
                node_paths[node_id] = (stable_id, *raw_path[1:]) if raw_path else (stable_id,)

        symbol_ids = {node.id for node in program_graph.symbol_nodes()}
        clusters = {
            cluster_id: members & symbol_ids
            for cluster_id, members in sorted(module_members.items())
            if members & symbol_ids
        }
        cluster_to_files = {
            cluster_id: {
                program_graph.nodes[node_id].file_path for node_id in members if program_graph.nodes[node_id].file_path
            }
            for cluster_id, members in clusters.items()
        }
        file_to_clusters: dict[str, set[int]] = {}
        for cluster_id, files in cluster_to_files.items():
            for file_path in files:
                if file_path:
                    file_to_clusters.setdefault(file_path, set()).add(cluster_id)

        return InfomapClusterSnapshot(
            cluster_result=ClusterResult(
                clusters=clusters,
                cluster_to_files=cluster_to_files,
                file_to_clusters=file_to_clusters,
                strategy=InfomapConfig.STRATEGY,
            ),
            node_paths=node_paths,
            module_members=module_members,
            next_cluster_id=next_cluster_id,
            codelength=codelength,
        )

    @staticmethod
    def _initial_partition(
        node_names: list[str],
        numeric_id: dict[str, int],
        previous: InfomapClusterSnapshot | None,
    ) -> dict[int, int]:
        if previous is None:
            return {}
        stable_to_compact: dict[int, int] = {}
        partition: dict[int, int] = {}
        next_module = 0
        for node_id in node_names:
            old_path = previous.node_paths.get(node_id)
            if old_path:
                stable_id = old_path[0]
                if stable_id not in stable_to_compact:
                    stable_to_compact[stable_id] = next_module
                    next_module += 1
                partition[numeric_id[node_id]] = stable_to_compact[stable_id]
            else:
                partition[numeric_id[node_id]] = next_module
                next_module += 1
        return partition

    @staticmethod
    def _reconcile(
        candidates: ModuleMembers,
        previous: InfomapClusterSnapshot | None,
    ) -> tuple[dict[int, int], int]:
        ordered_candidates = sorted(candidates, key=lambda raw: tuple(sorted(candidates[raw])))
        if previous is None:
            return ({raw: index for index, raw in enumerate(ordered_candidates, start=1)}, len(candidates) + 1)

        overlap_pairs: list[OverlapCandidate] = []
        for raw_id, members in candidates.items():
            signature: ModuleSignature = tuple(sorted(members))
            for old_id, old_members in previous.module_members.items():
                overlap = len(members & old_members)
                if overlap:
                    overlap_pairs.append((-overlap, signature, old_id, raw_id))
        overlap_pairs.sort()

        candidate_to_stable: dict[int, int] = {}
        used_old: set[int] = set()
        for _neg_overlap, _signature, old_id, raw_id in overlap_pairs:
            if raw_id in candidate_to_stable or old_id in used_old:
                continue
            candidate_to_stable[raw_id] = old_id
            used_old.add(old_id)

        next_cluster_id = max(previous.next_cluster_id, max(previous.module_members, default=0) + 1)
        for raw_id in ordered_candidates:
            if raw_id not in candidate_to_stable:
                candidate_to_stable[raw_id] = next_cluster_id
                next_cluster_id += 1
        return candidate_to_stable, next_cluster_id
