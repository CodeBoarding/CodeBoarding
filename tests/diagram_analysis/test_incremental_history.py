from diagram_analysis.incremental_history import (
    append_incremental_history_event,
    build_incremental_history_event,
    incremental_history_path,
    load_incremental_history,
)
from diagram_analysis.incremental_models import IncrementalRunStats, IncrementalSummary, IncrementalSummaryKind


def test_append_and_load_incremental_history(tmp_path):
    output_dir = tmp_path / ".codeboarding"
    summary = IncrementalSummary(
        kind=IncrementalSummaryKind.MATERIAL_IMPACT,
        message="The change broadens validation behavior to reject malformed inputs earlier.",
        used_llm=True,
    )
    stats = IncrementalRunStats(
        repo_commit="abc123",
        baseline_checkpoint_id="cp-1",
        result_checkpoint_id="cp-2",
        file_deltas_count=2,
        components_affected=1,
        impacted_methods_count=3,
        hops_used=2,
    )

    first = build_incremental_history_event(
        run_id="run-1",
        event_type="incremental_analysis",
        status="completed",
        message=summary.message,
        project_name="demo-project",
        summary=summary,
        stats=stats,
        timestamp="2026-04-03T12:00:00Z",
    )
    second = build_incremental_history_event(
        run_id="run-2",
        event_type="baseline_reset",
        status="completed",
        message="A full analysis completed and a new incremental baseline checkpoint was saved.",
        project_name="demo-project",
        stats=IncrementalRunStats(repo_commit="def456", result_checkpoint_id="cp-3"),
        timestamp="2026-04-03T12:05:00Z",
    )

    append_incremental_history_event(output_dir, first)
    append_incremental_history_event(output_dir, second)

    events = load_incremental_history(output_dir)

    assert len(events) == 2
    assert incremental_history_path(output_dir).is_file()
    assert events[0].timestamp == "2026-04-03T12:00:00Z"
    assert events[0].summary == summary.to_dict()
    assert events[0].stats == stats.to_dict()
    assert events[1].event_type == "baseline_reset"
    assert events[1].message == "A full analysis completed and a new incremental baseline checkpoint was saved."
