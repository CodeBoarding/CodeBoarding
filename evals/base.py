import dataclasses
import json
import logging
import os
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Ensure we can import from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.schemas import EvalResult, PipelineResult, ProjectSpec, RunData
from evals.utils import generate_system_specs
from monitoring.paths import get_latest_run_dir

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BaseEval(ABC):
    def __init__(self, name: str, output_dir: Path):
        self.name = name
        self.output_dir = output_dir
        self.results: list[EvalResult] = []
        self.project_root = self._get_project_root()

    def _get_project_root(self) -> Path:
        load_dotenv()
        project_root_env = os.getenv("PROJECT_ROOT")
        # Fallback to finding it relative to this file if not set
        if not project_root_env:
            # Assuming evals/base.py is 2 levels deep from root
            root = Path(__file__).parent.parent
            os.environ["PROJECT_ROOT"] = str(root)
            return root
        return Path(project_root_env)

    def get_latest_run_data(self, project_name: str) -> RunData:
        """Read all monitoring data for a project from its run directory."""
        run_dir = get_latest_run_dir(project_name)

        if not run_dir:
            logger.warning(f"No monitoring run found for: {project_name}")
            return RunData(run_dir="")

        data = RunData(run_dir=str(run_dir))

        # Helper to safely read json files
        def read_json(filename):
            fpath = run_dir / filename
            if fpath.exists():
                try:
                    with open(fpath) as f:
                        return json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read {filename}: {e}")
            return {}

        data.metadata = read_json("run_metadata.json")
        data.code_stats = read_json("code_stats.json")
        data.llm_usage = read_json("llm_usage.json")

        return data

    def run_pipeline(
        self,
        project: ProjectSpec,
        extra_args: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> PipelineResult:
        repo_url = project.url
        project_name = project.name
        output_dir = self.project_root / "evals/artifacts" / project_name
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Running pipeline for {project_name} ({repo_url})")

        env = os.environ.copy()
        env["ENABLE_MONITORING"] = "true"
        if not env.get("REPO_ROOT"):
            env["REPO_ROOT"] = "repos"

        if project.env_vars:
            env.update(project.env_vars)

        if env_vars:
            env.update(env_vars)

        cmd = [
            sys.executable,
            "main.py",
            repo_url,
            "--output-dir",
            str(output_dir),
            "--project-name",
            project_name,
        ]
        if extra_args:
            cmd.extend(extra_args)

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,  # 30 minutes
                env=env,
                cwd=self.project_root,
            )
            duration = time.time() - start_time
            success = result.returncode == 0
            stderr = result.stderr[-500:] if result.stderr else ""

            return PipelineResult(
                success=success,
                stderr=stderr,
                pipeline_duration=duration,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except subprocess.TimeoutExpired:
            return PipelineResult(
                success=False,
                stderr="Pipeline timed out (30 minutes)",
                pipeline_duration=time.time() - start_time,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            return PipelineResult(
                success=False,
                stderr=str(e),
                pipeline_duration=time.time() - start_time,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    @abstractmethod
    def extract_metrics(self, project: ProjectSpec, run_data: RunData) -> dict[str, Any]:
        """Subclasses must implement this to pick what they care about."""
        pass

    @abstractmethod
    def generate_report(self, results: list[EvalResult]) -> str:
        """Subclasses must implement this to format their specific Markdown report."""
        pass

    def run(
        self, projects: list[ProjectSpec], extra_args: list[str] | None = None, report_only: bool = False
    ) -> dict[str, Any]:
        """Orchestrator: Runs pipeline -> Extracts metrics -> Generates Report"""
        logger.info(f"Starting {self.name} evaluation for {len(projects)} projects")

        self.results = []
        for project in projects:
            logger.info(f"\n{'='*60}\nProject: {project.name}\n{'='*60}")

            pipeline_result = None

            # 1. Run Pipeline
            if not report_only:
                pipeline_result = self.run_pipeline(project, extra_args)

            # 2. Get Data
            run_data = self.get_latest_run_data(project.name)

            if report_only:
                meta = run_data.metadata
                if meta:
                    pipeline_result = PipelineResult(
                        success=meta.get("success", False),
                        stderr=meta.get("error") or "",
                        pipeline_duration=meta.get("duration_seconds", 0.0),
                        timestamp=meta.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    )
                else:
                    logger.warning(
                        f"No run data found for {project.name}, skipping report generation for this project."
                    )
                    pipeline_result = PipelineResult(
                        success=False,
                        stderr="No previous run data found for report generation",
                        pipeline_duration=0.0,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )

            # 3. Extract Metrics
            metrics = self.extract_metrics(project, run_data)

            # Ensure pipeline_result is set
            assert pipeline_result is not None

            # Construct EvalResult
            eval_result = EvalResult(
                project=project.name,
                url=project.url,
                expected_language=project.expected_language,
                success=pipeline_result.success,
                duration_seconds=pipeline_result.pipeline_duration,
                timestamp=pipeline_result.timestamp,
                error=pipeline_result.stderr if not pipeline_result.success else None,
                metrics=metrics,
            )

            # Also pull success/error from metadata if available and successful
            if pipeline_result.success:
                meta = run_data.metadata
                if meta:
                    eval_result.duration_seconds = meta.get("duration_seconds", eval_result.duration_seconds)

            self.results.append(eval_result)

            if eval_result.success:
                logger.info(f"✅ {project.name} completed in {eval_result.duration_seconds:.1f}s")
            else:
                logger.error(f"❌ {project.name} failed: {str(eval_result.error)[:100]}")

        # 4. Generate & Save Report
        report_content = self.generate_report(self.results)

        # Add system specs
        system_specs = generate_system_specs()
        full_report = f"{report_content}\n\n{system_specs}"

        report_path = self.output_dir / f"{self.name}-report.md"
        self._write_report(report_path, full_report)
        logger.info(f"Report generated: {report_path}")

        # Save raw JSON results too
        json_path = self.project_root / "evals/artifacts/monitoring_results/reports" / f"{self.name}_eval.json"
        self._write_json(
            json_path,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "projects": [dataclasses.asdict(r) for r in self.results],
            },
        )

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": self.results,
        }

    def _write_report(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _write_json(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
