"""Trace-based semantic impact analysis for incremental updates.

Given a set of changed methods (from git diff), traces forward through the
call graph to determine which methods' *semantic descriptions* are affected.
The LLM controls traversal inside bounded budgets; the system fetches code
blocks via symbol-table lookup.
"""

import logging
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
from langchain_core.language_models import BaseChatModel

from agents.change_status import ChangeStatus
from diagram_analysis.semantic_diff import (
    EXTENSION_TO_LANGUAGE,
    check_syntax_errors,
    fingerprint_method_signature,
    fingerprint_source_text,
    is_file_cosmetic,
    strip_comments_from_source,
)
from diagram_analysis.checkpoints import FileComponentIndex
from diagram_analysis.incremental_models import (
    ImpactedComponent,
    TraceConfig,
    TraceResponse,
    TraceResult,
    TraceStopReason,
)
from diagram_analysis.incremental_types import IncrementalDelta
from repo_utils.parsed_diff import ParsedGitDiff
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node

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


def _get_diff_hunks(
    repo_dir: Path,
    base_ref: str,
    file_path: str,
    parsed_diff: ParsedGitDiff | None = None,
) -> str:
    """Return unified diff hunks for a single file."""
    if parsed_diff is not None:
        file_diff = parsed_diff.get_file(file_path)
        return file_diff.patch_text if file_diff is not None else ""

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


# ---------------------------------------------------------------------------
# Seed context: what we send to the LLM on the first tracing step
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
    parallel_safe: bool = False


# ---------------------------------------------------------------------------
# Method resolver: wraps symbol table lookups
# ---------------------------------------------------------------------------
class MethodResolver:
    """Resolves method names to source code via static analysis references."""

    def __init__(self, static_analysis: StaticAnalysisResults, repo_dir: Path):
        self._static = static_analysis
        self._repo_dir = repo_dir
        self._unresolved: list[str] = []

    def resolve(self, qualified_name: str) -> tuple[Node | None, str | None]:
        """Resolve a method name to its Node and source body.

        Returns (node, body) or (None, None) with a warning logged.
        """
        node = self._try_lookup(qualified_name)
        if node is None:
            self._unresolved.append(qualified_name)
            logger.warning("Unresolved method during tracing: %s", qualified_name)
            return None, None
        body = _read_method_body(self._repo_dir, node.file_path, node.line_start, node.line_end)
        if body is not None:
            body = strip_comments_from_source(node.file_path, body)
        return node, body

    def _try_lookup(self, qualified_name: str) -> Node | None:
        for lang in self._static.get_languages():
            try:
                return self._static.get_reference(lang, qualified_name)
            except (ValueError, FileExistsError):
                _, node = self._static.get_loose_reference(lang, qualified_name)
                if node is not None:
                    return node
        return None

    @property
    def unresolved(self) -> list[str]:
        return list(self._unresolved)


# ---------------------------------------------------------------------------
# Neighbor extraction from CFG
# ---------------------------------------------------------------------------
NeighborIndex = tuple[dict[str, list[str]], dict[str, list[str]]]


def _build_neighbor_indexes(*cfg_dicts: dict[str, CallGraph]) -> NeighborIndex:
    """Build upstream/downstream adjacency maps from one or more CFG dicts.

    Returns ``(upstream_index, downstream_index)`` where each maps a qualified
    method name to its list of unique callers / callees.  Single O(N+E) pass
    per CFG dict replaces the previous O(E)-per-lookup scan.
    """
    upstream: dict[str, set[str]] = defaultdict(set)
    downstream: dict[str, set[str]] = defaultdict(set)
    for cfgs in cfg_dicts:
        for cfg in cfgs.values():
            for qname, node in cfg.nodes.items():
                if node.methods_called_by_me:
                    downstream.setdefault(qname, set()).update(node.methods_called_by_me)
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
    """Return (upstream_callers, downstream_callees) for a method via pre-built indexes."""
    return upstream_index.get(qualified_name, []), downstream_index.get(qualified_name, [])


# ---------------------------------------------------------------------------
# Build change groups from delta
# ---------------------------------------------------------------------------
def _read_method_body_at_ref(
    repo_dir: Path,
    base_ref: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str | None:
    """Read a method body slice from *base_ref* using the same line window."""
    try:
        result = subprocess.run(
            ["git", "show", f"{base_ref}:{file_path}"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    lines = result.stdout.splitlines(keepends=True)
    if start_line < 1 or end_line > len(lines):
        return None
    return "".join(lines[start_line - 1 : end_line])


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
        return f"file:{file_path}", False
    return f"region:{region_id}", True


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


def _append_method_to_group(
    groups: dict[str, ChangeGroup],
    region_key: str,
    file_path: str,
    diff_text: str,
    ctx: ChangedMethodContext,
    upstream_neighbors: list[str],
    downstream_neighbors: list[str],
) -> None:
    group = groups.setdefault(region_key, ChangeGroup(group_key=region_key))
    if file_path not in group.file_paths:
        group.file_paths.append(file_path)
    if diff_text and file_path not in group.diff_hunks_by_file:
        group.diff_hunks_by_file[file_path] = diff_text
    if diff_text:
        group.diff_hunks = f"{group.diff_hunks}\n{diff_text}".strip() if group.diff_hunks else diff_text
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


def _collapse_groups_to_single_region(groups: list[ChangeGroup]) -> list[ChangeGroup]:
    """Collapse multiple groups into one conservative combined trace region."""
    if len(groups) <= 1:
        return groups

    combined = ChangeGroup(group_key="region:combined")
    for group in groups:
        combined.file_paths.extend(group.file_paths)
        combined.methods.extend(group.methods)
        combined.upstream_neighbors.extend(group.upstream_neighbors)
        combined.downstream_neighbors.extend(group.downstream_neighbors)
        for file_path, diff_text in group.diff_hunks_by_file.items():
            combined.diff_hunks_by_file.setdefault(file_path, diff_text)
    return _finalize_groups([combined])


def _build_trace_plan(
    delta: IncrementalDelta,
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
    repo_dir: Path,
    base_ref: str,
    parsed_diff: ParsedGitDiff | None = None,
) -> TracePlan:
    """Build grouped trace regions plus deterministic fast-path impact decisions."""
    groups: dict[str, ChangeGroup] = {}
    graph_metadata = _build_graph_region_metadata(upstream_index, downstream_index)

    cosmetic_skipped = 0
    extension_skipped = 0
    fast_path_impacted_methods: set[str] = set()
    saw_fallback_region = False

    for file_delta in delta.file_deltas:
        fp = file_delta.file_path

        ext = Path(fp).suffix.lower()
        if ext not in EXTENSION_TO_LANGUAGE:
            extension_skipped += 1
            continue

        all_methods = file_delta.added_methods + file_delta.modified_methods
        if not all_methods and file_delta.file_status != ChangeStatus.DELETED:
            continue

        if (
            file_delta.file_status == ChangeStatus.MODIFIED
            and file_delta.modified_methods
            and not file_delta.added_methods
            and not file_delta.deleted_methods
            and is_file_cosmetic(repo_dir, base_ref, fp)
        ):
            cosmetic_skipped += 1
            logger.info("Skipping cosmetic-only file: %s", fp)
            continue

        diff_text = (
            _get_diff_hunks(repo_dir, base_ref, fp, parsed_diff) if file_delta.file_status != ChangeStatus.ADDED else ""
        )

        for mc in all_methods:
            body = _read_method_body(repo_dir, fp, mc.start_line, mc.end_line)
            if body is not None:
                body = strip_comments_from_source(fp, body)
            up, down = _get_neighbors(upstream_index, downstream_index, mc.qualified_name)

            if mc.change_type == ChangeStatus.MODIFIED:
                old_body = _read_method_body_at_ref(repo_dir, base_ref, fp, mc.start_line, mc.end_line)
                if old_body is not None:
                    old_body = strip_comments_from_source(fp, old_body)
                semantically_unchanged, signature_changed = _compare_modified_method_versions(fp, old_body, body)
                if semantically_unchanged:
                    continue
                if (
                    file_delta.file_status == ChangeStatus.MODIFIED
                    and not file_delta.added_methods
                    and not file_delta.deleted_methods
                    and not signature_changed
                    and not up
                ):
                    fast_path_impacted_methods.add(mc.qualified_name)
                    continue

            ctx = ChangedMethodContext(
                qualified_name=mc.qualified_name,
                file_path=fp,
                change_type=mc.change_type,
                new_body=body,
            )
            region_key, graph_backed = _determine_region_key(mc.qualified_name, fp, graph_metadata)
            if not graph_backed:
                saw_fallback_region = True
            _append_method_to_group(groups, region_key, fp, diff_text, ctx, up, down)

        for mc in file_delta.deleted_methods:
            ctx = ChangedMethodContext(
                qualified_name=mc.qualified_name,
                file_path=fp,
                change_type=ChangeStatus.DELETED,
                new_body=None,
            )
            up, down = _get_neighbors(upstream_index, downstream_index, mc.qualified_name)
            region_key, graph_backed = _determine_region_key(mc.qualified_name, fp, graph_metadata)
            if not graph_backed:
                saw_fallback_region = True
            _append_method_to_group(groups, region_key, fp, diff_text, ctx, up, down)

    if extension_skipped:
        logger.info("Skipped %d file(s) with non-analyzable extensions", extension_skipped)
    if cosmetic_skipped:
        logger.info("Skipped %d cosmetic-only file(s) from tracing", cosmetic_skipped)

    finalized_groups = _finalize_groups(list(groups.values()))
    parallel_safe = len(finalized_groups) > 1 and not saw_fallback_region
    if len(finalized_groups) > 1 and saw_fallback_region:
        logger.info("Graph coverage incomplete for some changed methods; collapsing trace regions conservatively")
        finalized_groups = _collapse_groups_to_single_region(finalized_groups)

    return TracePlan(
        groups=finalized_groups,
        fast_path_impacted_methods=sorted(fast_path_impacted_methods),
        parallel_safe=parallel_safe and len(finalized_groups) > 1,
    )


def build_trace_plan(
    delta: IncrementalDelta,
    cfgs: dict[str, CallGraph],
    repo_dir: Path,
    base_ref: str,
    baseline_cfgs: dict[str, CallGraph] | None = None,
    parsed_diff: ParsedGitDiff | None = None,
) -> TracePlan:
    """Build a trace plan from the current and optional baseline call graphs."""
    index_args: list[dict[str, CallGraph]] = [cfgs]
    if baseline_cfgs is not None:
        index_args.append(baseline_cfgs)
    upstream_idx, downstream_idx = _build_neighbor_indexes(*index_args)
    return _build_trace_plan(delta, upstream_idx, downstream_idx, repo_dir, base_ref, parsed_diff)


def _build_change_groups(
    delta: IncrementalDelta,
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
    repo_dir: Path,
    base_ref: str,
    parsed_diff: ParsedGitDiff | None = None,
) -> list[ChangeGroup]:
    """Group changed methods into trace regions, excluding deterministic fast-path cases."""
    return _build_trace_plan(delta, upstream_index, downstream_index, repo_dir, base_ref, parsed_diff).groups


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
_TRACE_SYSTEM = """\
You are a semantic impact analyzer for software architecture diagrams.

Given changed methods and their call-graph neighbors, determine which methods
have their *semantic role or behavior* materially affected by the changes.
A method is impacted if its description in an architecture diagram would need
updating — not just because it calls or is called by a changed method.

You control traversal: request additional method bodies to inspect by name.
Stay within the budget. When you have enough information, stop.
"""


def _supports_anthropic_prompt_caching(llm: BaseChatModel) -> bool:
    """Return True when *llm* is a langchain Anthropic chat model."""
    return llm.__class__.__module__.startswith("langchain_anthropic")


def _trace_message_content(
    text: str,
    llm: BaseChatModel,
    *,
    enable_cache: bool = False,
) -> str | list[dict[str, Any]]:
    """Return message content, adding Anthropic cache metadata when enabled."""
    if not enable_cache or not _supports_anthropic_prompt_caching(llm):
        return text
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _build_initial_prompt(groups: list[ChangeGroup]) -> str:
    parts = ["# Changed Methods\n"]
    for group in groups:
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
            parts.append(f"Upstream callers: {', '.join(group.upstream_neighbors[:20])}")
        if group.downstream_neighbors:
            parts.append(f"Downstream callees: {', '.join(group.downstream_neighbors[:20])}")
        parts.append("")

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
def _trace_single_group(
    group: ChangeGroup,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
    parsing_llm: BaseChatModel,
    config: TraceConfig,
) -> TraceResult:
    """Run the semantic tracing loop for a single change region."""
    from trustcall import create_extractor

    resolver = MethodResolver(static_analysis, repo_dir)
    extractor = create_extractor(parsing_llm, tools=[TraceResponse], tool_choice=TraceResponse.__name__)

    prompt = _build_initial_prompt([group])
    caching_supported = _supports_anthropic_prompt_caching(parsing_llm)
    cached_turns = 1 if caching_supported else 0
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _TRACE_SYSTEM},
        {"role": "user", "content": _trace_message_content(prompt, parsing_llm, enable_cache=caching_supported)},
    ]

    all_impacted: set[str] = set()
    total_fetched = 0
    hops_used = 0

    for hop in range(config.max_hops + 1):
        # Invoke LLM with structured messages
        try:
            result = extractor.invoke({"messages": messages})  # type: ignore[arg-type]
            response: TraceResponse
            if "responses" in result and result["responses"]:
                response = TraceResponse.model_validate(result["responses"][0])
            else:
                logger.warning("Extractor returned no responses at hop %d; stopping", hop)
                break
        except Exception as exc:
            logger.error("Trace LLM call failed at hop %d: %s", hop, exc)
            break

        # Accumulate impacted methods
        all_impacted.update(response.impacted_methods)

        logger.info(
            "Trace region %s hop %d: status=%s impacted=%d next=%d reason=%s",
            group.group_key,
            hop,
            response.status,
            len(all_impacted),
            len(response.next_methods_to_fetch),
            response.reason[:80],
        )
        if response.status == TraceStopReason.CLOSURE_REACHED and response.semantic_impact_summary:
            logger.info(
                "Trace semantic impact summary for %s: %s", group.group_key, response.semantic_impact_summary[:200]
            )

        # Check stop conditions
        if response.status != TraceStopReason.CONTINUE:
            hops_used = hop
            return TraceResult(
                all_impacted_methods=sorted(all_impacted),
                unresolved_frontier=resolver.unresolved + response.unresolved_frontier,
                stop_reason=response.status,
                hops_used=hops_used,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        # Determine how many methods we can still fetch
        remaining = config.max_fetched_methods - total_fetched
        to_fetch = response.next_methods_to_fetch[:remaining]

        if not to_fetch:
            logger.info("Fetch budget exhausted at hop %d", hop)
            return TraceResult(
                all_impacted_methods=sorted(all_impacted),
                unresolved_frontier=resolver.unresolved + response.unresolved_frontier,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hops_used,
            )

        # Fetch requested methods — only resolved ones count against budget
        fetched: dict[str, str | None] = {}
        for qname in to_fetch:
            _, body = resolver.resolve(qname)
            fetched[qname] = body

        resolved_count = sum(1 for b in fetched.values() if b is not None)
        total_fetched += resolved_count

        # Build continuation prompt
        cont_prompt = _build_continuation_prompt(fetched, response)
        use_cache = caching_supported and cached_turns < 4
        messages.append({"role": "assistant", "content": response.llm_str()})
        messages.append(
            {"role": "user", "content": _trace_message_content(cont_prompt, parsing_llm, enable_cache=use_cache)}
        )
        if use_cache:
            cached_turns += 1

    # Fell through max hops
    return TraceResult(
        all_impacted_methods=sorted(all_impacted),
        unresolved_frontier=resolver.unresolved,
        stop_reason=TraceStopReason.UNCERTAIN,
        hops_used=hop + 1,
    )


def _merge_trace_results(
    trace_results: list[TraceResult],
    fast_path_impacted_methods: list[str] | None = None,
) -> TraceResult:
    """Merge trace results from independent regions plus deterministic fast-path decisions."""
    all_impacted = set(fast_path_impacted_methods or [])
    unresolved_frontier: set[str] = set()
    hops_used = 0
    saw_uncertain = False
    saw_impact = bool(all_impacted)
    semantic_summaries: set[str] = set()

    for result in trace_results:
        all_impacted.update(result.all_impacted_methods)
        unresolved_frontier.update(result.unresolved_frontier)
        hops_used = max(hops_used, result.hops_used)
        if result.stop_reason == TraceStopReason.UNCERTAIN:
            saw_uncertain = True
        if result.stop_reason == TraceStopReason.CLOSURE_REACHED or result.all_impacted_methods:
            saw_impact = True
        if result.semantic_impact_summary:
            semantic_summaries.add(result.semantic_impact_summary)

    if saw_uncertain:
        stop_reason = TraceStopReason.UNCERTAIN
    elif saw_impact:
        stop_reason = TraceStopReason.CLOSURE_REACHED
    else:
        stop_reason = TraceStopReason.NO_MATERIAL_IMPACT

    return TraceResult(
        all_impacted_methods=sorted(all_impacted),
        unresolved_frontier=sorted(unresolved_frontier),
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
    agent_llm: BaseChatModel,
    parsing_llm: BaseChatModel,
    config: TraceConfig | None = None,
    baseline_cfgs: dict[str, CallGraph] | None = None,
    parsed_diff: ParsedGitDiff | None = None,
) -> TraceResult:
    """Run the semantic tracing loop over changed methods.

    Returns a TraceResult with impacted methods and components (unmapped —
    scope classification happens in the orchestrator).

    *baseline_cfgs*, when provided, supplies neighbor data for deleted methods
    that are no longer present in the current *cfgs*.
    """
    del agent_llm  # reserved for future routing; tracing currently uses parsing_llm

    cfg = config or TraceConfig()

    # Gate: abort if any analyzable changed file has syntax errors
    for file_delta in delta.file_deltas:
        if file_delta.file_status == ChangeStatus.DELETED:
            continue
        ext = Path(file_delta.file_path).suffix.lower()
        if ext not in EXTENSION_TO_LANGUAGE:
            continue
        errors = check_syntax_errors(repo_dir, file_delta.file_path)
        if errors:
            error_locations = ", ".join(f"line {line}:{col}" for line, col in errors)
            logger.error(
                "Syntax errors in %s at %s; aborting incremental trace — "
                "tree-sitter detected parse errors that would produce unreliable analysis",
                file_delta.file_path,
                error_locations,
            )
            return TraceResult(stop_reason=TraceStopReason.SYNTAX_ERROR)

    trace_plan = build_trace_plan(
        delta=delta,
        cfgs=cfgs,
        repo_dir=repo_dir,
        base_ref=base_ref,
        baseline_cfgs=baseline_cfgs,
        parsed_diff=parsed_diff,
    )
    if trace_plan.fast_path_impacted_methods:
        logger.info(
            "Using deterministic fast path for %d changed method(s)",
            len(trace_plan.fast_path_impacted_methods),
        )

    if not trace_plan.groups:
        if trace_plan.fast_path_impacted_methods:
            return TraceResult(
                all_impacted_methods=trace_plan.fast_path_impacted_methods,
                stop_reason=TraceStopReason.CLOSURE_REACHED,
            )
        logger.info("No traceable changed methods; skipping trace")
        return TraceResult()

    if trace_plan.parallel_safe:
        logger.info("Tracing %d independent change regions in parallel", len(trace_plan.groups))
        trace_results: list[TraceResult] = []
        with ThreadPoolExecutor(max_workers=min(len(trace_plan.groups), 8)) as executor:
            futures = {
                executor.submit(
                    _trace_single_group, group, static_analysis, repo_dir, parsing_llm, cfg
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
            _trace_single_group(group, static_analysis, repo_dir, parsing_llm, cfg) for group in trace_plan.groups
        ]

    return _merge_trace_results(trace_results, trace_plan.fast_path_impacted_methods)


# ---------------------------------------------------------------------------
# Scope classification (step 9): map impacted methods to components
# ---------------------------------------------------------------------------
def classify_scope(
    trace_result: TraceResult,
    file_component_index: FileComponentIndex,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
) -> TraceResult:
    """Deterministically map impacted methods to components using file index.

    Mutates trace_result.impacted_components and returns it.
    """
    component_methods: dict[str, list[str]] = {}

    for qname in trace_result.all_impacted_methods:
        # Resolve method to file
        file_path = _resolve_method_file(qname, static_analysis)
        if file_path is None:
            continue
        try:
            file_path = Path(file_path).resolve().relative_to(repo_dir.resolve()).as_posix()
        except ValueError:
            file_path = file_path.lstrip("./")
        comp_id = file_component_index.get_component_for_file(file_path)
        if comp_id is None:
            continue
        component_methods.setdefault(comp_id, []).append(qname)

    trace_result.impacted_components = [
        ImpactedComponent(component_id=cid, impacted_methods=methods)
        for cid, methods in sorted(component_methods.items())
    ]
    return trace_result


def _resolve_method_file(qualified_name: str, static_analysis: StaticAnalysisResults) -> str | None:
    for lang in static_analysis.get_languages():
        try:
            node = static_analysis.get_reference(lang, qualified_name)
            return node.file_path
        except (ValueError, FileExistsError):
            _, node = static_analysis.get_loose_reference(lang, qualified_name)
            if node is not None:
                return node.file_path
    return None
