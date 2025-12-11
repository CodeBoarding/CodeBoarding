import dataclasses
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from evals.base import BaseEval
from evals.schemas import AgentTokenBreakdown, ScalabilityMetrics
from evals.types import EvalResult, ProjectSpec, RunData
from evals.utils import generate_header


class ScalabilityEval(BaseEval):
    """
    Measures system performance characteristics as the size of the input codebase increases.
    Correlates Lines of Code (LOC) with execution time and token usage to assess efficiency.
    Generates visual plots to identify scaling trends and potential bottlenecks in the architecture.
    """

    def extract_metrics(self, project: ProjectSpec, run_data: RunData) -> dict[str, Any]:
        code_stats = run_data.code_stats
        llm_usage = run_data.llm_usage

        # Calculate total LOC
        total_loc = 0
        for stats in code_stats.get("languages", {}).values():
            total_loc += stats.get("lines_of_code", 0)

        # Calculate total tokens
        total_tokens = 0
        agent_token_usage: dict[str, AgentTokenBreakdown] = {}
        agent_tool_usage: dict[str, dict[str, int]] = {}

        for agent_name, agent_data in llm_usage.get("agents", {}).items():
            token_usage = agent_data.get("token_usage", {})
            total_tokens += token_usage.get("total_tokens", 0)

            # Store detailed usage for charts
            agent_token_usage[agent_name] = AgentTokenBreakdown(
                input=token_usage.get("input_tokens", 0),
                output=token_usage.get("output_tokens", 0),
            )

            tool_usage = agent_data.get("tool_usage", {}).get("counts", {})
            agent_tool_usage[agent_name] = tool_usage

        return ScalabilityMetrics(
            loc=total_loc,
            total_tokens=total_tokens,
            agent_token_usage=agent_token_usage,
            agent_tool_usage=agent_tool_usage,
        ).model_dump()

    def generate_report(self, results: list[EvalResult]) -> str:
        # Flatten results for DataFrame processing
        flat_results = []
        for r in results:
            item = dataclasses.asdict(r)
            metrics = item.pop("metrics", {})
            item.update(metrics)
            flat_results.append(item)

        # Filter valid results
        data = [r for r in flat_results if r.get("success") and r.get("loc", 0) > 0]

        if not data:
            return generate_header("Scalability Evaluation") + "\n\nNo successful runs to analyze."

        df = pd.DataFrame(data)

        # Prepare assets directory
        assets_dir = self.output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        # Set seaborn style
        sns.set_theme(style="whitegrid")

        # Plot 1: LOC vs Duration
        plt.figure(figsize=(10, 6))
        sns.regplot(data=df, x="loc", y="duration_seconds")
        plt.title("Scalability: LOC vs Duration")
        plt.xlabel("Lines of Code")
        plt.ylabel("Duration (seconds)")
        plt.tight_layout()
        plt.savefig(assets_dir / "loc_vs_duration.png")
        plt.close()

        # Plot 2: LOC vs Tokens
        plt.figure(figsize=(10, 6))
        sns.regplot(data=df, x="loc", y="total_tokens", color="orange")
        plt.title("Scalability: LOC vs Tokens")
        plt.xlabel("Lines of Code")
        plt.ylabel("Total Tokens")
        plt.tight_layout()
        plt.savefig(assets_dir / "loc_vs_tokens.png")
        plt.close()

        # Plot 3: Token Usage per Agent (Stacked Bar)
        # We'll take the average usage across projects for a representative view
        avg_agent_tokens: dict[str, dict[str, list[int]]] = {}
        for row in data:
            for agent, usage in row.get("agent_token_usage", {}).items():
                if agent not in avg_agent_tokens:
                    avg_agent_tokens[agent] = {"input": [], "output": []}
                avg_agent_tokens[agent]["input"].append(usage["input"])
                avg_agent_tokens[agent]["output"].append(usage["output"])

        # Calculate averages
        agents = []
        inputs = []
        outputs = []
        for agent, usage in avg_agent_tokens.items():
            agents.append(agent)
            inputs.append(sum(usage["input"]) / len(usage["input"]))
            outputs.append(sum(usage["output"]) / len(usage["output"]))

        if agents:
            plt.figure(figsize=(12, 6))
            plt.bar(agents, inputs, label="Input Tokens")
            plt.bar(agents, outputs, bottom=inputs, label="Output Tokens")
            plt.title("Average Token Usage per Agent")
            plt.xlabel("Agent")
            plt.ylabel("Tokens")
            plt.legend()
            plt.xticks(rotation=45)
            plt.tight_layout()
            plt.savefig(assets_dir / "agent_token_usage.png")
            plt.close()

        # Plot 4: Tool Usage Counts per Agent
        # We'll aggregate all tool calls across all projects
        tool_data = []
        for row in data:
            for agent, tools in row.get("agent_tool_usage", {}).items():
                for tool, count in tools.items():
                    tool_data.append({"Agent": agent, "Tool": tool, "Count": count})

        if tool_data:
            tool_df = pd.DataFrame(tool_data)
            # Sum counts for duplicate (Agent, Tool) pairs across projects
            tool_df = tool_df.groupby(["Agent", "Tool"]).sum().reset_index()

            plt.figure(figsize=(12, 8))
            sns.barplot(data=tool_df, x="Agent", y="Count", hue="Tool")
            plt.title("Total Tool Usage Counts per Agent")
            plt.xlabel("Agent")
            plt.ylabel("Count")
            plt.legend(title="Tool", bbox_to_anchor=(1.05, 1), loc="upper left")
            plt.xticks(rotation=45)
            plt.tight_layout()
            plt.savefig(assets_dir / "agent_tool_usage.png")
            plt.close()

        # Generate Markdown
        header = generate_header("Scalability Evaluation")

        lines = [
            header,
            "### Scalability Visualizations",
            "",
            "![LOC vs Duration](./assets/loc_vs_duration.png)",
            "",
            "![LOC vs Tokens](./assets/loc_vs_tokens.png)",
            "",
            "### Agent Performance",
            "",
            "#### Token Usage per Agent",
            "![Agent Token Usage](./assets/agent_token_usage.png)",
            "*Shows the average distribution of input and output tokens for each agent.*",
            "",
            "#### Tool Usage Counts per Agent",
            "![Agent Tool Usage](./assets/agent_tool_usage.png)",
            "*Details which tools were used by each agent and how frequently.*",
            "",
            "### Data Summary",
            "",
            "| Project | LOC | Duration (s) | Tokens |",
            "|---------|-----|--------------|--------|",
        ]

        for row in data:
            lines.append(
                f"| {row.get('project')} | {row.get('loc', 0):,} | {row.get('duration_seconds', 0):.1f} | {row.get('total_tokens', 0):,} |"
            )

        return "\n".join(lines)
