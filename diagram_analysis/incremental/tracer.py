"""Trace-based semantic impact analysis for incremental updates.

Given a set of changed methods (from git diff), traces forward through the
call graph to determine which methods' *semantic descriptions* are affected.
The LLM controls traversal inside bounded budgets; the system fetches code
blocks via symbol-table lookup.
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from trustcall import create_extractor

from agents.change_status import ChangeStatus
from agents.llm_config import supports_prompt_caching
from agents.prompts.prompt_factory import get_trace_system_message
from agents.retry import RetryAction, RetryDecision, default_backoff, with_retries
from diagram_analysis.incremental.delta import IncrementalDelta
from diagram_analysis.incremental.models import (
    DEFAULT_TRACE_CONFIG,
    ImpactedComponent,
    TraceConfig,
    TraceResponse,
    TraceResult,
    TraceStopReason,
)
from diagram_analysis.incremental.trace_planner import (
    ChangedMethodContext,
    ChangeGroup,
    TracePlan,
    _read_method_body,
    build_trace_plan,
)
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node
from diagram_analysis.incremental.semantic_diff import (
    check_syntax_errors,
    strip_comments_from_source,
)

logger = logging.getLogger(__name__)


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
        """Resolve a method name to its Node and source body."""
        node = self._static.resolve_across_languages(qualified_name)
        if node is None:
            self._unresolved.append(qualified_name)
            logger.warning("Unresolved method during tracing: %s", qualified_name)
            return None, None
        body = _read_method_body(self._repo_dir, node.file_path, node.line_start, node.line_end)
        if body is not None:
            body = strip_comments_from_source(node.file_path, body)
        return node, body

    @property
    def unresolved(self) -> list[str]:
        return list(self._unresolved)


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


def _build_initial_prompt(groups: list[ChangeGroup], max_neighbor_preview: int) -> str:
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
            parts.append(f"Upstream callers: {', '.join(group.upstream_neighbors[:max_neighbor_preview])}")
        if group.downstream_neighbors:
            parts.append(f"Downstream callees: {', '.join(group.downstream_neighbors[:max_neighbor_preview])}")
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
def _invoke_extractor_with_retry(
    extractor: Any,
    messages: list[dict[str, Any]],
    config: TraceConfig,
    *,
    hop: int,
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
        lambda: extractor.invoke({"messages": messages}),
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
) -> TraceResult:
    """Run the semantic tracing loop for a single change region."""
    resolver = MethodResolver(static_analysis, repo_dir)
    extractor = create_extractor(parsing_llm, tools=[TraceResponse], tool_choice=TraceResponse.__name__)

    prompt = _build_initial_prompt([group], config.max_neighbor_preview)
    caching_supported = supports_prompt_caching(parsing_llm)
    cached_turns = 1 if caching_supported else 0
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": get_trace_system_message()},
        {"role": "user", "content": _trace_message_content(prompt, parsing_llm, enable_cache=caching_supported)},
    ]

    all_impacted: set[str] = set()
    total_fetched = 0

    # `hop` is the loop index and the single authoritative value reported on return.
    for hop in range(config.max_hops + 1):
        result = _invoke_extractor_with_retry(extractor, messages, config, hop=hop)
        if result is None:
            return TraceResult(
                all_impacted_methods=sorted(all_impacted),
                unresolved_frontier=resolver.unresolved,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hop,
            )

        if "responses" not in result or not result["responses"]:
            logger.warning("Extractor returned no responses at hop %d; stopping", hop)
            return TraceResult(
                all_impacted_methods=sorted(all_impacted),
                unresolved_frontier=resolver.unresolved,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hop,
            )

        response = TraceResponse.model_validate(result["responses"][0])
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
                "Trace semantic impact summary for %s: %s",
                group.group_key,
                response.semantic_impact_summary[:200],
            )

        if response.status != TraceStopReason.CONTINUE:
            return TraceResult(
                all_impacted_methods=sorted(all_impacted),
                unresolved_frontier=resolver.unresolved + response.unresolved_frontier,
                stop_reason=response.status,
                hops_used=hop,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        remaining = config.max_fetched_methods - total_fetched
        to_fetch = response.next_methods_to_fetch[:remaining]

        if not to_fetch:
            logger.info("Fetch budget exhausted at hop %d", hop)
            return TraceResult(
                all_impacted_methods=sorted(all_impacted),
                unresolved_frontier=resolver.unresolved + response.unresolved_frontier,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hop,
            )

        fetched: dict[str, str | None] = {}
        for qname in to_fetch:
            _, body = resolver.resolve(qname)
            fetched[qname] = body

        resolved_count = sum(1 for b in fetched.values() if b is not None)
        total_fetched += resolved_count

        cont_prompt = _build_continuation_prompt(fetched, response)
        use_cache = caching_supported and cached_turns < config.max_cached_turns
        messages.append({"role": "assistant", "content": response.llm_str()})
        messages.append(
            {"role": "user", "content": _trace_message_content(cont_prompt, parsing_llm, enable_cache=use_cache)}
        )
        if use_cache:
            cached_turns += 1

    return TraceResult(
        all_impacted_methods=sorted(all_impacted),
        unresolved_frontier=resolver.unresolved,
        stop_reason=TraceStopReason.UNCERTAIN,
        hops_used=config.max_hops,
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
    parsing_llm: BaseChatModel,
    change_set: ChangeSet,
    config: TraceConfig = DEFAULT_TRACE_CONFIG,
) -> TraceResult:
    """Run the semantic tracing loop over changed methods.

    Returns a TraceResult with impacted methods. Scope classification
    (impacted_components) happens in the orchestrator via ``classify_scope``.
    """
    for file_delta in delta.file_deltas:
        if file_delta.file_status == ChangeStatus.DELETED:
            continue
        # Defense in depth: change_set filters unsupported extensions upstream.
        ext = Path(file_delta.file_path).suffix.lower()
        if ext not in SOURCE_EXTENSION_TO_LANGUAGE:
            continue
        errors = check_syntax_errors(repo_dir, file_delta.file_path)
        if errors:
            error_locations = ", ".join(f"line {line}:{col}" for line, col in errors)
            logger.error(
                "Syntax errors in %s at %s; aborting incremental trace",
                file_delta.file_path,
                error_locations,
            )
            return TraceResult(stop_reason=TraceStopReason.SYNTAX_ERROR)

    trace_plan: TracePlan = build_trace_plan(
        delta=delta,
        cfgs=cfgs,
        repo_dir=repo_dir,
        base_ref=base_ref,
        change_set=change_set,
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
        if trace_plan.cosmetic_skipped:
            logger.info("All changed files were cosmetic-only; skipping trace")
            return TraceResult(stop_reason=TraceStopReason.COSMETIC_ONLY)
        logger.info("No traceable changed methods; skipping trace")
        return TraceResult()

    if len(trace_plan.groups) > 1:
        max_workers = min(len(trace_plan.groups), config.max_parallel_regions)
        logger.info(
            "Tracing %d independent change regions in parallel (workers=%d)", len(trace_plan.groups), max_workers
        )
        trace_results: list[TraceResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _trace_single_group, group, static_analysis, repo_dir, parsing_llm, config
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
            _trace_single_group(group, static_analysis, repo_dir, parsing_llm, config) for group in trace_plan.groups
        ]

    return _merge_trace_results(trace_results, trace_plan.fast_path_impacted_methods)


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

    Uses a plain ``dict[str, str]`` (produced by ``AnalysisInsights.file_to_component()``).

    Mutates trace_result.impacted_components and returns it.
    """
    component_methods: dict[str, list[str]] = {}

    for qname in trace_result.all_impacted_methods:
        node = static_analysis.resolve_across_languages(qname)
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
