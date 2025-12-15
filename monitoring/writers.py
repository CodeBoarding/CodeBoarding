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

from pydantic import BaseModel, ConfigDict, PrivateAttr, Field

from monitoring.mixin import MonitoringMixin

logger = logging.getLogger("monitoring")


class StreamingStatsWriter(BaseModel):
    """
    Handles periodic writing of monitoring stats to a JSON file.
    Also tracks run timing and saves run_metadata.json on stop.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    monitoring_dir: Path
    agents_dict: dict[str, MonitoringMixin]
    repo_name: str
    output_dir: str | None = None
    interval: float = 5.0
    start_time: float | None = None

    _stop_event: threading.Event = PrivateAttr(default_factory=threading.Event)
    _thread: threading.Thread | None = PrivateAttr(default=None)
    _logger: logging.Logger = PrivateAttr(default_factory=lambda: logging.getLogger("monitoring.writer"))
    _error: str | None = PrivateAttr(default=None)
    _end_time: float | None = PrivateAttr(default=None)

    @property
    def llm_usage_file(self) -> Path:
        return self.monitoring_dir / "llm_usage.json"

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        error_msg = str(exc_val) if exc_val else None
        self.stop(error=error_msg)

    def start(self):
        """Start the background writer thread and record start time."""
        if self._thread is not None:
            return

        if self.start_time is None:
            self.start_time = time.time()
        self.monitoring_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._logger.info(f"Started streaming monitoring results to {self.monitoring_dir}")

    def stop(self, error: str | None = None):
        """Stop the writer thread, save final stats and run metadata."""
        if self._thread is None:
            return

        self._end_time = time.time()
        self._error = error
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._save_llm_usage()
        self._stream_token_usage()
        self._save_run_metadata()
        self._logger.info("Stopped streaming monitoring results")

    def _loop(self):
        while not self._stop_event.is_set():
            self._save_llm_usage()
            self._stream_token_usage()
            self._stop_event.wait(self.interval)

    def _stream_token_usage(self):
        total_input = 0
        total_output = 0
        total_tokens = 0
        model_name = "unknown"

        for agent in self.agents_dict.values():
            res = agent.get_monitoring_results()
            usage = res.get("token_usage", {})
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            total_tokens += usage.get("total_tokens", 0)
            if res.get("model_name"):
                model_name = str(res.get("model_name"))

        payload = {
            "token_usage": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_tokens,
            },
            "model_name": model_name,
        }
        # Print as a log message
        self._logger.info(f"TokenUsage: {json.dumps(payload)}")

    def _save_llm_usage(self):
        """Save LLM usage stats to llm_usage.json."""
        try:
            agents_payload = {}
            for name, agent in self.agents_dict.items():
                agents_payload[name] = agent.get_monitoring_results()

            if not agents_payload:
                return

            data = {}
            if agents_payload:
                data["agents"] = agents_payload

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
            end_time = self._end_time if self._end_time else time.time()
            duration = end_time - self.start_time if self.start_time else 0

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
