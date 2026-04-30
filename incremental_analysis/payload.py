"""Wire-format payloads emitted by the incremental pipeline.

Tagged union covering the three outcomes of an incremental run. Each variant
carries exactly the fields relevant to its state — no nullable grab-bag. The
outer CLI / JSON-RPC layer calls ``.to_dict()`` at the serialization boundary.

Why the split: combining these into one dataclass forced every successful-run
field to be ``| None`` because a "full required" result doesn't have a change
set and a "no changes" result doesn't have an incremental delta. The tagged
union makes the state machine explicit.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from diagram_analysis.incremental_updater import IncrementalDelta
from diagram_analysis.incremental_models import (
    IncrementalRunResult,
    IncrementalSummary,
    IncrementalSummaryKind,
)
from diagram_analysis.run_metadata import RunMode
from repo_utils.change_detector import ChangeSet


def _target_ref_wire(target_ref: str) -> str:
    """Empty target_ref means the worktree; serialize as ``WORKTREE`` on the wire."""
    return target_ref or "WORKTREE"


@dataclass
class RequiresFullAnalysisPayload:
    """Incremental was attempted but cannot proceed — caller must run full analysis."""

    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = IncrementalRunResult(
            summary=IncrementalSummary(
                kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
                message=self.message,
                requires_full_analysis=True,
            )
        )
        payload = result.to_dict()
        payload["mode"] = RunMode.INCREMENTAL
        payload["requiresFullAnalysis"] = True
        if self.error is not None:
            payload["error"] = self.error
        return payload

    @property
    def requires_full_analysis(self) -> bool:
        return True


@dataclass
class NoChangesPayload:
    """No file changes detected — existing analysis is still current."""

    base_ref: str
    target_ref: str
    resolved_target_commit: str
    change_set: ChangeSet
    metadata_path: Path
    analysis_path: Path

    def to_dict(self) -> dict[str, Any]:
        result = IncrementalRunResult(
            summary=IncrementalSummary(
                kind=IncrementalSummaryKind.NO_CHANGES,
                message="No file changes detected.",
            ),
            analysis_path=self.analysis_path,
        )
        payload = result.to_dict()
        payload["mode"] = RunMode.INCREMENTAL
        payload["requiresFullAnalysis"] = False
        payload["baseRef"] = self.base_ref
        payload["targetRef"] = _target_ref_wire(self.target_ref)
        payload["resolvedTargetCommit"] = self.resolved_target_commit
        payload["changeSet"] = self.change_set.to_dict()
        payload["metadataPath"] = str(self.metadata_path)
        return payload

    @property
    def requires_full_analysis(self) -> bool:
        return False


@dataclass
class IncrementalCompletedPayload:
    """Incremental pipeline ran end-to-end; result may still flag a full rerun.

    When ``result.summary.requires_full_analysis`` is True (e.g. all patches
    failed, uncertain trace), the baseline on disk does NOT advance — callers
    should run a full analysis. ``metadata_path`` is only populated when the
    baseline did advance.
    """

    result: IncrementalRunResult
    base_ref: str
    target_ref: str
    resolved_target_commit: str
    change_set: ChangeSet
    incremental_delta: IncrementalDelta
    metadata_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        payload = self.result.to_dict()
        payload["mode"] = RunMode.INCREMENTAL
        payload["requiresFullAnalysis"] = self.requires_full_analysis
        payload["baseRef"] = self.base_ref
        payload["targetRef"] = _target_ref_wire(self.target_ref)
        payload["resolvedTargetCommit"] = self.resolved_target_commit
        payload["changeSet"] = self.change_set.to_dict()
        payload["incrementalDelta"] = self.incremental_delta.to_dict()
        if self.metadata_path is not None:
            payload["metadataPath"] = str(self.metadata_path)
        return payload

    @property
    def requires_full_analysis(self) -> bool:
        return self.result.summary.requires_full_analysis


IncrementalRunPayload = Union[RequiresFullAnalysisPayload, NoChangesPayload, IncrementalCompletedPayload]
