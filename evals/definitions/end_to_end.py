import logging
from pathlib import Path
from typing import Any

from agents.agent_responses import AnalysisInsights
from evals.base import BaseEval
from evals.schemas import (
    EndToEndMetrics,
    EvalResult,
    MonitoringMetrics,
    ProjectSpec,
    RunData,
    TokenUsage,
    ToolUsage,
)
from evals.utils import generate_header
from output_generators.markdown import generated_mermaid_str

logger = logging.getLogger(__name__)


def _strip_mermaid_fences(mermaid: str) -> str:
    """
    `output_generators.markdown.generated_mermaid_str()` returns a fenced block:
    ```mermaid
    ...
    ```
    This report writer adds its own fences, so we strip them here.
    """

    lines = mermaid.splitlines()
    if lines and lines[0].strip() == "```mermaid":
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip("\n")


class EndToEndEval(BaseEval):
    """
    Evaluates the full execution pipeline to determine overall success rates and resource consumption.
    Aggregates metrics such as token usage, tool calls, and execution time across all agents.
    Provides a high-level summary of system reliability and performance on real-world projects.
    """

    def _aggregate_llm_usage(self, llm_data: dict) -> MonitoringMetrics:
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        tool_counts: dict[str, int] = {}
        tool_errors: dict[str, int] = {}

        for agent_data in llm_data.get("agents", {}).values():
            token_usage = agent_data.get("token_usage", {})
            total_tokens += token_usage.get("total_tokens", 0)
            input_tokens += token_usage.get("input_tokens", 0)
            output_tokens += token_usage.get("output_tokens", 0)

            for tool, count in agent_data.get("tool_usage", {}).get("counts", {}).items():
                tool_counts[tool] = tool_counts.get(tool, 0) + count
            for tool, count in agent_data.get("tool_usage", {}).get("errors", {}).items():
                tool_errors[tool] = tool_errors.get(tool, 0) + count

        return MonitoringMetrics(
            token_usage=TokenUsage(
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            tool_usage=ToolUsage(
                counts=tool_counts,
                errors=tool_errors,
            ),
        )

    def _project_artifacts_dir(self, project_name: str) -> Path:
        return self.project_root / "evals" / "artifacts" / project_name

    def _load_top_level_mermaid_diagram(self, project_name: str) -> str:
        artifacts_dir = self._project_artifacts_dir(project_name)
        analysis_path = artifacts_dir / "analysis.json"
        if not analysis_path.exists():
            logger.warning("End-to-end report: missing analysis.json at %s", analysis_path)
            return ""

        try:
            insights = AnalysisInsights.model_validate_json(analysis_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("End-to-end report: failed to parse %s (%s)", analysis_path, e)
            return ""

        linked_files = list(artifacts_dir.glob("*.json"))
        # end-to-end report is written to evals/reports/, so point click-links at ../artifacts/<project>/...
        repo_ref = f"../artifacts/{project_name}"

        try:
            mermaid_fenced = generated_mermaid_str(
                analysis=insights,
                linked_files=linked_files,
                repo_ref=repo_ref,
                project=project_name,
                demo=False,
            )
        except Exception as e:
            logger.warning("End-to-end report: failed to generate Mermaid for %s (%s)", project_name, e)
            return ""

        return _strip_mermaid_fences(mermaid_fenced)

    def extract_metrics(self, project: ProjectSpec, run_data: RunData) -> dict[str, Any]:
        llm_data = run_data.llm_usage
        code_stats = run_data.code_stats

        # Only embed diagrams for successful runs (when metadata is available).
        meta = run_data.metadata or {}
        is_success = meta.get("success", True)
        mermaid_diagram = self._load_top_level_mermaid_diagram(project.name) if is_success else ""

        return EndToEndMetrics(
            monitoring=self._aggregate_llm_usage(llm_data),
            code_stats=code_stats,
            mermaid_diagram=mermaid_diagram,
        ).model_dump()

    def generate_report(self, results: list[EvalResult]) -> str:
        header = generate_header("End-to-End Pipeline Evaluation")

        lines = [
            header,
            "### Summary",
            "",
            "| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |",
            "|---------|----------|--------|----------|--------------|------------|",
        ]

        total_tokens_all = 0
        total_tools_all = 0
        success_count = 0

        for r in results:
            status = "✅ Success" if r.success else "❌ Failed"
            time_taken = f"{r.duration_seconds:.1f}"
            lang = r.expected_language or "Unknown"

            if r.success:
                success_count += 1
                monitoring = r.metrics.get("monitoring", {})
                token_usage = monitoring.get("token_usage", {})
                tool_usage = monitoring.get("tool_usage", {})

                t_tokens = token_usage.get("total_tokens", 0)
                t_tools = sum(tool_usage.get("counts", {}).values())

                total_tokens_all += t_tokens
                total_tools_all += t_tools
            else:
                t_tokens = 0
                t_tools = 0

            lines.append(f"| {r.project} | {lang} | {status} | {time_taken} | {t_tokens:,} | {t_tools} |")

        lines.extend(
            [
                "",
                f"**Success:** {success_count}/{len(results)}",
                f"**Total Tokens:** {total_tokens_all:,}",
                f"**Total Tool Calls:** {total_tools_all}",
            ]
        )

        # Add Generated Diagrams section (Placeholder logic as per original code)
        lines.extend(
            [
                "",
                "## Generated Top-Level Diagrams",
                "",
            ]
        )

        for r in results:
            project_name = r.project
            mermaid_diagram = r.metrics.get("mermaid_diagram", "")

            lines.append(f"### {project_name}")
            lines.append("")

            if mermaid_diagram:
                lines.append("```mermaid")
                lines.append(mermaid_diagram)
                lines.append("```")
            else:
                lines.append("*No diagram generated for this project.*")

            lines.append("")

        return "\n".join(lines)
