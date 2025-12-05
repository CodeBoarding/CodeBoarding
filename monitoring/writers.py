"""
Writers for persisting monitoring data to files.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("monitoring")


class StreamingStatsWriter:
    """
    Handles periodic writing of monitoring stats to a JSON file.
    Also tracks run timing and saves run_metadata.json on stop.
    """

    def __init__(
        self,
        monitoring_dir: Path,
        agents_dict: dict,
        repo_name: str,
        output_dir: str | None = None,
        interval: float = 5.0,
    ):
        self.monitoring_dir = Path(monitoring_dir)
        self.llm_usage_file = self.monitoring_dir / "llm_usage.json"
        self.agents_dict = agents_dict
        self.repo_name = repo_name
        self.output_dir = output_dir
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger("monitoring.writer")
        self._start_time: float | None = None
        self._error: str | None = None

    def start(self):
        """Start the background writer thread and record start time."""
        if self._thread is not None:
            return

        self._start_time = time.time()
        self.monitoring_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._logger.info(f"Started streaming monitoring results to {self.monitoring_dir}")

    def stop(self, error: str | None = None):
        """Stop the writer thread, save final stats and run metadata."""
        if self._thread is None:
            return

        self._error = error
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._save_llm_usage()
        self._save_run_metadata()
        self._logger.info("Stopped streaming monitoring results")

    def _loop(self):
        while not self._stop_event.is_set():
            self._save_llm_usage()
            time.sleep(self.interval)

    def _save_llm_usage(self):
        """Save LLM usage stats to llm_usage.json."""
        try:
            agents_payload = {}
            for name, agent in self.agents_dict.items():
                if agent and hasattr(agent, "get_monitoring_results"):
                    agents_payload[name] = agent.get_monitoring_results()

            if not agents_payload:
                return

            data = {"agents": agents_payload}

            # Atomic write
            temp_file = self.llm_usage_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self.llm_usage_file)

        except Exception as e:
            self._logger.error(f"Failed to write LLM usage stats: {e}")

    def _save_run_metadata(self):
        """Save run metadata including timing information."""
        try:
            duration = time.time() - self._start_time if self._start_time else 0

            # Count output files
            json_count = 0
            md_count = 0
            if self.output_dir:
                output_path = Path(self.output_dir)
                if output_path.exists():
                    json_count = len(list(output_path.glob("*.json")))
                    md_count = len(list(output_path.glob("*.md")))

            metadata = {
                "repo_name": self.repo_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": round(duration, 2),
                "success": self._error is None,
                "error": self._error,
                "files_generated": {
                    "json": json_count,
                    "markdown": md_count,
                },
                "output_dir": self.output_dir,
            }

            metadata_file = self.monitoring_dir / "run_metadata.json"
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)

        except Exception as e:
            self._logger.error(f"Failed to write run metadata: {e}")


def save_static_stats(monitoring_dir: Path, stats_dict: dict):
    """Save static analysis stats to code_stats.json."""
    try:
        monitoring_dir.mkdir(parents=True, exist_ok=True)
        stats_file = monitoring_dir / "code_stats.json"
        with open(stats_file, "w") as f:
            json.dump(stats_dict, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save static stats: {e}")
