import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from diagram_analysis import DiagramGenerator
from diagram_analysis.analysis_json import AnalysisInsightsJson
from evals.base import BaseEval
from evals.tasks.accuracy.config import PROJECTS as PROJECTS_ACCURACY
from evals.tasks.end_to_end.config import PROJECTS as PROJECTS_E2E
from evals.tasks.scalability.config import PROJECTS as PROJECTS_SCALING
from evals.tasks.static_analysis.config import PROJECTS as PROJECTS_STATIC_ANALYSIS
from evals.schemas import EvalResult, PipelineResult, ProjectSpec, RunData
from evals.utils import generate_header
from output_generators import sanitize

logger = logging.getLogger(__name__)


class LevelTwoFromLevelOneEval(BaseEval):
    """
    Generates level 2 diagrams using existing level 1 analysis artifacts.

    This evaluation scans the dataset for level 1 entries and expands their
    components to create detailed internal views. It's useful for testing
    the component expansion functionality without re-running the full pipeline.

    The evaluation:
    1. Finds projects with existing level 1 analysis.json files
    2. Loads the level 1 analysis and identifies expandable components
    3. Generates detailed level 2 diagrams for each component
    4. Reports on coverage and any missing outputs

    Note: This evaluation discovers projects from the dataset automatically.
    The projects parameter in run() is ignored.
    """

    def __init__(self, name: str, output_dir: Path):
        super().__init__(name, output_dir)
        self.include_system_specs_in_footer = True
        self._report_only = False

    def _metrics_path(self, project_name: str) -> Path:
        return self.project_root / "evals" / "artifacts" / project_name / "level2_metrics.json"

    def _analysis_output_dir(self, project_name: str) -> Path:
        return self.project_root / "evals" / "artifacts" / project_name

    def _dataset_path(self) -> Path:
        path = self.project_root / "evals" / "tasks" / "accuracy" / "datasets" / "train.json"
        # Import and use the download helper from dataset_manager
        from evals.tasks.accuracy.dataset_manager import _ensure_dataset_downloaded

        _ensure_dataset_downloaded(path, "train.json")
        return path

    def _get_project_base_name(self, project_name: str) -> str:
        if "-depth-" in project_name:
            return project_name.rsplit("-depth-", 1)[0]
        return project_name

    def _find_project_spec(self, graph_id: str) -> ProjectSpec | None:
        candidates = PROJECTS_ACCURACY + PROJECTS_E2E + PROJECTS_SCALING + PROJECTS_STATIC_ANALYSIS
        for project in candidates:
            if self._get_project_base_name(project.name) == graph_id:
                return project
        return None

    def _load_dataset_entries(self) -> list[dict[str, Any]]:
        dataset_path = self._dataset_path()
        if not dataset_path.exists():
            logger.warning("Level2 eval: missing dataset at %s", dataset_path)
            return []

        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [entry for entry in payload if isinstance(entry, dict)]
        if isinstance(payload, dict):
            return [payload]
        return []

    def _find_l1_analysis_path(self, graph_id: str) -> Path:
        candidates = [
            self.project_root / "evals" / "artifacts" / f"{graph_id}-depth-1" / "analysis.json",
            self.project_root / "evals" / "artifacts" / graph_id / "analysis.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"Missing level 1 analysis.json for {graph_id}")

    def _resolve_repo_path(self, graph_id: str, analysis: AnalysisInsightsJson) -> Path | None:
        repo_root = Path(os.getenv("REPO_ROOT", "repos"))
        candidate = repo_root / graph_id
        if candidate.exists():
            return candidate

        for component in analysis.components:
            for reference in component.referenced_source_code:
                reference_file = reference.reference_file
                if not reference_file:
                    continue
                file_path = Path(reference_file)
                if not file_path.is_absolute():
                    continue
                if not file_path.exists():
                    continue
                for parent in [file_path, *file_path.parents]:
                    if (parent / ".git").exists():
                        return parent
        return None

    def _projects_from_dataset(self) -> list[ProjectSpec]:
        entries = self._load_dataset_entries()
        graph_ids = sorted(
            {entry.get("graph_id") for entry in entries if entry.get("graph_id") and entry.get("level_of_depth") == 1}
        )

        projects: list[ProjectSpec] = []
        for graph_id in graph_ids:
            if not isinstance(graph_id, str):
                continue
            try:
                self._find_l1_analysis_path(graph_id)
            except FileNotFoundError:
                logger.info("Level2 eval: skipping %s (no L1 artifact)", graph_id)
                continue

            spec = self._find_project_spec(graph_id)
            projects.append(
                ProjectSpec(
                    name=f"{graph_id}-depth-2",
                    url=spec.url if spec else "",
                    expected_language=spec.expected_language if spec else "",
                )
            )
        return projects

    def run(
        self,
        extra_args: list[str] | None = None,
        report_only: bool = False,
    ) -> dict[str, Any]:
        self._report_only = report_only
        dataset_projects = self._projects_from_dataset()
        if not dataset_projects:
            logger.warning("Level2 eval: no dataset-backed projects found")
        return super().run(dataset_projects, extra_args, report_only=report_only)

    def run_pipeline(
        self,
        project: ProjectSpec,
        extra_args: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> PipelineResult:
        start_time = time.time()
        base_name = self._get_project_base_name(project.name)
        output_dir = self._analysis_output_dir(project.name)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            l1_analysis_path = self._find_l1_analysis_path(base_name)
            l1_analysis = AnalysisInsightsJson.model_validate_json(l1_analysis_path.read_text(encoding="utf-8"))

            repo_path = self._resolve_repo_path(base_name, l1_analysis)
            if repo_path is None:
                raise FileNotFoundError(
                    f"Unable to locate repo for {base_name}. "
                    "Ensure it exists under REPO_ROOT or the L1 analysis uses absolute paths."
                )

            generator = DiagramGenerator(
                repo_location=repo_path,
                temp_folder=output_dir,
                repo_name=base_name,
                output_dir=output_dir,
                depth_level=2,
            )
            generator.pre_analysis()

            expandable_components = [c for c in l1_analysis.components if c.can_expand]
            if not expandable_components:
                expandable_components = list(l1_analysis.components)

            generated_files: list[str] = []
            for component in expandable_components:
                result_path, _ = generator.process_component(component)
                if result_path:
                    generated_files.append(str(result_path))

            (output_dir / "analysis.json").write_text(
                l1_analysis_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            logger.info(
                "Level2 eval: generated %d component analyses for %s",
                len(generated_files),
                base_name,
            )

            return PipelineResult(
                success=True,
                stderr="",
                pipeline_duration=time.time() - start_time,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            logger.error("Level2 eval failed for %s: %s", project.name, exc)
            return PipelineResult(
                success=False,
                stderr=str(exc),
                pipeline_duration=time.time() - start_time,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    def extract_metrics(self, project: ProjectSpec, run_data: RunData) -> dict[str, Any]:
        try:
            metrics_path = self._metrics_path(project.name)
            if self._report_only and metrics_path.exists():
                return json.loads(metrics_path.read_text(encoding="utf-8"))

            base_name = self._get_project_base_name(project.name)
            output_dir = self._analysis_output_dir(project.name)
            l1_analysis_path = self._find_l1_analysis_path(base_name)
            l1_analysis = AnalysisInsightsJson.model_validate_json(l1_analysis_path.read_text(encoding="utf-8"))

            expandable_components = [c.name for c in l1_analysis.components if c.can_expand]
            if not expandable_components:
                expandable_components = [c.name for c in l1_analysis.components]

            generated_files = [
                path.name
                for path in output_dir.glob("*.json")
                if path.name not in {"analysis.json", "codeboarding_version.json", "level2_metrics.json"}
            ]

            expected_files = {f"{sanitize(name)}.json" for name in expandable_components}
            generated_set = set(generated_files)

            metrics = {
                "l1_analysis_path": str(l1_analysis_path),
                "l2_output_dir": str(output_dir),
                "expanded_components": expandable_components,
                "expanded_count": len(expandable_components),
                "generated_files": sorted(generated_files),
                "generated_count": len(generated_files),
                "missing_outputs": sorted(expected_files - generated_set),
                "extra_outputs": sorted(generated_set - expected_files),
            }

            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            return metrics

        except Exception as exc:
            logger.warning("Level2 metrics failed for %s: %s", project.name, exc)
            metrics = {"error": str(exc)}
            metrics_path = self._metrics_path(project.name)
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            return metrics

    def generate_report(self, results: list[EvalResult]) -> str:
        header = generate_header("Level 2 Expansion Evaluation")
        lines = [
            header,
            "### Summary",
            "",
            "| Project | Status | Expanded | Generated | Missing |",
            "|---------|--------|----------|-----------|---------|",
        ]

        for result in results:
            status = "✅ Success" if result.success else "❌ Failed"
            expanded = result.metrics.get("expanded_count", "N/A")
            generated = result.metrics.get("generated_count", "N/A")
            missing = len(result.metrics.get("missing_outputs", []))
            lines.append(f"| {result.project} | {status} | {expanded} | {generated} | {missing} |")

        return "\n".join(lines)


def run_level2_from_level1_eval(
    output_dir: Path | None = None,
    report_only: bool = False,
) -> dict[str, Any]:
    report_dir = output_dir or Path("evals/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    eval_instance = LevelTwoFromLevelOneEval("level2-expansion", report_dir)
    return eval_instance.run(extra_args=[], report_only=report_only)
