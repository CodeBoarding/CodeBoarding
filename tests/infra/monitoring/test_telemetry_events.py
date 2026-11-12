from pathlib import Path
from types import SimpleNamespace

import pytest

import infra.monitoring.telemetry.events as events
import infra.monitoring.telemetry.service as service
from infra.monitoring.telemetry.events import capture_error, track_analysis, track_lsp_result
from infra.monitoring.telemetry.service import ProductTelemetry


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
    def run_full(repo_name, run_id=None, depth_cap=1):
        raise RuntimeError("git fetch failed: bad object")

    with pytest.raises(RuntimeError):
        run_full("x", run_id="r1", depth_cap=2)

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
        depth_cap = 3

        @track_analysis
        def generate_analysis(self):
            return "done"

    Generator().generate_analysis()

    started = props_for(captured, "analysis_started")
    assert started["command"] == "generate_analysis"
    assert started["run_id"] == "from-self"
    assert started["depth_cap"] == 3
    assert "depth_level" not in started
    assert props_for(captured, "analysis_completed")["depth_cap"] == 3
    assert "depth_level" not in props_for(captured, "analysis_completed")
    assert props_for(captured, "analysis_completed")["run_id"] == "from-self"


def test_track_analysis_propagates_run_id_to_nested_repo_scanned(captured):
    """The ContextVar set by track_analysis is visible to track_tech_stack."""

    class Generator:
        run_id = "nested-id"
        depth_cap = 1

        @track_analysis
        def generate_analysis(self):
            events.track_tech_stack(Path("/repo"), 100, [])
            return "done"

    Generator().generate_analysis()

    assert props_for(captured, "repo_scanned")["run_id"] == "nested-id"


def test_repo_scanned_reads_run_id_from_env_without_track_analysis(captured, monkeypatch):
    """repo_scanned stays correlated even when the scan fires outside a decorated scope."""
    monkeypatch.setenv("CODEBOARDING_RUN_ID", "env-run")
    events.track_tech_stack(Path("/env-repo"), 100, [])

    assert props_for(captured, "repo_scanned")["run_id"] == "env-run"


def test_repo_is_the_project_directory_basename_only(captured, tmp_path, monkeypatch):
    project = tmp_path / "parent-secret" / "my-project"
    project.mkdir(parents=True)
    events.track_tech_stack(project, 100, [])
    monkeypatch.chdir(project)
    track_lsp_result(
        repo_path=Path("."), language="python", loc=0, status="success", duration_ms=1, analysis={}, diagnostics={}
    )

    for name in ("repo_scanned", "lsp_analysis_result"):
        props = props_for(captured, name)
        assert props["repo"] == "my-project"
        assert "parent-secret" not in str(props)


@pytest.mark.parametrize(
    "opt_out,sent_repos",
    [({}, ["my-project"] * 2), ({"CODEBOARDING_TELEMETRY": "false"}, []), ({"DO_NOT_TRACK": "1"}, [])],
)
def test_opting_out_sends_no_repo_name(monkeypatch, tmp_path, opt_out, sent_repos):
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CODEBOARDING_TELEMETRY", raising=False)
    for key, value in opt_out.items():
        monkeypatch.setenv(key, value)
    sent: list[dict] = []
    monkeypatch.setattr(service, "Posthog", lambda **_: SimpleNamespace(capture=lambda **kw: sent.append(kw)))
    monkeypatch.setattr(ProductTelemetry, "_instance", None)
    monkeypatch.setattr(events, "telemetry", ProductTelemetry())
    project = tmp_path / "my-project"
    project.mkdir()

    events.track_tech_stack(project, 100, [])
    track_lsp_result(
        repo_path=project, language="python", loc=0, status="success", duration_ms=1, analysis={}, diagnostics={}
    )

    assert [kw["properties"]["repo"] for kw in sent] == sent_repos


def test_lsp_analysis_result_marks_zero_nodes_as_error(captured, monkeypatch):
    monkeypatch.setenv("CODEBOARDING_RUN_ID", "lsp-run")

    analysis = {
        "call_graph": SimpleNamespace(nodes={}, edges=[]),
        "source_files": ["a.py", "b.py"],
    }

    track_lsp_result(
        repo_path=Path("/repo"),
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
    track_lsp_result(
        repo_path=Path("/repo"), language="python", loc=0, status="error", duration_ms=10, analysis={}, diagnostics={}
    )

    assert "LSP analysis result for python is degraded: missing call graph, missing source files" in caplog.text
    props = props_for(captured, "lsp_analysis_result")
    assert props["source_file_count"] == 0
    assert props["node_count"] == 0
    assert props["edge_count"] == 0
    assert props["quality_status"] == "warning"


@pytest.mark.parametrize("errors,quality", [([0, 0, 0], "ok"), ([0, 2, 5], "warning")])
def test_lsp_analysis_result_reports_parser_quality(captured, errors, quality):
    track_lsp_result(
        repo_path=Path("/repo"),
        language="cpp",
        loc=100,
        status="success",
        duration_ms=20,
        analysis={
            "call_graph": SimpleNamespace(nodes={"caller": 1, "callee": 2}, edges=[1]),
            "source_files": ["private.cpp"],
            "parsed_file_errors": dict(zip(["private.cpp", "private.h", "other.h"], errors)),
            "parse_error_samples": (
                {"private.h": [{"syntax_trace": ["translation_unit", "ERROR"]}] * 25} if quality == "warning" else {}
            ),
        },
        diagnostics={},
    )
    props = props_for(captured, "lsp_analysis_result")
    assert props["parsed_file_count"] == 3
    assert props["parse_error_file_count"] == (2 if quality == "warning" else 0)
    assert props["parse_error_node_count"] == (7 if quality == "warning" else 0)
    assert props["quality_status"] == quality
    assert props["status"] == "success"
    assert len(props["parse_error_samples"]) == (20 if quality == "warning" else 0)
    if quality == "warning":
        assert props["parse_error_samples"][0] == {"syntax_trace": ["translation_unit", "ERROR"], "file_index": 0}
    assert "private" not in str(props)
    assert "parsed_file_errors" not in props


@pytest.mark.parametrize(
    "parsed,affected,quality", [(0, 0, "ok"), (100, 4, "ok"), (100, 5, "warning"), (100, 6, "warning")]
)
def test_parser_warning_threshold_per_technology(captured, caplog, parsed, affected, quality):
    for language, errors in (
        ("python", {str(i): 0 for i in range(1000)}),
        ("cpp", {str(i): 10 if i < affected else 0 for i in range(parsed)}),
    ):
        track_lsp_result(
            repo_path=Path("/repo"),
            language=language,
            loc=100,
            status="success",
            duration_ms=1,
            analysis={
                "call_graph": SimpleNamespace(nodes={"a": 1, "b": 2}, edges=[1]),
                "source_files": ["a", "b"],
                "parsed_file_errors": errors,
                "parse_error_samples": {"a": [{"kind": "error"}]} if affected and language == "cpp" else {},
            },
            diagnostics={},
        )

    python, cpp = [props for event, props in captured.events if event == "lsp_analysis_result"]
    assert python["quality_status"] == "ok"
    assert cpp["quality_status"] == quality
    assert cpp["parsed_file_count"] == parsed
    assert cpp["parse_error_file_count"] == affected
    assert cpp["parse_error_node_count"] == affected * 10
    assert bool(cpp["parse_error_samples"]) == bool(affected)
    assert ("is degraded" in caplog.text) == (quality == "warning")


def test_track_analysis_env_run_id_overrides_self(captured, monkeypatch):
    """CODEBOARDING_RUN_ID (VSCode correlation id) wins over self.run_id."""
    monkeypatch.setenv("CODEBOARDING_RUN_ID", "vscode-run")

    class Generator:
        run_id = "internal-id"
        depth_cap = 1

        @track_analysis
        def generate_analysis(self):
            return "done"

    Generator().generate_analysis()

    assert props_for(captured, "analysis_started")["run_id"] == "vscode-run"
    assert props_for(captured, "analysis_completed")["run_id"] == "vscode-run"
