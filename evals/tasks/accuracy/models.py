from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CodeSizeCategory(Enum):
    """Code size categories with display character and sort order."""

    SMALL = ("S", "small", 0)
    MEDIUM = ("M", "medium", 1)
    LARGE = ("L", "large", 2)
    HUGE = ("H", "huge", 3)
    UNKNOWN = ("?", "unknown", 4)

    def __init__(self, char: str, label: str, order: int):
        self.char = char
        self.label = label
        self.order = order

    @classmethod
    def from_label(cls, label: str) -> "CodeSizeCategory":
        for category in cls:
            if category.label == label.lower():
                return category
        return cls.UNKNOWN

    @classmethod
    def from_char(cls, char: str) -> "CodeSizeCategory":
        for category in cls:
            if category.char == char.upper():
                return category
        return cls.UNKNOWN


class SimilarityScoreOutput(BaseModel):
    """
    LLM output schema for diagram similarity scoring.

    Used with LangChain's with_structured_output() to guarantee valid responses.
    All fields are required since the LLM must provide complete structured output.
    """

    score: int = Field(
        ...,
        ge=1,
        le=10,
        description="Similarity score from 1 (completely unrelated) to 10 (identical structure and semantics)",
    )
    node_coverage_reasoning: str = Field(
        ...,
        description="Concise 1-sentence reasoning for node coverage: overlap of components/nodes present",
    )
    relationship_fidelity_reasoning: str = Field(
        ...,
        description="Concise 1-sentence reasoning for relationship fidelity: correctness of edges/relations",
    )
    structural_coherence_reasoning: str = Field(
        ...,
        description="Concise 1-sentence reasoning for structural coherence: overall topology/flow alignment",
    )


class SimilarityScore(BaseModel):
    """
    Structured similarity score from LLM judge.

    This is the return type that allows for error cases (None score).
    Use SimilarityScoreOutput for LLM structured output.
    """

    score: int | None = Field(None, ge=1, le=10, description="Similarity score 1-10")
    node_coverage_reasoning: str = Field("", description="Reasoning for node coverage")
    relationship_fidelity_reasoning: str = Field("", description="Reasoning for relationship fidelity")
    structural_coherence_reasoning: str = Field("", description="Reasoning for structural coherence")

    @property
    def is_valid(self) -> bool:
        return self.score is not None

    @classmethod
    def from_output(cls, output: SimilarityScoreOutput) -> "SimilarityScore":
        return cls(
            score=output.score,
            node_coverage_reasoning=output.node_coverage_reasoning,
            relationship_fidelity_reasoning=output.relationship_fidelity_reasoning,
            structural_coherence_reasoning=output.structural_coherence_reasoning,
        )


class ScoredResult(BaseModel):
    """A single comparison result with score and metadata."""

    score: int | None = None
    node_coverage_reasoning: str = ""
    relationship_fidelity_reasoning: str = ""
    structural_coherence_reasoning: str = ""
    code_size: str = "unknown"


class ProjectMetrics(BaseModel):
    """Metrics extracted for a single project evaluation."""

    analysis_path: str = ""
    similarity_results: list[ScoredResult] = Field(default_factory=list)
    average_similarity_score: float | None = None
    bin_average_scores: dict[str, float | None] = Field(default_factory=dict)
    dataset_samples: int = 0
    error: str | None = None


class DatasetEntry(BaseModel):
    """A single entry from the ground-truth dataset."""

    graph_id: str = ""
    code_size: str = ""
    level_of_depth: int = 1
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "DatasetEntry":
        return cls(
            graph_id=raw.get("graph_id", ""),
            code_size=raw.get("code_size", ""),
            level_of_depth=raw.get("level_of_depth", 1),
            data=raw,
        )


@dataclass
class ProjectWithDepth:
    """A project specification with explicit depth level."""

    name: str
    url: str
    expected_language: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    code_size: CodeSizeCategory = CodeSizeCategory.UNKNOWN
    depth_level: int = 1

    @property
    def full_name(self) -> str:
        return f"{self.name}-depth-{self.depth_level}"

    @property
    def base_name(self) -> str:
        return self.name


class HistoricalRun(BaseModel):
    """A single historical evaluation run."""

    commit: str
    timestamp: str
    scores: dict[str, float | None] = Field(default_factory=dict)
    system_specs: dict[str, str] = Field(default_factory=dict)


class HistoricalReasoning(BaseModel):
    """Historical reasoning for a single project evaluation."""

    commit: str
    project: str
    depth: int
    score: float | None = None
    node_coverage: str = ""
    relationship_fidelity: str = ""
    structural_coherence: str = ""


class ScoreHistory(BaseModel):
    """Complete historical score data."""

    runs: list[HistoricalRun] = Field(default_factory=list)
    reasoning: list[HistoricalReasoning] = Field(default_factory=list)
    project_sizes: dict[str, str] = Field(default_factory=dict)
