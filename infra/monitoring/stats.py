"""
RunStats: Thread-safe statistics container for monitoring.
"""

import threading
from collections import defaultdict
from contextvars import ContextVar


class RunStats:
    """Thread-safe container for runtime statistics."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        """Reset all statistics to initial state."""
        with self._lock:
            self.model_name = None
            self.total_tokens = 0
            self.input_tokens = 0
            self.output_tokens = 0
            self.tool_counts = defaultdict(int)
            self.tool_errors = defaultdict(int)
            self.tool_latency_ms = defaultdict(list)

    def merge(self, other: "RunStats") -> None:
        """Fold another RunStats' token and tool totals into this one (thread-safe)."""
        with self._lock, other._lock:
            self.total_tokens += other.total_tokens
            self.input_tokens += other.input_tokens
            self.output_tokens += other.output_tokens
            for tool, count in other.tool_counts.items():
                self.tool_counts[tool] += count
            for tool, count in other.tool_errors.items():
                self.tool_errors[tool] += count
            for tool, latencies in other.tool_latency_ms.items():
                self.tool_latency_ms[tool].extend(latencies)

    def to_dict(self):
        """Convert stats to a dictionary representation."""
        with self._lock:
            return {
                "model_name": self.model_name,
                "token_usage": {
                    "total_tokens": self.total_tokens,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                },
                "tool_usage": {
                    "counts": dict(self.tool_counts),
                    "errors": dict(self.tool_errors),
                    "avg_latency_ms": {
                        tool: sum(latencies) / len(latencies) if latencies else 0
                        for tool, latencies in self.tool_latency_ms.items()
                    },
                },
            }


# Context variable for the current RunStats instance
current_stats: ContextVar[RunStats] = ContextVar("current_stats")
