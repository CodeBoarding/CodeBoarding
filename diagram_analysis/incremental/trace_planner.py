"""Deterministic trace planning: build change groups before the LLM trace loop.

Pure data transformation — no LLM, no I/O beyond reading source slices from
the worktree and the base ref. Consumes an ``IncrementalDelta`` + call graphs
+ ``ChangeSet`` and produces a :class:`TracePlan` that the tracer then feeds
through the LLM loop.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from agents.change_status import ChangeStatus
from diagram_analysis.incremental.delta import FileDelta, IncrementalDelta
from repo_utils.change_detector import ChangeSet
from repo_utils.git_ops import read_file_at_ref
from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE
from static_analyzer.graph import CallGraph
from diagram_analysis.incremental.semantic_diff import (
    fingerprint_method_signature,
    fingerprint_source_text,
    is_file_cosmetic,
    strip_comments_from_source,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: read source body from working tree
# ---------------------------------------------------------------------------
def _read_method_body(repo_dir: Path, file_path: str, start_line: int, end_line: int) -> str | None:
    full_path = repo_dir / file_path
    if not full_path.is_file():
        return None
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if start_line < 1 or end_line > len(lines):
            return None
        return "".join(lines[start_line - 1 : end_line])
    except OSError:
        return None


def _get_diff_hunks(change_set: ChangeSet, file_path: str) -> str:
    """Return unified diff hunks for a single file from *change_set*."""
    file_diff = change_set.get_file(file_path)
    return file_diff.patch_text if file_diff is not None else ""


def _read_method_body_at_ref(
    repo_dir: Path,
    base_ref: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str | None:
    """Read a method body slice from *base_ref* using the same line window."""
    content = read_file_at_ref(repo_dir, base_ref, file_path)
    if content is None:
        return None
    lines = content.splitlines(keepends=True)
    if start_line < 1 or end_line > len(lines):
        return None
    return "".join(lines[start_line - 1 : end_line])


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ChangedMethodContext:
    """Context for a single changed method."""

    qualified_name: str
    file_path: str
    change_type: str
    new_body: str | None = None


@dataclass
class ChangeGroup:
    """A group of related changed methods."""

    group_key: str
    file_paths: list[str] = field(default_factory=list)
    methods: list[ChangedMethodContext] = field(default_factory=list)
    upstream_neighbors: list[str] = field(default_factory=list)
    downstream_neighbors: list[str] = field(default_factory=list)
    diff_hunks: str = ""
    diff_hunks_by_file: dict[str, str] = field(default_factory=dict)
    graph_backed: bool = True


@dataclass
class GraphRegionMetadata:
    """Region metadata derived from SCC condensation of the call graph."""

    method_to_scc: dict[str, int] = field(default_factory=dict)
    scc_to_methods: dict[int, set[str]] = field(default_factory=dict)
    scc_to_region: dict[int, int] = field(default_factory=dict)
    method_to_region: dict[str, int] = field(default_factory=dict)


@dataclass
class TracePlan:
    """Planned trace work after deterministic filtering and region grouping."""

    groups: list[ChangeGroup] = field(default_factory=list)
    fast_path_impacted_methods: list[str] = field(default_factory=list)
    cosmetic_skipped: int = 0


# ---------------------------------------------------------------------------
# Neighbor extraction from CFG
# ---------------------------------------------------------------------------
NeighborIndex = tuple[dict[str, list[str]], dict[str, list[str]]]


def _build_neighbor_indexes(*cfg_dicts: dict[str, CallGraph]) -> NeighborIndex:
    """Build upstream/downstream adjacency maps from one or more CFG dicts."""
    upstream: dict[str, set[str]] = defaultdict(set)
    downstream: dict[str, set[str]] = defaultdict(set)
    for cfgs in cfg_dicts:
        for cfg in cfgs.values():
            for qname, node in cfg.nodes.items():
                if node.methods_called_by_me:
                    downstream[qname].update(node.methods_called_by_me)
            for edge in cfg.edges:
                upstream[edge.get_destination()].add(edge.get_source())
    return (
        {k: list(v) for k, v in upstream.items()},
        {k: list(v) for k, v in downstream.items()},
    )


def _get_neighbors(
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
    qualified_name: str,
) -> tuple[list[str], list[str]]:
    """Return (upstream_callers, downstream_callees) for a method."""
    return upstream_index.get(qualified_name, []), downstream_index.get(qualified_name, [])


# ---------------------------------------------------------------------------
# Region grouping via SCC condensation
# ---------------------------------------------------------------------------
def _build_graph_region_metadata(
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
) -> GraphRegionMetadata:
    """Build SCC and weak-component metadata for region grouping."""
    graph = nx.DiGraph()
    all_nodes: set[str] = set(upstream_index) | set(downstream_index)
    for neighbors in upstream_index.values():
        all_nodes.update(neighbors)
    for src, neighbors in downstream_index.items():
        all_nodes.add(src)
        all_nodes.update(neighbors)
        for dst in neighbors:
            graph.add_edge(src, dst)

    if all_nodes:
        graph.add_nodes_from(all_nodes)
    if graph.number_of_nodes() == 0:
        return GraphRegionMetadata()

    method_to_scc: dict[str, int] = {}
    scc_to_methods: dict[int, set[str]] = {}
    for scc_id, members in enumerate(nx.strongly_connected_components(graph)):
        member_set = set(members)
        scc_to_methods[scc_id] = member_set
        for method in member_set:
            method_to_scc[method] = scc_id

    condensed = nx.DiGraph()
    condensed.add_nodes_from(scc_to_methods)
    for src, dst in graph.edges():
        src_scc = method_to_scc[src]
        dst_scc = method_to_scc[dst]
        if src_scc != dst_scc:
            condensed.add_edge(src_scc, dst_scc)

    scc_to_region: dict[int, int] = {}
    method_to_region: dict[str, int] = {}
    for region_id, component in enumerate(nx.weakly_connected_components(condensed)):
        for scc_id in component:
            scc_to_region[scc_id] = region_id
            for method in scc_to_methods[scc_id]:
                method_to_region[method] = region_id

    return GraphRegionMetadata(
        method_to_scc=method_to_scc,
        scc_to_methods=scc_to_methods,
        scc_to_region=scc_to_region,
        method_to_region=method_to_region,
    )


def _determine_region_key(
    qualified_name: str,
    file_path: str,
    graph_metadata: GraphRegionMetadata,
) -> tuple[str, bool]:
    """Return a region key for a method and whether it is graph-backed."""
    region_id = graph_metadata.method_to_region.get(qualified_name)
    if region_id is None:
        logger.debug("No call-graph region for %s in %s; using file-level fallback", qualified_name, file_path)
        return f"file:{file_path}", False
    return f"region:{region_id}", True


# ---------------------------------------------------------------------------
# Method-level classification
# ---------------------------------------------------------------------------
def _compare_modified_method_versions(
    file_path: str,
    old_body: str | None,
    new_body: str | None,
) -> tuple[bool, bool]:
    """Return ``(semantically_unchanged, signature_changed)`` for a modified method."""
    if old_body is None or new_body is None:
        return False, True

    if fingerprint_source_text(file_path, old_body) == fingerprint_source_text(file_path, new_body):
        return True, False

    old_sig = fingerprint_method_signature(file_path, old_body)
    new_sig = fingerprint_method_signature(file_path, new_body)
    if old_sig is None or new_sig is None:
        return False, True
    return False, old_sig != new_sig


def _is_pure_in_place_edit(file_delta: FileDelta) -> bool:
    """True when *file_delta* contains only body edits to existing methods.

    Structural precondition for both the cosmetic-file skip and the LLM-skip
    fast path: no added/deleted methods at the file level, just modifications
    of methods that already existed at the base ref.
    """
    return (
        file_delta.file_status == ChangeStatus.MODIFIED
        and bool(file_delta.modified_methods)
        and not file_delta.added_methods
        and not file_delta.deleted_methods
    )


def _is_fast_path_candidate(
    file_delta: FileDelta,
    signature_changed: bool,
    upstream_callers: list[str],
) -> bool:
    """Return True if a modified method can skip the LLM trace and be marked impacted directly.

    Why: when a method body changes but its signature is stable AND nothing
    calls it (no upstream edges in the call graph), there is no way for the
    change to propagate outward — so we can record it as impacted without
    asking the LLM. The name "fast path" refers to this LLM-skip shortcut;
    it is not about downstream propagation (hence ``downstream`` is not checked).
    """
    return _is_pure_in_place_edit(file_delta) and not signature_changed and not upstream_callers


# ---------------------------------------------------------------------------
# Group assembly
# ---------------------------------------------------------------------------
def _append_method_to_group(
    groups: dict[str, ChangeGroup],
    region_key: str,
    graph_backed: bool,
    file_path: str,
    diff_text: str,
    ctx: ChangedMethodContext,
    upstream_neighbors: list[str],
    downstream_neighbors: list[str],
) -> None:
    group = groups.setdefault(region_key, ChangeGroup(group_key=region_key, graph_backed=graph_backed))
    if file_path not in group.file_paths:
        group.file_paths.append(file_path)
    if diff_text and file_path not in group.diff_hunks_by_file:
        group.diff_hunks_by_file[file_path] = diff_text
    if diff_text:
        group.diff_hunks = f"{group.diff_hunks}\n{diff_text}".strip() if group.diff_hunks else diff_text
    # Dedupe on append (rework 4): avoid duplicate ctx across iterations.
    if not any(m.qualified_name == ctx.qualified_name for m in group.methods):
        group.methods.append(ctx)
    group.upstream_neighbors.extend(upstream_neighbors)
    group.downstream_neighbors.extend(downstream_neighbors)


def _finalize_groups(groups: list[ChangeGroup]) -> list[ChangeGroup]:
    """Deduplicate and normalize grouped change regions."""
    finalized: list[ChangeGroup] = []
    for index, group in enumerate(groups, start=1):
        group.file_paths = sorted(set(group.file_paths))
        group.upstream_neighbors = sorted(set(group.upstream_neighbors))
        group.downstream_neighbors = sorted(set(group.downstream_neighbors))
        if len(group.file_paths) == 1:
            group.group_key = group.file_paths[0]
        else:
            group.group_key = f"region:{index}"
        finalized.append(group)
    return finalized


def _collapse_fallback_groups(groups: list[ChangeGroup]) -> list[ChangeGroup]:
    """Collapse ONLY the fallback (non-graph-backed) groups into one
    conservative combined region, keeping graph-backed groups independent.

    This preserves parallelism for well-understood regions while keeping
    coverage-gap methods safe.
    """
    graph_backed = [g for g in groups if g.graph_backed]
    fallback = [g for g in groups if not g.graph_backed]
    if len(fallback) <= 1:
        return groups

    combined = ChangeGroup(group_key="region:fallback-combined", graph_backed=False)
    seen_methods: set[str] = set()
    for group in fallback:
        combined.file_paths.extend(group.file_paths)
        for m in group.methods:
            if m.qualified_name not in seen_methods:
                combined.methods.append(m)
                seen_methods.add(m.qualified_name)
        combined.upstream_neighbors.extend(group.upstream_neighbors)
        combined.downstream_neighbors.extend(group.downstream_neighbors)
        for file_path, diff_text in group.diff_hunks_by_file.items():
            combined.diff_hunks_by_file.setdefault(file_path, diff_text)

    return _finalize_groups(graph_backed + [combined])


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------
def build_trace_plan(
    delta: IncrementalDelta,
    cfgs: dict[str, CallGraph],
    repo_dir: Path,
    base_ref: str,
    change_set: ChangeSet,
) -> TracePlan:
    """Build grouped trace regions plus deterministic fast-path impact decisions."""
    upstream_index, downstream_index = _build_neighbor_indexes(cfgs)
    groups: dict[str, ChangeGroup] = {}
    graph_metadata = _build_graph_region_metadata(upstream_index, downstream_index)

    cosmetic_skipped = 0
    extension_skipped = 0
    fast_path_impacted_methods: set[str] = set()
    saw_fallback_region = False

    for file_delta in delta.file_deltas:
        fp = file_delta.file_path

        # Defense in depth: change_set already filters unsupported extensions,
        # but synthetic deltas in tests may still include them.
        ext = Path(fp).suffix.lower()
        if ext not in SOURCE_EXTENSION_TO_LANGUAGE:
            extension_skipped += 1
            continue

        all_methods = file_delta.added_methods + file_delta.modified_methods
        if not all_methods and not file_delta.deleted_methods and file_delta.file_status != ChangeStatus.DELETED:
            continue

        if _is_pure_in_place_edit(file_delta) and is_file_cosmetic(repo_dir, base_ref, fp):
            cosmetic_skipped += 1
            logger.info("Skipping cosmetic-only file: %s", fp)
            continue

        diff_text = _get_diff_hunks(change_set, fp) if file_delta.file_status != ChangeStatus.ADDED else ""

        for method in all_methods:
            body = _read_method_body(repo_dir, fp, method.start_line, method.end_line)
            if body is not None:
                body = strip_comments_from_source(fp, body)
            up, down = _get_neighbors(upstream_index, downstream_index, method.qualified_name)

            if method.change_type == ChangeStatus.MODIFIED:
                old_body = _read_method_body_at_ref(repo_dir, base_ref, fp, method.start_line, method.end_line)
                if old_body is not None:
                    old_body = strip_comments_from_source(fp, old_body)
                semantically_unchanged, signature_changed = _compare_modified_method_versions(fp, old_body, body)
                if semantically_unchanged:
                    continue
                if _is_fast_path_candidate(file_delta, signature_changed, up):
                    fast_path_impacted_methods.add(method.qualified_name)
                    continue

            ctx = ChangedMethodContext(
                qualified_name=method.qualified_name,
                file_path=fp,
                change_type=method.change_type,
                new_body=body,
            )
            region_key, graph_backed = _determine_region_key(method.qualified_name, fp, graph_metadata)
            if not graph_backed:
                saw_fallback_region = True
            _append_method_to_group(groups, region_key, graph_backed, fp, diff_text, ctx, up, down)

        for method in file_delta.deleted_methods:
            ctx = ChangedMethodContext(
                qualified_name=method.qualified_name,
                file_path=fp,
                change_type=ChangeStatus.DELETED,
                new_body=None,
            )
            up, down = _get_neighbors(upstream_index, downstream_index, method.qualified_name)
            region_key, graph_backed = _determine_region_key(method.qualified_name, fp, graph_metadata)
            if not graph_backed:
                saw_fallback_region = True
            _append_method_to_group(groups, region_key, graph_backed, fp, diff_text, ctx, up, down)

    if extension_skipped:
        logger.info("Skipped %d file(s) with non-analyzable extensions", extension_skipped)
    if cosmetic_skipped:
        logger.info("Skipped %d cosmetic-only file(s) from tracing", cosmetic_skipped)

    finalized_groups = _finalize_groups(list(groups.values()))
    if saw_fallback_region:
        logger.info("Graph coverage incomplete for some changed methods; collapsing fallback regions only")
        finalized_groups = _collapse_fallback_groups(finalized_groups)

    return TracePlan(
        groups=finalized_groups,
        fast_path_impacted_methods=sorted(fast_path_impacted_methods),
        cosmetic_skipped=cosmetic_skipped,
    )
