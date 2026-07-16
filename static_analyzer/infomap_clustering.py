"""Deterministic hierarchical Infomap clustering for ProgramGraph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from infomap import Infomap

from static_analyzer.graph import ClusterResult

if TYPE_CHECKING:
    from static_analyzer.program_graph import ProgramGraph


@dataclass
class InfomapClusterSnapshot:
    cluster_result: ClusterResult
    node_paths: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # Includes symbol and structural nodes so prior partitions retain file and
    # package context. ClusterResult below intentionally exposes symbols only.
    module_members: dict[int, set[str]] = field(default_factory=dict)
    next_cluster_id: int = 1
    codelength: float = 0.0


class HierarchicalInfomapClusterer:
    """Run Infomap with canonical input and reconcile top-module identities."""

    def __init__(self, *, seed: int = 42, num_trials: int = 3) -> None:
        self.seed = seed
        self.num_trials = num_trials

    def cluster(
        self,
        program_graph: "ProgramGraph",
        *,
        previous: InfomapClusterSnapshot | None = None,
    ) -> InfomapClusterSnapshot:
        from static_analyzer.program_graph import ProgramNodeKind

        graph = program_graph.clustering_graph()
        symbol_ids = {node.node_id for node in program_graph.symbol_nodes() if node.node_id in graph}
        if not graph:
            return InfomapClusterSnapshot(cluster_result=ClusterResult(strategy="infomap_empty"))

        node_names = sorted(graph.nodes)
        numeric_id = {node_id: index for index, node_id in enumerate(node_names, start=1)}
        infomap = Infomap(
            directed=True,
            silent=True,
            seed=self.seed,
            num_trials=self.num_trials,
        )
        for node_id in node_names:
            infomap.add_node(numeric_id[node_id], node_id)
        for source, target, attrs in sorted(graph.edges(data=True), key=lambda item: (item[0], item[1])):
            infomap.add_link(numeric_id[source], numeric_id[target], float(attrs.get("weight", 1.0)))

        initial_partition = self._initial_partition(node_names, numeric_id, previous)
        if initial_partition:
            result = infomap.run(initial_partition=initial_partition)
        else:
            result = infomap.run()

        raw_paths_by_numeric = result.multilevel_modules()
        raw_paths = {node_id: tuple(raw_paths_by_numeric[numeric_id[node_id]]) for node_id in node_names}
        candidates: dict[int, set[str]] = {}
        for node_id in node_names:
            path = raw_paths[node_id]
            raw_top = path[0] if path else numeric_id[node_id]
            candidates.setdefault(raw_top, set()).add(node_id)

        candidate_to_stable, next_cluster_id = self._reconcile(candidates, previous)
        module_members: dict[int, set[str]] = {}
        node_paths: dict[str, tuple[int, ...]] = {}
        for raw_top, members in sorted(candidates.items(), key=lambda item: self._signature(item[1])):
            stable_id = candidate_to_stable[raw_top]
            module_members.setdefault(stable_id, set()).update(members)
            for node_id in sorted(members):
                raw_path = raw_paths[node_id]
                node_paths[node_id] = (stable_id, *raw_path[1:]) if raw_path else (stable_id,)

        clusters = {
            cluster_id: set(members & symbol_ids)
            for cluster_id, members in sorted(module_members.items())
            if members & symbol_ids
        }
        cluster_to_files: dict[int, set[str]] = {}
        file_to_clusters: dict[str, set[int]] = {}
        for cluster_id, members in clusters.items():
            files = {
                program_graph.nodes[node_id].file_path
                for node_id in members
                if program_graph.nodes[node_id].kind == ProgramNodeKind.SYMBOL
                and program_graph.nodes[node_id].file_path
            }
            cluster_to_files[cluster_id] = files
            for file_path in files:
                file_to_clusters.setdefault(file_path, set()).add(cluster_id)

        return InfomapClusterSnapshot(
            cluster_result=ClusterResult(
                clusters=clusters,
                cluster_to_files=cluster_to_files,
                file_to_clusters=file_to_clusters,
                strategy="hierarchical_infomap",
            ),
            node_paths=node_paths,
            module_members=module_members,
            next_cluster_id=next_cluster_id,
            codelength=float(result.codelength),
        )

    def _initial_partition(
        self,
        node_names: list[str],
        numeric_id: dict[str, int],
        previous: InfomapClusterSnapshot | None,
    ) -> dict[int, int]:
        if previous is None:
            return {}
        stable_to_compact: dict[int, int] = {}
        result: dict[int, int] = {}
        next_module = 0
        for node_id in node_names:
            old_path = previous.node_paths.get(node_id)
            if old_path:
                stable_id = old_path[0]
                if stable_id not in stable_to_compact:
                    stable_to_compact[stable_id] = next_module
                    next_module += 1
                result[numeric_id[node_id]] = stable_to_compact[stable_id]
            else:
                result[numeric_id[node_id]] = next_module
                next_module += 1
        return result

    def _reconcile(
        self,
        candidates: dict[int, set[str]],
        previous: InfomapClusterSnapshot | None,
    ) -> tuple[dict[int, int], int]:
        ordered_candidates = sorted(candidates, key=lambda raw: self._signature(candidates[raw]))
        if previous is None:
            return ({raw: index for index, raw in enumerate(ordered_candidates, start=1)}, len(candidates) + 1)

        overlap_pairs: list[tuple[int, tuple[str, ...], int, int]] = []
        for raw_id, members in candidates.items():
            signature = self._signature(members)
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

    @staticmethod
    def _signature(members: set[str]) -> tuple[str, ...]:
        return tuple(sorted(members))
