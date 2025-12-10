"""
RunStats: Thread-safe statistics container for monitoring.
"""

import threading
from collections import defaultdict


class RunStats:
    """Thread-safe container for runtime statistics."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        """Reset all statistics to initial state."""
        with self._lock:
            self.total_tokens = 0
            self.input_tokens = 0
            self.output_tokens = 0
            self.tool_counts = defaultdict(int)
            self.tool_errors = defaultdict(int)
            self.tool_latency_ms = defaultdict(list)

    def to_dict(self):
        """Convert stats to a dictionary representation."""
        with self._lock:
            return {
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


# Global stats instance - Lean & Simple for CLI tools
stats = RunStats()
