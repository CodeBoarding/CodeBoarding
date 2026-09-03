"""Entry point for clustering a call graph: draft the tree specification once, replay it everywhere."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from clustering_ids import ROOT_SCOPE_ID, ClusterId, ComponentId, ScopeId
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.cfg.edge import EdgeKind
from static_analyzer.clustering.exceptions import IncrementalCacheMissingError
from static_analyzer.clustering.models import (
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
    GroupConnection,
)
from static_analyzer.clustering.names import (
    AffinityGrouper,
    ComponentRule,
    Grouper,
    Partition,
    ScopeSpec,
    TreeSpec,
    Unit,
    draft_scope,
    draft_tree,
    replay,
    role_words_for,
    units_from_graphs,
)
from static_analyzer.clustering.names.draft import DETERMINISTIC_GROUPERS, UNPLACED_NAME, Links
from static_analyzer.clustering.names.replay import divergence
from static_analyzer.clustering.names.spec import Prefix
from static_analyzer.clustering.names.spec import UNPLACED
from static_analyzer.config import CALLABLE_TYPES, CLASS_TYPES

AFFINE_REFERENCE_KINDS = frozenset({EdgeKind.INHERITS, EdgeKind.TYPEREF})
"""Reference edges that count as links between files, with the call edges. CONTAINS never
crosses a file; IMPORT is not emitted yet and would be too dense to weigh."""

FILE_STRATEGY = "file_leaves"
"""Recorded on a ``ClusterResult`` whose leaves are files: the unit the names partition."""

NEW_SCOPE = "new_scope"
logger = logging.getLogger(__name__)


def file_leaf_clusters(graph: CallGraph) -> ClusterResult:
    """One leaf cluster per file.

    Why files: Leiden's communities cross the boundaries a name partition keeps, which capped
    any grouping over them at 0.34 on the Beacon ruler against 0.94 for the same names over
    files. A file pools its identifiers and crosses no boundary in any ruler.
    """
    clusters: dict[ClusterId, set[str]] = {}
    cluster_to_files: dict[ClusterId, set[str]] = {}
    file_to_clusters: dict[str, set[ClusterId]] = {}
    for cluster_id, file_path in enumerate(sorted({node.file_path for node in graph.nodes.values() if node.file_path})):
        clusters[cluster_id] = {name for name, node in graph.nodes.items() if node.file_path == file_path}
        cluster_to_files[cluster_id] = {file_path}
        file_to_clusters[file_path] = {cluster_id}
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=file_to_clusters,
        strategy=FILE_STRATEGY,
    )


def unit_links(graphs: Mapping[str, CallGraph]) -> Links:
    """Edges between files: call edges plus the reference kinds that cross a file."""
    links: dict[tuple[str, str], int] = {}
    for graph in graphs.values():
        pairs = [(edge.get_source(), edge.get_destination()) for edge in graph.edges]
        pairs.extend((ref.src, ref.dst) for ref in graph.reference_edges if ref.kind in AFFINE_REFERENCE_KINDS)
        for source, target in pairs:
            left, right = graph.nodes.get(source), graph.nodes.get(target)
            if left is None or right is None or not left.file_path or not right.file_path:
                continue
            if left.file_path != right.file_path:
                key = (min(left.file_path, right.file_path), max(left.file_path, right.file_path))
                links[key] = links.get(key, 0) + 1
    return links


class ClusteringService:
    """Build the component hierarchy from what the qualified names say.

    A full run drafts a ``TreeSpec`` one level deeper than it materializes, so that whether
    a component is expandable is a recorded decision. An incremental run replays the stored
    specification over the live names, appends a rule for every new scope, and never moves
    an unchanged unit. A partial run replays or drafts one scope. ``spec`` holds whatever the
    last build produced or extended; the caller persists it.
    """

    def __init__(self, grouper: Grouper | None = None) -> None:
        self.grouper: Grouper = grouper if grouper is not None else AffinityGrouper()
        self.spec = TreeSpec(grouper=self.grouper.name)
        self._links: Links = {}

    def build_full_hierarchy(self, static_analysis: StaticAnalysisResults, max_depth: int) -> ClusterScopeResult:
        """Draft the specification from every language's names and materialize the tree."""
        graphs = static_analysis.available_cfgs()
        units = units_from_graphs(graphs)
        self._links = unit_links(graphs)
        self.spec = draft_tree(units, self.grouper, max_depth + 1, links=self._links)
        hierarchy = self._materialize(graphs, units, ROOT_SCOPE_ID, 1, max_depth)
        hierarchy.index_hierarchy()
        return hierarchy

    def build_incremental_hierarchy(
        self,
        static_analysis: StaticAnalysisResults,
        max_depth: int,
        spec: TreeSpec,
        persisted_scopes: Mapping[ScopeId, Any],
        repo_dir: Path,
        artifact_dir: Path,
    ) -> ClusterScopeResult:
        """Replay ``spec`` over the live names; a new scope becomes a new rule, nothing else moves."""
        base = static_analysis.incremental_base_results
        graphs = static_analysis.available_cfgs()
        if base is None:
            raise IncrementalCacheMissingError(artifact_dir)
        if spec.scope(ROOT_SCOPE_ID) is None:
            raise IncrementalCacheMissingError(artifact_dir, "the baseline carries no tree specification")
        if graphs and not base.available_cfgs():
            raise IncrementalCacheMissingError(artifact_dir, "the baseline static analysis carries no call graph")
        self.spec = spec
        if spec.grouper in DETERMINISTIC_GROUPERS:
            self.grouper = DETERMINISTIC_GROUPERS[spec.grouper]()
        elif spec.grouper:
            logger.warning(
                "[Names] the baseline was drawn by the %s grouper, which needs a model; a scope it never reached "
                "is drafted by %s on this run",
                spec.grouper,
                self.grouper.name,
            )
        units = units_from_graphs(graphs)
        self._links = unit_links(graphs)
        baseline = _Baseline(persisted_scopes, repo_dir, units_from_graphs(base.available_cfgs()))
        hierarchy = self._materialize(graphs, units, ROOT_SCOPE_ID, 1, max_depth, baseline=baseline)
        hierarchy.index_hierarchy()
        return hierarchy

    def build_scope_hierarchy(
        self,
        graphs: Mapping[str, CallGraph],
        max_depth: int,
        root_scope_id: ScopeId,
        spec: TreeSpec,
    ) -> ClusterScopeResult:
        """Replay or draft one existing component's scope and the tree below it.

        A scope the specification already calls a leaf comes back without groups; the
        caller decides what to tell the user.
        """
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        self.spec = spec
        units = units_from_graphs(graphs)
        self._links = unit_links(graphs)
        hierarchy = self._materialize(graphs, units, root_scope_id, 1, max_depth)
        hierarchy.index_hierarchy()
        return hierarchy

    def _materialize(
        self,
        graphs: Mapping[str, CallGraph],
        units: list[Unit],
        scope_id: ScopeId,
        depth: int,
        max_depth: int,
        *,
        baseline: _Baseline | None = None,
    ) -> ClusterScopeResult:
        scope = self._scope_rules(scope_id, units)
        result = ClusterScopeResult(scope_id=scope_id, graphs_by_language=dict(graphs))
        if scope.is_leaf:
            return result
        role_words = role_words_for(self.spec.machinery)
        partition = replay(units, scope, role_words)
        if baseline is not None:
            partition = self._admit_new_scopes(scope, units, partition, role_words, baseline)
            self._retire_empty_rules(scope, partition)
        result.leaf_clusters_by_language = self._leaf_clusters(graphs)
        leaf_by_unit = {
            file_path: cluster_id
            for partition_result in result.leaf_clusters_by_language.values()
            for cluster_id, files in partition_result.cluster_to_files.items()
            for file_path in files
        }
        previous_ids = baseline.component_ids(scope_id) if baseline is not None else frozenset()
        for rule in scope.rules:
            members = partition.members.get(rule.component_id, [])
            if not members:
                continue
            group = ClusterGroup(
                group_id=rule.component_id,
                cluster_ids=sorted(leaf_by_unit[unit.unit_id] for unit in members),
                previous_component_id=rule.component_id if rule.component_id in previous_ids else "",
            )
            for unit in members:
                # Every name voted; the members the agents own are the callables and classes.
                nodes = graphs[unit.language].nodes
                owned = {name for name in unit.names if nodes[name].type in CALLABLE_TYPES | CLASS_TYPES}
                group.symbol_members_by_language.setdefault(unit.language, set()).update(owned)
            result.groups.append(group)
        result.connections = self._build_connections(graphs, result.groups)
        for group in result.groups:
            child_graphs = self._induced_graphs(partition.members.get(group.group_id, []), graphs)
            child_units = units_from_graphs(child_graphs)
            child = self._scope_rules(group.group_id, child_units)
            group.expandable = not child.is_leaf and bool(child_units)
            if not group.expandable or depth >= max_depth:
                continue
            group.children = self._materialize(
                child_graphs, child_units, group.group_id, depth + 1, max_depth, baseline=baseline
            )
        return result

    def _scope_rules(self, scope_id: ScopeId, units: list[Unit]) -> ScopeSpec:
        """The scope's rules from the specification, drafted now if it was never reached."""
        scope = self.spec.scope(scope_id)
        if scope is not None:
            return scope
        parts: tuple[ComponentRule, ...] = ()
        parent_id = scope_id.rpartition(".")[0] or ROOT_SCOPE_ID
        parent = self.spec.scope(parent_id) if scope_id != ROOT_SCOPE_ID else None
        if parent is not None:
            rule = parent.rule(scope_id)
            parts = rule.parts if rule is not None else ()
        scope, _ = draft_scope(
            scope_id, units, role_words_for(self.spec.machinery), self.grouper, parts=parts, links=self._links
        )
        self.spec.set_scope(scope)
        return scope

    def _admit_new_scopes(
        self,
        scope: ScopeSpec,
        units: list[Unit],
        partition: Partition,
        role_words: frozenset[str],
        baseline: _Baseline,
    ) -> Partition:
        """Append a rule for every group of new units that leaves the tree of the baseline's units.

        Why the baseline's tree and not the owned prefixes or the live units: a scope drawn from
        words owns no prefix at all, and a directory whose every file was replaced in one update
        is not a new directory. Why new units only: a unit the baseline already placed is a rule's
        residue. Why prefix only: a new rule with words could re-vote an existing unit.
        """
        taken = baseline.component_ids(scope.scope_id)
        owned = {prefix for rule in scope.rules for prefix in rule.prefixes + rule.fallback_prefixes}
        fresh: dict[Prefix, list[Unit]] = {}
        for unit in (unit for members in partition.new_scopes.values() for unit in members if baseline.is_new(unit)):
            fresh.setdefault(divergence(unit.position, baseline.positions), []).append(unit)
        added = False
        for key, members in sorted(fresh.items()):
            if len(members) < 2 or key in owned:
                continue
            scope.rules.append(
                ComponentRule(scope.next_id(taken), key[-1] if key else "New scope", prefixes=(key,), origin=NEW_SCOPE)
            )
            added = True
            logger.info("[Names] %s: new scope %s (%d units)", scope.scope_id, ".".join(key), len(members))
        if added:
            partition = replay(units, scope, role_words)
        if partition.unplaced and scope.unplaced_rule is None:
            scope.rules.append(ComponentRule(scope.next_id(taken), UNPLACED_NAME, origin=UNPLACED, kind=UNPLACED))
            partition = replay(units, scope, role_words)
        return partition

    def _retire_empty_rules(self, scope: ScopeSpec, partition: Partition) -> None:
        """Drop a rule that claims nothing, and the scopes below it, so the spec matches the tree.

        A fallback-only rule stays: it is the scope's last resort and legitimately empty.
        """
        for rule in list(scope.rules):
            if rule.is_fallback_only or rule.kind == UNPLACED or partition.size(rule.component_id):
                continue
            scope.rules.remove(rule)
            for scope_id in [
                s for s in self.spec.scopes if s == rule.component_id or s.startswith(f"{rule.component_id}.")
            ]:
                del self.spec.scopes[scope_id]
            logger.info("[Names] %s: retired %s (%s), it claims nothing", scope.scope_id, rule.component_id, rule.name)

    @staticmethod
    def _leaf_clusters(graphs: Mapping[str, CallGraph]) -> dict[str, ClusterResult]:
        """File leaves per language, ids disjoint across languages."""
        leaves: dict[str, ClusterResult] = {}
        offset = 0
        for language in sorted(graphs):
            partition = file_leaf_clusters(graphs[language])
            leaves[language] = ClusterResult(
                clusters={cluster_id + offset: members for cluster_id, members in partition.clusters.items()},
                cluster_to_files={
                    cluster_id + offset: files for cluster_id, files in partition.cluster_to_files.items()
                },
                file_to_clusters={
                    path: {cluster_id + offset for cluster_id in ids}
                    for path, ids in partition.file_to_clusters.items()
                },
                strategy=FILE_STRATEGY,
            )
            offset += len(partition.clusters)
        return leaves

    @staticmethod
    def _induced_graphs(members: Iterable[Unit], graphs: Mapping[str, CallGraph]) -> dict[str, CallGraph]:
        """The per-language subgraphs of the members' files, every declaration included.

        Why files and not the agents' symbol members: the child scope was drafted over the units
        its parent placed, data-only files among them, and must replay over the same.
        """
        files = {(unit.language, unit.unit_id) for unit in members}
        child_graphs: dict[str, CallGraph] = {}
        for language, graph in graphs.items():
            names = {name for name, node in graph.nodes.items() if (language, node.file_path) in files}
            if names:
                child_graphs[language] = graph.filter_by_nodes(names)
        return child_graphs

    @staticmethod
    def _build_connections(graphs: Mapping[str, CallGraph], groups: list[ClusterGroup]) -> list[GroupConnection]:
        group_id_by_qualified_name = {
            (language, qualified_name): group.group_id
            for group in groups
            for language, qualified_names in group.symbol_members_by_language.items()
            for qualified_name in qualified_names
        }
        by_pair: dict[tuple[ComponentId, ComponentId], GroupConnection] = {}
        for language, graph in graphs.items():
            for edge in graph.edges:
                source = edge.get_source()
                target = edge.get_destination()
                source_group = group_id_by_qualified_name.get((language, source), "")
                target_group = group_id_by_qualified_name.get((language, target), "")
                if not source_group or not target_group or source_group == target_group:
                    continue
                connection = by_pair.setdefault(
                    (source_group, target_group),
                    GroupConnection(source_group_id=source_group, target_group_id=target_group),
                )
                connection.edges.append(
                    ClusterConnectionEdge(
                        language=language,
                        source_qualified_name=source,
                        target_qualified_name=target,
                        call_sites=edge.call_sites,
                    )
                )
        return [by_pair[pair] for pair in sorted(by_pair)]


class _Baseline:
    """What the previous run knew: component ids per scope, and every file its graph declared.

    Why the graph and not the persisted members: a file declaring only data is a unit the
    partition places but never a member the agents describe, so it is absent from
    ``file_methods`` and would look new on every run.
    """

    def __init__(self, persisted_scopes: Mapping[ScopeId, Any], repo_dir: Path, units: Iterable[Unit]) -> None:
        self._repo_dir = repo_dir
        self._ids_by_scope = {
            scope_id: frozenset(component.component_id for component in scope.components if component.component_id)
            for scope_id, scope in persisted_scopes.items()
        }
        units = list(units)
        self._files = {normalize_repo_path(unit.unit_id, repo_dir) for unit in units}
        self.positions = {unit.position for unit in units}
        """Where the baseline's units sat: the tree a new directory must leave to be one."""

    def component_ids(self, scope_id: ScopeId) -> frozenset[ComponentId]:
        return self._ids_by_scope.get(scope_id, frozenset())

    def is_new(self, unit: Unit) -> bool:
        return normalize_repo_path(unit.unit_id, self._repo_dir) not in self._files


def hierarchy_differs(hierarchy: ClusterScopeResult, persisted_scopes: Mapping[ScopeId, Any]) -> bool:
    """Whether any scope gained, lost, or re-membered a component against what was persisted."""
    for scope_id, scope in _scopes(hierarchy):
        persisted = persisted_scopes.get(scope_id)
        if persisted is None:
            return True
        persisted_members = {
            component.component_id: {
                method.qualified_name for group in component.file_methods for method in group.methods
            }
            for component in persisted.components
            if component.component_id
        }
        live_members = {group.group_id: group.qualified_names for group in scope.groups}
        if set(live_members) != set(persisted_members):
            return True
        if any(live_members[component_id] != persisted_members[component_id] for component_id in live_members):
            return True
    return False


def _scopes(scope: ClusterScopeResult) -> Iterable[tuple[ScopeId, ClusterScopeResult]]:
    yield scope.scope_id, scope
    for group in scope.groups:
        if group.children is not None:
            yield from _scopes(group.children)
