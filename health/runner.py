import fnmatch
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from health.checks.circular_deps import check_circular_dependencies
from health.checks.cohesion import check_component_cohesion
from health.checks.coupling import check_fan_in, check_fan_out
from health.checks.function_size import check_function_size
from health.checks.god_class import check_god_classes
from health.checks.inheritance import check_inheritance_depth
from health.checks.instability import check_package_instability
from health.checks.unused_code_diagnostics import (
    LSPDiagnosticsCollector,
    check_unused_code_diagnostics,
)
from health.models import (
    CircularDependencyCheck,
    FileHealthSummary,
    HealthCheckConfig,
    HealthReport,
    Severity,
    StandardCheckSummary,
)
from core import get_registries
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)

CheckSummaryList = list[StandardCheckSummary | CircularDependencyCheck]


def _matches_exclude_pattern(entity_name: str, file_path: str | None, patterns: list[str]) -> bool:
    """Check if an entity or file path matches any of the exclusion patterns."""
    for pattern in patterns:
        if fnmatch.fnmatch(entity_name, pattern):
            return True
        if file_path and fnmatch.fnmatch(file_path, pattern):
            return True
    return False


def _apply_exclude_patterns(summaries: CheckSummaryList, patterns: list[str]) -> None:
    """Remove findings that match any exclusion pattern from all check summaries."""
    if not patterns:
        return
    for summary in summaries:
        if not isinstance(summary, StandardCheckSummary):
            continue
        excluded = 0
        for group in summary.finding_groups:
            original_count = len(group.entities)
            group.entities = [
                entity
                for entity in group.entities
                if not _matches_exclude_pattern(entity.entity_name, entity.file_path, patterns)
            ]
            excluded += original_count - len(group.entities)
        summary.finding_groups = [g for g in summary.finding_groups if g.entities]
        if excluded:
            summary.findings_count = max(0, summary.findings_count - excluded)
            logger.debug(f"Excluded {excluded} findings from {summary.check_name} via .healthignore patterns")


def _relativize_path(file_path: str, repo_root: str) -> str:
    """Convert an absolute file path to a path relative to the repository root."""
    return os.path.relpath(file_path, repo_root)


def _collect_checks_for_language(
    static_analysis: StaticAnalysisResults,
    language: str,
    config: HealthCheckConfig,
) -> CheckSummaryList:
    """Run all applicable health checks for a single language and return the summaries."""
    summaries: CheckSummaryList = []

    call_graph = static_analysis.get_cfg(language)
    try:
        hierarchy = static_analysis.get_hierarchy(language)
    except ValueError:
        hierarchy = None

    summaries.append(check_function_size(call_graph, config))
    summaries.append(check_fan_out(call_graph, config))
    summaries.append(check_fan_in(call_graph, config))
    summaries.append(check_god_classes(call_graph, hierarchy, config))

    if hierarchy:
        summaries.append(check_inheritance_depth(hierarchy, config))

    try:
        package_deps = static_analysis.get_package_dependencies(language)
    except ValueError:
        package_deps = None
    if package_deps:
        summaries.append(check_circular_dependencies(package_deps, config))
        summaries.append(check_package_instability(package_deps, config))

    summaries.append(check_component_cohesion(call_graph, config))

    # Run LSP-based unused code detection
    exclude_patterns = config.health_exclude_patterns
    collector = LSPDiagnosticsCollector()
    language_diagnostics = static_analysis.diagnostics.get(language, {})
    if language_diagnostics:
        for file_path, file_diagnostics in language_diagnostics.items():
            if exclude_patterns and _matches_exclude_pattern("", file_path, exclude_patterns):
                logger.debug(f"Excluding diagnostics for {file_path} via .healthignore patterns")
                continue
            for diagnostic in file_diagnostics:
                collector.add_diagnostic(file_path, diagnostic)
    else:
        logger.debug(f"No LSP diagnostics available for {language}")
    # Always add the check summary, even if empty
    summaries.append(check_unused_code_diagnostics(collector, config))

    # Run plugin-provided health checks
    for name, check_func in get_registries().health_checks.all().items():
        try:
            plugin_summaries = check_func(static_analysis, language, config)
            summaries.extend(plugin_summaries)
        except Exception:
            logger.exception(f"Plugin health check '{name}' failed for language '{language}'")

    # Apply .healthignore exclusion patterns across all check findings
    _apply_exclude_patterns(summaries, exclude_patterns)

    return summaries


def _compute_overall_score(check_summaries: CheckSummaryList) -> float:
    """Calculate the overall score as a weighted average of standard check scores."""
    total_entities = sum(s.total_entities_checked for s in check_summaries if isinstance(s, StandardCheckSummary))
    if total_entities == 0:
        return 1.0
    return (
        sum(s.score * s.total_entities_checked for s in check_summaries if isinstance(s, StandardCheckSummary))
        / total_entities
    )


def _aggregate_file_summaries(
    check_summaries: CheckSummaryList,
) -> list[FileHealthSummary]:
    """Aggregate findings per file and compute composite risk scores.

    Returns the top 20 highest-risk files sorted by composite score.
    """
    file_risk: dict[str, FileHealthSummary] = {}
    for summary in check_summaries:
        if not isinstance(summary, StandardCheckSummary):
            continue
        for group in summary.finding_groups:
            for entity in group.entities:
                if not entity.file_path:
                    continue
                if entity.file_path not in file_risk:
                    file_risk[entity.file_path] = FileHealthSummary(file_path=entity.file_path)
                file_risk[entity.file_path].total_findings += 1
                if group.severity == Severity.WARNING:
                    file_risk[entity.file_path].warning_findings += 1

    for file_summary in file_risk.values():
        base_score = min(file_summary.total_findings * 10, 50)
        severity_bonus = file_summary.warning_findings * 5
        file_summary.composite_risk_score = min(base_score + severity_bonus, 100)

    return sorted(file_risk.values(), key=lambda f: f.composite_risk_score, reverse=True)[:20]


def _relativize_report_paths(
    check_summaries: CheckSummaryList,
    file_summaries: list[FileHealthSummary],
    repo_root: str,
) -> None:
    """Convert absolute file paths in summaries to paths relative to the repo root."""
    for summary in check_summaries:
        if not isinstance(summary, StandardCheckSummary):
            continue
        for group in summary.finding_groups:
            for entity in group.entities:
                if entity.file_path and os.path.isabs(entity.file_path):
                    entity.file_path = _relativize_path(entity.file_path, repo_root)

    for file_summary in file_summaries:
        if file_summary.file_path and os.path.isabs(file_summary.file_path):
            file_summary.file_path = _relativize_path(file_summary.file_path, repo_root)


def run_health_checks(
    static_analysis: StaticAnalysisResults,
    repo_name: str,
    config: HealthCheckConfig | None = None,
    repo_path: Path | str | None = None,
) -> HealthReport | None:
    """Run all health checks against the static analysis results and produce a HealthReport.

    Args:
        static_analysis: The static analysis results to check.
        repo_name: Name of the repository.
        config: Optional health check configuration overrides.
        repo_path: Repository root path. When provided, all file paths in the
            report are made relative to this directory for portability.

    Returns:
        A HealthReport, or None if no languages were found in the static analysis.
    """
    if config is None:
        config = HealthCheckConfig()

    languages = static_analysis.get_languages()
    if not languages:
        logger.warning("No languages found in static analysis results; skipping health checks")
        return None

    repo_root = str(repo_path) if repo_path is not None else None

    check_summaries: CheckSummaryList = []

    for language in languages:
        lang_summaries = _collect_checks_for_language(static_analysis, language, config)
        if len(languages) > 1:
            for summary in lang_summaries:
                summary.language = language
        check_summaries.extend(lang_summaries)

    overall_score = _compute_overall_score(check_summaries)
    file_summaries = _aggregate_file_summaries(check_summaries)

    if repo_root:
        _relativize_report_paths(check_summaries, file_summaries, repo_root)

    return HealthReport(
        repository_name=repo_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        overall_score=overall_score,
        check_summaries=check_summaries,
        file_summaries=file_summaries,
    )
