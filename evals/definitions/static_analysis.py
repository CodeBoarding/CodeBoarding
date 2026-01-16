import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.base import BaseEval
from evals.schemas import (
    EvalResult,
    LanguageSummary,
    ProjectSpec,
    RunData,
    StaticAnalysisMetrics,
    StaticAnalysisSummary,
)
from evals.utils import generate_header

logger = logging.getLogger(__name__)


class StaticAnalysisEval(BaseEval):
    def run_static_analysis(self, project: ProjectSpec) -> dict[str, Any]:
        """
        Run static analysis directly using StaticAnalyzer instead of full pipeline.
        Returns code stats without requiring the full diagram generation pipeline.
        """
        from static_analyzer import StaticAnalyzer
        from static_analyzer.scanner import ProjectScanner

        repo_url = project.url
        project_name = project.name
        repo_root = Path(os.getenv("REPO_ROOT", "repos"))

        # Clone repository if not already present
        repo_path = repo_root / project_name
        if not repo_path.exists():
            logger.info(f"Cloning {repo_url} to {repo_path}")
            result = subprocess.run(
                ["git", "clone", repo_url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to clone repository: {result.stderr}")

        # Run static analysis
        logger.info(f"Running static analysis for {project_name}")
        analyzer = StaticAnalyzer(repo_path)
        analysis = analyzer.analyze()

        # Collect stats similar to diagram_generator.py
        static_stats = {"repo_name": project_name, "languages": {}}

        # Use ProjectScanner to get accurate LOC counts
        scanner = ProjectScanner(repo_path)
        loc_by_language = {pl.language: pl.size for pl in scanner.scan()}

        for language in analysis.get_languages():
            files = analysis.get_source_files(language)
            static_stats["languages"][language] = {
                "file_count": len(files),
                "lines_of_code": loc_by_language.get(language, 0),
            }

        return static_stats

    def extract_metrics(self, project: ProjectSpec, run_data: RunData) -> dict[str, Any]:
        code_stats = run_data.code_stats

        # If no code_stats from monitoring, run static analysis directly
        if not code_stats or "languages" not in code_stats:
            logger.info(f"No code_stats found in monitoring data for {project.name}, running static analysis directly")
            code_stats = self.run_static_analysis(project)

        # Calculate totals
        total_files = 0
        total_loc = 0
        languages_summary = {}

        for lang, stats in code_stats.get("languages", {}).items():
            file_count = stats.get("file_count", 0)
            loc = stats.get("lines_of_code", 0)
            total_files += file_count
            total_loc += loc
            languages_summary[lang] = LanguageSummary(files=file_count, loc=loc)

        return StaticAnalysisMetrics(
            code_stats=StaticAnalysisSummary(
                total_files=total_files,
                total_loc=total_loc,
                languages=languages_summary,
            )
        ).model_dump()

    def generate_report(self, results: list[EvalResult]) -> str:
        header = generate_header("Static Analysis Performance Evaluation")

        # Aggregate totals
        total_files = 0
        total_loc = 0
        for r in results:
            if r.success:
                code_stats = r.metrics.get("code_stats", {})
                total_files += code_stats.get("total_files", 0)
                total_loc += code_stats.get("total_loc", 0)

        lines = [
            header,
            "### Summary",
            "",
            "| Project | Language | Status | Time (s) | Files | LOC |",
            "|---------|----------|--------|----------|-------|-----|",
        ]

        for r in results:
            status = "✅ Success" if r.success else "❌ Failed"
            time_taken = f"{r.duration_seconds:.1f}"
            lang = r.expected_language or "Unknown"

            files = 0
            loc = 0
            if r.success:
                code_stats = r.metrics.get("code_stats", {})
                files = code_stats.get("total_files", 0)
                loc = code_stats.get("total_loc", 0)

            lines.append(f"| {r.project} | {lang} | {status} | {time_taken} | {files:,} | {loc:,} |")

        lines.extend(
            [
                "",
                f"**Total Files:** {total_files:,}",
                f"**Total LOC:** {total_loc:,}",
            ]
        )

        return "\n".join(lines)

    def run(
        self, projects: list[ProjectSpec], extra_args: list[str] | None = None, report_only: bool = False
    ) -> dict[str, Any]:
        """
        Override run() to skip the full pipeline and run static analysis directly.
        This is more efficient than running the full diagram generation pipeline.
        """
        logger.info(f"Starting {self.name} evaluation for {len(projects)} projects")

        self.results = []
        for project in projects:
            logger.info(f"\n{'='*60}\nProject: {project.name}\n{'='*60}")

            start_time = time.time()
            try:
                # Run static analysis directly instead of full pipeline
                code_stats = self.run_static_analysis(project)
                duration = time.time() - start_time

                # Extract metrics
                metrics = self.extract_metrics(project, RunData(run_dir="", code_stats=code_stats))

                # Create evaluation result
                eval_result = EvalResult(
                    project=project.name,
                    url=project.url,
                    expected_language=project.expected_language,
                    success=True,
                    duration_seconds=duration,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    error=None,
                    metrics=metrics,
                )

                self.results.append(eval_result)
                logger.info(f"✅ {project.name} completed in {duration:.1f}s")

            except Exception as e:
                duration = time.time() - start_time
                error_msg = str(e)
                logger.error(f"❌ {project.name} failed: {error_msg}")

                eval_result = EvalResult(
                    project=project.name,
                    url=project.url,
                    expected_language=project.expected_language,
                    success=False,
                    duration_seconds=duration,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    error=error_msg,
                    metrics={},
                )

                self.results.append(eval_result)

        # Generate & save report
        report_content = self.generate_report(self.results)

        from evals.utils import generate_system_specs

        system_specs = generate_system_specs()
        full_report = f"{report_content}\n\n{system_specs}"

        report_path = self.output_dir / f"{self.name}-report.md"
        self._write_report(report_path, full_report)
        logger.info(f"Report generated: {report_path}")

        # Save raw JSON results
        json_path = self.project_root / "evals/artifacts/monitoring_results/reports" / f"{self.name}_eval.json"
        import dataclasses

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
