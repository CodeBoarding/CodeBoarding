"""Trace-based semantic impact analysis for incremental updates.

Given a set of changed methods (from git diff), traces forward through the
call graph to determine which methods' *semantic descriptions* are affected.
The LLM controls traversal inside bounded budgets; the system fetches code
blocks via symbol-table lookup.
"""

import logging
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from agents.change_status import ChangeStatus
from diagram_analysis.semantic_diff import is_file_cosmetic, strip_comments_from_source
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
    """A group of related changed methods (same file/component)."""

    group_key: str  # file_path or component_id
    methods: list[ChangedMethodContext] = field(default_factory=list)
    upstream_neighbors: list[str] = field(default_factory=list)
    downstream_neighbors: list[str] = field(default_factory=list)
    diff_hunks: str = ""


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
def _build_change_groups(
    delta: IncrementalDelta,
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
    repo_dir: Path,
    base_ref: str,
    parsed_diff: ParsedGitDiff | None = None,
) -> list[ChangeGroup]:
    """Group changed methods by file and attach neighbor metadata."""
    groups: dict[str, ChangeGroup] = {}

    cosmetic_skipped = 0
    for file_delta in delta.file_deltas:
        fp = file_delta.file_path
        all_methods = file_delta.added_methods + file_delta.modified_methods
        if not all_methods and file_delta.file_status != ChangeStatus.DELETED:
            continue

        # Skip files whose changes are purely cosmetic (formatting, comments, renames)
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

        group = groups.setdefault(fp, ChangeGroup(group_key=fp, diff_hunks=diff_text))
        for mc in all_methods:
            body = _read_method_body(repo_dir, fp, mc.start_line, mc.end_line)
            if body is not None:
                body = strip_comments_from_source(fp, body)
            ctx = ChangedMethodContext(
                qualified_name=mc.qualified_name,
                file_path=fp,
                change_type=mc.change_type,
                new_body=body,
            )
            group.methods.append(ctx)
            up, down = _get_neighbors(upstream_index, downstream_index, mc.qualified_name)
            group.upstream_neighbors.extend(up)
            group.downstream_neighbors.extend(down)

        # Deleted methods: include in context but mark as deleted
        for mc in file_delta.deleted_methods:
            ctx = ChangedMethodContext(
                qualified_name=mc.qualified_name,
                file_path=fp,
                change_type=ChangeStatus.DELETED,
                new_body=None,
            )
            group.methods.append(ctx)
            up, down = _get_neighbors(upstream_index, downstream_index, mc.qualified_name)
            group.upstream_neighbors.extend(up)
            group.downstream_neighbors.extend(down)

        group.upstream_neighbors = list(set(group.upstream_neighbors))
        group.downstream_neighbors = list(set(group.downstream_neighbors))

    if cosmetic_skipped:
        logger.info("Skipped %d cosmetic-only file(s) from tracing", cosmetic_skipped)

    return list(groups.values())


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
        parts.append(f"## File: {group.group_key}\n")
        for m in group.methods:
            parts.append(f"### {m.qualified_name} ({m.change_type})")
            if m.new_body:
                parts.append(f"```\n{m.new_body}\n```")
        if group.diff_hunks:
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
    from trustcall import create_extractor

    cfg = config or TraceConfig()

    # Build neighbor indexes once — O(N+E) total
    index_args: list[dict[str, CallGraph]] = [cfgs]
    if baseline_cfgs is not None:
        index_args.append(baseline_cfgs)
    upstream_idx, downstream_idx = _build_neighbor_indexes(*index_args)

    groups = _build_change_groups(delta, upstream_idx, downstream_idx, repo_dir, base_ref, parsed_diff)

    if not groups:
        logger.info("No traceable changed methods; skipping trace")
        return TraceResult()

    resolver = MethodResolver(static_analysis, repo_dir)
    extractor = create_extractor(parsing_llm, tools=[TraceResponse], tool_choice=TraceResponse.__name__)

    # Initial prompt
    prompt = _build_initial_prompt(groups)
    caching_supported = _supports_anthropic_prompt_caching(parsing_llm)
    cached_turns = 1 if caching_supported else 0
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _TRACE_SYSTEM},
        {"role": "user", "content": _trace_message_content(prompt, parsing_llm, enable_cache=caching_supported)},
    ]

    all_impacted: set[str] = set()
    total_fetched = 0
    hops_used = 0

    for hop in range(cfg.max_hops + 1):
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
            "Trace hop %d: status=%s impacted=%d next=%d reason=%s",
            hop,
            response.status,
            len(all_impacted),
            len(response.next_methods_to_fetch),
            response.reason[:80],
        )
        if response.status == TraceStopReason.CLOSURE_REACHED and response.semantic_impact_summary:
            logger.info("Trace semantic impact summary: %s", response.semantic_impact_summary[:200])

        # Check stop conditions
        if response.status != TraceStopReason.CONTINUE:
            hops_used = hop
            return TraceResult(
                all_impacted_methods=sorted(all_impacted),
                unresolved_frontier=resolver.unresolved + response.unresolved_frontier,
                stop_reason=response.status,
                hops_used=hops_used,
            )

        # Determine how many methods we can still fetch
        remaining = cfg.max_fetched_methods - total_fetched
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
        hops_used=hop + 1 if groups else 0,
    )


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
