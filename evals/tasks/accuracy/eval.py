import json
import logging
from pathlib import Path
from typing import Any

from evals.base import BaseEval
from evals.tasks.accuracy.config import (
    DEPTH_LEVELS,
    PROJECTS,
)
from evals.tasks.accuracy.report_builder import AccuracyReportBuilder
from evals.tasks.accuracy.dataset_manager import DatasetManager
from evals.tasks.accuracy.models import (
    HistoricalReasoning,
    ProjectMetrics,
    ScoredResult,
)
from evals.tasks.accuracy.score_history import ScoreHistoryStore, get_system_specs
from evals.tasks.accuracy.similarity_judge import DiagramSimilarityJudge
from evals.schemas import EvalResult, PipelineResult, ProjectSpec, RunData
from evals.utils import get_git_commit_short

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

        self._judge = DiagramSimilarityJudge()
        self._dataset_manager = DatasetManager(self.project_root)
        self._history_store = ScoreHistoryStore(output_dir)
        self._report_only = False

    @property
    def depth_levels(self) -> list[int]:
        return DEPTH_LEVELS

    def _analysis_path(self, project_name: str) -> Path:
        return self.project_root / "evals" / "artifacts" / project_name / "analysis.json"

    def _metrics_path(self, project_name: str) -> Path:
        return self.project_root / "evals" / "artifacts" / project_name / "accuracy_metrics.json"

    def _get_base_name(self, project_name: str) -> str:
        if "-depth-" in project_name:
            return project_name.rsplit("-depth-", 1)[0]
        return project_name

    def _get_depth(self, project_name: str) -> int:
        if "-depth-" in project_name:
            try:
                return int(project_name.rsplit("-depth-", 1)[-1])
            except ValueError:
                pass
        return 1

    def _get_project_depth_level(self, project: ProjectSpec) -> int | None:
        if project.env_vars and project.env_vars.get("DIAGRAM_DEPTH_LEVEL"):
            try:
                return int(project.env_vars["DIAGRAM_DEPTH_LEVEL"])
            except ValueError:
                pass
        return self._get_depth(project.name) if "-depth-" in project.name else None

    def _expand_projects_for_depths(self, projects: list[ProjectSpec]) -> list[ProjectSpec]:
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
                        ground_truth_commit=project.ground_truth_commit,
                    )
                )

        return expanded

    def _load_analysis_json(self, project_name: str) -> dict[str, Any]:
        analysis_path = self._analysis_path(project_name)
        if not analysis_path.exists():
            raise FileNotFoundError(f"Missing analysis.json at {analysis_path}")
        return json.loads(analysis_path.read_text(encoding="utf-8"))

    def _load_expected_entries(self, project: ProjectSpec) -> list[dict[str, Any]]:
        base_name = self._get_base_name(project.name)
        depth_level = self._get_project_depth_level(project)

        entries = self._dataset_manager.get_entries(
            graph_id=base_name,
            depth_level=depth_level,
        )

        # Warn if ground truth commit doesn't match config commit
        self._check_commit_mismatch(project, entries)

        return [self._dataset_manager.get_raw_data(e) for e in entries]

    def _check_commit_mismatch(
        self,
        project: ProjectSpec,
        entries: list,
    ) -> None:
        """Warn if the ground truth commit doesn't match the project config commit."""
        from evals.tasks.accuracy.models import DatasetEntry

        config_commit = project.ground_truth_commit
        if not config_commit:
            return

        for entry in entries:
            if not isinstance(entry, DatasetEntry):
                continue

            dataset_commit = entry.ground_truth_commit
            if not dataset_commit:
                logger.warning(
                    "Dataset entry '%s' (depth=%d) has no ground_truth_commit specified. "
                    "Consider adding it to ensure evaluation consistency.",
                    entry.graph_id,
                    entry.level_of_depth,
                )
                continue

            if dataset_commit != config_commit:
                logger.warning(
                    "Commit mismatch for '%s' (depth=%d): "
                    "config specifies '%s' but ground truth was labelled for '%s'. "
                    "Results may not be accurate if the codebase has changed.",
                    entry.graph_id,
                    entry.level_of_depth,
                    config_commit,
                    dataset_commit,
                )

    def run(
        self,
        projects: list[ProjectSpec],
        extra_args: list[str] | None = None,
        report_only: bool = False,
        max_concurrency: int | None = None,
    ) -> dict[str, Any]:
        # Ensure datasets are downloaded from Hugging Face before starting evaluation
        logger.info("Ensuring evaluation datasets are available...")
        self._dataset_manager.ensure_datasets_downloaded()

        self._report_only = report_only
        expanded_projects = self._expand_projects_for_depths(projects)
        return super().run(
            expanded_projects,
            extra_args,
            report_only=report_only,
            max_concurrency=max_concurrency,
        )

    def run_pipeline(
        self,
        project: ProjectSpec,
        extra_args: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> PipelineResult:
        # Set up static analysis cache directory for this project
        cache_env = self._get_static_analysis_cache_env(project)
        merged_env = {**(env_vars or {}), **cache_env}
        return super().run_pipeline(project, extra_args=extra_args, env_vars=merged_env)

    async def run_pipeline_async(
        self,
        project: ProjectSpec,
        extra_args: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> PipelineResult:
        # Set up static analysis cache directory for this project
        cache_env = self._get_static_analysis_cache_env(project)
        merged_env = {**(env_vars or {}), **cache_env}
        return await super().run_pipeline_async(project, extra_args=extra_args, env_vars=merged_env)

    def _get_static_analysis_cache_env(self, project: ProjectSpec) -> dict[str, str]:
        """Get environment variables for static analysis caching."""
        if not project.ground_truth_commit:
            return {}

        # Cache directory: evals/artifacts/<project>/static_analysis_cache/
        cache_dir = self.project_root / "evals" / "artifacts" / project.name / "static_analysis_cache"
        return {
            "STATIC_ANALYSIS_CACHE_DIR": str(cache_dir),
            "STATIC_ANALYSIS_CACHE_COMMIT": project.ground_truth_commit,
        }

    def extract_metrics(self, project: ProjectSpec, run_data: RunData) -> dict[str, Any]:
        try:
            metrics_path = self._metrics_path(project.name)

            if self._report_only and metrics_path.exists():
                return json.loads(metrics_path.read_text(encoding="utf-8"))

            actual = self._load_analysis_json(project.name)
            expected_entries = self._load_expected_entries(project)

            results, bin_scores = self._score_all_entries(project, actual, expected_entries)

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
        results: list[ScoredResult] = []
        bin_scores: dict[str, list[float]] = {}

        for expected in expected_entries:
            score = self._judge.score(actual, expected)

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

    def generate_report(self, results: list[EvalResult]) -> str:
        duration_seconds = getattr(self, "total_duration_seconds", None)
        commit_hash = get_git_commit_short()
        system_specs = get_system_specs()

        project_scores = self._collect_scores(results)
        reasoning_entries = self._collect_reasoning(results, commit_hash)
        project_sizes = self._get_project_sizes(results)

        history = self._history_store.append_run(
            commit=commit_hash,
            scores=project_scores,
            reasoning=reasoning_entries,
            system_specs=system_specs,
            project_sizes=project_sizes,
        )

        report = (
            AccuracyReportBuilder(self.output_dir)
            .with_header(duration_seconds=duration_seconds)
            .with_methodology()
            .with_depth_sections(history, self.depth_levels)
            .with_score_plot(history, self.depth_levels)
            .with_reasoning(history)
            .build()
        )

        return report

    def _collect_scores(self, results: list[EvalResult]) -> dict[str, float | None]:
        return {result.project: result.metrics.get("average_similarity_score") for result in results}

    def _collect_reasoning(
        self,
        results: list[EvalResult],
        commit: str,
    ) -> list[HistoricalReasoning]:
        reasoning_list: list[HistoricalReasoning] = []

        for result in results:
            similarity_results = result.metrics.get("similarity_results", [])
            if not similarity_results:
                continue

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
        size_mapping: dict[str, str] = {}

        for proj in PROJECTS:
            if proj.code_size:
                size_mapping[proj.name] = proj.code_size
                for depth in self.depth_levels:
                    size_mapping[f"{proj.name}-depth-{depth}"] = proj.code_size

        return size_mapping


def run_accuracy_eval(
    output_dir: Path | None = None,
    report_only: bool = False,
) -> dict[str, Any]:
    report_dir = output_dir or Path("evals/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    eval_instance = AccuracyEval("accuracy", report_dir)
    return eval_instance.run(PROJECTS, extra_args=[], report_only=report_only)
