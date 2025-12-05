from typing import Any
from evals.base import BaseEval
from evals.utils import generate_header


class EndToEndEval(BaseEval):
    """
    Evaluates the full execution pipeline to determine overall success rates and resource consumption.
    Aggregates metrics such as token usage, tool calls, and execution time across all agents.
    Provides a high-level summary of system reliability and performance on real-world projects.
    """
    def _aggregate_llm_usage(self, llm_data: dict) -> dict[str, Any]:
        """Aggregate token and tool usage across all agents."""
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

        return {
            "token_usage": {
                "total_tokens": total_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "tool_usage": {"counts": tool_counts, "errors": tool_errors},
        }

    def extract_metrics(self, project: dict, run_data: dict) -> dict[str, Any]:
        llm_data = run_data.get("llm_usage", {})
        code_stats = run_data.get("code_stats", {})

        return {
            "monitoring": self._aggregate_llm_usage(llm_data),
            "code_stats": code_stats,
            "mermaid_diagram": "",
        }

    def generate_report(self, results: list[dict]) -> str:
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
            status = "✅ Success" if r.get("success") else "❌ Failed"
            time_taken = f"{r.get('duration_seconds', 0):.1f}"
            lang = r.get("expected_language", "Unknown")

            if r.get("success"):
                success_count += 1
                monitoring = r.get("monitoring", {})
                token_usage = monitoring.get("token_usage", {})
                tool_usage = monitoring.get("tool_usage", {})

                t_tokens = token_usage.get("total_tokens", 0)
                t_tools = sum(tool_usage.get("counts", {}).values())

                total_tokens_all += t_tokens
                total_tools_all += t_tools
            else:
                t_tokens = 0
                t_tools = 0

            lines.append(
                f"| {r.get('project', 'Unknown')} | {lang} | {status} | {time_taken} | {t_tokens:,} | {t_tools} |"
            )

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
            project_name = r.get("project", "Unknown")
            mermaid_diagram = r.get("mermaid_diagram", "")

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
