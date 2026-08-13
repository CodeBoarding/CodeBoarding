"""The clustering stage of the analysis pipeline.

Sits between static analysis and the LLM agents: ``ClusteringService`` consumes
``StaticAnalysisResults`` and produces ``ClusteringResults`` — leaf clusters, the
scoped call graphs they were derived from, and their deterministic grouping into
component-sized groups. Agents receive those results as plain inputs and never
cluster anything themselves.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from agents.agent_responses import ClusterAnalysis, ClustersComponent, Component
from static_analyzer.clustering import separability
from static_analyzer.clustering.cluster_helpers import (
    SUBCOMPONENTS_MAX,
    SUBCOMPONENTS_MIN,
    TOP_LEVEL_COMPONENTS_MAX,
    TOP_LEVEL_COMPONENTS_MIN,
    build_all_cluster_results,
    combine_cluster_results,
    group_symbols,
    reindex_across_languages,
    supercluster_leaf_ids,
)
from constants import MIN_CLUSTERS_THRESHOLD
from diagram_analysis.cluster_delta import _delta_for_language
from diagram_analysis.cluster_snapshot import ClusterSnapshotEntry
from static_analyzer import StaticAnalysisFatalError
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES, Language
from static_analyzer.clustering.models import ClusterResult, METHOD_LEVEL_STRATEGY
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


@dataclass
class ClusteringResults:
    """One scope's clustering output — the agents' single analysis input.

    Produced for the whole repository (``cluster_project``) or for one
    component's subgraph (``cluster_component``). Carries the
    ``StaticAnalysisResults`` the clustering was derived from, so consumers
    need no separate static-analysis handle.
    """

    #: language -> leaf clusters
    cluster_results: dict[str, ClusterResult]
    #: language -> the call graph the clusters were derived from
    cfg_graphs: dict[str, CallGraph]
    #: deterministic component groups ("Group i"); the LLM only names them
    cluster_analysis: ClusterAnalysis
    #: the static analysis this clustering was derived from
    static_analysis: StaticAnalysisResults
    #: component id whose subgraph this scope is; "" for the whole project
    scope_id: str = ""


def scoped_snapshot_from_lineage(cfg: CallGraph, scope_id: str) -> dict[int, ClusterSnapshotEntry]:
    """Build a scoped snapshot from each method's recorded cluster ancestry/path."""
    if not scope_id:
        return {}
    prefix = f"{scope_id}."
    entries: dict[int, ClusterSnapshotEntry] = {}
    for qname, cluster_ids in cfg.method_cluster_paths_snapshot():
        if qname not in cfg.nodes:
            continue
        for cluster_id in cluster_ids:
            if not cluster_id.startswith(prefix):
                continue
            local_id = cluster_id.removeprefix(prefix)
            if not local_id.isdigit():
                continue
            entry = entries.setdefault(int(local_id), ClusterSnapshotEntry())
            entry.members.add(qname)
            file_path = cfg.nodes[qname].file_path
            if file_path:
                entry.files.add(file_path)
                entry.member_files[qname] = file_path
    return entries


def _summarize_group(
    group: set[int],
    node_lookup: dict[int, set[str]],
    file_lookup: dict[int, set[str]],
    max_symbols: int = 12,
    max_files: int = 8,
) -> str:
    """A deterministic, name-rich blurb so the LLM can name a group without re-clustering."""
    symbols = group_symbols(sorted(group), node_lookup)
    files = sorted({path for cid in group for path in file_lookup.get(cid, set())})
    file_names = [Path(path).name for path in files]

    parts = [f"{len(group)} leaf clusters, {len(symbols)} symbols across {len(files)} files."]
    if file_names:
        shown = ", ".join(file_names[:max_files])
        parts.append(f"Files: {shown}{', ...' if len(file_names) > max_files else ''}")
    if symbols:
        shown = ", ".join(symbols[:max_symbols])
        parts.append(f"Key symbols: {shown}{', ...' if len(symbols) > max_symbols else ''}")
    return " ".join(parts)


def _expand_to_method_level_clusters(cfg: CallGraph, cluster_result: ClusterResult) -> ClusterResult:
    """Fan a too-coarse cluster result out to one synthetic cluster per method.

    When a subgraph has fewer than ``MIN_CLUSTERS_THRESHOLD`` clusters, each
    callable node becomes its own cluster so method assignment stays
    fine-grained even for small components.
    """
    num_clusters = len(cluster_result.clusters)

    if num_clusters >= MIN_CLUSTERS_THRESHOLD:
        return cluster_result

    logger.info(f"Expanding to method-level clusters: {num_clusters} clusters < {MIN_CLUSTERS_THRESHOLD} threshold")

    new_clusters: dict[int, set[str]] = {}
    new_cluster_to_files: dict[int, set[str]] = {}
    new_file_to_clusters: dict[str, set[int]] = defaultdict(set)

    cluster_id = 0
    for qname, node in sorted(cfg.nodes.items()):
        if node.type not in CALLABLE_TYPES:
            continue

        new_clusters[cluster_id] = {qname}
        new_cluster_to_files[cluster_id] = {node.file_path}
        new_file_to_clusters[node.file_path].add(cluster_id)
        cluster_id += 1

    # If we still have few clusters (e.g. only classes, no methods), include classes too
    if len(new_clusters) < MIN_CLUSTERS_THRESHOLD:
        for qname, node in sorted(cfg.nodes.items()):
            if node.type in CLASS_TYPES and qname not in {n for members in new_clusters.values() for n in members}:
                new_clusters[cluster_id] = {qname}
                new_cluster_to_files[cluster_id] = {node.file_path}
                new_file_to_clusters[node.file_path].add(cluster_id)
                cluster_id += 1

    logger.info(f"Created {len(new_clusters)} method-level clusters from {len(cfg.nodes)} nodes")

    return ClusterResult(
        clusters=new_clusters,
        cluster_to_files=new_cluster_to_files,
        file_to_clusters=dict(new_file_to_clusters),
        strategy=METHOD_LEVEL_STRATEGY,
    )


class ClusteringService:
    """Derives every scope's deterministic cluster structure from static analysis."""

    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis
        # Separability verdict per component member set: traversal asks once per
        # component and every save asks again for the whole tree, and the subgraph
        # build plus Leiden sweep behind each answer is the expensive part of the
        # deterministic pipeline. A changed component gets a different key.
        self._separable_cache: dict[frozenset[tuple[str, str]], bool] = {}

    def cluster_project(self) -> ClusteringResults:
        """Cluster the whole repository and group the leaf clusters into top-level components.

        Raises ``StaticAnalysisFatalError`` when the static analysis produced no
        callable structure at all (unsupported/empty/no-code repo) — failing loudly
        here beats handing the agents an empty architecture that crashes downstream.
        """
        cluster_results = build_all_cluster_results(self.static_analysis)
        cluster_analysis = self._group_clusters(
            cluster_results,
            {lang: self.static_analysis.get_cfg(Language(lang)).clustering_networkx() for lang in cluster_results},
        )
        if not cluster_analysis.cluster_components:
            raise StaticAnalysisFatalError(
                f"No component groups found for {self.repo_dir.name}: the static analysis produced "
                "no callable structure to build an architecture from."
            )
        return ClusteringResults(
            cluster_results=cluster_results,
            cfg_graphs=self.static_analysis.available_cfgs(),
            cluster_analysis=cluster_analysis,
            static_analysis=self.static_analysis,
        )

    def cluster_component(self, component: Component) -> ClusteringResults:
        """Cluster one component's subgraph and group it into sub-component groups.

        Records the resulting cluster lineage on the parent CFG under the
        component's id.
        """
        cluster_results, subgraph_cfgs = self._component_subgraph(component, component.component_id)
        cluster_analysis = self._group_clusters(
            cluster_results,
            {lang: cfg.clustering_networkx() for lang, cfg in subgraph_cfgs.items()},
            SUBCOMPONENTS_MIN,
            SUBCOMPONENTS_MAX,
        )
        return ClusteringResults(
            cluster_results=cluster_results,
            cfg_graphs=subgraph_cfgs,
            cluster_analysis=cluster_analysis,
            static_analysis=self.static_analysis,
            scope_id=component.component_id,
        )

    def subgraph_clusters(self, component: Component) -> tuple[dict[str, ClusterResult], dict[str, CallGraph]]:
        """The component's scoped leaf clusters and subgraph CFGs, without grouping.

        For consumers that need the raw scoped clustering (e.g. the incremental
        structural diff) rather than component groups. Records cluster lineage
        under the component's id, like ``cluster_component``.
        """
        return self._component_subgraph(component, component.component_id)

    def component_is_separable(self, component: Component) -> bool:
        """Deterministic gate: should this component be split into sub-components?

        A component past the leaf ceiling is split whatever its call structure
        says — it is too big to read as one box, and that verdict needs no
        subgraph. Otherwise the component's own subgraph decides, against a bar
        that eases as the component grows. If the subgraph can't be built (e.g. a
        legacy static-analysis baseline whose pickled edges predate the current
        schema), fall back to the structural default of expanding rather than
        aborting the run.

        Memoized on the component's member set; a component whose membership
        changed gets a different key and is re-evaluated. The probe never
        records cluster lineage.
        """
        load = separability.leaf_load(component)
        if load >= 1.0:
            logger.info(
                f"[Clustering] Component '{component.name}' is past the leaf ceiling (load {load:.2f}); expanding"
            )
            return True
        key = separability.member_keys(component)
        if key in self._separable_cache:
            return self._separable_cache[key]
        try:
            cluster_results, subgraph_cfgs = self._component_subgraph(component, source_cluster_id_prefix="")
        except Exception:
            logger.exception("Separability check failed for '%s'; defaulting to expandable", component.name)
            return True
        if not cluster_results:
            separable = False
        else:
            # Reference-augmented graph, matching the production split (deterministic_cluster_grouping ->
            # supercluster_by_modularity_peak): a component separable only via CONTAINS/INHERITS edges
            # must not be judged cohesive on a call-only graph.
            cfg_graphs = {lang: cfg.clustering_networkx() for lang, cfg in subgraph_cfgs.items()}
            separable = separability.component_is_separable(cluster_results, cfg_graphs, load)
        self._separable_cache[key] = separable
        return separable

    def _group_clusters(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, nx.DiGraph],
        low: int = TOP_LEVEL_COMPONENTS_MIN,
        high: int = TOP_LEVEL_COMPONENTS_MAX,
    ) -> ClusterAnalysis:
        """Partition leaf clusters into fixed component groups via resolution-tuned Leiden.

        The count (modularity peak over ``[low, high]``) and membership are chosen
        deterministically, so the structure is stable across re-runs — the LLM no
        longer decides it. Each group gets a stable ``Group i`` label and a summary
        of its members; the agents' analysis-shell step only names and describes them.

        ``cfg_graphs`` must span exactly the same scope as ``cluster_results`` — the
        component's own subgraph when splitting a component, the whole repo at the
        top level. Handing it the repo graph for a component scope makes the split
        disagree with the separability gate, which reads the subgraph.
        """
        groups, _modularity = supercluster_leaf_ids(cluster_results, cfg_graphs, low, high)
        combined = combine_cluster_results(cluster_results)
        cluster_components = [
            ClustersComponent(
                name=f"Group {i}",
                cluster_ids=sorted(group),
                description=_summarize_group(group, combined.clusters, combined.cluster_to_files),
            )
            for i, group in enumerate(groups, start=1)
        ]
        logger.info(
            f"[ClusteringService] Partitioned {sum(len(g) for g in groups)} leaf clusters "
            f"into {len(cluster_components)} deterministic groups"
        )
        return ClusterAnalysis(cluster_components=cluster_components)

    def _component_subgraph(
        self,
        component: Component,
        source_cluster_id_prefix: str = "",
    ) -> tuple[dict[str, ClusterResult], dict[str, CallGraph]]:
        """Cluster the subgraph spanned by exactly the component's own methods.

        Filtering by the component's qualified names (not its files) keeps a
        sibling component's methods out even when they share a file. A subgraph
        with fewer than ``MIN_CLUSTERS_THRESHOLD`` clusters is expanded to
        method-level granularity so assignment stays fine-grained.

        Returns ``(cluster_results, subgraph_cfgs)``, both keyed by language.
        Passing ``source_cluster_id_prefix`` also records the resulting cluster
        lineage on the parent CFG, so leave it empty for a read-only probe.
        """
        assigned_qnames = {method.qualified_name for group in component.file_methods for method in group.methods}
        if not assigned_qnames:
            logger.warning(f"Component {component.name} has no assigned methods")
            return {}, {}

        cluster_results: dict[str, ClusterResult] = {}
        subgraph_cfgs: dict[str, CallGraph] = {}

        for lang in self.static_analysis.get_languages():
            sub_cfg = self.static_analysis.get_cfg(lang).filter_by_nodes(assigned_qnames)
            if not sub_cfg.nodes:
                continue
            subgraph_cfgs[lang] = sub_cfg

            seeded_snapshot = scoped_snapshot_from_lineage(sub_cfg, source_cluster_id_prefix)
            if seeded_snapshot:
                sub_cluster_result = _delta_for_language(
                    str(lang), sub_cfg.clustering_networkx(), seeded_snapshot
                ).cluster_results
            else:
                sub_cluster_result = sub_cfg.cluster()

            cluster_results[lang] = _expand_to_method_level_clusters(sub_cfg, sub_cluster_result)

        reindex_across_languages(cluster_results)

        if source_cluster_id_prefix:
            for lang, cluster_result in cluster_results.items():
                self.static_analysis.get_cfg(Language(lang)).record_cluster_paths(
                    cluster_result, source_cluster_id_prefix
                )

        return cluster_results, subgraph_cfgs
