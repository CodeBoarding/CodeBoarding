"""Tests for the references-phase memory bound (``LSPRecycler``)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.engine.lsp_constants import (
    MAX_MEMORY_BUDGET,
    MEMORY_BUDGET_ENV_VAR,
    MIN_MEMORY_BUDGET,
)
from static_analyzer.engine.lsp_recycler import (
    LSPRecycler,
    default_memory_budget,
)
from static_analyzer.engine.process_memory import process_tree_rss

GB = 1024**3


def test_process_tree_rss_includes_descendants_only():
    table = {
        10: (1, 100),
        11: (10, 50),
        12: (11, 25),
        20: (1, 1_000),
    }
    with (
        patch("static_analyzer.engine.process_memory._IS_LINUX", True),
        patch("static_analyzer.engine.process_memory._linux_process_table", return_value=table),
    ):
        assert process_tree_rss(10) == 175


@pytest.fixture
def make_recycler(monkeypatch):
    """Build a recycler whose memory readings are scripted, holding the last value."""

    def factory(rss_values: list[int], budget: int = 4 * GB) -> tuple[LSPRecycler, MagicMock]:
        readings = iter(rss_values)
        last = [rss_values[-1]]

        def fake_rss(_pid=None):
            last[0] = next(readings, last[0])
            return last[0]

        monkeypatch.setattr("static_analyzer.engine.lsp_recycler.process_tree_rss", fake_rss)
        lsp = MagicMock()
        lsp.pid = 4242
        return LSPRecycler(lsp, Path("/repo/a.cs"), probe_timeout=600, budget_bytes=budget), lsp

    return factory


class TestBudget:
    def test_scales_with_physical_memory(self):
        with patch("static_analyzer.engine.lsp_recycler.physical_memory_bytes", return_value=16 * GB):
            assert default_memory_budget() == int(16 * GB * 0.4)

    def test_clamped_at_both_ends(self):
        with patch("static_analyzer.engine.lsp_recycler.physical_memory_bytes", return_value=1 * GB):
            assert default_memory_budget() == MIN_MEMORY_BUDGET
        with patch("static_analyzer.engine.lsp_recycler.physical_memory_bytes", return_value=512 * GB):
            assert default_memory_budget() == MAX_MEMORY_BUDGET

    def test_unknown_physical_memory_falls_back_to_the_floor(self):
        with patch("static_analyzer.engine.lsp_recycler.physical_memory_bytes", return_value=0):
            assert default_memory_budget() == MIN_MEMORY_BUDGET

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv(MEMORY_BUDGET_ENV_VAR, "1536")
        assert default_memory_budget() == 1536 * 1024**2

    def test_unparseable_env_override_falls_back_to_the_derived_budget(self, monkeypatch):
        monkeypatch.setenv(MEMORY_BUDGET_ENV_VAR, "lots")
        with patch("static_analyzer.engine.lsp_recycler.physical_memory_bytes", return_value=16 * GB):
            assert default_memory_budget() == int(16 * GB * 0.4)


class TestRecycling:
    def test_stays_out_of_the_way_under_budget(self, make_recycler):
        recycler, lsp = make_recycler([1 * GB])

        for _ in range(20):
            recycler.before_batch()

        lsp.restart.assert_not_called()

    def test_restarts_and_reprobes_when_over_budget(self, make_recycler):
        recycler, lsp = make_recycler([9 * GB, 1 * GB])

        recycler.before_batch()
        lsp.restart.assert_called_once()
        # The reprobe is what makes the restart synchronous: a workspace-backed
        # server cannot answer until it has re-read the project.
        lsp.document_symbol.assert_called_once_with(Path("/repo/a.cs"), timeout=600)
        assert recycler.recycle_count == 1

    def test_recycles_again_when_it_grows_back(self, make_recycler):
        recycler, _ = make_recycler([9 * GB, 1 * GB, 1 * GB, 9 * GB, 1 * GB])

        for _ in range(5):
            recycler.before_batch()

        assert recycler.recycle_count == 2

    def test_under_budget_batches_are_left_at_full_size(self, make_recycler):
        # 3GB of a 4GB budget: close to the ceiling but not over it. Throttling
        # here cost 38% of Phase 2's wall clock and bought almost no headroom.
        recycler, lsp = make_recycler([3 * GB])

        recycler.before_batch()

        lsp.restart.assert_not_called()

    def test_restart_line_carries_phase_position(self, make_recycler, caplog):
        recycler, _ = make_recycler([9 * GB, 1 * GB])
        recycler.note_progress(4200, 15229)

        with caplog.at_level("WARNING"):
            recycler.before_batch()

        assert "4200/15229" in caplog.text

    def test_dead_server_reads_as_zero_and_is_not_restarted(self):
        lsp = MagicMock()
        lsp.pid = None
        recycler = LSPRecycler(lsp, Path("/repo/a.cs"), probe_timeout=600, budget_bytes=1)

        for _ in range(5):
            recycler.before_batch()

        lsp.restart.assert_not_called()

    def test_disarms_when_a_bare_reload_already_exceeds_the_budget(self, make_recycler):
        # Over budget -> restart -> still over budget. Restarting cannot help a
        # workspace this large, and retrying every batch would livelock.
        recycler, lsp = make_recycler([9 * GB, 9 * GB])

        for _ in range(5):
            recycler.before_batch()

        assert lsp.restart.call_count == 1
        assert recycler.recycle_count == 1

    def test_warms_the_reference_index_after_a_restart(self, make_recycler):
        # A loaded workspace is not a warm reference index; without this the
        # first real batch after a restart burns its deadline building one.
        recycler, lsp = make_recycler([9 * GB, 1 * GB])

        recycler.before_batch()

        lsp.references.assert_called_once_with(Path("/repo/a.cs"), 0, 0, timeout=600)

    def test_warms_on_the_position_the_caller_is_about_to_query(self, make_recycler):
        # Warming on a synthetic (0, 0) usually lands on no symbol, so the server
        # does no work and the next real batch pays the cold-start stall instead.
        recycler, lsp = make_recycler([9 * GB, 1 * GB])

        recycler.before_batch((Path("/repo/hot.cs"), 42, 7))

        lsp.references.assert_called_once_with(Path("/repo/hot.cs"), 42, 7, timeout=600)

    def test_a_failed_warmup_does_not_abort_the_recycle(self, make_recycler):
        recycler, lsp = make_recycler([9 * GB, 1 * GB])
        lsp.references.side_effect = TimeoutError("cold server")

        recycler.before_batch()

        assert recycler.recycle_count == 1
