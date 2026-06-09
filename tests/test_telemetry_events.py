import pytest

import telemetry.events as events
from telemetry.events import capture_error, track_analysis


@pytest.fixture
def captured(monkeypatch):
    events_log: list[tuple[str, dict]] = []
    monkeypatch.setattr(events.telemetry, "capture", lambda event, props=None: events_log.append((event, props or {})))
    monkeypatch.setattr(events.telemetry, "flush", lambda: None)
    return events_log


def test_track_analysis_captures_message_and_stacktrace_on_error(captured):
    @track_analysis
    def run_full(repo_name, run_id=None, depth_level=1):
        raise RuntimeError("git fetch failed: bad object")

    with pytest.raises(RuntimeError):
        run_full("x", run_id="r1", depth_level=2)

    completed = dict(captured)["analysis_completed"]
    assert completed["status"] == "error"
    assert completed["error_type"] == "RuntimeError"
    assert completed["error_message"] == "git fetch failed: bad object"
    assert "Traceback" in completed["error_stacktrace"]
    assert completed["run_id"] == "r1"


def test_track_analysis_success_has_no_error_fields(captured):
    @track_analysis
    def run_full(repo_name, run_id=None):
        return "ok"

    assert run_full("x", run_id="r2") == "ok"

    completed = dict(captured)["analysis_completed"]
    assert completed["status"] == "success"
    assert "error_message" not in completed
    assert "error_stacktrace" not in completed


def test_capture_error_emits_error_event(captured):
    try:
        raise ValueError("standalone boom")
    except ValueError as exc:
        capture_error("expand", exc, extra={"run_id": "r9"})

    event, props = captured[-1]
    assert event == "error"
    assert props["command"] == "expand"
    assert props["error_type"] == "ValueError"
    assert props["error_message"] == "standalone boom"
    assert "Traceback" in props["error_stacktrace"]
    assert props["run_id"] == "r9"


def test_truncate_keeps_tail_and_annotates():
    out = events._truncate("a" * 100, 10)
    assert out.startswith("...[truncated 90 chars]")
    assert out.endswith("a" * 10)
