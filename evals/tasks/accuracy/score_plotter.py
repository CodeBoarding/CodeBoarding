import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from evals.tasks.accuracy.models import CodeSizeCategory, ScoreHistory

logger = logging.getLogger(__name__)


THEME = {
    "background": "#1a1a2e",
    "text": "#e0e0e0",
    "text_secondary": "#b0b0b0",
    "grid": "#4a4a6a",
    "spine": "#4a4a6a",
    "legend_bg": "#2a2a4e",
    "average_line": "#ffffff",
}

PROJECT_COLORS = [
    "#22c55e",
    "#3b82f6",
    "#f59e0b",
    "#ef4444",
    "#a855f7",
    "#06b6d4",
    "#ec4899",
    "#84cc16",
    "#f97316",
    "#6366f1",
    "#14b8a6",
    "#eab308",
]

SIZE_MARKERS = {
    CodeSizeCategory.SMALL: "o",
    CodeSizeCategory.MEDIUM: "s",
    CodeSizeCategory.LARGE: "^",
    CodeSizeCategory.HUGE: "D",
    CodeSizeCategory.UNKNOWN: "x",
}


class ScoreHistoryPlotter:
    """
    Generates score history plots for accuracy evaluation.

    Creates side-by-side line plots showing score progression across commits,
    with one subplot per depth level.

    Example:
        plotter = ScoreHistoryPlotter(output_dir)
        filename = plotter.generate(history, project_sizes)
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def generate(
        self,
        history: ScoreHistory,
        depth_levels: list[int] | None = None,
    ) -> str | None:
        if not history.runs:
            return None

        try:
            depth_data = self._organize_by_depth(history, depth_levels)

            if not depth_data:
                return None

            num_plots = len(depth_data)
            fig_width = 7 * num_plots
            fig, axes = plt.subplots(1, num_plots, figsize=(fig_width, 6), squeeze=False)
            fig.patch.set_facecolor(THEME["background"])

            commits = [run.commit for run in history.runs]

            for plot_idx, (depth, projects) in enumerate(sorted(depth_data.items())):
                ax = axes[0, plot_idx]
                self._plot_depth(ax, history, depth, projects, commits)

            plt.tight_layout()

            filename = "accuracy-score-history.png"
            plot_path = self.output_dir / filename
            fig.savefig(
                plot_path,
                dpi=150,
                bbox_inches="tight",
                facecolor=THEME["background"],
                edgecolor="none",
            )
            plt.close(fig)

            logger.info("Generated score history plot: %s", plot_path)
            return filename

        except Exception as e:
            logger.warning("Failed to generate score history plot: %s", e)
            return None

    def _organize_by_depth(
        self,
        history: ScoreHistory,
        depth_levels: list[int] | None = None,
    ) -> dict[int, list[str]]:
        depth_projects: dict[int, list[str]] = {}

        all_projects: set[str] = set()
        for run in history.runs:
            all_projects.update(run.scores.keys())

        for project in all_projects:
            depth = self._extract_depth(project)
            if depth_levels is not None and depth not in depth_levels:
                continue
            depth_projects.setdefault(depth, []).append(project)

        return depth_projects

    def _extract_depth(self, project_name: str) -> int:
        if "-depth-" in project_name:
            try:
                return int(project_name.rsplit("-depth-", 1)[-1])
            except ValueError:
                pass
        return 1

    def _get_base_name(self, project_name: str) -> str:
        if "-depth-" in project_name:
            return project_name.rsplit("-depth-", 1)[0]
        return project_name

    def _plot_depth(
        self,
        ax: plt.Axes,
        history: ScoreHistory,
        depth: int,
        projects: list[str],
        commits: list[str],
    ) -> None:
        ax.set_facecolor(THEME["background"])

        all_scores_for_avg: list[list[float | None]] = []

        for idx, project in enumerate(sorted(projects)):
            scores = self._get_project_scores(history, project)
            all_scores_for_avg.append(scores)

            color = PROJECT_COLORS[idx % len(PROJECT_COLORS)]
            size_label = history.project_sizes.get(project, "unknown")
            size_cat = CodeSizeCategory.from_label(size_label)
            marker = SIZE_MARKERS.get(size_cat, "o")

            valid_points = [(i, s) for i, s in enumerate(scores) if s is not None]
            if not valid_points:
                continue

            x_vals, y_vals = zip(*valid_points)
            base_name = self._get_base_name(project)

            ax.plot(
                x_vals,
                y_vals,
                marker=marker,
                label=f"{base_name} [{size_cat.char}]",
                color=color,
                linewidth=2,
                markersize=8,
                alpha=0.85,
            )

        self._plot_average(ax, all_scores_for_avg)

        self._style_axis(ax, depth, commits)

    def _get_project_scores(
        self,
        history: ScoreHistory,
        project: str,
    ) -> list[float | None]:
        return [run.scores.get(project) for run in history.runs]

    def _plot_average(
        self,
        ax: plt.Axes,
        all_scores: list[list[float | None]],
    ) -> None:
        if not all_scores:
            return

        num_runs = len(all_scores[0]) if all_scores else 0
        avg_scores: list[float | None] = []

        for run_idx in range(num_runs):
            run_scores = [scores[run_idx] for scores in all_scores if scores[run_idx] is not None]
            if run_scores:
                avg_scores.append(sum(run_scores) / len(run_scores))
            else:
                avg_scores.append(None)

        valid_points = [(i, s) for i, s in enumerate(avg_scores) if s is not None]
        if not valid_points:
            return

        x_vals, y_vals = zip(*valid_points)
        ax.plot(
            x_vals,
            y_vals,
            marker="s",
            label="Average",
            color=THEME["average_line"],
            linewidth=3,
            markersize=8,
            linestyle="--",
            alpha=0.9,
        )

    def _style_axis(
        self,
        ax: plt.Axes,
        depth: int,
        commits: list[str],
    ) -> None:
        ax.set_xlabel("Commit", fontsize=12, color=THEME["text"])
        ax.set_ylabel("Similarity Score", fontsize=12, color=THEME["text"])

        depth_title = f"Level {depth}: {'Architecture Overview' if depth == 1 else 'Component Internals'}"
        ax.set_title(depth_title, fontsize=14, fontweight="bold", color=THEME["text"])

        ax.set_xticks(range(len(commits)))
        ax.set_xticklabels(
            commits,
            rotation=45,
            ha="right",
            fontsize=10,
            color=THEME["text_secondary"],
        )

        ax.set_ylim(0, 10.5)
        ax.set_yticks(range(0, 11))
        ax.tick_params(axis="y", colors=THEME["text_secondary"])

        ax.grid(True, linestyle="--", alpha=0.3, color=THEME["grid"])
        ax.set_axisbelow(True)

        legend = ax.legend(
            loc="upper left",
            bbox_to_anchor=(1.02, 1),
            fontsize=9,
            framealpha=0.9,
        )
        legend.get_frame().set_facecolor(THEME["legend_bg"])
        for text in legend.get_texts():
            text.set_color(THEME["text"])

        for spine in ax.spines.values():
            spine.set_color(THEME["spine"])
