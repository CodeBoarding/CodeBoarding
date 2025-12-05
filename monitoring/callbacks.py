"""
LangChain callback handler for monitoring LLM usage.
"""

import json
import logging
import time
from typing import Any, Dict, Mapping, MutableMapping, cast
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from monitoring.stats import RunStats, stats
from monitoring.context import current_step

logger = logging.getLogger("monitoring")


class MonitoringCallback(BaseCallbackHandler):
    """
    Captures LLM events, tags them with the current step, and updates stats.
    """

    def __init__(self, stats_container: RunStats | None = None):
        # runtime bookkeeping
        self._tool_start_times: Dict[str, float] = {}  # run_id -> start_time
        self._tool_names: Dict[str, str] = {}  # run_id -> tool_name
        self.stats = stats_container if stats_container is not None else stats

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        step_name = current_step.get()

        # Extract usage
        usage = self._extract_usage(response)

        if not usage:
            return

        # Update State
        with self.stats._lock:
            self.stats.total_tokens += usage.get("total_tokens", 0)
            self.stats.input_tokens += usage.get("prompt_tokens", 0)
            self.stats.output_tokens += usage.get("completion_tokens", 0)

        # Log Event
        logger.info(
            json.dumps(
                {
                    "event": "llm_usage",
                    "step": step_name,
                    "model": response.llm_output.get("model_name") if response.llm_output else "unknown",
                    "usage": usage,
                }
            )
        )

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        run_id_any = kwargs.get("run_id")
        run_id: str | None = str(run_id_any) if run_id_any else None
        tool_name = (
            serialized.get("name")
            or serialized.get("id")
            or serialized.get("lc_namespace", ["tool"])[-1]
            or "unknown_tool"
        )
        with self.stats._lock:
            self.stats.tool_counts[tool_name] += 1

        now = time.time()
        if run_id:
            self._tool_start_times[run_id] = now
            self._tool_names[run_id] = tool_name

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id_any = kwargs.get("run_id")
        run_id: str | None = str(run_id_any) if run_id_any else None
        if run_id and run_id in self._tool_start_times:
            start = self._tool_start_times.pop(run_id)
            tool_name = self._tool_names.pop(run_id, "unknown_tool")
            latency = int((time.time() - start) * 1000)
            with self.stats._lock:
                self.stats.tool_latency_ms[tool_name].append(latency)

    def on_tool_error(
        self, error: BaseException, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any
    ) -> Any:
        tool_name = "unknown_tool"
        run_id_str = str(run_id)
        if run_id_str in self._tool_names:
            tool_name = self._tool_names[run_id_str]
        with self.stats._lock:
            self.stats.tool_errors[tool_name] += 1

        # Clean up any in-flight timing
        if run_id_str in self._tool_start_times:
            self._tool_start_times.pop(run_id_str, None)
            self._tool_names.pop(run_id_str, None)

    def _extract_usage(self, response: LLMResult) -> Dict[str, int]:
        def _coerce_int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        def _extract_usage_from_mapping(mapping: Mapping[str, Any]) -> dict[str, int]:
            # Handle both prompt/completion and input/output styles
            prompt = mapping.get("prompt_tokens", mapping.get("input_tokens", 0))
            completion = mapping.get("completion_tokens", mapping.get("output_tokens", 0))
            total = mapping.get("total_tokens", mapping.get("total_token_count", None))

            prompt_i = _coerce_int(prompt)
            completion_i = _coerce_int(completion)

            if total is None:
                total_i = prompt_i + completion_i
            else:
                total_i = _coerce_int(total)

            return {
                "prompt_tokens": prompt_i,
                "completion_tokens": completion_i,
                "total_tokens": total_i,
            }

        usage_mapping: MutableMapping[str, Any] = {}

        # 1) Try llm_output
        llm_output = response.llm_output or {}
        if "token_usage" in llm_output:
            raw = cast(Mapping[str, Any], llm_output.get("token_usage") or {})
            usage_mapping = dict(raw)
        elif "usage" in llm_output:
            raw = cast(Mapping[str, Any], llm_output.get("usage") or {})
            usage_mapping = dict(raw)

        # 2) Fallback to first generation's message metadata
        if not usage_mapping and response.generations:
            first_gen = response.generations[0][0]
            message = getattr(first_gen, "message", None) or getattr(first_gen, "text", None)
            meta: Mapping[str, Any] = {}
            usage_meta: Mapping[str, Any] = {}

            if message is not None:
                meta = getattr(message, "response_metadata", {}) or {}
                usage_meta = getattr(message, "usage_metadata", {}) or {}

            if "token_usage" in meta:
                raw = cast(Mapping[str, Any], meta.get("token_usage") or {})
                usage_mapping = dict(raw)
            elif "usage" in meta:
                raw = cast(Mapping[str, Any], meta.get("usage") or {})
                usage_mapping = dict(raw)
            elif usage_meta:
                usage_mapping = dict(usage_meta)

        if not usage_mapping:
            return {}

        return _extract_usage_from_mapping(usage_mapping)
