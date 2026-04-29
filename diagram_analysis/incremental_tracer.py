"""Trace-based semantic impact analysis for incremental updates.

Given a set of changed methods (from git diff), traces forward through the
call graph to determine which methods' *semantic descriptions* are affected.
The LLM controls traversal inside bounded budgets; the system fetches code
blocks via symbol-table lookup.

Combines the deterministic plan-construction pass and the LLM-driven trace
loop in one module, matching the pre-merge layout.
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables.config import RunnableConfig
from trustcall import create_extractor

from agents.change_status import ChangeStatus
from agents.llm_config import supports_prompt_caching
from agents.prompts.prompt_factory import get_trace_system_message
from agents.retry import RetryAction, RetryDecision, default_backoff, with_retries
from diagram_analysis.incremental_models import (
    DEFAULT_TRACE_CONFIG,
    ImpactedComponent,
    TraceConfig,
    TraceResponse,
    TraceResult,
    TraceStopReason,
)
from diagram_analysis.incremental_updater import FileDelta, IncrementalDelta
from repo_utils.git_ops import read_file_at_ref
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE
from static_analyzer.graph import CallGraph
from static_analyzer.graph_query import resolve_method_node
from static_analyzer.semantic_diff import (
    check_syntax_errors,
    fingerprint_method_signature,
    fingerprint_source_text,
    is_file_cosmetic,
    strip_comments_from_source,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File-slice helpers
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


def _get_diff_hunks(
    repo_dir: Path,
    base_ref: str,
    file_path: str,
    parsed_diff: Any | None = None,
) -> str:
    """Return unified diff hunks for *file_path*.

    When *parsed_diff* (a ChangeSet-shaped object exposing ``get_file``) is
    provided, the patch is read from there. Otherwise we fall back to ``git
    diff -U3`` for the file. Matching the pre-merge tracer signature so the
    generator can pass either parsed diffs or rely on git directly.
    """
    if parsed_diff is not None:
        file_diff = parsed_diff.get_file(file_path)
        return file_diff.patch_text if file_diff is not None else ""

    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "-U3", base_ref, "--", file_path],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


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
# Plan dataclasses
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
    disconnected_files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Method resolver
# ---------------------------------------------------------------------------
class MethodResolver:
    """Resolves method names to source bodies via static analysis references."""

    def __init__(self, static_analysis: StaticAnalysisResults, repo_dir: Path):
        self._static = static_analysis
        self._repo_dir = repo_dir
        self._unresolved: list[str] = []

    def resolve(self, qualified_name: str) -> tuple[str | None, str | None]:
        """Resolve a method name to its (resolved qname, source body)."""
        node = resolve_method_node(self._static, qualified_name)
        if node is None:
            self._unresolved.append(qualified_name)
            logger.warning("Unresolved method during tracing: %s", qualified_name)
            return None, None
        body = _read_method_body(self._repo_dir, node.file_path, node.line_start, node.line_end)
        if body is not None:
            body = strip_comments_from_source(node.file_path, body)
        return node.fully_qualified_name, body

    @property
    def unresolved(self) -> list[str]:
        return list(self._unresolved)


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
    """True if a modified method can skip the LLM trace and be marked impacted directly.

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
    """Collapse non-graph-backed groups into one combined region.

    Preserves parallelism for graph-backed regions while keeping
    coverage-gap methods in a single conservative region.
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
    *,
    parsed_diff: Any | None = None,
    excluded_files: set[str] | None = None,
) -> TracePlan:
    """Build grouped trace regions plus deterministic fast-path impact decisions."""
    upstream_index, downstream_index = _build_neighbor_indexes(cfgs)
    groups: dict[str, ChangeGroup] = {}
    graph_metadata = _build_graph_region_metadata(upstream_index, downstream_index)

    cosmetic_skipped = 0
    extension_skipped = 0
    fast_path_impacted_methods: set[str] = set()
    saw_fallback_region = False
    disconnected_files: set[str] = set()

    for file_delta in delta.file_deltas:
        fp = file_delta.file_path

        if excluded_files and fp in excluded_files:
            continue

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

        diff_text = (
            _get_diff_hunks(repo_dir, base_ref, fp, parsed_diff) if file_delta.file_status != ChangeStatus.ADDED else ""
        )

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
                if file_delta.file_status == ChangeStatus.ADDED:
                    disconnected_files.add(fp)
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
        disconnected_files=sorted(disconnected_files),
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def _trace_message_content(
    text: str,
    llm: BaseChatModel,
    *,
    enable_cache: bool = False,
) -> str | list[dict[str, Any]]:
    """Return message content, adding Anthropic cache metadata when enabled."""
    if not enable_cache or not supports_prompt_caching(llm):
        return text
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _build_initial_prompt(group: ChangeGroup, max_neighbor_preview: int) -> str:
    parts = ["# Changed Methods\n"]
    file_paths = group.file_paths or sorted({method.file_path for method in group.methods}) or [group.group_key]
    methods_by_file: dict[str, list[ChangedMethodContext]] = defaultdict(list)
    for method in group.methods:
        methods_by_file[method.file_path].append(method)

    if len(file_paths) == 1:
        parts.append(f"## File: {file_paths[0]}\n")
    else:
        parts.append(f"## Region: {group.group_key}")
        parts.append(f"Files: {', '.join(file_paths)}")

    for file_path in file_paths:
        if len(file_paths) > 1:
            parts.append(f"### File: {file_path}")
        for method in methods_by_file.get(file_path, []):
            parts.append(f"### {method.qualified_name} ({method.change_type})")
            if method.new_body:
                parts.append(f"```\n{method.new_body}\n```")
        diff_text = group.diff_hunks_by_file.get(file_path, "")
        if diff_text:
            parts.append(f"Diff:\n```diff\n{diff_text}\n```")
    if not group.diff_hunks_by_file and group.diff_hunks:
        parts.append(f"Diff:\n```diff\n{group.diff_hunks}\n```")

    if group.upstream_neighbors:
        parts.append(f"Upstream callers: {', '.join(group.upstream_neighbors[:max_neighbor_preview])}")
    if group.downstream_neighbors:
        parts.append(f"Downstream callees: {', '.join(group.downstream_neighbors[:max_neighbor_preview])}")
    parts.append(
        "Analyze these changes. Respond with:\n"
        "- status: continue (if you need to inspect more methods) or a stop reason\n"
        "- impacted_methods: methods whose diagram description needs updating\n"
        "- next_methods_to_fetch: methods to inspect next (if continuing)\n"
        "- reason: brief explanation\n"
        "- semantic_impact_summary: one sentence describing the semantic change at a high level only when status is stop_material_semantic_impact_closure_reached; otherwise leave it empty. Do not mention method names, files, or component names.\n"
        "- confidence: 0.0-1.0\n"
    )
    return "\n".join(parts)


def _build_continuation_prompt(
    fetched_bodies: dict[str, str | None],
    previous_response: TraceResponse,
) -> str:
    parts = ["# Additional Method Bodies\n"]
    for qname, body in fetched_bodies.items():
        parts.append(f"## {qname}")
        if body:
            parts.append(f"```\n{body}\n```")
        else:
            parts.append("(could not resolve — method not found)")
        parts.append("")
    parts.append(
        f"Previous assessment: {previous_response.reason}\n"
        f"Previously identified impacted methods: {', '.join(previous_response.impacted_methods)}\n\n"
        "Continue your analysis with the additional context above.\n"
        "Update impacted_methods (cumulative) and either request more methods or stop.\n"
        "Only populate semantic_impact_summary if you conclude there is material semantic impact and closure has been reached.\n"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core tracing loop
# ---------------------------------------------------------------------------
def _invoke_extractor_with_retry(
    extractor: Any,
    messages: list[dict[str, Any]],
    config: TraceConfig,
    *,
    hop: int,
    invoke_config: RunnableConfig,
) -> dict[str, Any] | None:
    """Invoke the extractor with bounded retry/backoff.

    Returns the extractor result or None if all retries fail.
    """

    def classify(_exc: Exception, attempt: int) -> RetryDecision:
        return RetryDecision(
            action=RetryAction.RETRY,
            backoff_s=default_backoff(
                attempt,
                initial_s=config.llm_initial_backoff_s,
                multiplier=config.llm_backoff_multiplier,
                max_s=None,
            ),
        )

    return with_retries(
        lambda: extractor.invoke({"messages": messages}, config=invoke_config),
        max_attempts=config.llm_max_retries + 1,
        classify=classify,
        on_exhausted=lambda _e: None,
        log_prefix=f"Trace LLM call (hop {hop})",
    )


def _trace_single_group(
    group: ChangeGroup,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
    parsing_llm: BaseChatModel,
    config: TraceConfig,
    callbacks: list | None = None,
) -> TraceResult:
    """Run the semantic tracing loop for a single change region."""
    resolver = MethodResolver(static_analysis, repo_dir)
    extractor = create_extractor(parsing_llm, tools=[TraceResponse], tool_choice=TraceResponse.__name__)
    invoke_config: RunnableConfig = {"callbacks": callbacks} if callbacks else {}

    prompt = _build_initial_prompt(group, config.max_neighbor_preview)
    caching_supported = supports_prompt_caching(parsing_llm)
    cached_turns = 1 if caching_supported else 0
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": get_trace_system_message()},
        {"role": "user", "content": _trace_message_content(prompt, parsing_llm, enable_cache=caching_supported)},
    ]

    impacted_methods: set[str] = set()
    visited_methods: set[str] = {method.qualified_name for method in group.methods}
    total_fetched = 0

    for hop in range(config.max_hops + 1):
        result = _invoke_extractor_with_retry(extractor, messages, config, hop=hop, invoke_config=invoke_config)
        if result is None:
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=resolver.unresolved,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hop,
            )

        if "responses" not in result or not result["responses"]:
            logger.warning("Extractor returned no responses at hop %d; stopping", hop)
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=resolver.unresolved,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hop,
            )

        response = TraceResponse.model_validate(result["responses"][0])
        impacted_methods.update(response.impacted_methods)

        logger.info(
            "Trace region %s hop %d: status=%s impacted=%d next=%d reason=%s",
            group.group_key,
            hop,
            response.status,
            len(impacted_methods),
            len(response.next_methods_to_fetch),
            response.reason[:80],
        )
        if response.status == TraceStopReason.CLOSURE_REACHED and response.semantic_impact_summary:
            logger.info(
                "Trace semantic impact summary for %s: %s",
                group.group_key,
                response.semantic_impact_summary[:200],
            )

        if response.status != TraceStopReason.CONTINUE:
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=sorted(set(resolver.unresolved + response.unresolved_frontier)),
                stop_reason=response.status,
                hops_used=hop,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        remaining = config.max_fetched_methods - total_fetched
        to_fetch = response.next_methods_to_fetch[:remaining]

        if not to_fetch:
            logger.info("Fetch budget exhausted at hop %d", hop)
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=sorted(set(resolver.unresolved + response.unresolved_frontier)),
                stop_reason=TraceStopReason.BUDGET_EXHAUSTED,
                hops_used=hop,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        fetched: dict[str, str | None] = {}
        for qname in to_fetch:
            visited_methods.add(qname)
            resolved_name, body = resolver.resolve(qname)
            if resolved_name is not None:
                visited_methods.add(resolved_name)
            fetched[qname] = body

        resolved_count = sum(1 for b in fetched.values() if b is not None)
        total_fetched += resolved_count

        if resolved_count == 0:
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=sorted(set(resolver.unresolved + response.unresolved_frontier)),
                stop_reason=TraceStopReason.FRONTIER_EXHAUSTED,
                hops_used=hop,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        cont_prompt = _build_continuation_prompt(fetched, response)
        use_cache = caching_supported and cached_turns < config.max_cached_turns
        messages.append({"role": "assistant", "content": response.llm_str()})
        messages.append(
            {"role": "user", "content": _trace_message_content(cont_prompt, parsing_llm, enable_cache=use_cache)}
        )
        if use_cache:
            cached_turns += 1

    return TraceResult(
        visited_methods=sorted(visited_methods),
        impacted_methods=sorted(impacted_methods),
        unresolved_frontier=resolver.unresolved,
        stop_reason=TraceStopReason.BUDGET_EXHAUSTED,
        hops_used=config.max_hops,
    )


def _merge_trace_results(
    trace_results: list[TraceResult],
    *,
    fast_path_impacted_methods: list[str] | None = None,
    disconnected_files: list[str] | None = None,
    non_traceable_files: list[str] | None = None,
) -> TraceResult:
    """Merge trace results from independent regions plus deterministic fast-path decisions.

    Severity-based stop-reason combination preserves observability when results
    diverge across regions (e.g. one region finishes cleanly while another runs
    out of budget). The escalation rule below upgrades a NO_MATERIAL_IMPACT
    verdict to UNCERTAIN whenever the planner had coverage gaps the LLM never
    saw — a no-impact conclusion under those gaps is not trustworthy.
    """
    visited_methods: set[str] = set()
    impacted_methods: set[str] = set(fast_path_impacted_methods or [])
    unresolved_frontier: set[str] = set()
    semantic_summaries: set[str] = set()
    hops_used = 0
    stop_reason = TraceStopReason.NO_MATERIAL_IMPACT

    severity = {
        TraceStopReason.NO_MATERIAL_IMPACT: 0,
        TraceStopReason.CLOSURE_REACHED: 1,
        TraceStopReason.FRONTIER_EXHAUSTED: 2,
        TraceStopReason.BUDGET_EXHAUSTED: 3,
        TraceStopReason.UNCERTAIN: 4,
    }

    for result in trace_results:
        visited_methods.update(result.visited_methods)
        impacted_methods.update(result.impacted_methods)
        unresolved_frontier.update(result.unresolved_frontier)
        hops_used = max(hops_used, result.hops_used)
        if severity.get(result.stop_reason, 0) > severity.get(stop_reason, 0):
            stop_reason = result.stop_reason
        if result.semantic_impact_summary:
            semantic_summaries.add(result.semantic_impact_summary)

    if impacted_methods and stop_reason == TraceStopReason.NO_MATERIAL_IMPACT:
        stop_reason = TraceStopReason.CLOSURE_REACHED
    if not trace_results and impacted_methods:
        stop_reason = TraceStopReason.CLOSURE_REACHED
    # Coverage-gap escalation: a "no impact" verdict is not trustworthy when the
    # planner had files it could not analyze.
    if (non_traceable_files or disconnected_files) and stop_reason == TraceStopReason.NO_MATERIAL_IMPACT:
        stop_reason = TraceStopReason.UNCERTAIN

    return TraceResult(
        visited_methods=sorted(visited_methods | impacted_methods),
        impacted_methods=sorted(impacted_methods),
        unresolved_frontier=sorted(unresolved_frontier),
        non_traceable_files=sorted(set(non_traceable_files or [])),
        disconnected_files=sorted(set(disconnected_files or [])),
        stop_reason=stop_reason,
        hops_used=hops_used,
        semantic_impact_summary=semantic_summaries.pop() if len(semantic_summaries) == 1 else "",
    )


def run_trace(
    delta: IncrementalDelta,
    cfgs: dict[str, CallGraph],
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
    base_ref: str,
    parsing_llm: BaseChatModel,
    *,
    parsed_diff: Any | None = None,
    config: TraceConfig = DEFAULT_TRACE_CONFIG,
    callbacks: list | None = None,
) -> TraceResult:
    """Run the semantic tracing loop over changed methods.

    Returns a TraceResult with impacted methods. Scope classification
    (impacted_components) happens in the orchestrator via ``classify_scope``.

    Files with syntax errors are excluded from tracing and surfaced via
    ``TraceResult.non_traceable_files`` so the caller can treat coverage gaps
    as uncertainty rather than aborting outright.
    """
    non_traceable_files: set[str] = set()
    for file_delta in delta.file_deltas:
        if file_delta.file_status == ChangeStatus.DELETED:
            continue
        ext = Path(file_delta.file_path).suffix.lower()
        if ext not in SOURCE_EXTENSION_TO_LANGUAGE:
            continue
        errors = check_syntax_errors(repo_dir, file_delta.file_path)
        if errors:
            error_locations = ", ".join(f"line {line}:{col}" for line, col in errors)
            logger.warning(
                "Syntax errors in %s at %s; excluding from incremental trace",
                file_delta.file_path,
                error_locations,
            )
            non_traceable_files.add(file_delta.file_path)

    trace_plan = build_trace_plan(
        delta=delta,
        cfgs=cfgs,
        repo_dir=repo_dir,
        base_ref=base_ref,
        parsed_diff=parsed_diff,
        excluded_files=non_traceable_files,
    )
    if trace_plan.fast_path_impacted_methods:
        logger.info(
            "Using deterministic fast path for %d changed method(s)",
            len(trace_plan.fast_path_impacted_methods),
        )

    if not trace_plan.groups:
        if trace_plan.fast_path_impacted_methods:
            return _merge_trace_results(
                [],
                fast_path_impacted_methods=trace_plan.fast_path_impacted_methods,
                disconnected_files=trace_plan.disconnected_files,
                non_traceable_files=sorted(non_traceable_files),
            )
        if trace_plan.cosmetic_skipped:
            logger.info("All changed files were cosmetic-only; skipping trace")
            return TraceResult(
                stop_reason=TraceStopReason.COSMETIC_ONLY,
                non_traceable_files=sorted(non_traceable_files),
                disconnected_files=trace_plan.disconnected_files,
            )
        logger.info("No traceable changed methods; skipping trace")
        return _merge_trace_results(
            [],
            disconnected_files=trace_plan.disconnected_files,
            non_traceable_files=sorted(non_traceable_files),
        )

    if len(trace_plan.groups) > 1:
        max_workers = min(len(trace_plan.groups), config.max_parallel_regions)
        logger.info(
            "Tracing %d independent change regions in parallel (workers=%d)", len(trace_plan.groups), max_workers
        )
        trace_results: list[TraceResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _trace_single_group, group, static_analysis, repo_dir, parsing_llm, config, callbacks
                ): group.group_key
                for group in trace_plan.groups
            }
            for future in as_completed(futures):
                region_key = futures[future]
                try:
                    trace_results.append(future.result())
                except Exception as exc:
                    logger.error("Trace region %s failed: %s", region_key, exc)
                    trace_results.append(TraceResult(stop_reason=TraceStopReason.UNCERTAIN))
    else:
        trace_results = [
            _trace_single_group(group, static_analysis, repo_dir, parsing_llm, config, callbacks)
            for group in trace_plan.groups
        ]

    return _merge_trace_results(
        trace_results,
        fast_path_impacted_methods=trace_plan.fast_path_impacted_methods,
        disconnected_files=trace_plan.disconnected_files,
        non_traceable_files=sorted(non_traceable_files),
    )


# ---------------------------------------------------------------------------
# Scope classification: map impacted methods to components
# ---------------------------------------------------------------------------
def classify_scope(
    trace_result: TraceResult,
    file_to_component: dict[str, str],
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
) -> TraceResult:
    """Deterministically map impacted methods to components using a file->component map.

    Mutates trace_result.impacted_components and returns it.
    """
    component_methods: dict[str, list[str]] = {}

    for qname in trace_result.impacted_methods:
        node = resolve_method_node(static_analysis, qname)
        if node is None:
            continue
        file_path = node.file_path
        try:
            file_path = Path(file_path).resolve().relative_to(repo_dir.resolve()).as_posix()
        except ValueError:
            file_path = file_path.lstrip("./")
        comp_id = file_to_component.get(file_path)
        if comp_id is None:
            continue
        component_methods.setdefault(comp_id, []).append(qname)

    trace_result.impacted_components = [
        ImpactedComponent(component_id=cid, impacted_methods=methods)
        for cid, methods in sorted(component_methods.items())
    ]
    return trace_result
