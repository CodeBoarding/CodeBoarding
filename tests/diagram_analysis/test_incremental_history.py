from pathlib import Path

from diagram_analysis.incremental_history import load_incremental_history, record_incremental_history


def test_record_and_load_incremental_history(tmp_path):
    output_dir = tmp_path / "out"

    path = record_incremental_history(
        output_dir,
        {
            "runId": "run-1",
            "projectName": "repo",
            "mode": "incremental",
            "resetBaseline": False,
            "analysisPath": "analysis.json",
            "incrementalSummary": {"kind": "scoped"},
        },
    )

    assert path == output_dir / "incremental_history.jsonl"

    entries = load_incremental_history(output_dir)
    assert len(entries) == 1
    assert entries[0]["runId"] == "run-1"
    assert entries[0]["projectName"] == "repo"
    assert entries[0]["mode"] == "incremental"
    assert entries[0]["resetBaseline"] is False
