"""Tests for codeboarding_web.state.RunState transitions."""

import pytest
from codeboarding_web.state import RunState, RunPhase, RunBusyError


def test_starts_idle() -> None:
    s = RunState()
    assert s.phase is RunPhase.IDLE
    assert not s.is_busy


def test_begin_marks_running() -> None:
    s = RunState()
    s.begin("r1", "full")
    assert s.is_busy
    assert s.phase is RunPhase.RUNNING
    assert s.run_id == "r1"
    assert s.scope == "full"


def test_begin_while_running_raises() -> None:
    s = RunState()
    s.begin("r1", "full")
    with pytest.raises(RunBusyError):
        s.begin("r2", "incremental")


def test_finish_success() -> None:
    s = RunState()
    s.begin("r1", "full")
    s.finish()
    assert s.phase is RunPhase.DONE
    assert s.error is None
    assert not s.is_busy


def test_finish_with_error() -> None:
    s = RunState()
    s.begin("r1", "full")
    s.finish(error="boom")
    assert s.phase is RunPhase.ERROR
    assert s.error == "boom"


def test_finish_from_idle_is_noop() -> None:
    s = RunState()
    s.finish()
    assert s.phase is RunPhase.IDLE
