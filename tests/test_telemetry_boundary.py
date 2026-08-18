"""A user-visible failure must be reported exactly once."""

from unittest.mock import patch

import pytest

from telemetry.events import already_captured, capture_error


def test_capture_error_marks_the_exception():
    exc = RuntimeError("boom")
    assert not already_captured(exc)
    with patch("telemetry.events.telemetry"):
        capture_error("test", exc)
    assert already_captured(exc)


def test_second_reporter_defers_to_the_first():
    """The engine reports with its context; the CLI must not report again.

    Two events for one failure would double-count every error in the dashboard
    and hide which frame actually had the useful context.
    """
    exc = RuntimeError("boom")
    with patch("telemetry.events.telemetry") as tele:
        capture_error("static_analysis.definition_batch", exc, extra={"file": "app.cs"})
        assert tele.capture_exception.call_count == 1
        if not already_captured(exc):
            capture_error("cli.full", exc)
        assert tele.capture_exception.call_count == 1


def test_builtin_without_dict_still_reports():
    """An exception that cannot carry the mark is reported rather than dropped."""
    with patch("telemetry.events.telemetry") as tele:
        capture_error("test", KeyboardInterrupt())
        assert tele.capture_exception.call_count == 1
