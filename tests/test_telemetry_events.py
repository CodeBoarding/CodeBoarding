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


def test_track_analysis_reads_run_id_from_self_for_generator_methods(captured):
    """Generator methods (generate_analysis) keep run_id on self, not as a param."""

    class Generator:
        run_id = "from-self"
        depth_level = 3

        @track_analysis
        def generate_analysis(self):
            return "done"

    Generator().generate_analysis()

    started, started_props = captured[0]
    assert started == "analysis_started"
    assert started_props["command"] == "generate_analysis"
    assert started_props["run_id"] == "from-self"
    assert started_props["depth_level"] == 3
    completed = dict(captured)["analysis_completed"]
    assert completed["run_id"] == "from-self"


def test_track_analysis_propagates_run_id_to_nested_repo_scanned(captured):
    """The ContextVar set by track_analysis is visible to track_tech_stack."""

    class Generator:
        run_id = "nested-id"
        depth_level = 1

        @track_analysis
        def generate_analysis(self):
            events.track_tech_stack("/repo", 100, [])
            return "done"

    Generator().generate_analysis()

    scanned = dict(captured)["repo_scanned"]
    assert scanned["run_id"] == "nested-id"


def test_repo_scanned_reads_run_id_from_env_without_track_analysis(captured, monkeypatch):
    """repo_scanned stays correlated even when the scan fires outside a decorated scope."""
    monkeypatch.setenv("CODEBOARDING_RUN_ID", "env-run")
    events.track_tech_stack("/env-repo", 100, [])

    scanned = dict(captured)["repo_scanned"]
    assert scanned["run_id"] == "env-run"


def test_track_analysis_env_run_id_overrides_self(captured, monkeypatch):
    """CODEBOARDING_RUN_ID (VSCode correlation id) wins over self.run_id."""
    monkeypatch.setenv("CODEBOARDING_RUN_ID", "vscode-run")

    class Generator:
        run_id = "internal-id"
        depth_level = 1

        @track_analysis
        def generate_analysis(self):
            return "done"

    Generator().generate_analysis()

    assert dict(captured)["analysis_started"]["run_id"] == "vscode-run"
    assert dict(captured)["analysis_completed"]["run_id"] == "vscode-run"


def test_truncate_keeps_tail_and_annotates():
    out = events._truncate("a" * 100, 10)
    assert out.startswith("...[truncated 90 chars]")
    assert out.endswith("a" * 10)
