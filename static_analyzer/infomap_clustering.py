"""Deterministic hierarchical Infomap clustering."""

from __future__ import annotations

from collections import defaultdict
from math import log1p

from infomap import Infomap, Options

from static_analyzer.clustering import (
    ClusterResult,
    InfomapClusterSnapshot,
    ModuleMembers,
    RawInfomapPartition,
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
            self._inherit_global_namespace(snapshot, previous)
            program_graph.cluster_snapshot = snapshot
            return snapshot.cluster_result

        weighted_edges = self._weighted_edges(program_graph, set(node_names))
        if previous is None:
            partition = self._partition(node_names, weighted_edges)
            ordered_modules = sorted(
                partition.module_members,
                key=lambda raw: tuple(sorted(partition.module_members[raw])),
            )
            candidate_to_stable = {raw_id: stable_id for stable_id, raw_id in enumerate(ordered_modules, start=1)}
            module_members, node_paths = self._stable_partition(partition, candidate_to_stable)
            snapshot = self._materialize_snapshot(
                program_graph,
                module_members,
                node_paths,
                len(ordered_modules) + 1,
                partition.codelength,
            )
        else:
            snapshot = self._incremental_snapshot(program_graph, node_names, weighted_edges, previous)
            self._inherit_global_namespace(snapshot, previous)
        program_graph.cluster_snapshot = snapshot
        return snapshot.cluster_result

    @staticmethod
    def _inherit_global_namespace(
        snapshot: InfomapClusterSnapshot,
        previous: InfomapClusterSnapshot | None,
    ) -> None:
        """Retain root cluster IDs even when local modules disappear."""
        if previous is None:
            return
        snapshot.global_cluster_ids = dict(getattr(previous, "global_cluster_ids", {}))
        snapshot.next_global_cluster_id = int(
            getattr(
                previous,
                "next_global_cluster_id",
                max(snapshot.global_cluster_ids.values(), default=0) + 1,
            )
        )

    def _partition(
        self,
        node_names: list[str],
        weighted_edges: list[tuple[str, str, float]],
    ) -> RawInfomapPartition:
        infomap, numeric_id = self._network(node_names, weighted_edges)
        result = infomap.run()
        raw_paths_by_numeric = result.multilevel_modules()
        node_paths = {node_id: tuple(raw_paths_by_numeric[numeric_id[node_id]]) for node_id in node_names}
        module_members: ModuleMembers = {}
        for node_id, path in node_paths.items():
            raw_top = path[0] if path else numeric_id[node_id]
            module_members.setdefault(raw_top, set()).add(node_id)
        return RawInfomapPartition(node_paths, module_members, float(result.codelength))

    def _network(
        self,
        node_names: list[str],
        weighted_edges: list[tuple[str, str, float]],
    ) -> tuple[Infomap, dict[str, int]]:
        numeric_id = {node_id: index for index, node_id in enumerate(node_names, start=1)}
        infomap = Infomap(directed=True, silent=True, seed=self.seed, num_trials=self.num_trials)
        for node_id in node_names:
            infomap.add_node(numeric_id[node_id], node_id)
        for source, target, weight in weighted_edges:
            infomap.add_link(numeric_id[source], numeric_id[target], weight)
        return infomap, numeric_id

    def _evaluate_codelength(
        self,
        node_names: list[str],
        weighted_edges: list[tuple[str, str, float]],
        node_paths: dict[str, tuple[int, ...]],
    ) -> float:
        infomap, numeric_id = self._network(node_names, weighted_edges)
        stable_to_compact = {
            stable_id: compact_id
            for compact_id, stable_id in enumerate(sorted({path[0] for path in node_paths.values()}))
        }
        initial_partition = {numeric_id[node_id]: stable_to_compact[path[0]] for node_id, path in node_paths.items()}
        return float(
            infomap.run(
                initial_partition=initial_partition,
                options=Options(no_infomap=True),
            ).codelength
        )

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

    def _incremental_snapshot(
        self,
        program_graph: ProgramGraph,
        node_names: list[str],
        weighted_edges: list[tuple[str, str, float]],
        previous: InfomapClusterSnapshot,
    ) -> InfomapClusterSnapshot:
        surviving = set(node_names)
        module_members = {
            cluster_id: members & surviving
            for cluster_id, members in previous.module_members.items()
            if members & surviving
        }
        node_paths = {node_id: path for node_id, path in previous.node_paths.items() if node_id in surviving}
        new_node_names = sorted(surviving - set(previous.node_paths))
        if not new_node_names:
            return self._materialize_snapshot(
                program_graph,
                module_members,
                node_paths,
                previous.next_cluster_id,
                self._evaluate_codelength(node_names, weighted_edges, node_paths),
            )

        new_nodes = set(new_node_names)
        partition = self._partition(
            new_node_names,
            [edge for edge in weighted_edges if edge[0] in new_nodes and edge[1] in new_nodes],
        )
        prior_owner = {node_id: cluster_id for cluster_id, members in module_members.items() for node_id in members}
        affinity = self._prior_affinity(new_nodes, weighted_edges, prior_owner)
        next_cluster_id = max(
            previous.next_cluster_id,
            max(previous.module_members, default=0) + 1,
        )
        candidate_to_stable: dict[int, int] = {}
        for raw_id, members in sorted(
            partition.module_members.items(),
            key=lambda item: tuple(sorted(item[1])),
        ):
            target = self._affinity_target(program_graph, members, affinity)
            if target is None:
                target = next_cluster_id
                next_cluster_id += 1
            candidate_to_stable[raw_id] = target

        added_members, added_paths = self._stable_partition(partition, candidate_to_stable)
        for cluster_id, members in added_members.items():
            module_members.setdefault(cluster_id, set()).update(members)
        node_paths.update(added_paths)
        return self._materialize_snapshot(
            program_graph,
            module_members,
            node_paths,
            next_cluster_id,
            self._evaluate_codelength(node_names, weighted_edges, node_paths),
        )

    @staticmethod
    def _prior_affinity(
        new_nodes: set[str],
        weighted_edges: list[tuple[str, str, float]],
        prior_owner: dict[str, int],
    ) -> dict[str, dict[int, float]]:
        affinity: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        for source, target, weight in weighted_edges:
            if source in new_nodes and target in prior_owner:
                affinity[source][prior_owner[target]] += weight
            if target in new_nodes and source in prior_owner:
                affinity[target][prior_owner[source]] += weight
        return {node_id: dict(scores) for node_id, scores in affinity.items()}

    @staticmethod
    def _affinity_target(
        program_graph: ProgramGraph,
        members: set[str],
        affinity: dict[str, dict[int, float]],
    ) -> int | None:
        if any(program_graph.nodes[node_id].kind == ProgramNodeKind.PACKAGE for node_id in members):
            return None
        scores: defaultdict[int, float] = defaultdict(float)
        for node_id in members:
            for cluster_id, weight in affinity.get(node_id, {}).items():
                scores[cluster_id] += weight
        return max(scores, key=lambda cluster_id: (scores[cluster_id], -cluster_id)) if scores else None

    @staticmethod
    def _stable_partition(
        partition: RawInfomapPartition,
        candidate_to_stable: dict[int, int],
    ) -> tuple[ModuleMembers, dict[str, tuple[int, ...]]]:
        module_members: ModuleMembers = {}
        node_paths: dict[str, tuple[int, ...]] = {}
        for raw_top, members in sorted(
            partition.module_members.items(),
            key=lambda item: tuple(sorted(item[1])),
        ):
            stable_id = candidate_to_stable[raw_top]
            module_members.setdefault(stable_id, set()).update(members)
            for node_id in sorted(members):
                raw_path = partition.node_paths[node_id]
                node_paths[node_id] = (stable_id, *raw_path[1:]) if raw_path else (stable_id,)
        return module_members, node_paths

    @staticmethod
    def _materialize_snapshot(
        program_graph: ProgramGraph,
        module_members: ModuleMembers,
        node_paths: dict[str, tuple[int, ...]],
        next_cluster_id: int,
        codelength: float,
    ) -> InfomapClusterSnapshot:
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
