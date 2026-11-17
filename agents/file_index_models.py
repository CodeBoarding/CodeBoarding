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
    module_hash: str = Field(
        default="",
        description=(
            "Truncated SHA-256 of the file's module-level lines (everything outside indexed "
            "method spans); '' when unavailable. Lets the incremental path attribute a "
            "module-level edit even when a sibling method in the same file also changed."
        ),
    )

    def merge_from(self, other: FileEntry) -> FileEntry:
        """Merge another entry while retaining independent, canonical method metadata."""
        if not self.content_hash:
            self.content_hash = other.content_hash
        if not self.module_hash:
            self.module_hash = other.module_hash

        methods_by_qname: dict[str, MethodEntry] = {}
        for method in [*self.methods, *other.methods]:
            candidate = method.model_copy(deep=True)
            indexed = methods_by_qname.get(candidate.qualified_name)
            if indexed is None:
                methods_by_qname[candidate.qualified_name] = candidate
                continue

            if bool(indexed.content_hash) != bool(candidate.content_hash) and candidate.content_hash:
                preferred, fallback = candidate, indexed
            else:
                preferred, fallback = indexed, candidate
            preferred.start_line = preferred.start_line or fallback.start_line
            preferred.end_line = preferred.end_line or fallback.end_line
            preferred.content_hash = preferred.content_hash or fallback.content_hash
            methods_by_qname[candidate.qualified_name] = preferred

        self.methods = sorted(
            methods_by_qname.values(),
            key=lambda method: (method.start_line, method.end_line, method.qualified_name),
        )
        return self

    def merge_method_spans(self, spans: dict[str, tuple[int, int]]) -> None:
        """Fill missing spans for methods already owned by this file entry."""
        methods_by_qname = {method.qualified_name: method for method in self.methods}
        for qualified_name, (start_line, end_line) in spans.items():
            method = methods_by_qname.get(qualified_name)
            if method is None:
                continue
            method.start_line = method.start_line or start_line
            method.end_line = method.end_line or end_line
        self.methods.sort(key=lambda method: (method.start_line, method.end_line, method.qualified_name))
