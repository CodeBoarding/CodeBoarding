"""
Accuracy evaluation for CodeBoarding diagram generation.

Compares generated `analysis.json` diagrams against a curated ground-truth dataset
using an LLM-as-judge approach to score structural similarity.

This module provides a thin orchestrator that composes:
- DiagramSimilarityJudge: LLM-based scoring
- DatasetManager: Ground-truth data loading/filtering
- ScoreHistoryStore: Persistent score history (JSON-based)
- AccuracyReportBuilder: Markdown report generation
- ScoreHistoryPlotter: Visualization
"""

import json
import logging
from pathlib import Path
from typing import Any

from evals.base import BaseEval
from evals.tasks.accuracy.config import (
    CODE_SIZE_BINS,
    DEPTH_LEVELS,
    PROJECTS,
    SAMPLES_PER_BIN,
    SCORING_MODEL,
)
from evals.tasks.accuracy.report_builder import AccuracyReportBuilder
from evals.tasks.accuracy.dataset_manager import DatasetManager
from evals.tasks.accuracy.models import (
    CodeSizeCategory,
    HistoricalReasoning,
    ProjectMetrics,
    ScoredResult,
)
from evals.tasks.accuracy.score_history import ScoreHistoryStore, get_system_specs
from evals.tasks.accuracy.similarity_judge import DiagramSimilarityJudge
from evals.schemas import EvalResult, ProjectSpec, RunData
from evals.utils import get_git_commit_short

# Re-export for backward compatibility
from evals.tasks.accuracy.level_two import (  # noqa: F401
    LevelTwoFromLevelOneEval,
    run_level2_from_level1_eval,
)

logger = logging.getLogger(__name__)


class AccuracyEval(BaseEval):
    """
    Runs the main CodeBoarding pipeline and scores similarity between
    generated `analysis.json` and the expected dataset in `train.json`.

    This is a thin orchestrator that composes specialized components for:
    - LLM-based similarity scoring
    - Dataset loading and filtering
    - Historical score persistence
    - Report generation

    Example:
        eval = AccuracyEval("accuracy", output_dir)
        results = eval.run(PROJECTS)
    """

    def __init__(self, name: str, output_dir: Path):
        super().__init__(name, output_dir)
        self.include_system_specs_in_footer = False

        # Initialize components
        self._judge = DiagramSimilarityJudge(model_override=SCORING_MODEL)
        self._dataset_manager = DatasetManager(self.project_root)
        self._history_store = ScoreHistoryStore(output_dir)
        self._report_only = False

    # =========================================================================
    # Configuration
    # =========================================================================

    @property
    def depth_levels(self) -> list[int]:
        """Configured depth levels to evaluate."""
        return DEPTH_LEVELS

    @property
    def code_size_bins(self) -> list[CodeSizeCategory]:
        """Configured code size bins to include."""
        if not CODE_SIZE_BINS:
            return list(CodeSizeCategory)
        return [CodeSizeCategory.from_label(s) for s in CODE_SIZE_BINS]

    @property
    def samples_per_bin(self) -> int | None:
        """Maximum samples per code size bin (0 or None = unlimited)."""
        if SAMPLES_PER_BIN and SAMPLES_PER_BIN > 0:
            return SAMPLES_PER_BIN
        return None

    # =========================================================================
    # Path Helpers
    # =========================================================================

    def _analysis_path(self, project_name: str) -> Path:
        """Path to the generated analysis.json for a project."""
        return self.project_root / "evals" / "artifacts" / project_name / "analysis.json"

    def _metrics_path(self, project_name: str) -> Path:
        """Path to store metrics for a project."""
        return self.project_root / "evals" / "artifacts" / project_name / "accuracy_metrics.json"

    # =========================================================================
    # Project Depth Handling
    # =========================================================================

    def _get_base_name(self, project_name: str) -> str:
        """Get base project name without depth suffix."""
        if "-depth-" in project_name:
            return project_name.rsplit("-depth-", 1)[0]
        return project_name

    def _get_depth(self, project_name: str) -> int:
        """Extract depth level from project name."""
        if "-depth-" in project_name:
            try:
                return int(project_name.rsplit("-depth-", 1)[-1])
            except ValueError:
                pass
        return 1

    def _get_project_depth_level(self, project: ProjectSpec) -> int | None:
        """Get depth level from project spec (env vars or name)."""
        if project.env_vars and project.env_vars.get("DIAGRAM_DEPTH_LEVEL"):
            try:
                return int(project.env_vars["DIAGRAM_DEPTH_LEVEL"])
            except ValueError:
                pass
        return self._get_depth(project.name) if "-depth-" in project.name else None

    def _expand_projects_for_depths(self, projects: list[ProjectSpec]) -> list[ProjectSpec]:
        """
        Expand each project into multiple entries, one per configured depth level.

        For example, if depth_levels is [1, 2] and we have one project "markitdown",
        this returns ["markitdown-depth-1", "markitdown-depth-2"].
        """
        expanded: list[ProjectSpec] = []

        for project in projects:
            for depth in self.depth_levels:
                name = f"{project.name}-depth-{depth}"
                env_vars = dict(project.env_vars) if project.env_vars else {}
                env_vars["DIAGRAM_DEPTH_LEVEL"] = str(depth)

                expanded.append(
                    ProjectSpec(
                        name=name,
                        url=project.url,
                        expected_language=project.expected_language,
                        env_vars=env_vars,
                        code_size=project.code_size,
                    )
                )

        return expanded

    # =========================================================================
    # Data Loading
    # =========================================================================

    def _load_analysis_json(self, project_name: str) -> dict[str, Any]:
        """Load the generated analysis.json for a project."""
        analysis_path = self._analysis_path(project_name)
        if not analysis_path.exists():
            raise FileNotFoundError(f"Missing analysis.json at {analysis_path}")
        return json.loads(analysis_path.read_text(encoding="utf-8"))

    def _load_expected_entries(self, project: ProjectSpec) -> list[dict[str, Any]]:
        """Load and filter expected entries from the ground-truth dataset."""
        base_name = self._get_base_name(project.name)
        depth_level = self._get_project_depth_level(project)

        entries = self._dataset_manager.get_entries(
            graph_id=base_name,
            code_sizes=self.code_size_bins if self.code_size_bins else None,
            depth_level=depth_level,
            limit_per_size=self.samples_per_bin,
        )

        # Return raw data for scoring
        return [self._dataset_manager.get_raw_data(e) for e in entries]

    # =========================================================================
    # Evaluation Orchestration
    # =========================================================================

    def run(
        self,
        projects: list[ProjectSpec],
        extra_args: list[str] | None = None,
        report_only: bool = False,
    ) -> dict[str, Any]:
        """
        Run accuracy evaluation for the given projects.

        Projects are automatically expanded to cover all configured depth levels.
        """
        self._report_only = report_only
        expanded_projects = self._expand_projects_for_depths(projects)
        return super().run(expanded_projects, extra_args, report_only=report_only)

    def run_pipeline(
        self,
        project: ProjectSpec,
        extra_args: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ):
        """Run the pipeline with depth level from project spec."""
        return super().run_pipeline(project, extra_args=extra_args, env_vars=env_vars)

    def extract_metrics(self, project: ProjectSpec, run_data: RunData) -> dict[str, Any]:
        """
        Extract accuracy metrics for a project.

        Scores the generated analysis against all matching ground-truth entries
        and computes per-bin and overall averages.
        """
        try:
            metrics_path = self._metrics_path(project.name)

            # If report-only mode, try to load cached metrics
            if self._report_only and metrics_path.exists():
                return json.loads(metrics_path.read_text(encoding="utf-8"))

            # Load data
            actual = self._load_analysis_json(project.name)
            expected_entries = self._load_expected_entries(project)

            # Score against each expected entry
            results, bin_scores = self._score_all_entries(project, actual, expected_entries)

            # Compute averages
            all_scores = [s for scores in bin_scores.values() for s in scores]
            average_score = sum(all_scores) / len(all_scores) if all_scores else None
            bin_averages = {
                bin_key: (sum(scores) / len(scores) if scores else None) for bin_key, scores in bin_scores.items()
            }

            metrics = ProjectMetrics(
                analysis_path=str(self._analysis_path(project.name)),
                similarity_results=results,
                average_similarity_score=average_score,
                bin_average_scores=bin_averages,
                dataset_samples=len(expected_entries),
            )

            # Persist metrics
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(metrics.model_dump_json(indent=2), encoding="utf-8")

            return metrics.model_dump()

        except Exception as exc:
            logger.warning("Accuracy evaluation failed for %s: %s", project.name, exc)
            metrics = ProjectMetrics(
                analysis_path=str(self._analysis_path(project.name)),
                error=str(exc),
            )
            metrics_path = self._metrics_path(project.name)
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
            return metrics.model_dump()

    def _score_all_entries(
        self,
        project: ProjectSpec,
        actual: dict[str, Any],
        expected_entries: list[dict[str, Any]],
    ) -> tuple[list[ScoredResult], dict[str, list[float]]]:
        """Score the actual diagram against all expected entries."""
        results: list[ScoredResult] = []
        bin_scores: dict[str, list[float]] = {}

        for expected in expected_entries:
            score = self._judge.score(actual, expected)

            # Determine code size
            code_size = project.code_size
            if not code_size and isinstance(expected, dict):
                code_size = expected.get("code_size", "unknown")
            bin_key = code_size if code_size else "unknown"

            result = ScoredResult(
                score=score.score,
                node_coverage_reasoning=score.node_coverage_reasoning,
                relationship_fidelity_reasoning=score.relationship_fidelity_reasoning,
                structural_coherence_reasoning=score.structural_coherence_reasoning,
                code_size=bin_key,
            )
            results.append(result)

            if score.is_valid:
                bin_scores.setdefault(bin_key, []).append(float(score.score))

        return results, bin_scores

    # =========================================================================
    # Report Generation
    # =========================================================================

    def generate_report(self, results: list[EvalResult]) -> str:
        """Generate the accuracy evaluation report."""
        duration_seconds = getattr(self, "total_duration_seconds", None)
        commit_hash = get_git_commit_short()
        system_specs = get_system_specs()

        # Collect scores and reasoning from results
        project_scores = self._collect_scores(results)
        reasoning_entries = self._collect_reasoning(results, commit_hash)
        project_sizes = self._get_project_sizes(results)

        # Append to history store
        history = self._history_store.append_run(
            commit=commit_hash,
            scores=project_scores,
            reasoning=reasoning_entries,
            system_specs=system_specs,
            project_sizes=project_sizes,
        )

        # Build report
        report = (
            AccuracyReportBuilder(self.output_dir)
            .with_header(duration_seconds=duration_seconds)
            .with_methodology()
            .with_depth_sections(history, self.depth_levels)
            .with_score_plot(history, self.depth_levels)
            .with_reasoning(history)
            .with_glossary()
            .build()
        )

        return report

    def _collect_scores(self, results: list[EvalResult]) -> dict[str, float | None]:
        """Collect average scores from evaluation results."""
        return {result.project: result.metrics.get("average_similarity_score") for result in results}

    def _collect_reasoning(
        self,
        results: list[EvalResult],
        commit: str,
    ) -> list[HistoricalReasoning]:
        """Collect reasoning entries from evaluation results."""
        reasoning_list: list[HistoricalReasoning] = []

        for result in results:
            similarity_results = result.metrics.get("similarity_results", [])
            if not similarity_results:
                continue

            # Use the first result (typically there's one per expected entry)
            first_result = similarity_results[0] if similarity_results else {}
            if not isinstance(first_result, dict):
                continue

            score = result.metrics.get("average_similarity_score")
            reasoning_list.append(
                HistoricalReasoning(
                    commit=commit,
                    project=self._get_base_name(result.project),
                    depth=self._get_depth(result.project),
                    score=score,
                    node_coverage=first_result.get("node_coverage_reasoning", ""),
                    relationship_fidelity=first_result.get("relationship_fidelity_reasoning", ""),
                    structural_coherence=first_result.get("structural_coherence_reasoning", ""),
                )
            )

        return reasoning_list

    def _get_project_sizes(self, results: list[EvalResult]) -> dict[str, str]:
        """Get project name to code size mapping."""
        size_mapping: dict[str, str] = {}

        for proj in PROJECTS:
            if proj.code_size:
                size_mapping[proj.name] = proj.code_size
                for depth in self.depth_levels:
                    size_mapping[f"{proj.name}-depth-{depth}"] = proj.code_size

        return size_mapping


# =============================================================================
# Convenience Functions
# =============================================================================


def run_accuracy_eval(
    output_dir: Path | None = None,
    report_only: bool = False,
) -> dict[str, Any]:
    """
    Convenience function to run the accuracy evaluation.

    Args:
        output_dir: Directory for reports (defaults to evals/reports)
        report_only: If True, only generate report from existing artifacts

    Returns:
        Evaluation results dictionary
    """
    report_dir = output_dir or Path("evals/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    eval_instance = AccuracyEval("accuracy", report_dir)
    return eval_instance.run(PROJECTS, extra_args=[], report_only=report_only)
