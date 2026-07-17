"""Deterministic method deltas and scope impact projection."""

import logging
from pathlib import Path

from agents.analysis_result_responses import AnalysisInsights, Component
from agents.cluster_ids import CodeBoardingClusterIds
from agents.content_hash import SourceCache, hash_method_body, read_source_lines
from diagram_analysis.incremental.errors import IncrementalAnalysisError
from diagram_analysis.incremental.models import (
    MethodDelta,
    MethodRecord,
    PartitionGroup,
    ScopeGraph,
    ScopeImpact,
    ScopePartition,
)
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES

logger = logging.getLogger(__name__)


class IncrementalImpactAnalyzer:
    """Compare static-analysis generations and project changes onto component scopes."""

    def __init__(
        self,
        repo_dir: Path,
        previous_static: StaticAnalysisResults,
        current_static: StaticAnalysisResults,
        baseline_analysis: AnalysisInsights,
        previous_root_scope: ScopeGraph,
        current_root_scope: ScopeGraph,
    ) -> None:
        self.repo_dir = repo_dir
        self.previous_static = previous_static
        self.current_static = current_static
        self.old_methods = self._old_method_records(baseline_analysis)
        self.current_methods = self._current_method_records()
        self.delta = self._build_method_delta(previous_root_scope, current_root_scope)

    def scope_impact(self, components: list[Component], scope: ScopeGraph) -> ScopeImpact:
        old_methods = {component.component_id: component_method_names(component) for component in components}
        old_owner: dict[str, str] = {}
        for component_id, methods in old_methods.items():
            for method in methods:
                existing_owner = old_owner.get(method)
                if existing_owner is not None and existing_owner != component_id:
                    raise IncrementalAnalysisError(
                        f"Method {method!r} belongs to multiple components in the incremental baseline"
                    )
                old_owner[method] = component_id
        scope_methods = set(old_owner) | set(scope.method_to_cluster)
        affected_methods = self.delta.architecture_methods & scope_methods

        deleted_ids = {
            component_id
            for component_id, methods in old_methods.items()
            if methods and not (methods & set(self.current_methods))
        }
        cluster_owners = {
            cluster_id: {
                old_owner[method] for method in members if method in old_owner and old_owner[method] not in deleted_ids
            }
            for cluster_id, members in scope.cluster_members.items()
        }
        mutable_clusters = {
            cluster_id for cluster_id, members in scope.cluster_members.items() if members & affected_methods
        }
        mutable_clusters.update(cluster_id for cluster_id, owners in cluster_owners.items() if len(owners) != 1)
        mutable_ids = {
            owner for method in affected_methods if (owner := old_owner.get(method)) is not None
        } - deleted_ids
        mutable_ids.update(owner for cluster_id in mutable_clusters for owner in cluster_owners[cluster_id])

        changed = True
        while changed:
            previous_ids = set(mutable_ids)
            previous_clusters = set(mutable_clusters)
            owned_methods = (
                set().union(*(old_methods[component_id] for component_id in mutable_ids)) if mutable_ids else set()
            )
            mutable_clusters.update(
                cluster_id for cluster_id, members in scope.cluster_members.items() if members & owned_methods
            )
            mutable_ids.update(owner for cluster_id in mutable_clusters for owner in cluster_owners[cluster_id])
            changed = previous_ids != mutable_ids or previous_clusters != mutable_clusters

        affected_methods.update(
            method
            for cluster_id in mutable_clusters
            for method in scope.cluster_members[cluster_id]
            if method in self.current_methods
        )
        live_ids = set(old_methods) - deleted_ids
        immutable_ids = live_ids - mutable_ids
        immutable_assignments: dict[str, list[str]] = {}
        assigned_immutable_clusters: set[str] = set()
        for component_id in sorted(immutable_ids):
            surviving_methods = old_methods[component_id] & set(self.current_methods)
            unclustered = surviving_methods - set(scope.method_to_cluster)
            if unclustered:
                raise IncrementalAnalysisError(
                    f"Frozen component {component_id!r} has methods outside the current scoped partition: "
                    f"{sorted(unclustered)[:20]}"
                )
            cluster_ids = {
                scope.method_to_cluster[method] for method in surviving_methods if method in scope.method_to_cluster
            }
            conflicting = cluster_ids & mutable_clusters
            if conflicting:
                raise IncrementalAnalysisError(
                    f"Frozen component {component_id!r} overlaps mutable modules: "
                    f"{CodeBoardingClusterIds.sort(conflicting)}"
                )
            immutable_assignments[component_id] = CodeBoardingClusterIds.sort(cluster_ids)
            assigned_immutable_clusters.update(cluster_ids)

        all_cluster_ids = set(scope.cluster_members)
        covered_cluster_ids = mutable_clusters | assigned_immutable_clusters
        if covered_cluster_ids != all_cluster_ids:
            raise IncrementalAnalysisError(
                "Incremental scoped ownership is incomplete before LLM analysis: "
                f"missing={CodeBoardingClusterIds.sort(all_cluster_ids - covered_cluster_ids)}, "
                f"unexpected={CodeBoardingClusterIds.sort(covered_cluster_ids - all_cluster_ids)}"
            )

        proposed = self._proposed_partition(components, old_methods, mutable_ids, mutable_clusters, scope)
        return ScopeImpact(
            affected_methods,
            mutable_ids,
            deleted_ids,
            mutable_clusters,
            immutable_assignments,
            proposed,
        )

    def _old_method_records(self, analysis: AnalysisInsights) -> dict[str, MethodRecord]:
        records: dict[str, MethodRecord] = {}
        for file_path, file_entry in analysis.files.items():
            for method in file_entry.methods:
                if not method.content_hash:
                    raise IncrementalAnalysisError(
                        f"Baseline method {method.qualified_name!r} has no content hash; run a full analysis first"
                    )
                records[method.qualified_name] = MethodRecord(
                    method.qualified_name,
                    file_path,
                    method.start_line,
                    method.end_line,
                    method.node_type,
                    method.content_hash,
                )
        if not records:
            raise IncrementalAnalysisError("Baseline analysis has no persisted method index")
        return records

    def _current_method_records(self) -> dict[str, MethodRecord]:
        records: dict[str, MethodRecord] = {}
        source_cache: SourceCache = {}
        allowed_types = CALLABLE_TYPES | CLASS_TYPES
        for graph in self.current_static.available_program_graphs().values():
            for node in graph.symbol_nodes():
                if node.symbol_type not in allowed_types or node.symbol_type is None:
                    continue
                file_path = normalize_repo_path(node.file_path, self.repo_dir)
                content_hash = hash_method_body(
                    read_source_lines(self.repo_dir, file_path, source_cache),
                    node.line_start,
                    node.line_end,
                )
                if not content_hash:
                    raise IncrementalAnalysisError(
                        f"Current method {node.id!r} could not be content-hashed; incremental state is untrustworthy"
                    )
                records[node.id] = MethodRecord(
                    node.id,
                    file_path,
                    node.line_start,
                    node.line_end,
                    node.symbol_type.name,
                    content_hash,
                )
        if not records:
            raise IncrementalAnalysisError("Current static analysis contains no methods")
        return records

    def _build_method_delta(self, previous_scope: ScopeGraph, current_scope: ScopeGraph) -> MethodDelta:
        old_names = set(self.old_methods)
        current_names = set(self.current_methods)
        shared = old_names & current_names
        modified = {
            name for name in shared if self.old_methods[name].content_hash != self.current_methods[name].content_hash
        }
        metadata_only = {
            name
            for name in shared - modified
            if (
                self.old_methods[name].file_path,
                self.old_methods[name].start_line,
                self.old_methods[name].end_line,
            )
            != (
                self.current_methods[name].file_path,
                self.current_methods[name].start_line,
                self.current_methods[name].end_line,
            )
        }
        cluster_reassigned = {
            name
            for name in shared
            if previous_scope.method_to_cluster.get(name) != current_scope.method_to_cluster.get(name)
        }
        old_calls = call_topology(self.previous_static)
        current_calls = call_topology(self.current_static)
        added_calls = current_calls - old_calls
        deleted_calls = old_calls - current_calls
        delta = MethodDelta(
            added=current_names - old_names,
            deleted=old_names - current_names,
            modified=modified,
            cluster_reassigned=cluster_reassigned,
            call_boundary_changed={endpoint for edge in added_calls | deleted_calls for endpoint in edge},
            metadata_only=metadata_only,
            added_calls=added_calls,
            deleted_calls=deleted_calls,
        )
        logger.info(
            "Method delta: added=%d deleted=%d modified=%d reassigned=%d call-boundary=%d metadata-only=%d",
            len(delta.added),
            len(delta.deleted),
            len(delta.modified),
            len(delta.cluster_reassigned),
            len(delta.call_boundary_changed),
            len(delta.metadata_only),
        )
        return delta

    @staticmethod
    def _proposed_partition(
        components: list[Component],
        old_methods: dict[str, set[str]],
        mutable_ids: set[str],
        mutable_clusters: set[str],
        scope: ScopeGraph,
    ) -> ScopePartition:
        component_by_id = {component.component_id: component for component in components}
        grouped_clusters: dict[str, list[str]] = {}
        grouped_component_ids: dict[str, str] = {}
        for cluster_id in CodeBoardingClusterIds.sort(mutable_clusters):
            members = scope.cluster_members[cluster_id]
            candidates: list[tuple[int, int, str]] = []
            for component_id in mutable_ids:
                overlap = len(members & old_methods[component_id])
                stable = int(cluster_id in component_by_id[component_id].source_cluster_ids)
                if overlap or stable:
                    candidates.append((overlap, stable, component_id))
            if candidates:
                _overlap, _stable, component_id = min(
                    candidates,
                    key=lambda item: (-item[0], -item[1], item[2]),
                )
                group_key = f"existing:{component_id}"
                grouped_component_ids[group_key] = component_id
            else:
                group_key = f"new:{cluster_id}"
                grouped_component_ids[group_key] = ""
            grouped_clusters.setdefault(group_key, []).append(cluster_id)

        return ScopePartition(
            groups={
                group_key: PartitionGroup(
                    component_id=grouped_component_ids[group_key],
                    cluster_ids=CodeBoardingClusterIds.sort(set(cluster_ids)),
                )
                for group_key, cluster_ids in sorted(grouped_clusters.items())
            }
        )


def component_method_names(component: Component) -> set[str]:
    return {method.qualified_name for file_group in component.file_methods for method in file_group.methods}


def call_topology(static_analysis: StaticAnalysisResults) -> set[tuple[str, str]]:
    return {
        (edge.source, edge.target)
        for graph in static_analysis.available_program_graphs().values()
        for edge in graph.call_edges()
    }
