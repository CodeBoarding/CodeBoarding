"""Pydantic models for the persisted ``analysis.json`` schema.

The on-disk format keeps a top-level ``files`` index plus a flat
``methods_index`` keyed by ``"<file_path>|<qualified_name>"``, so
component ``file_methods`` only have to store qualified names — no
duplicated method metadata.
"""

from pydantic import BaseModel, Field

from agents.agent_responses import Component, Relation


class RelationJson(Relation):
    """Relation subclass that includes src_id/dst_id and static analysis evidence in JSON serialization."""

    src_id: str = Field(default="", description="Component ID of the source.")
    dst_id: str = Field(default="", description="Component ID of the destination.")
    edge_count: int = Field(default=0, description="Number of CFG edges backing this relation.")
    is_static: bool = Field(default=False, description="True if derived from static CFG analysis.")


class ComponentJson(Component):
    # Override to include in JSON serialization (parent has exclude=True)
    component_id: str = Field(description="Deterministic unique identifier for this component.")
    source_cluster_ids: list[int] = Field(
        description="List of cluster IDs from CFG analysis that this component encompasses.",
        default_factory=list,
    )
    can_expand: bool = Field(
        description="Whether the component can be expanded in detail or not.",
        default=False,
    )
    file_methods: list["ComponentFileMethodGroupJson"] = Field(
        description="Component method references grouped by file. Each methods entry stores only qualified_name.",
        default_factory=list,
    )
    # Exclude intermediate field from JSON output
    source_group_names: list[str] = Field(default_factory=list, exclude=True)
    # Nested sub-analysis for expanded components
    components: list["ComponentJson"] | None = Field(
        description="Sub-components if expanded, None otherwise.", default=None
    )
    components_relations: list[RelationJson] | None = Field(
        description="Relations among sub-components if expanded, None otherwise.",
        default=None,
    )


class NotAnalyzedFile(BaseModel):
    path: str = Field(description="Relative path of the file.")
    reason: str = Field(description="Exclusion reason for the file.")


class FileCoverageSummary(BaseModel):
    total_files: int = Field(description="Total number of text files in the repository.")
    analyzed: int = Field(description="Number of files included in the analysis.")
    not_analyzed: int = Field(description="Number of files excluded from the analysis.")
    not_analyzed_by_reason: dict[str, int] = Field(
        default_factory=dict, description="Count of excluded files grouped by reason."
    )


class FileCoverageReport(BaseModel):
    version: int = Field(default=1, description="Schema version of the file coverage report.")
    generated_at: str = Field(description="ISO timestamp of when the report was generated.")
    analyzed_files: list[str] = Field(description="List of analyzed file paths.")
    not_analyzed_files: list[NotAnalyzedFile] = Field(description="List of excluded files with optional reasons.")
    summary: FileCoverageSummary = Field(description="Aggregated coverage counts.")


class AnalysisMetadata(BaseModel):
    generated_at: str = Field(description="ISO timestamp of when the analysis was generated.")
    commit_hash: str = Field(default="", description="Git commit hash at which the analysis was generated.")
    repo_name: str = Field(description="Name of the analyzed repository.")
    depth_level: int = Field(description="Maximum depth level of the analysis.")
    file_coverage_summary: FileCoverageSummary = Field(
        default_factory=lambda: FileCoverageSummary(
            total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={}
        ),
        description="Lightweight file coverage counts.",
    )


class MethodIndexEntry(BaseModel):
    file_path: str = Field(description="Relative path to the source file.")
    qualified_name: str = Field(description="Fully qualified method/function name.")
    start_line: int = Field(description="Starting line number in the file.")
    end_line: int = Field(description="Ending line number in the file.")
    type: str = Field(description="Node type name (METHOD, FUNCTION, CLASS, ...).")


class ComponentFileMethodGroupJson(BaseModel):
    file_path: str = Field(description="Relative path to the source file.")
    methods: list[str] = Field(
        default_factory=list,
        description="Qualified method/function names assigned to this component in this file.",
    )


class FileEntryJson(BaseModel):
    """Persisted file entry — stores only method-index keys.

    Full method metadata lives in ``methods_index``; this avoids duplication.
    """

    method_keys: list[str] = Field(
        default_factory=list,
        description="Keys into ``methods_index`` ('<file_path>|<qualified_name>'), in declaration order.",
    )


class UnifiedAnalysisJson(BaseModel):
    metadata: AnalysisMetadata = Field(description="Metadata about the analysis run.")
    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph."
    )
    files: dict[str, FileEntryJson] = Field(
        default_factory=dict,
        description="Top-level file index keyed by relative file path.",
    )
    methods_index: dict[str, MethodIndexEntry] = Field(
        default_factory=dict,
        description="Canonical method metadata keyed by '<file_path>|<qualified_name>'.",
    )
    components: list[ComponentJson] = Field(description="List of the components identified in the project.")
    components_relations: list[RelationJson] = Field(description="List of relations among the components.")


def method_key(file_path: str, qualified_name: str) -> str:
    """Compose the ``methods_index`` key for ``(file_path, qualified_name)``."""
    return f"{file_path}|{qualified_name}"
