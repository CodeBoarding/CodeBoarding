"""Tests for incremental analysis Pydantic models."""

from diagram_analysis.incremental_models import (
    AnalysisPatch,
    EscalationLevel,
    IncrementalRunStats,
    IncrementalSummary,
    IncrementalSummaryKind,
    ImpactedComponent,
    JsonPatchOp,
    TraceConfig,
    TraceResponse,
    TraceResult,
    TraceStopReason,
)


def test_trace_config_defaults():
    cfg = TraceConfig()
    assert cfg.max_hops == 3
    assert cfg.max_fetched_methods == 30


def test_trace_response_continue():
    resp = TraceResponse(
        status=TraceStopReason.CONTINUE,
        impacted_methods=["foo.bar"],
        next_methods_to_fetch=["baz.qux"],
        reason="Need more context",
    )
    assert resp.status == TraceStopReason.CONTINUE
    assert "foo.bar" in resp.llm_str()
    assert resp.semantic_impact_summary == ""


def test_trace_response_stop_no_impact():
    resp = TraceResponse(
        status=TraceStopReason.NO_MATERIAL_IMPACT,
        reason="Change is cosmetic",
    )
    assert resp.status == "stop_no_material_semantic_impact"
    assert resp.impacted_methods == []
    assert resp.next_methods_to_fetch == []
    assert resp.semantic_impact_summary == ""


def test_trace_response_closure_with_semantic_summary():
    resp = TraceResponse(
        status=TraceStopReason.CLOSURE_REACHED,
        impacted_methods=["foo.bar"],
        reason="Enough evidence gathered",
        semantic_impact_summary="The change broadens validation semantics by enforcing stricter input checks.",
    )
    assert resp.status == TraceStopReason.CLOSURE_REACHED
    assert "Semantic impact summary:" in resp.llm_str()


def test_trace_result_empty():
    result = TraceResult()
    assert result.impacted_components == []
    assert result.all_impacted_methods == []
    assert result.stop_reason == TraceStopReason.NO_MATERIAL_IMPACT
    assert result.semantic_impact_summary == ""


def test_impacted_component():
    ic = ImpactedComponent(component_id="1.2", impacted_methods=["a.b", "c.d"])
    assert ic.component_id == "1.2"
    assert len(ic.impacted_methods) == 2


def test_json_patch_op():
    op = JsonPatchOp(op="replace", path="/components/aa/description", value="New desc")
    dumped = op.model_dump(exclude_none=True)
    assert dumped["op"] == "replace"
    assert dumped["path"] == "/components/aa/description"
    assert dumped["value"] == "New desc"


def test_json_patch_op_remove():
    op = JsonPatchOp(op="remove", path="/components/ab")
    dumped = op.model_dump(exclude_none=True)
    assert dumped["op"] == "remove"
    assert "value" not in dumped or dumped["value"] is None


def test_analysis_patch():
    patch = AnalysisPatch(
        sub_analysis_id="1",
        reasoning="Updated component description",
        patches=[
            JsonPatchOp(op="replace", path="/components/aa/description", value="Updated"),
        ],
    )
    assert patch.sub_analysis_id == "1"
    assert len(patch.patches) == 1
    assert "replace" in patch.llm_str()


def test_escalation_levels():
    assert EscalationLevel.NONE == "none"
    assert EscalationLevel.SCOPED == "scoped"
    assert EscalationLevel.ROOT == "root"
    assert EscalationLevel.FULL == "full"


def test_incremental_summary_to_dict():
    summary = IncrementalSummary(
        kind=IncrementalSummaryKind.RENAME_ONLY,
        message="Only file renames were detected, so the architecture analysis was updated without semantic tracing.",
        used_llm=False,
        trace_stop_reason=TraceStopReason.NO_MATERIAL_IMPACT,
        escalation_level=EscalationLevel.NONE,
    )

    assert summary.to_dict() == {
        "kind": IncrementalSummaryKind.RENAME_ONLY,
        "message": "Only file renames were detected, so the architecture analysis was updated without semantic tracing.",
        "usedLlm": False,
        "traceStopReason": TraceStopReason.NO_MATERIAL_IMPACT,
        "escalationLevel": EscalationLevel.NONE,
        "requiresFullAnalysis": False,
    }


def test_incremental_run_stats_to_dict():
    stats = IncrementalRunStats(
        repo_commit="abc123",
        baseline_checkpoint_id="cp-1",
        result_checkpoint_id="cp-2",
        file_deltas_count=2,
        components_affected=1,
        impacted_methods_count=3,
        hops_used=2,
    )

    assert stats.to_dict() == {
        "repoCommit": "abc123",
        "baselineCheckpointId": "cp-1",
        "resultCheckpointId": "cp-2",
        "fileDeltasCount": 2,
        "componentsAffected": 1,
        "impactedMethodsCount": 3,
        "hopsUsed": 2,
    }
