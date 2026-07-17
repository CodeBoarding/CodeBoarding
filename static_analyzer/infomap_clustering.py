"""Deterministic hierarchical Infomap clustering over the ProgramGraph.

Modules come from the deepest hierarchy level that still yields components rather
than one hairball: ``path[0]`` is the top of a multilevel map and is meant to be
coarse (2 modules holding 75% of a 295-symbol repo), while one level down gives
~43 modules at ~8%.

Incremental runs warm-start from the previous partition and re-optimize the whole
graph, then map the result back onto the previous cluster ids by greatest overlap.
Identity comes from that reconciliation, not from pinning nodes in place — pinning
makes the partition blind to any change that does not add a symbol.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from math import log1p

from infomap import Infomap

from static_analyzer.clustering import (
    ClusterResult,
    InfomapClusterSnapshot,
    ModuleMembers,
    ModulePath,
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
        max_module_share: float = InfomapConfig.MAX_MODULE_SHARE,
    ) -> None:
        self.seed = seed
        self.num_trials = num_trials
        self.max_module_share = max_module_share

    def cluster(self, program_graph: ProgramGraph) -> ClusterResult:
        previous = program_graph.cluster_snapshot
        node_names = sorted(
            node.id for node in program_graph.nodes.values() if node.kind != ProgramNodeKind.EXTERNAL_PACKAGE
        )
        if not node_names:
            snapshot = InfomapClusterSnapshot(cluster_result=ClusterResult(strategy=InfomapConfig.EMPTY_STRATEGY))
            program_graph.cluster_snapshot = snapshot
            return snapshot.cluster_result

        weighted_edges = self._weighted_edges(program_graph, set(node_names))
        symbol_ids = {node.id for node in program_graph.symbol_nodes()}

        fingerprint = self._fingerprint(node_names, weighted_edges)
        if previous is not None and previous.graph_fingerprint == fingerprint:
            # The partition is a pure function of the weighted graph, so an
            # unchanged graph must not churn cluster ids — a change that only
            # moves line numbers would otherwise re-detail components for nothing.
            return previous.cluster_result

        prior_members = self._surviving_prior(previous, set(node_names)) if previous else {}
        partition = self._partition(node_names, weighted_edges, prior_members)
        level = self._choose_level(partition.node_paths, symbol_ids)
        candidates = self._group(partition.node_paths, level, symbol_ids)
        prior_symbols = {cid: members & symbol_ids for cid, members in prior_members.items() if members & symbol_ids}
        mapping = self._reconcile(candidates, prior_symbols, previous.next_cluster_id if previous else 1)

        module_members: ModuleMembers = {}
        node_paths: dict[str, ModulePath] = {}
        for key, members in self._group(partition.node_paths, level).items():
            stable_id = mapping.get(key)
            if stable_id is None:
                continue
            module_members.setdefault(stable_id, set()).update(members)
            for node_id in members:
                node_paths[node_id] = (stable_id, *partition.node_paths[node_id][level:])

        # Monotonic across runs: an id, once issued, is never handed to a different module.
        next_cluster_id = (
            max([*mapping.values(), *prior_members, previous.next_cluster_id - 1 if previous else 0, 0]) + 1
        )
        snapshot = self._materialize_snapshot(
            program_graph, module_members, node_paths, next_cluster_id, partition.codelength, level, fingerprint
        )
        program_graph.cluster_snapshot = snapshot
        return snapshot.cluster_result

    @staticmethod
    def _fingerprint(node_names: list[str], weighted_edges: list[tuple[str, str, float]]) -> str:
        digest = sha256()
        for node_id in node_names:
            digest.update(node_id.encode("utf-8", "surrogatepass"))
            digest.update(b"\x00")
        digest.update(b"\x01")
        for source, target, weight in weighted_edges:
            digest.update(f"{source}\x00{target}\x00{weight!r}\x00".encode("utf-8", "surrogatepass"))
        return digest.hexdigest()

    @staticmethod
    def _surviving_prior(previous: InfomapClusterSnapshot, surviving: set[str]) -> ModuleMembers:
        return {
            cluster_id: members & surviving
            for cluster_id, members in previous.module_members.items()
            if members & surviving
        }

    def _partition(
        self,
        node_names: list[str],
        weighted_edges: list[tuple[str, str, float]],
        prior_members: ModuleMembers,
    ) -> RawInfomapPartition:
        infomap, numeric_id = self._network(node_names, weighted_edges)
        initial = self._initial_partition(node_names, numeric_id, prior_members)
        result = infomap.run(initial_partition=initial) if initial else infomap.run()
        raw_paths = result.multilevel_modules()
        node_paths = {node_id: tuple(raw_paths[numeric_id[node_id]]) for node_id in node_names}
        module_members: ModuleMembers = {}
        for node_id, path in node_paths.items():
            module_members.setdefault(path[0] if path else numeric_id[node_id], set()).add(node_id)
        return RawInfomapPartition(node_paths, module_members, float(result.codelength))

    @staticmethod
    def _initial_partition(
        node_names: list[str],
        numeric_id: dict[str, int],
        prior_members: ModuleMembers,
    ) -> dict[int, int]:
        """Seed the optimizer with the previous partition; new symbols start as singletons.

        Why: Infomap has no fixed-membership option, so a warm start cannot pin the
        unchanged part of the graph. It does not need to — starting from a sound
        partition converges on the same map as a cold run while staying near it, and
        ``_reconcile`` is what carries the ids across.
        """
        if not prior_members:
            return {}
        owner = {node_id: cluster_id for cluster_id, members in prior_members.items() for node_id in members}
        compact = {cluster_id: index for index, cluster_id in enumerate(sorted(prior_members))}
        initial: dict[int, int] = {}
        singleton = len(compact)
        for node_id in node_names:
            if node_id in owner:
                initial[numeric_id[node_id]] = compact[owner[node_id]]
            else:
                initial[numeric_id[node_id]] = singleton
                singleton += 1
        return initial

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

    def _choose_level(self, node_paths: dict[str, ModulePath], symbol_ids: set[str]) -> int:
        """Deepest level whose largest module stays under the share cap.

        Why: a count cannot pick the level — a graph can have 4 top modules and 65
        leaf ones, and neither a floor nor a ceiling on k tells a real decomposition
        apart from one module holding two thirds of the code.
        """
        depth = max((len(path) for path in node_paths.values()), default=1)
        level = 1
        for candidate in range(1, depth + 1):
            sizes = [len(members) for members in self._group(node_paths, candidate, symbol_ids).values()]
            if sizes and max(sizes) / sum(sizes) <= self.max_module_share:
                level = candidate
        return level

    @staticmethod
    def _group(
        node_paths: dict[str, ModulePath],
        level: int,
        symbol_ids: set[str] | None = None,
    ) -> dict[ModulePath, set[str]]:
        """Group nodes by their module path prefix; ``symbol_ids`` narrows to symbols."""
        modules: defaultdict[ModulePath, set[str]] = defaultdict(set)
        for node_id, path in node_paths.items():
            if symbol_ids is None or node_id in symbol_ids:
                modules[tuple(path[:level])].add(node_id)
        return dict(modules)

    @staticmethod
    def _reconcile(
        candidates: dict[ModulePath, set[str]],
        prior: ModuleMembers,
        next_cluster_id: int = 1,
    ) -> dict[ModulePath, int]:
        """Map freshly discovered modules onto prior cluster ids by greatest overlap.

        Leftover candidates take fresh ids drawn above every id ever issued, not
        merely above the surviving ones — otherwise deleting a cluster frees its id
        for an unrelated module and the delta reads that module as *changed*,
        handing its symbols to the component that owned the id before.
        """
        ordered = sorted(candidates, key=lambda key: tuple(sorted(candidates[key])))
        if not prior:
            return {key: index for index, key in enumerate(ordered, start=1)}

        pairs = [
            (len(members & prior_members), cluster_id, key)
            for key, members in candidates.items()
            for cluster_id, prior_members in prior.items()
            if members & prior_members
        ]
        pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))

        mapping: dict[ModulePath, int] = {}
        used: set[int] = set()
        for _, cluster_id, key in pairs:
            if key in mapping or cluster_id in used:
                continue
            mapping[key] = cluster_id
            used.add(cluster_id)

        nxt = max(next_cluster_id, max(prior, default=0) + 1)
        for key in ordered:
            if key not in mapping:
                mapping[key] = nxt
                nxt += 1
        return mapping

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
    def _materialize_snapshot(
        program_graph: ProgramGraph,
        module_members: ModuleMembers,
        node_paths: dict[str, ModulePath],
        next_cluster_id: int,
        codelength: float,
        level: int,
        graph_fingerprint: str,
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
            level=level,
            graph_fingerprint=graph_fingerprint,
        )
