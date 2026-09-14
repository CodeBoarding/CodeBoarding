import logging
from unittest.mock import patch

import pytest

from static_analyzer.engine.progress import PROGRESS_INTERVAL_SEC, ProgressLogger


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    clock = _Clock()
    with patch("static_analyzer.engine.progress.time.monotonic", clock):
        yield clock


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def test_a_phase_logs_once_per_interval_however_many_rounds_finish(clock, caplog):
    caplog.set_level(logging.INFO, logger="static_analyzer.engine.progress")
    progress = ProgressLogger("Phase 2b (impl)", 1000, unit="target")

    for _ in range(9):
        clock.now += PROGRESS_INTERVAL_SEC / 10
        progress.update(50)
    assert _lines(caplog) == []

    clock.now += PROGRESS_INTERVAL_SEC / 10
    progress.update(50)
    assert _lines(caplog) == ["Phase 2b (impl): 50% (500/1000 targets, 30.0s elapsed)"]


def test_a_phase_shorter_than_the_interval_logs_only_its_completion(clock, caplog):
    caplog.set_level(logging.INFO, logger="static_analyzer.engine.progress")
    progress = ProgressLogger("Phase 1 (symbols)", 3, unit="file")

    for _ in range(3):
        clock.now += 1
        progress.update()
    progress.finish()

    assert _lines(caplog) == ["Phase 1 (symbols): 100% (3/3 files, 3.0s elapsed)"]


def test_completion_is_logged_once_when_the_last_round_lands_past_the_interval(clock, caplog):
    caplog.set_level(logging.INFO, logger="static_analyzer.engine.progress")
    progress = ProgressLogger("Phase 2 (definitions)", 2, unit="file")

    clock.now += PROGRESS_INTERVAL_SEC * 2
    progress.update(2)
    progress.finish()

    assert _lines(caplog) == ["Phase 2 (definitions): 100% (2/2 files, 60.0s elapsed)"]
