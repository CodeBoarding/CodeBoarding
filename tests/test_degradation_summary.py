"""Absorbed failures are summarized, not sent one by one."""

from unittest.mock import patch

from telemetry import degradations
from telemetry.events import flush_degradations


def setup_function():
    degradations.reset()


def test_thousands_of_failures_produce_one_event():
    """The hierarchy path visits ~3,900 class symbols in a single abp engine.

    One event each would bury every other signal in the dashboard, so volume must
    scale with the number of distinct problems rather than the repository size.
    """
    for i in range(3900):
        degradations.record("type_hierarchy_supertypes", f"Sym{i}: server closed")

    with patch("telemetry.events.telemetry") as tele:
        payload = flush_degradations("static_analysis")

    assert tele.capture.call_count == 1
    assert payload["degraded_events"] == 3900
    assert payload["by_category"]["type_hierarchy_supertypes"]["occurrences"] == 3900
    assert payload["by_category"]["type_hierarchy_supertypes"]["example"] == "Sym0: server closed"


def test_cost_is_reported_not_just_occurrences():
    """Ten failed batches of fifty is a different problem from ten failed symbols."""
    degradations.record("references_batch", "TimeoutError", items=50)
    degradations.record("references_batch", "TimeoutError", items=50)

    with patch("telemetry.events.telemetry"):
        payload = flush_degradations("static_analysis")

    assert payload["by_category"]["references_batch"] == {
        "occurrences": 2,
        "items": 100,
        "example": "TimeoutError",
    }


def test_clean_run_sends_nothing():
    with patch("telemetry.events.telemetry") as tele:
        assert flush_degradations("static_analysis") == {}
    assert tele.capture.call_count == 0


def test_flush_resets_so_the_next_run_starts_clean():
    degradations.record("references_batch", "boom")
    with patch("telemetry.events.telemetry"):
        flush_degradations("static_analysis")
        assert flush_degradations("static_analysis") == {}
