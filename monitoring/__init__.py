"""
Monitoring package for tracking LLM usage, tool calls, and static analysis metrics.

Usage:
    from monitoring import RunStats, MonitoringCallback, StreamingStatsWriter
    from monitoring import monitor_execution, trace, current_step
"""

from .stats import RunStats
from .callbacks import MonitoringCallback
from .writers import StreamingStatsWriter, save_static_stats
from .context import monitor_execution, trace, current_step
