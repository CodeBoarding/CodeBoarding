import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import networkx as nx

from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    Component,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.cluster_budget import ClusterPromptBudget
from agents.content_hash import (
    SourceCache,
    hash_method_body,
    read_source_lines,
)
from agents.cluster_ids import CodeBoardingClusterId, CodeBoardingClusterIds, GraphClusterId
from agents.llm_config import get_current_agent_context_window, get_current_agent_model_ref
from agents.model_capabilities import ContextWindow
from constants import MIN_CLUSTERS_THRESHOLD
from diagram_analysis.cluster_snapshot import ClusterSnapshotEntry
from diagram_analysis.file_index import build_files_index
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg_skip_planner import ContextBudgetExceededError, plan_skip_set
from static_analyzer.cluster_helpers import (
    reindex_cross_language_clusters,
)
from static_analyzer.cluster_relations import (
    build_component_relations,
    build_node_to_component_map,
    merge_relations,
)
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES, Language
from static_analyzer.clustering import ClusterResult
from static_analyzer.program_graph import ProgramGraph, ProgramNode
from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RenderedClusterString:
    text: str
    by_language: dict[str, str]
    cluster_ids: set[GraphClusterId]


def scoped_snapshot_from_lineage(program_graph: ProgramGraph, scope_id: str) -> dict[int, ClusterSnapshotEntry]:
    """Build a scoped snapshot from each method's recorded cluster ancestry/path."""
    if not scope_id:
        return {}
    prefix = f"{scope_id}."
    symbols = program_graph.symbols
    entries: dict[int, ClusterSnapshotEntry] = {}
    for qname, cluster_ids in program_graph.method_cluster_paths.snapshot():
        node = symbols.get(qname)
        if node is None:
            continue
        for cluster_id in cluster_ids:
            if not cluster_id.startswith(prefix):
                continue
            local_id = cluster_id.removeprefix(prefix)
            if not local_id.isdigit():
                continue
            entry = entries.setdefault(int(local_id), ClusterSnapshotEntry())
            entry.members.add(qname)
            if node.file_path:
                entry.files.add(node.file_path)
                entry.member_files[qname] = node.file_path
    return entries


def _describe_window(ctx: ContextWindow) -> str:
    suffix = "; fallback default, model window unresolved" if ctx.is_fallback else ""
    return f"{ctx.input_tokens} input tokens for {get_current_agent_model_ref()}{suffix}"


def _window_telemetry(ctx: ContextWindow, char_budget: int) -> dict:
    return {
        "char_budget": char_budget,
        "window_input_tokens": ctx.input_tokens,
        "window_is_fallback": ctx.is_fallback,
        "agent_model": get_current_agent_model_ref(),
    }


class ClusterMethodsMixin:
    """
    Mixin providing shared cluster-related functionality for agents.

    This mixin provides methods for:
    - Rendering Infomap cluster results with call evidence
    - Assigning files to components based on clusters and key_entities
    - Ensuring unique key entities across components

    Hierarchical Infomap is the sole source of cluster assignments.

    IMPORTANT: All methods are stateless with respect to ClusterResult.
    Cluster results must be passed explicitly as parameters.
    """

    # These attributes must be provided by the class using this mixin
    repo_dir: Path
    static_analysis: StaticAnalysisResults

    def _build_cluster_string(
        self,
        programming_langs: list[Language],
        cluster_results: dict[str, ClusterResult],
        cluster_ids: set[int] | None = None,
        prompt_overhead_chars: int = 0,
    ) -> str:
        """
        Build a cluster string for LLM consumption using pre-computed cluster results.

        Args:
            programming_langs: List of languages to include
            cluster_results: Pre-computed cluster results mapping language -> ClusterResult
            cluster_ids: Optional set of cluster IDs to filter by
            prompt_overhead_chars: Characters used by everything else in the
                prompt (system message + rendered template with an empty
                ``cfg_clusters`` slot). The skip planner subtracts this from
                the model's input window before computing the char budget for
                the cluster string.

        Returns:
            Formatted cluster string with headers per language
        """
        rendered = self._render_cluster_string(programming_langs, cluster_results, cluster_ids, {})
        if cluster_ids:
            return rendered.text

        char_budget = self._cluster_prompt_budget(prompt_overhead_chars)
        if len(rendered.text) <= char_budget:
            return rendered.text

        per_lang_skip = self._plan_skip_sets(programming_langs, cluster_results, prompt_overhead_chars)
        rendered_with_skips = self._render_cluster_string(
            programming_langs, cluster_results, cluster_ids, per_lang_skip
        )
        if len(rendered_with_skips.text) > char_budget:
            self._raise_cluster_budget_error(char_budget, rendered_with_skips, per_lang_skip)

        return rendered_with_skips.text

    def _render_cluster_string(
        self,
        programming_langs: list[Language],
        cluster_results: dict[str, ClusterResult],
        cluster_ids: set[int] | None,
        skip_sets: dict[str, set[str]],
    ) -> _RenderedClusterString:
        cluster_lines: list[str] = []
        by_language: dict[str, str] = {}
        all_cluster_ids: set[int] = set()

        for lang in programming_langs:
            cfg = self.static_analysis.get_program_graph(lang)
            cluster_result = cluster_results.get(lang)
            if cluster_result is None:
                continue
            cluster_str = cfg.to_cluster_string(
                cluster_result,
                cluster_ids or set(),
                skip_nodes=skip_sets.get(lang, set()),
            )

            if cluster_str.strip() and cluster_str not in ("empty", "none", "No clusters found."):
                header = "Component CFG" if cluster_ids else "Clusters"
                lang_text = f"\n## {lang.capitalize()} - {header}\n{cluster_str}\n"
                cluster_lines.append(lang_text)
                by_language[lang] = lang_text
                if cluster_result:
                    lang_ids = cluster_ids if cluster_ids else cluster_result.get_cluster_ids()
                    all_cluster_ids.update(lang_ids)

        if all_cluster_ids and not cluster_ids:
            sorted_cluster_ids = sorted(all_cluster_ids)
            cluster_lines.append(
                f"\n## All Cluster IDs ({len(sorted_cluster_ids)} total)\n"
                f"Every one of these IDs: {sorted_cluster_ids} must appear in exactly one group."
            )

        return _RenderedClusterString(text="".join(cluster_lines), by_language=by_language, cluster_ids=all_cluster_ids)

    def _plan_skip_sets(
        self,
        programming_langs: list[Language],
        cluster_results: dict[str, ClusterResult],
        prompt_overhead_chars: int,
    ) -> dict[str, set[str]]:
        """Compute per-language skip sets so the final combined cluster string fits."""
        char_budget = self._cluster_prompt_budget(prompt_overhead_chars)
        if char_budget <= 0:
            ctx = get_current_agent_context_window()
            msg = (
                f"Prompt overhead ({prompt_overhead_chars} chars) consumes the entire agent input "
                f"window ({_describe_window(ctx)}); no room for cluster renderings."
            )
            logger.error("[CFG skip planner] %s", msg)
            raise ContextBudgetExceededError(msg, telemetry_properties=_window_telemetry(ctx, char_budget))

        langs_with_clusters = [l for l in programming_langs if cluster_results.get(l)]
        if not langs_with_clusters:
            return {}

        skip_sets: dict[str, set[str]] = {}
        rendered = self._render_cluster_string(programming_langs, cluster_results, None, skip_sets)
        if len(rendered.text) <= char_budget:
            return skip_sets

        max_iterations = max(1, len(langs_with_clusters) * 5)
        for _ in range(max_iterations):
            deficit = len(rendered.text) - char_budget
            ordered_langs = sorted(
                langs_with_clusters,
                key=lambda lang: len(rendered.by_language.get(lang, "")),
                reverse=True,
            )
            progressed = False

            for lang in ordered_langs:
                lang_text = rendered.by_language.get(lang, "")
                current_len = len(lang_text)
                if current_len == 0:
                    continue

                for target in self._language_budget_targets(current_len, deficit):
                    try:
                        skip = plan_skip_set(
                            self.static_analysis.get_program_graph(lang), cluster_results[lang], target
                        )
                    except ContextBudgetExceededError:
                        continue

                    if skip == skip_sets.get(lang, set()):
                        continue

                    trial_skip_sets = dict(skip_sets)
                    if skip:
                        trial_skip_sets[lang] = skip
                    else:
                        trial_skip_sets.pop(lang, None)

                    trial_rendered = self._render_cluster_string(
                        programming_langs, cluster_results, None, trial_skip_sets
                    )
                    if len(trial_rendered.text) >= len(rendered.text):
                        continue

                    skip_sets = trial_skip_sets
                    rendered = trial_rendered
                    progressed = True
                    break

                if progressed:
                    break

            if len(rendered.text) <= char_budget:
                return skip_sets
            if not progressed:
                break

        self._raise_cluster_budget_error(char_budget, rendered, skip_sets)

    @staticmethod
    def _language_budget_targets(current_len: int, deficit: int) -> list[int]:
        exact_target = max(0, current_len - deficit)
        targets = {
            exact_target,
            int(current_len * 0.9),
            int(current_len * 0.75),
            int(current_len * 0.5),
            0,
        }
        return sorted((target for target in targets if target < current_len), reverse=True)

    @staticmethod
    def _raise_cluster_budget_error(
        char_budget: int,
        rendered: _RenderedClusterString,
        skip_sets: dict[str, set[str]],
    ) -> NoReturn:
        ctx = get_current_agent_context_window()
        per_lang_sizes = {lang: len(text) for lang, text in rendered.by_language.items()}
        skipped_counts = {lang: len(skip) for lang, skip in skip_sets.items() if skip}
        msg = (
            f"Cluster render {len(rendered.text)} chars exceeds budget {char_budget} "
            f"(agent window: {_describe_window(ctx)}). "
            f"Per-language sizes: {per_lang_sizes}; skipped nodes: {skipped_counts}."
        )
        logger.error("[CFG skip planner] %s", msg)
        telemetry = _window_telemetry(ctx, char_budget) | {
            "render_chars": len(rendered.text),
            "per_language_chars": per_lang_sizes,
            "skipped_node_counts": skipped_counts,
        }
        raise ContextBudgetExceededError(msg, telemetry_properties=telemetry)

    @staticmethod
    def _cluster_prompt_budget(prompt_overhead_chars: int) -> int:
        ctx = get_current_agent_context_window()
        return ClusterPromptBudget(input_tokens=ctx.input_tokens).available_chars(prompt_overhead_chars)

    def _ensure_unique_key_entities(self, analysis: AnalysisInsights):
        """
        Ensure that key_entities are unique across components.

        If a key_entity (identified by qualified_name) appears in multiple components,
        keep it only in the component where it's most relevant:
        1. If it's in the component's file_methods -> keep it there (highest priority)
        2. Otherwise, keep it in the first component that references it

        This prevents confusion in documentation where the same class/method
        is listed as a "key entity" for multiple components.
        """
        logger.info("Ensuring key_entities are unique across components")

        seen_entities: dict[str, Component] = {}

        for component in analysis.components:
            entities_to_remove = []

            for key_entity in component.key_entities:
                qname = key_entity.qualified_name

                if qname in seen_entities:
                    original_component = seen_entities[qname]
                    ref_file = key_entity.reference_file

                    component_files = component.file_paths()
                    original_files = original_component.file_paths()
                    current_has_file = ref_file and any(ref_file in f for f in component_files)
                    original_has_file = ref_file and any(ref_file in f for f in original_files)

                    if current_has_file and not original_has_file:
                        # Move to current component
                        original_component.key_entities = [
                            e for e in original_component.key_entities if e.qualified_name != qname
                        ]
                        seen_entities[qname] = component
                        logger.debug(f"Moved key_entity '{qname}' from {original_component.name} to {component.name}")
                    else:
                        # Keep in original component
                        entities_to_remove.append(key_entity)
                        logger.debug(
                            f"Removed duplicate key_entity '{qname}' from {component.name} (kept in {original_component.name})"
                        )
                else:
                    seen_entities[qname] = component

            component.key_entities = [e for e in component.key_entities if e not in entities_to_remove]

    def _resolve_cluster_ids_from_groups(self, analysis: AnalysisInsights, cluster_analysis: ClusterAnalysis) -> None:
        """Resolve source_cluster_ids deterministically from source_group_names via case-insensitive lookup."""
        group_name_to_ids: dict[str, list[GraphClusterId]] = {
            cc.name.lower(): cc.cluster_ids for cc in cluster_analysis.cluster_components
        }

        for component in analysis.components:
            resolved_ids = [
                cid for gname in component.source_group_names for cid in group_name_to_ids.get(gname.lower(), [])
            ]
            unresolved = [g for g in component.source_group_names if g.lower() not in group_name_to_ids]
            for gname in unresolved:
                logger.warning(
                    f"[{self.__class__.__name__}] Unresolved group name '{gname}' for component '{component.name}'"
                )
            component.source_cluster_ids = CodeBoardingClusterIds.from_graph_ids(set(resolved_ids))

    def _expand_to_method_level_clusters(self, cfg: ProgramGraph, cluster_result: ClusterResult) -> ClusterResult:
        """
        Expand cluster results to method-level granularity when there are too few clusters.

        When a subgraph has fewer than MIN_CLUSTERS_THRESHOLD clusters, this creates
        synthetic clusters where each method/function becomes its own cluster. This
        ensures fine-grained method assignment even for small components.

        Args:
            cfg: The ProgramGraph containing symbols to cluster
            cluster_result: Original cluster result (may have insufficient clusters)

        Returns:
            New ClusterResult with method-level clusters (each method = 1 cluster)
        """
        num_clusters = len(cluster_result.clusters)

        if num_clusters >= MIN_CLUSTERS_THRESHOLD:
            return cluster_result

        logger.info(f"Expanding to method-level clusters: {num_clusters} clusters < {MIN_CLUSTERS_THRESHOLD} threshold")

        # Create synthetic clusters: each callable node becomes its own cluster
        new_clusters: dict[int, set[str]] = {}
        new_cluster_to_files: dict[int, set[str]] = {}
        new_file_to_clusters: dict[str, set[int]] = defaultdict(set)

        cluster_id = 0
        for qname, node in sorted(cfg.symbols.items()):
            # Only create clusters for callable types (functions, methods)
            if node.symbol_type not in CALLABLE_TYPES:
                continue

            new_clusters[cluster_id] = {qname}
            new_cluster_to_files[cluster_id] = {node.file_path}
            new_file_to_clusters[node.file_path].add(cluster_id)
            cluster_id += 1

        # If we still have few clusters (e.g., only classes, no methods), include classes too
        if len(new_clusters) < MIN_CLUSTERS_THRESHOLD:
            for qname, node in sorted(cfg.symbols.items()):
                if node.symbol_type in CLASS_TYPES and qname not in {
                    name for members in new_clusters.values() for name in members
                }:
                    new_clusters[cluster_id] = {qname}
                    new_cluster_to_files[cluster_id] = {node.file_path}
                    new_file_to_clusters[node.file_path].add(cluster_id)
                    cluster_id += 1

        logger.info(f"Created {len(new_clusters)} method-level clusters from {len(cfg.symbols)} nodes")

        return ClusterResult(
            clusters=new_clusters,
            cluster_to_files=new_cluster_to_files,
            file_to_clusters=dict(new_file_to_clusters),
            strategy="method_level_expansion",
        )

    def _create_strict_component_subgraph(
        self,
        component: Component,
        source_cluster_id_prefix: str = "",
    ) -> tuple[str, dict[str, ClusterResult], dict[str, ProgramGraph]]:
        """
        Create a strict subgraph containing ONLY nodes from the component's file_methods.
        This ensures the analysis is strictly scoped to the component's boundaries.

        If the resulting subgraph has fewer than MIN_CLUSTERS_THRESHOLD clusters,
        automatically expands to method-level clustering (each method = 1 cluster)
        to ensure fine-grained component assignment.

        Args:
            component: Component with file_methods to filter by

        Returns:
            Tuple of (formatted cluster string, cluster_results dict, subgraph_cfgs dict)
            where cluster_results maps language -> ClusterResult for the subgraph
            and subgraph_cfgs maps language -> filtered ProgramGraph for the subgraph
        """
        component_files = component.file_paths()
        if not component_files:
            logger.warning(f"Component {component.name} has no assigned files")
            return "No assigned files found for this component.", {}, {}

        # Collect qualified names for method-level filtering
        assigned_qnames: set[str] = set()
        for group in component.file_methods:
            for method in group.methods:
                assigned_qnames.add(method.qualified_name)

        cluster_results: dict[str, ClusterResult] = {}
        subgraph_cfgs: dict[str, ProgramGraph] = {}

        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_program_graph(lang)

            # Filter by exact method set to prevent scope leakage
            sub_cfg = cfg.filter_by_nodes(assigned_qnames)

            if sub_cfg.nodes:
                subgraph_cfgs[lang] = sub_cfg
                program_graph = self.static_analysis.get_program_graph(lang)
                scoped_program_graph = program_graph.induced_by_symbols(assigned_qnames)
                sub_cluster_result = HierarchicalInfomapClusterer().cluster(scoped_program_graph)

                # Expand to method-level if insufficient clusters
                sub_cluster_result = self._expand_to_method_level_clusters(sub_cfg, sub_cluster_result)
                cluster_results[lang] = sub_cluster_result

        # Hierarchical Infomap chooses granularity; only ID namespaces need
        # reconciliation across languages.
        if len(cluster_results) > 1:
            reindex_cross_language_clusters(cluster_results)

        if source_cluster_id_prefix:
            for lang, cluster_result in cluster_results.items():
                self.static_analysis.get_program_graph(Language(lang)).record_cluster_paths(
                    cluster_result, source_cluster_id_prefix
                )

        result_parts = []
        for lang in self.static_analysis.get_languages():
            if lang not in cluster_results:
                continue
            cluster_str = subgraph_cfgs[lang].to_cluster_string(cluster_result=cluster_results[lang])
            if cluster_str.strip() and cluster_str not in ("empty", "none", "No clusters found."):
                result_parts.append(f"\n## {lang.capitalize()} - Component CFG\n")
                result_parts.append(cluster_str)
                result_parts.append("\n")

        result = "".join(result_parts)

        if not result.strip():
            logger.warning(f"No CFG found for component {component.name} with {len(assigned_qnames)} methods")
            return "No relevant CFG clusters found for this component.", cluster_results, subgraph_cfgs

        return result, cluster_results, subgraph_cfgs

    def _collect_all_cfg_nodes(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, ProgramGraph] | None = None,
    ) -> dict[str, ProgramNode]:
        """Build a lookup of qualified_name -> Node for all languages present in cluster_results.

        Args:
            cluster_results: Language -> ClusterResult mapping (used to determine languages).
            cfg_graphs: Optional scoped CallGraphs to use instead of the global CFG.
                        When provided (e.g. subgraph from DetailsAgent), only nodes
                        from these graphs are included, preventing scope leakage.
        """
        all_nodes: dict[str, ProgramNode] = {}
        for lang in cluster_results:
            cfg = (
                cfg_graphs[lang]
                if cfg_graphs and lang in cfg_graphs
                else self.static_analysis.get_program_graph(Language(lang))
            )
            all_nodes.update(cfg.symbols)
        return all_nodes

    def _build_undirected_graphs(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, ProgramGraph] | None = None,
    ) -> dict[str, nx.Graph]:
        """Pre-build undirected networkx graphs for each language in cluster_results.

        Meant to be called once before iterating over orphan nodes, so that
        ``_find_nearest_cluster`` doesn't rebuild the graph on every call.

        Args:
            cluster_results: Language -> ClusterResult mapping (used to determine languages).
            cfg_graphs: Optional scoped CallGraphs to use instead of the global CFG.
        """
        graphs: dict[str, nx.Graph] = {}
        for lang in cluster_results:
            cfg = (
                cfg_graphs[lang]
                if cfg_graphs and lang in cfg_graphs
                else self.static_analysis.get_program_graph(Language(lang))
            )
            graphs[lang] = cfg.to_networkx().to_undirected()
        return graphs

    def _find_nearest_cluster(
        self,
        node_name: str,
        cluster_results: dict[str, ClusterResult],
        undirected_graphs: dict[str, nx.Graph],
    ) -> int | None:
        """Find the cluster whose members are closest to *node_name* in the call graph.

        Uses undirected shortest-path distance so that both callers and callees
        are considered.  Returns the cluster_id of the nearest cluster, or None
        if the node is completely disconnected.

        Args:
            node_name: Fully qualified name of the node to find the nearest cluster for.
            cluster_results: Language -> ClusterResult mapping.
            undirected_graphs: Pre-built undirected graphs (from ``_build_undirected_graphs``).
        """
        best_cluster: int | None = None
        best_dist = float("inf")

        for lang, cr in cluster_results.items():
            nx_graph = undirected_graphs.get(lang)
            if nx_graph is None or node_name not in nx_graph:
                continue

            try:
                distances = nx.single_source_shortest_path_length(nx_graph, node_name)
            except nx.NetworkXError:
                continue

            for cluster_id, members in cr.clusters.items():
                for member in members:
                    d = distances.get(member)
                    if d is not None and d < best_dist:
                        best_dist = d
                        best_cluster = cluster_id

        return best_cluster

    def _build_file_methods_from_nodes(
        self, nodes: list[ProgramNode], source_cache: SourceCache | None = None
    ) -> list[FileMethodGroup]:
        """Group a flat list of Nodes into FileMethodGroups sorted by file then line.

        Only includes methods, functions, and classes/interfaces — variables,
        constants, properties, and fields are excluded. Pass ``source_cache`` to
        reuse file reads across a whole ``populate_file_methods`` pass.
        """
        allowed_types = CALLABLE_TYPES | CLASS_TYPES
        by_file: dict[str, dict[tuple[int, int, str, str], MethodEntry]] = defaultdict(dict)
        if source_cache is None:
            source_cache = {}

        def _is_more_specific(candidate: str, current: str) -> bool:
            """Prefer the most specific qualified name for the same symbol span.

            Example: keep ``module.Class.method`` over ``module.method`` when both
            point to the same file range and symbol kind.
            """
            candidate_parts = candidate.split(".")
            current_parts = current.split(".")
            if candidate_parts[-1] == current_parts[-1]:
                return len(candidate_parts) > len(current_parts)
            return len(candidate) > len(current)

        for node in nodes:
            if node.symbol_type not in allowed_types or node.symbol_type is None:
                continue

            rel_path = normalize_repo_path(node.file_path, self.repo_dir)

            method_name = node.id.split(".")[-1]
            dedupe_key = (node.line_start, node.line_end, node.symbol_type.name, method_name)
            candidate = MethodEntry(
                qualified_name=node.id,
                start_line=node.line_start,
                end_line=node.line_end,
                node_type=node.symbol_type.name,
                content_hash=hash_method_body(
                    read_source_lines(self.repo_dir, rel_path, source_cache),
                    node.line_start,
                    node.line_end,
                ),
            )

            existing = by_file[rel_path].get(dedupe_key)
            if existing is None or _is_more_specific(candidate.qualified_name, existing.qualified_name):
                by_file[rel_path][dedupe_key] = candidate

        groups: list[FileMethodGroup] = []
        for file_path in sorted(by_file):
            methods = sorted(by_file[file_path].values(), key=lambda m: (m.start_line, m.end_line, m.qualified_name))
            groups.append(FileMethodGroup(file_path=file_path, methods=methods))
        return groups

    def _build_cluster_to_component_map(self, analysis: AnalysisInsights) -> dict[CodeBoardingClusterId, Component]:
        """Build cluster_id -> Component mapping from source_cluster_ids."""
        cluster_to_component: dict[CodeBoardingClusterId, Component] = {}
        for comp in analysis.components:
            for cid in comp.source_cluster_ids:
                cluster_to_component[cid] = comp
        return cluster_to_component

    def _build_node_to_cluster_map(
        self, cluster_results: dict[str, ClusterResult], source_cluster_id_prefix: str = ""
    ) -> tuple[dict[str, CodeBoardingClusterId], set[CodeBoardingClusterId]]:
        """Build node_name (qualified name) -> cluster_id mapping and collect all cluster IDs."""
        all_cluster_ids: set[CodeBoardingClusterId] = set()
        node_to_cluster: dict[str, CodeBoardingClusterId] = {}
        for cr in cluster_results.values():
            for cid, members in cr.clusters.items():
                cluster_id = CodeBoardingClusterIds.qualify_local_id(
                    CodeBoardingClusterIds.from_graph_id(cid), source_cluster_id_prefix
                )
                all_cluster_ids.add(cluster_id)
                for name in members:
                    node_to_cluster[name] = cluster_id
        return node_to_cluster, all_cluster_ids

    def _validate_cluster_coverage(
        self, cluster_to_component: dict[CodeBoardingClusterId, Component], all_cluster_ids: set[CodeBoardingClusterId]
    ) -> None:
        """Log an error if any cluster IDs are not mapped to a component."""
        unmapped_cluster_ids = sorted(all_cluster_ids - set(cluster_to_component.keys()))
        if unmapped_cluster_ids:
            logger.error(
                f"{len(unmapped_cluster_ids)}/{len(all_cluster_ids)} clusters not mapped "
                f"via source_cluster_ids: {unmapped_cluster_ids}. This should never happen — all clusters must be "
                f"assigned to components by the LLM."
            )

    def _find_component_by_file(
        self,
        node: ProgramNode,
        cluster_results: dict[str, ClusterResult],
        cluster_to_component: dict[str, Component],
        source_cluster_id_prefix: str = "",
    ) -> Component | None:
        """Try to assign a node to a component based on its file already belonging to a cluster."""
        file_path = node.file_path
        if not file_path:
            return None
        for cr in cluster_results.values():
            cluster_ids = cr.get_clusters_for_file(file_path)
            for cid in cluster_ids:
                cluster_id = CodeBoardingClusterIds.qualify_local_id(
                    CodeBoardingClusterIds.from_graph_id(cid), source_cluster_id_prefix
                )
                comp = cluster_to_component.get(cluster_id)
                if comp is not None:
                    return comp
        return None

    def _assign_nodes_to_components(
        self,
        all_nodes: dict[str, ProgramNode],
        node_to_cluster: dict[str, str],
        cluster_to_component: dict[str, Component],
        cluster_results: dict[str, ClusterResult],
        fallback_component: Component,
        cfg_graphs: dict[str, ProgramGraph] | None = None,
        source_cluster_id_prefix: str = "",
    ) -> dict[str, list[ProgramNode]]:
        """Assign every node to a component via its cluster, file co-location, graph distance, or fallback."""
        component_nodes: dict[str, list[ProgramNode]] = defaultdict(list)
        unassigned: list[str] = []

        for qname, node in all_nodes.items():
            cid = node_to_cluster.get(qname)
            if cid is not None and cid in cluster_to_component:
                component_nodes[cluster_to_component[cid].component_id].append(node)
            else:
                unassigned.append(qname)

        if unassigned:
            logger.info(f"Assigning {len(unassigned)} orphan node(s)")

        assigned_by_file = 0
        assigned_by_graph = 0
        assigned_by_fallback = 0
        fallback_files: set[str] = set()

        # Pre-build undirected graphs once for all orphan lookups
        undirected_graphs = self._build_undirected_graphs(cluster_results, cfg_graphs) if unassigned else {}

        for qname in unassigned:
            node = all_nodes[qname]

            # 1. Try file co-location: if the node's file already belongs to a cluster/component
            comp = self._find_component_by_file(node, cluster_results, cluster_to_component, source_cluster_id_prefix)
            if comp is not None:
                assigned_by_file += 1
                component_nodes[comp.component_id].append(node)
                continue

            # 2. Try graph distance: find the nearest cluster in the call graph
            nearest_cid = self._find_nearest_cluster(qname, cluster_results, undirected_graphs)
            nearest_cluster_id = (
                CodeBoardingClusterIds.qualify_local_id(
                    CodeBoardingClusterIds.from_graph_id(nearest_cid), source_cluster_id_prefix
                )
                if nearest_cid is not None
                else ""
            )
            if nearest_cluster_id in cluster_to_component:
                comp = cluster_to_component[nearest_cluster_id]
                assigned_by_graph += 1
                component_nodes[comp.component_id].append(node)
                continue

            # 3. Last resort: fallback component
            assigned_by_fallback += 1
            fallback_files.add(node.file_path)
            component_nodes[fallback_component.component_id].append(node)

        if unassigned:
            logger.info(
                f"Orphan assignment: {assigned_by_file} by file, "
                f"{assigned_by_graph} by graph distance, {assigned_by_fallback} to fallback"
            )
        if assigned_by_fallback:
            logger.error(
                f"{assigned_by_fallback} node(s) fell back to '{fallback_component.name}' "
                f"— files: {sorted(fallback_files)}"
            )

        return component_nodes

    def _log_node_coverage(self, analysis: AnalysisInsights, total_nodes: int) -> None:
        """Log the percentage of nodes assigned to components."""
        assigned_nodes = sum(len(fg.methods) for comp in analysis.components for fg in comp.file_methods)
        pct = (assigned_nodes / total_nodes * 100) if total_nodes else 0
        logger.info(f"Node coverage: {assigned_nodes}/{total_nodes} ({pct:.1f}%) nodes assigned to components")

    def populate_file_methods(
        self,
        analysis: AnalysisInsights,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, ProgramGraph] | None = None,
        source_cluster_id_prefix: str = "",
    ) -> None:
        """Deterministically populate ``file_methods`` on every component.

        Node-centric approach guaranteeing 100% coverage:
        1. Build cluster_id -> component mapping from source_cluster_ids.
        2. Validate that all clusters are mapped (log error if not).
        3. For each node, assign via its cluster -> component mapping.
        4. Orphan nodes (not in any cluster) go to the nearest cluster's component
           or fall back to the first component.
        5. Build ``FileMethodGroup`` lists grouped by file path.

        Args:
            analysis: The analysis insights to populate.
            cluster_results: Language -> ClusterResult mapping.
            cfg_graphs: Optional scoped CallGraphs (e.g. subgraph from DetailsAgent).
                        When provided, only nodes from these graphs are considered,
                        preventing child components from exceeding parent scope.
        """
        # NOTE: These maps are intentionally rebuilt on each call — not cached — because
        # cluster_results differ per invocation (full graph in AbstractionAgent vs.
        # per-component subgraph in DetailsAgent, which runs in parallel).
        all_nodes = self._collect_all_cfg_nodes(cluster_results, cfg_graphs)
        cluster_to_component = self._build_cluster_to_component_map(analysis)
        node_to_cluster, all_cluster_ids = self._build_node_to_cluster_map(cluster_results, source_cluster_id_prefix)
        self._validate_cluster_coverage(cluster_to_component, all_cluster_ids)

        component_nodes = self._assign_nodes_to_components(
            all_nodes,
            node_to_cluster,
            cluster_to_component,
            cluster_results,
            analysis.components[0],
            cfg_graphs,
            source_cluster_id_prefix,
        )

        # One cache shared across the per-component method build and the files
        # index so each source file is read from disk once, not twice.
        source_cache: SourceCache = {}
        for comp in analysis.components:
            comp.file_methods = self._build_file_methods_from_nodes(
                component_nodes.get(comp.component_id, []), source_cache
            )

        analysis.files = build_files_index(analysis, self.repo_dir, source_cache)

        self._log_node_coverage(analysis, len(all_nodes))

    def build_static_relations(
        self,
        analysis: AnalysisInsights,
        cfg_graphs: dict[str, ProgramGraph] | None = None,
        source_cluster_id_prefix: str = "",
    ) -> None:
        """Build inter-component relations from CFG edges and merge with LLM relations.

        Static analysis supplies evidence for LLM-discovered architectural relations:
        - LLM + static match: keep LLM label and attach all matching edges.
        - LLM only with evidence/key_edges: keep as runtime or external communication.
        - Static only: keep out of user-facing relations unless the LLM selected the pair.

        If cfg_graphs is not provided, builds them from self.static_analysis.
        """
        if cfg_graphs is None:
            cfg_graphs = self.static_analysis.available_program_graphs()
        node_to_component = build_node_to_component_map(analysis)
        static_relations = build_component_relations(node_to_component, cfg_graphs)
        analysis.components_relations = merge_relations(analysis.components_relations, static_relations, analysis)
        self._prefix_local_cluster_ids(analysis, source_cluster_id_prefix)

    def _prefix_local_cluster_ids(self, analysis: AnalysisInsights, prefix: str) -> None:
        """Prefix detail-subgraph cluster ids with their owning component scope."""
        for component in analysis.components:
            component.source_cluster_ids = CodeBoardingClusterIds.qualify_local_ids(
                component.source_cluster_ids, prefix
            )

    def build_scope_cfg_string(self, analysis: AnalysisInsights) -> str:
        """Render cross-component communication edges as a human-readable string for the LLM.

        For every CFG edge where src belongs to component A and dst belongs to
        component B (A != B), this produces a grouped summary like:

            ComponentA -> ComponentB (3 edges):
              src_pkg.MethodX -> dst_pkg.MethodY
              src_pkg.MethodZ -> dst_pkg.MethodW
        """
        node_to_component = build_node_to_component_map(analysis)
        id_to_name = {c.component_id: c.name for c in analysis.components}
        cfg_graphs = self.static_analysis.available_program_graphs()
        static_relations = build_component_relations(node_to_component, cfg_graphs)

        if not static_relations:
            return "No cross-component communication edges found."

        lines: list[str] = []
        for relation in static_relations:
            src_id = relation.src_cluster_id
            dst_id = relation.dst_cluster_id
            src_label = id_to_name.get(src_id, src_id)
            dst_label = id_to_name.get(dst_id, dst_id)
            edge_count = len(relation.all_edges)
            lines.append(f"\n{src_label} -> {dst_label} ({edge_count} edge{'s' if edge_count != 1 else ''}):")
            for edge in relation.all_edges[:10]:
                short_s = edge.source.qualified_name.split(".")[-1]
                short_d = edge.target.qualified_name.split(".")[-1]
                lines.append(f"  {short_s} -> {short_d}")
            if edge_count > 10:
                lines.append(f"  ... and {edge_count - 10} more")

        return "\n".join(lines)
