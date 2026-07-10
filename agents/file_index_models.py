"""File/method index models — internal infrastructure, never sent to the LLM.

These pydantic models track methods and files during analysis. Unlike the models
in ``agent_responses``, they are not LLM request/response schemas (they live on
``Component``/``AnalysisInsights`` under ``exclude=True`` fields), so they live
here to keep that distinction clear.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MethodEntry(BaseModel):
    """A single method/function within a file, with its location and identity."""

    qualified_name: str = Field(description="Fully qualified name of the method or function.")
    start_line: int = Field(description="Starting line number in the file.")
    end_line: int = Field(description="Ending line number in the file.")
    node_type: str = Field(description="Node type name matching NodeType enum (e.g. METHOD, FUNCTION, CLASS).")
    content_hash: str = Field(
        default="",
        description="Truncated SHA-256 of the method's source lines; '' when source was unavailable.",
    )

    def __hash__(self) -> int:
        return hash(self.qualified_name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MethodEntry):
            return NotImplemented
        return self.qualified_name == other.qualified_name

    @classmethod
    def from_node(cls, node) -> MethodEntry:
        """Build from a ``static_analyzer.Node``. Accepts ``Any`` to avoid a hard dep."""
        return cls(
            qualified_name=node.fully_qualified_name,
            start_line=node.line_start,
            end_line=node.line_end,
            node_type=node.type.name,
        )


class FileMethodGroup(BaseModel):
    """All methods/functions belonging to a component within a single file."""

    file_path: str = Field(description="Relative path to the source file.")
    methods: list[MethodEntry] = Field(
        default_factory=list,
        description="Methods and functions in this file that belong to the component, sorted by start_line.",
    )


class FileEntry(BaseModel):
    """Single source of truth for methods in one file."""

    methods: list[MethodEntry] = Field(
        default_factory=list,
        description="Methods and functions in this file, sorted by start line.",
    )
    content_hash: str = Field(
        default="",
        description="Truncated SHA-256 of the entire file's bytes; '' when source was unavailable.",
    )
