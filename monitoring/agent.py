import sys, json, time
import os
import functools
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class MonitoringCallback(BaseCallbackHandler):
    def __init__(self):
        # token usage
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

        # tool accounting
        self.tool_counts = defaultdict(int)  # times each tool was called
        self.tool_errors = defaultdict(int)  # errors per tool
        self.tool_latency_ms = defaultdict(list)  # latencies per tool

        # runtime bookkeeping
        self._tool_start_times = {}  # run_id -> start_time
        self._tool_names = {}  # run_id -> tool_name

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage: Dict[str, int] = (response.llm_output or {}).get("token_usage", {}) or {}
        self.prompt_tokens += int(usage.get("prompt_tokens", 0))
        self.completion_tokens += int(usage.get("completion_tokens", 0))
        if usage.get("total_tokens") is not None:
            self.total_tokens += int(usage.get("total_tokens", 0))
        else:
            self.total_tokens += int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
        # LangChain passes a 'run_id' you can use to correlate start/end
        run_id: Optional[str] = kwargs.get("run_id")
        tool_name = (
            serialized.get("name")
            or serialized.get("id")
            or serialized.get("lc_namespace", ["tool"])[-1]
            or "unknown_tool"
        )
        self.tool_counts[tool_name] += 1
        now = time.time()
        if run_id:
            self._tool_start_times[run_id] = now
            self._tool_names[run_id] = tool_name

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id: Optional[str] = kwargs.get("run_id")
        if run_id and run_id in self._tool_start_times:
            start = self._tool_start_times.pop(run_id)
            tool_name = self._tool_names.pop(run_id, "unknown_tool")
            self.tool_latency_ms[tool_name].append(int((time.time() - start) * 1000))

    def on_tool_error(self, error: Exception, **kwargs: Any) -> None:
        run_id: Optional[str] = kwargs.get("run_id")
        tool_name = "unknown_tool"
        if run_id and run_id in self._tool_names:
            tool_name = self._tool_names[run_id]
        self.tool_errors[tool_name] += 1
        # Clean up any in-flight timing
        if run_id and run_id in self._tool_start_times:
            self._tool_start_times.pop(run_id, None)
            self._tool_names.pop(run_id, None)


def monitoring(func):
    """
    Decorator that enables monitoring for agent methods.
    Checks ENABLE_MONITORING environment variable to determine if monitoring should be active.
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        enable_monitoring = os.getenv("ENABLE_MONITORING", "").lower() in ("true", "1", "yes", "on")
        if not enable_monitoring:
            return func(self, *args, **kwargs)

        if not hasattr(self, "_monitoring_callback") or self._monitoring_callback is None:
            self._monitoring_callback = MonitoringCallback()

        original_func = func

        return original_func(self, *args, **kwargs)

    return wrapper
