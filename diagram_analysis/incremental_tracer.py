import logging
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables.config import RunnableConfig
from trustcall import create_extractor

from agents.change_status import ChangeStatus
from diagram_analysis.incremental_models import (
    TraceResponse,
    TraceResult,
    TraceStopReason,
)
from diagram_analysis.incremental_updater import IncrementalDelta
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph
from static_analyzer.graph_query import (
    GraphRegionMetadata,
    build_graph_region_metadata,
    build_neighbor_indexes,
    determine_region_key,
    get_neighbors,
    resolve_method_node,
)
from static_analyzer.cosmetic_diff import (
    check_syntax_errors,
    is_file_cosmetic,
    strip_comments_from_source,
)
from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE
from static_analyzer.method_fingerprint import fingerprint_method_signature, fingerprint_source_text

logger = logging.getLogger(__name__)

_MAX_HOPS = 3
_MAX_FETCHED_METHODS = 30
_MAX_PARALLEL_REGIONS = 4
_MAX_NEIGHBOR_PREVIEW = 8


def _read_method_body(repo_dir: Path, file_path: str, start_line: int, end_line: int) -> str | None:
    full_path = repo_dir / file_path
    if not full_path.is_file():
        return None
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as file_handle:
            lines = file_handle.readlines()
    except OSError:
        return None
    if start_line < 1 or end_line > len(lines):
        return None
    return "".join(lines[start_line - 1 : end_line])


def _read_method_body_at_ref(
    repo_dir: Path,
    base_ref: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str | None:
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


def _get_diff_hunks(
    repo_dir: Path,
    base_ref: str,
    file_path: str,
    parsed_diff: Any | None = None,
) -> str:
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
class TracePlan:
    """Planned trace work after deterministic filtering and grouping."""

    groups: list[ChangeGroup] = field(default_factory=list)
    fast_path_impacted_methods: list[str] = field(default_factory=list)
    disconnected_files: list[str] = field(default_factory=list)
    parallel_safe: bool = False


class MethodResolver:
    """Resolves method names to source bodies via static analysis references."""

    def __init__(self, static_analysis: StaticAnalysisResults, repo_dir: Path):
        self._static_analysis = static_analysis
        self._repo_dir = repo_dir
        self.unresolved: list[str] = []

    def resolve(self, qualified_name: str) -> tuple[str | None, str | None]:
        node = resolve_method_node(self._static_analysis, qualified_name)
        if node is None:
            self.unresolved.append(qualified_name)
            logger.warning("Unresolved method during tracing: %s", qualified_name)
            return None, None

        body = _read_method_body(self._repo_dir, node.file_path, node.line_start, node.line_end)
        if body is not None:
            body = strip_comments_from_source(node.file_path, body)
        return node.fully_qualified_name, body


def _compare_modified_method_versions(
    file_path: str,
    old_body: str | None,
    new_body: str | None,
) -> tuple[bool, bool]:
    if old_body is None or new_body is None:
        return False, True

    if fingerprint_source_text(file_path, old_body) == fingerprint_source_text(file_path, new_body):
        return True, False

    old_signature = fingerprint_method_signature(file_path, old_body)
    new_signature = fingerprint_method_signature(file_path, new_body)
    if old_signature is None or new_signature is None:
        return False, True
    return False, old_signature != new_signature


def _append_method_to_group(
    groups: dict[str, ChangeGroup],
    region_key: str,
    file_path: str,
    diff_text: str,
    context: ChangedMethodContext,
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
    group.methods.append(context)
    group.upstream_neighbors.extend(upstream_neighbors)
    group.downstream_neighbors.extend(downstream_neighbors)


def _finalize_groups(groups: list[ChangeGroup]) -> list[ChangeGroup]:
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
    combined.diff_hunks = "\n".join(text for text in combined.diff_hunks_by_file.values() if text).strip()
    return _finalize_groups([combined])


def _build_trace_plan(
    delta: IncrementalDelta,
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
    repo_dir: Path,
    base_ref: str,
    *,
    parsed_diff: Any | None = None,
    excluded_files: set[str] | None = None,
) -> TracePlan:
    groups: dict[str, ChangeGroup] = {}
    graph_metadata: GraphRegionMetadata = build_graph_region_metadata(upstream_index, downstream_index)
    disconnected_files: set[str] = set()
    fast_path_impacted_methods: set[str] = set()
    saw_file_fallback = False

    for file_delta in delta.file_deltas:
        file_path = file_delta.file_path
        if excluded_files and file_path in excluded_files:
            continue

        diff_text = (
            ""
            if file_delta.file_status == ChangeStatus.ADDED
            else _get_diff_hunks(repo_dir, base_ref, file_path, parsed_diff)
        )
        all_methods = file_delta.added_methods + file_delta.modified_methods

        if (
            Path(file_path).suffix.lower() in SOURCE_EXTENSION_TO_LANGUAGE
            and file_delta.file_status == ChangeStatus.MODIFIED
            and file_delta.modified_methods
            and not file_delta.added_methods
            and not file_delta.deleted_methods
            and is_file_cosmetic(repo_dir, base_ref, file_path)
        ):
            logger.info("Skipping cosmetic-only file from tracing: %s", file_path)
            continue

        for method_change in all_methods:
            body = _read_method_body(repo_dir, file_path, method_change.start_line, method_change.end_line)
            if body is not None:
                body = strip_comments_from_source(file_path, body)

            upstream_neighbors, downstream_neighbors = get_neighbors(
                upstream_index,
                downstream_index,
                method_change.qualified_name,
            )

            if method_change.change_type == ChangeStatus.MODIFIED:
                old_start = method_change.old_start_line or method_change.start_line
                old_end = method_change.old_end_line or method_change.end_line
                old_body = _read_method_body_at_ref(
                    repo_dir,
                    base_ref,
                    file_path,
                    old_start,
                    old_end,
                )
                if old_body is not None:
                    old_body = strip_comments_from_source(file_path, old_body)

                semantically_unchanged, signature_changed = _compare_modified_method_versions(file_path, old_body, body)
                if semantically_unchanged:
                    continue
                if not signature_changed and not upstream_neighbors and not downstream_neighbors:
                    fast_path_impacted_methods.add(method_change.qualified_name)
                    continue

            context = ChangedMethodContext(
                qualified_name=method_change.qualified_name,
                file_path=file_path,
                change_type=method_change.change_type,
                new_body=body,
            )
            region_key, graph_backed = determine_region_key(method_change.qualified_name, file_path, graph_metadata)
            if not graph_backed:
                saw_file_fallback = True
                if file_delta.file_status == ChangeStatus.ADDED:
                    disconnected_files.add(file_path)
            _append_method_to_group(
                groups,
                region_key,
                file_path,
                diff_text,
                context,
                upstream_neighbors,
                downstream_neighbors,
            )

        for method_change in file_delta.deleted_methods:
            upstream_neighbors, downstream_neighbors = get_neighbors(
                upstream_index,
                downstream_index,
                method_change.qualified_name,
            )
            context = ChangedMethodContext(
                qualified_name=method_change.qualified_name,
                file_path=file_path,
                change_type=ChangeStatus.DELETED,
                new_body=None,
            )
            region_key, graph_backed = determine_region_key(method_change.qualified_name, file_path, graph_metadata)
            if not graph_backed:
                saw_file_fallback = True
            _append_method_to_group(
                groups,
                region_key,
                file_path,
                diff_text,
                context,
                upstream_neighbors,
                downstream_neighbors,
            )

    finalized_groups = _finalize_groups(list(groups.values()))
    parallel_safe = len(finalized_groups) > 1 and not saw_file_fallback
    if len(finalized_groups) > 1 and saw_file_fallback:
        finalized_groups = _collapse_groups_to_single_region(finalized_groups)

    return TracePlan(
        groups=finalized_groups,
        fast_path_impacted_methods=sorted(fast_path_impacted_methods),
        disconnected_files=sorted(disconnected_files),
        parallel_safe=parallel_safe,
    )


def build_trace_plan(
    delta: IncrementalDelta,
    cfgs: dict[str, CallGraph],
    repo_dir: Path,
    base_ref: str,
    *,
    parsed_diff: Any | None = None,
    excluded_files: set[str] | None = None,
) -> TracePlan:
    upstream_index, downstream_index = build_neighbor_indexes(cfgs)
    return _build_trace_plan(
        delta,
        upstream_index,
        downstream_index,
        repo_dir,
        base_ref,
        parsed_diff=parsed_diff,
        excluded_files=excluded_files,
    )


_TRACE_SYSTEM = """\
You are a semantic impact analyzer for software architecture diagrams.

Given changed methods and their call-graph neighbors, determine which methods
have their semantic role or behavior materially affected by the changes.
A method is impacted if its description in an architecture diagram would need
updating, not just because it calls or is called by a changed method.

You control traversal: request additional method bodies to inspect by name.
Stay within the budget. When you have enough information, stop.
"""


def _build_initial_prompt(group: ChangeGroup) -> str:
    parts = ["# Changed Methods\n"]
    methods_by_file: dict[str, list[ChangedMethodContext]] = defaultdict(list)
    for method in group.methods:
        methods_by_file[method.file_path].append(method)

    if len(group.file_paths) == 1:
        parts.append(f"## File: {group.file_paths[0]}\n")
    else:
        parts.append(f"## Region: {group.group_key}")
        parts.append(f"Files: {', '.join(group.file_paths)}")

    for file_path in group.file_paths:
        if len(group.file_paths) > 1:
            parts.append(f"### File: {file_path}")
        for method in methods_by_file.get(file_path, []):
            parts.append(f"### {method.qualified_name} ({method.change_type})")
            if method.new_body:
                parts.append(f"```\n{method.new_body}\n```")
        diff_text = group.diff_hunks_by_file.get(file_path, "")
        if diff_text:
            parts.append(f"Diff:\n```diff\n{diff_text}\n```")

    if group.upstream_neighbors:
        parts.append(f"Upstream callers: {', '.join(group.upstream_neighbors[:_MAX_NEIGHBOR_PREVIEW])}")
    if group.downstream_neighbors:
        parts.append(f"Downstream callees: {', '.join(group.downstream_neighbors[:_MAX_NEIGHBOR_PREVIEW])}")
    parts.append(
        "Respond with:\n"
        "- status: continue or a stop reason\n"
        "- impacted_methods: methods whose diagram description needs updating\n"
        "- next_methods_to_fetch: methods to inspect next if continuing\n"
        "- reason: brief explanation\n"
        "- semantic_impact_summary: one high-level sentence only when material impact exists\n"
        "- confidence: 0.0-1.0\n"
    )
    return "\n".join(parts)


def _build_continuation_prompt(
    fetched_bodies: dict[str, str | None],
    previous_response: TraceResponse,
) -> str:
    parts = ["# Additional Method Bodies\n"]
    for qualified_name, body in fetched_bodies.items():
        parts.append(f"## {qualified_name}")
        if body:
            parts.append(f"```\n{body}\n```")
        else:
            parts.append("(could not resolve)")
        parts.append("")

    parts.append(
        f"Previous assessment: {previous_response.reason}\n"
        f"Previously identified impacted methods: {', '.join(previous_response.impacted_methods)}\n\n"
        "Continue the analysis with the additional context above.\n"
        "Update impacted_methods cumulatively and either request more methods or stop.\n"
    )
    return "\n".join(parts)


def _trace_single_group(
    group: ChangeGroup,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
    parsing_llm: BaseChatModel,
    callbacks: list | None = None,
) -> TraceResult:
    resolver = MethodResolver(static_analysis, repo_dir)
    extractor = create_extractor(parsing_llm, tools=[TraceResponse], tool_choice=TraceResponse.__name__)
    invoke_config: RunnableConfig = {"callbacks": callbacks} if callbacks else {}

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _TRACE_SYSTEM},
        {"role": "user", "content": _build_initial_prompt(group)},
    ]

    impacted_methods: set[str] = set()
    visited_methods: set[str] = {method.qualified_name for method in group.methods}
    total_fetched = 0

    for hop in range(_MAX_HOPS + 1):
        try:
            result = extractor.invoke({"messages": messages}, config=invoke_config)
        except Exception as exc:
            logger.error("Trace LLM call failed at hop %d for %s: %s", hop, group.group_key, exc)
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=resolver.unresolved,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hop,
            )

        if "responses" not in result or not result["responses"]:
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=resolver.unresolved,
                stop_reason=TraceStopReason.UNCERTAIN,
                hops_used=hop,
            )

        response = TraceResponse.model_validate(result["responses"][0])
        impacted_methods.update(response.impacted_methods)

        if response.status != TraceStopReason.CONTINUE:
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=sorted(set(resolver.unresolved + response.unresolved_frontier)),
                stop_reason=response.status,
                hops_used=hop,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        remaining_budget = _MAX_FETCHED_METHODS - total_fetched
        next_methods = response.next_methods_to_fetch[:remaining_budget]
        if not next_methods:
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=sorted(set(resolver.unresolved + response.unresolved_frontier)),
                stop_reason=TraceStopReason.BUDGET_EXHAUSTED,
                hops_used=hop,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        fetched_bodies: dict[str, str | None] = {}
        for qualified_name in next_methods:
            visited_methods.add(qualified_name)
            resolved_name, body = resolver.resolve(qualified_name)
            if resolved_name is not None:
                visited_methods.add(resolved_name)
            fetched_bodies[qualified_name] = body
        total_fetched += sum(1 for body in fetched_bodies.values() if body is not None)

        if not any(body is not None for body in fetched_bodies.values()):
            return TraceResult(
                visited_methods=sorted(visited_methods),
                impacted_methods=sorted(impacted_methods),
                unresolved_frontier=sorted(set(resolver.unresolved + response.unresolved_frontier)),
                stop_reason=TraceStopReason.FRONTIER_EXHAUSTED,
                hops_used=hop,
                semantic_impact_summary=response.semantic_impact_summary,
            )

        messages.append({"role": "assistant", "content": response.llm_str()})
        messages.append({"role": "user", "content": _build_continuation_prompt(fetched_bodies, response)})

    return TraceResult(
        visited_methods=sorted(visited_methods),
        impacted_methods=sorted(impacted_methods),
        unresolved_frontier=resolver.unresolved,
        stop_reason=TraceStopReason.BUDGET_EXHAUSTED,
        hops_used=_MAX_HOPS,
    )


def _merge_trace_results(
    trace_results: list[TraceResult],
    *,
    fast_path_impacted_methods: list[str] | None = None,
    disconnected_files: list[str] | None = None,
    non_traceable_files: list[str] | None = None,
) -> TraceResult:
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
        if severity[result.stop_reason] > severity[stop_reason]:
            stop_reason = result.stop_reason
        if result.semantic_impact_summary:
            semantic_summaries.add(result.semantic_impact_summary)

    if impacted_methods and stop_reason == TraceStopReason.NO_MATERIAL_IMPACT:
        stop_reason = TraceStopReason.CLOSURE_REACHED
    if not trace_results and impacted_methods:
        stop_reason = TraceStopReason.CLOSURE_REACHED
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
    callbacks: list | None = None,
) -> TraceResult:
    non_traceable_files: set[str] = set()
    for file_delta in delta.file_deltas:
        if file_delta.file_status == ChangeStatus.DELETED:
            continue
        if Path(file_delta.file_path).suffix.lower() not in SOURCE_EXTENSION_TO_LANGUAGE:
            continue
        if check_syntax_errors(repo_dir, file_delta.file_path):
            non_traceable_files.add(file_delta.file_path)

    trace_plan = build_trace_plan(
        delta,
        cfgs,
        repo_dir,
        base_ref,
        parsed_diff=parsed_diff,
        excluded_files=non_traceable_files,
    )

    if not trace_plan.groups:
        return _merge_trace_results(
            [],
            fast_path_impacted_methods=trace_plan.fast_path_impacted_methods,
            disconnected_files=trace_plan.disconnected_files,
            non_traceable_files=sorted(non_traceable_files),
        )

    if trace_plan.parallel_safe:
        trace_results: list[TraceResult] = []
        with ThreadPoolExecutor(max_workers=min(len(trace_plan.groups), _MAX_PARALLEL_REGIONS)) as executor:
            futures = {
                executor.submit(_trace_single_group, group, static_analysis, repo_dir, parsing_llm, callbacks): group
                for group in trace_plan.groups
            }
            for future in as_completed(futures):
                group = futures[future]
                try:
                    trace_results.append(future.result())
                except Exception as exc:
                    logger.error("Trace region %s failed: %s", group.group_key, exc)
                    trace_results.append(TraceResult(stop_reason=TraceStopReason.UNCERTAIN))
    else:
        trace_results = [
            _trace_single_group(group, static_analysis, repo_dir, parsing_llm, callbacks) for group in trace_plan.groups
        ]

    return _merge_trace_results(
        trace_results,
        fast_path_impacted_methods=trace_plan.fast_path_impacted_methods,
        disconnected_files=trace_plan.disconnected_files,
        non_traceable_files=sorted(non_traceable_files),
    )
