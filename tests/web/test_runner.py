import asyncio
import pytest
from pathlib import Path
from codeboarding_web.state import RunState, RunBusyError
from codeboarding_web.events import EventBus
from codeboarding_web.runner import AnalysisRunner


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
