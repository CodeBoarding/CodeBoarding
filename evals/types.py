from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ProjectSpec:
    name: str
    url: str
    expected_language: str


@dataclass
class PipelineResult:
    success: bool
    stderr: str
    pipeline_duration: float
    timestamp: str


@dataclass
class RunData:
    run_dir: str
    metadata: dict[str, Any] = field(default_factory=dict)
    code_stats: dict[str, Any] = field(default_factory=dict)
    llm_usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    project: str
    url: str
    expected_language: Optional[str]
    success: bool
    duration_seconds: float
    timestamp: str
    error: Optional[str] = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Helper to ease transition from dict.
        Checks attributes first, then metrics dict.
        """
        if hasattr(self, key):
            return getattr(self, key)
        return self.metrics.get(key, default)
