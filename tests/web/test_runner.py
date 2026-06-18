import asyncio
import types
import pytest
from pathlib import Path
import codeboarding_web.runner as runner_module
from codeboarding_web.state import RunState, RunBusyError
from codeboarding_web.events import EventBus
from codeboarding_web.runner import AnalysisRunner
from diagram_analysis.exceptions import IncrementalCacheMissingError
from codeboarding_workflows.analysis import BaselineUnavailableError


def _make(tmp_path, bus):
    return AnalysisRunner(
        repo_path=tmp_path,
        output_dir=tmp_path,
        project_name="demo",
        state=RunState(),
        bus=bus,
    )


def test_start_rejects_unknown_scope(tmp_path):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    with pytest.raises(ValueError):
        r.start("bogus")
    loop.close()


def test_start_when_busy_raises(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    r.state.begin("existing", "full")
    with pytest.raises(RunBusyError):
        r.start("full")
    loop.close()


def test_depth_level_stored(tmp_path):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    runner = AnalysisRunner(
        repo_path=tmp_path,
        output_dir=tmp_path,
        project_name="demo",
        state=RunState(),
        bus=bus,
        depth_level=2,
    )
    assert runner.depth_level == 2
    loop.close()


def test_start_invokes_driver_and_finishes(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    called = {}

    def fake_drive(scope, run_id, base_ref, target_ref, progress_callback):
        called["scope"] = scope
        progress_callback()  # exercise the callback path

    monkeypatch.setattr(r, "_drive_pipeline", fake_drive)
    run_id = r.start("full")
    r._thread.join(timeout=5)
    assert called["scope"] == "full"
    assert r.state.phase.value in ("done", "error")
    assert run_id
    loop.close()


def _make_fake_src_ctx(tmp_path: Path) -> tuple[types.SimpleNamespace, types.SimpleNamespace]:
    fake_src = types.SimpleNamespace(project_name="demo", repo_path=tmp_path, artifact_dir=tmp_path)
    fake_ctx = types.SimpleNamespace(run_id="abc123", log_path=tmp_path / "run.log")
    return fake_src, fake_ctx


def test_incremental_falls_back_to_full_on_cache_missing(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    fake_src, fake_ctx = _make_fake_src_ctx(tmp_path)

    monkeypatch.setattr(
        runner_module, "run_incremental", lambda **_: (_ for _ in ()).throw(IncrementalCacheMissingError(tmp_path))
    )
    full_calls: list[str] = []
    monkeypatch.setattr(r, "_run_full", lambda src, ctx, cb: full_calls.append("called"))

    r._run_scope("incremental", fake_src, fake_ctx, lambda: None, "HEAD~1", "HEAD")

    assert full_calls == ["called"]
    loop.close()


def test_incremental_falls_back_on_baseline_unavailable(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    fake_src, fake_ctx = _make_fake_src_ctx(tmp_path)

    monkeypatch.setattr(
        runner_module, "run_incremental", lambda **_: (_ for _ in ()).throw(BaselineUnavailableError("no baseline"))
    )
    full_calls: list[str] = []
    monkeypatch.setattr(r, "_run_full", lambda src, ctx, cb: full_calls.append("called"))

    r._run_scope("incremental", fake_src, fake_ctx, lambda: None, "HEAD~1", "HEAD")

    assert full_calls == ["called"]
    loop.close()


def test_incremental_success_does_not_fall_back(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    fake_src, fake_ctx = _make_fake_src_ctx(tmp_path)

    monkeypatch.setattr(runner_module, "run_incremental", lambda **_: None)
    full_calls: list[str] = []
    monkeypatch.setattr(r, "_run_full", lambda src, ctx, cb: full_calls.append("called"))

    r._run_scope("incremental", fake_src, fake_ctx, lambda: None, "HEAD~1", "HEAD")

    assert full_calls == []
    loop.close()


def test_full_scope_calls_run_full(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    fake_src, fake_ctx = _make_fake_src_ctx(tmp_path)

    full_calls: list[str] = []
    monkeypatch.setattr(r, "_run_full", lambda src, ctx, cb: full_calls.append("called"))
    incremental_calls: list[str] = []
    monkeypatch.setattr(runner_module, "run_incremental", lambda **_: incremental_calls.append("called"))

    r._run_scope("full", fake_src, fake_ctx, lambda: None, "HEAD~1", "HEAD")

    assert full_calls == ["called"]
    assert incremental_calls == []
    loop.close()
