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

from evals.utils import generate_system_specs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BaseEval(ABC):
    def __init__(self, name: str, output_dir: Path):
        self.name = name
        self.output_dir = output_dir
        self.results: list[dict[str, Any]] = []
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

    def get_latest_run_dir(self, project_name: str) -> Path | None:
        """Find the most recent monitoring run directory for a project."""
        runs_dir = self.project_root / "evals/artifacts/monitoring_results/runs"

        if not runs_dir.exists():
            return None

        matching_dirs = sorted(
            [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith(f"{project_name}_")],
            key=lambda x: x.name,
            reverse=True,
        )

        return matching_dirs[0] if matching_dirs else None

    def get_latest_run_data(self, project_name: str) -> dict[str, Any]:
        """Read all monitoring data for a project from its run directory."""
        run_dir = self.get_latest_run_dir(project_name)

        if not run_dir:
            logger.warning(f"No monitoring run found for: {project_name}")
            return {}

        data = {"run_dir": str(run_dir)}

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

        data["metadata"] = read_json("run_metadata.json")
        data["code_stats"] = read_json("code_stats.json")
        data["llm_usage"] = read_json("llm_usage.json")

        return data

    def run_pipeline(self, project: dict[str, str], extra_args: list[str] | None = None) -> dict[str, Any]:
        """Run the CodeBoarding pipeline for a project."""
        repo_url = project["url"]
        project_name = project["name"]
        output_dir = self.project_root / "evals/artifacts" / project_name
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Running pipeline for {project_name} ({repo_url})")

        env = os.environ.copy()
        env["ENABLE_MONITORING"] = "true"
        if not env.get("REPO_ROOT"):
            env["REPO_ROOT"] = "repos"

        cmd = [sys.executable, "demo.py", repo_url, "--output-dir", str(output_dir)]
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

            return {
                "success": success,
                "stderr": stderr,
                "pipeline_duration": duration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stderr": "Pipeline timed out (30 minutes)",
                "pipeline_duration": time.time() - start_time,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {
                "success": False,
                "stderr": str(e),
                "pipeline_duration": time.time() - start_time,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    @abstractmethod
    def extract_metrics(self, project: dict, run_data: dict) -> dict[str, Any]:
        """Subclasses must implement this to pick what they care about."""
        pass

    @abstractmethod
    def generate_report(self, results: list[dict]) -> str:
        """Subclasses must implement this to format their specific Markdown report."""
        pass

    def run(self, projects: list[dict], extra_args: list[str] | None = None) -> dict[str, Any]:
        """Orchestrator: Runs pipeline -> Extracts metrics -> Generates Report"""
        logger.info(f"Starting {self.name} evaluation for {len(projects)} projects")

        self.results = []
        for project in projects:
            logger.info(f"\n{'='*60}\nProject: {project['name']}\n{'='*60}")

            # 1. Run Pipeline
            pipeline_result = self.run_pipeline(project, extra_args)

            # 2. Get Data
            run_data = self.get_latest_run_data(project["name"])

            # 3. Extract Metrics
            # We merge pipeline result into run_data metadata for consistency if needed,
            # but primary source of truth for metrics is the run_data files.
            # However, success/fail of the RUN itself is in pipeline_result.

            # Check if pipeline failed but we have old data? No, we want fresh data.
            # If pipeline failed, run_data might be stale or empty.

            metrics = self.extract_metrics(project, run_data)

            # Merge high-level execution info
            metrics.update(
                {
                    "project": project["name"],
                    "url": project["url"],
                    "expected_language": project.get("expected_language"),
                    "success": pipeline_result["success"],
                    "duration_seconds": pipeline_result["pipeline_duration"],
                    "error": pipeline_result["stderr"] if not pipeline_result["success"] else None,
                    "timestamp": pipeline_result["timestamp"],
                }
            )

            # Also pull success/error from metadata if available and successful
            if pipeline_result["success"]:
                meta = run_data.get("metadata", {})
                if meta:
                    metrics["duration_seconds"] = meta.get("duration_seconds", metrics["duration_seconds"])

            self.results.append(metrics)

            if metrics["success"]:
                logger.info(f"✅ {project['name']} completed in {metrics['duration_seconds']:.1f}s")
            else:
                logger.error(f"❌ {project['name']} failed: {metrics.get('error', 'Unknown')[:100]}")

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
        self._write_json(json_path, {"timestamp": datetime.now(timezone.utc).isoformat(), "projects": self.results})

        return {"timestamp": datetime.now(timezone.utc).isoformat(), "results": self.results}

    def _write_report(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _write_json(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
