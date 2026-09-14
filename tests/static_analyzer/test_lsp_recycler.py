"""Tests for the per-engine language-server memory budget."""

from unittest.mock import patch

import pytest

from static_analyzer.engine.lsp_constants import (
    MAX_MEMORY_BUDGET,
    MEMORY_BUDGET_ENV_VAR,
    MIN_MEMORY_BUDGET,
)
from static_analyzer.engine.lsp_recycler import (
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
