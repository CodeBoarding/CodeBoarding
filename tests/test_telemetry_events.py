from types import SimpleNamespace

import pytest

import telemetry.events as events
from telemetry.events import capture_error, track_analysis, track_lsp_result


@pytest.fixture
def captured(monkeypatch):
    """Capture both lifecycle events and forwarded exceptions."""
    cap = SimpleNamespace(events=[], exceptions=[])
    monkeypatch.setattr(events.telemetry, "capture", lambda event, props=None: cap.events.append((event, props or {})))
    monkeypatch.setattr(
        events.telemetry,
        "capture_exception",
        lambda exc, *, properties=None: cap.exceptions.append((exc, properties or {})),
    )
    monkeypatch.setattr(events.telemetry, "flush", lambda: None)
    return cap


def props_for(cap, name):
    return next(p for n, p in cap.events if n == name)


def test_track_analysis_on_error_marks_status_and_forwards_exception(captured):
    @track_analysis
    def run_full(repo_name, run_id=None, depth_level=1):
        raise RuntimeError("git fetch failed: bad object")

    with pytest.raises(RuntimeError):
        run_full("x", run_id="r1", depth_level=2)

    completed = props_for(captured, "analysis_completed")
    assert completed["status"] == "error"
    assert completed["run_id"] == "r1"
    # Exception details go to PostHog's $exception event, not the lifecycle event.
    assert "error_message" not in completed
    assert len(captured.exceptions) == 1
    raised, props = captured.exceptions[0]
    assert isinstance(raised, RuntimeError)
    assert props["command"] == "run_full"
    assert props["run_id"] == "r1"


def test_track_analysis_success_has_no_exception(captured):
    @track_analysis
    def run_full(repo_name, run_id=None):
        return "ok"

    assert run_full("x", run_id="r2") == "ok"

    assert props_for(captured, "analysis_completed")["status"] == "success"
    assert captured.exceptions == []


def test_capture_error_forwards_to_posthog(captured):
    try:
        raise ValueError("standalone boom")
    except ValueError as exc:
        capture_error("expand", exc, extra={"run_id": "r9"})

    assert len(captured.exceptions) == 1
    raised, props = captured.exceptions[0]
    assert isinstance(raised, ValueError)
    assert props["command"] == "expand"
    assert props["run_id"] == "r9"


def test_exception_attached_telemetry_properties_are_forwarded(captured):
    # Why: budget-overflow errors attach window provenance so the $exception
    # event is diagnosable without a local repro.
    @track_analysis
    def generate_analysis(run_id=None):
        exc = RuntimeError("render exceeds budget")
        exc.telemetry_properties = {"window_is_fallback": True, "char_budget": 776_700}  # type: ignore[attr-defined]
        raise exc

    with pytest.raises(RuntimeError):
        generate_analysis(run_id="r3")

    _, props = captured.exceptions[0]
    assert props["window_is_fallback"] is True
    assert props["char_budget"] == 776_700
    assert props["command"] == "generate_analysis"


def test_capture_error_merges_exception_telemetry_properties(captured):
    exc = RuntimeError("boom")
    exc.telemetry_properties = {"window_is_fallback": False}  # type: ignore[attr-defined]
    capture_error("expand", exc)

    _, props = captured.exceptions[0]
    assert props["window_is_fallback"] is False


def test_track_analysis_reads_run_id_from_self_for_generator_methods(captured):
    """Generator methods (generate_analysis) keep run_id on self, not as a param."""

    class Generator:
        run_id = "from-self"
        depth_level = 3

        @track_analysis
        def generate_analysis(self):
            return "done"

    Generator().generate_analysis()

    started = props_for(captured, "analysis_started")
    assert started["command"] == "generate_analysis"
    assert started["run_id"] == "from-self"
    assert started["depth_level"] == 3
    assert props_for(captured, "analysis_completed")["run_id"] == "from-self"


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

    assert props_for(captured, "repo_scanned")["run_id"] == "nested-id"


def test_repo_scanned_reads_run_id_from_env_without_track_analysis(captured, monkeypatch):
    """repo_scanned stays correlated even when the scan fires outside a decorated scope."""
    monkeypatch.setenv("CODEBOARDING_RUN_ID", "env-run")
    events.track_tech_stack("/env-repo", 100, [])

    assert props_for(captured, "repo_scanned")["run_id"] == "env-run"


def test_lsp_analysis_result_marks_zero_nodes_as_error(captured, monkeypatch):
    monkeypatch.setenv("CODEBOARDING_RUN_ID", "lsp-run")

    analysis = {
        "call_graph": SimpleNamespace(nodes={}, edges=[]),
        "source_files": ["a.py", "b.py"],
    }

    track_lsp_result(
        language="python",
        loc=42,
        status="success",
        duration_ms=250,
        analysis=analysis,
        diagnostics={},
    )

    props = props_for(captured, "lsp_analysis_result")
    assert props["run_id"] == "lsp-run"
    assert props["language"] == "python"
    assert "project_path" not in props
    assert props["source_file_count"] == 2
    assert props["quality_status"] == "error"
    assert props["zero_nodes_with_loc"] is True
    assert props["zero_edges_with_loc"] is True


def test_lsp_analysis_result_warns_when_summary_fields_missing(captured, caplog):
    track_lsp_result(language="python", loc=0, status="error", duration_ms=10, analysis={}, diagnostics={})

    assert "LSP analysis result for python is degraded: missing program graph, missing source files" in caplog.text
    props = props_for(captured, "lsp_analysis_result")
    assert props["source_file_count"] == 0
    assert props["node_count"] == 0
    assert props["edge_count"] == 0
    assert props["quality_status"] == "warning"


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

    assert props_for(captured, "analysis_started")["run_id"] == "vscode-run"
    assert props_for(captured, "analysis_completed")["run_id"] == "vscode-run"
