"""Pydantic models for telemetry payloads.

Event models are dumped with ``model_dump(exclude_none=True)`` at the call site
so optional fields are simply absent rather than arriving as ``null``.
"""

from pydantic import BaseModel


class LanguageStat(BaseModel):
    language: str
    loc: int
    percentage: float


class RepoScanned(BaseModel):
    version: str
    total_loc: int
    language_count: int
    languages: list[LanguageStat]
    stack: str
    run_id: str | None = None


class LspAnalysisResult(BaseModel):
    version: str
    language: str
    loc: int
    status: str
    duration_ms: int
    source_file_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    reference_count: int = 0
    diagnostic_file_count: int = 0
    diagnostic_count: int = 0
    quality_status: str = "ok"
    zero_nodes_with_loc: bool = False
    zero_edges_with_loc: bool = False
    run_id: str | None = None


class AnalysisStarted(BaseModel):
    command: str
    version: str
    run_id: str | None = None
    depth_level: int | None = None


class AnalysisCompleted(BaseModel):
    command: str
    version: str
    status: str
    duration_ms: int
    model_name: str | None = None
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    run_id: str | None = None
    depth_level: int | None = None


class TokenSnapshot(BaseModel):
    model_name: str | None = None
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
