import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.tasks.accuracy.models import (
    HistoricalReasoning,
    HistoricalRun,
    ScoreHistory,
)

logger = logging.getLogger(__name__)


class ScoreHistoryStore:
    """
    Persistent storage for accuracy evaluation score history.

    Stores scores, reasoning, and metadata in a JSON file that survives
    across evaluation runs. The report is generated from this data,
    not the other way around.

    Example:
        store = ScoreHistoryStore(output_dir)
        store.append_run(
            commit="abc123",
            scores={"markitdown-depth-1": 8.5},
            reasoning=[...],
            system_specs={...},
        )
        history = store.load()
    """

    FILENAME = "accuracy_history.json"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.path = output_dir / self.FILENAME

    def load(self) -> ScoreHistory:
        if not self.path.exists():
            return ScoreHistory()

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ScoreHistory.model_validate(data)
        except Exception as e:
            logger.warning("Failed to load score history: %s", e)
            return ScoreHistory()

    def save(self, history: ScoreHistory) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            history.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def append_run(
        self,
        commit: str,
        scores: dict[str, float | None],
        reasoning: list[HistoricalReasoning],
        system_specs: dict[str, str],
        project_sizes: dict[str, str] | None = None,
    ) -> ScoreHistory:
        history = self.load()

        run = HistoricalRun(
            commit=commit,
            timestamp=datetime.now(timezone.utc).isoformat(),
            scores=scores,
            system_specs=system_specs,
        )
        history.runs.append(run)

        history.reasoning.extend(reasoning)

        if project_sizes:
            history.project_sizes.update(project_sizes)

        self.save(history)
        return history

    def get_commits(self) -> list[str]:
        history = self.load()
        return [run.commit for run in history.runs]

    def get_project_names(self) -> set[str]:
        history = self.load()
        projects: set[str] = set()
        for run in history.runs:
            projects.update(run.scores.keys())
        return projects

    def get_scores_by_depth(self, depth: int) -> dict[str, list[tuple[str, float | None]]]:
        history = self.load()
        suffix = f"-depth-{depth}"

        result: dict[str, list[tuple[str, float | None]]] = {}

        for run in history.runs:
            for project, score in run.scores.items():
                if project.endswith(suffix):
                    base_name = project[: -len(suffix)]
                    if base_name not in result:
                        result[base_name] = []
                    result[base_name].append((run.commit, score))

        return result

    def get_average_by_size(
        self,
        commit: str,
        depth: int | None = None,
    ) -> dict[str, float | None]:
        history = self.load()

        run = next((r for r in history.runs if r.commit == commit), None)
        if not run:
            return {}

        size_scores: dict[str, list[float]] = {}

        for project, score in run.scores.items():
            if score is None:
                continue

            if depth is not None and f"-depth-{depth}" not in project:
                continue

            size_label = history.project_sizes.get(project, "unknown")
            if size_label not in size_scores:
                size_scores[size_label] = []
            size_scores[size_label].append(score)

        return {size: sum(scores) / len(scores) if scores else None for size, scores in size_scores.items()}


def get_system_specs() -> dict[str, str]:
    import os
    import platform

    from evals.utils import get_git_user

    return {
        "OS": platform.system(),
        "CPU": platform.processor() or platform.machine(),
        "Cores": str(os.cpu_count()),
        "User": get_git_user(),
    }
