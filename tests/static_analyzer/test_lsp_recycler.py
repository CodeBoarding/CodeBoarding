"""Tests for the per-engine language-server memory budget."""

from unittest.mock import MagicMock, patch

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
    def test_three_quarters_of_physical_memory(self):
        assert _budget(ram_gb=8) == 6 * GB

    def test_a_cgroup_v2_limit_below_the_ram_is_what_is_shared(self):
        """A container sees the host's RAM; its limit is what the kernel enforces."""
        assert _budget(ram_gb=64, cgroup=str(4 * GB)) == 3 * GB

    def test_a_cgroup_without_a_limit_leaves_the_ram(self):
        assert _budget(ram_gb=8, cgroup="max") == 6 * GB

    def test_capped(self):
        assert _budget(ram_gb=512) == MAX_MEMORY_BUDGET

    def test_unknown_memory_falls_back_to_the_floor(self):
        assert _budget(ram_gb=None) == MIN_MEMORY_BUDGET

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv(MEMORY_BUDGET_ENV_VAR, "1536")
        assert default_memory_budget() == 1536 * 1024**2

    def test_unparseable_env_override_falls_back_to_the_derived_budget(self, monkeypatch):
        monkeypatch.setenv(MEMORY_BUDGET_ENV_VAR, "lots")
        assert _budget(ram_gb=8) == 6 * GB


def _budget(ram_gb: float | None, cgroup: str | None = None) -> int:
    memory_max = MagicMock()
    memory_max.read_text.side_effect = OSError if cgroup is None else None
    memory_max.read_text.return_value = cgroup
    with (
        patch("static_analyzer.engine.lsp_recycler.total_ram_gb", return_value=ram_gb),
        patch("static_analyzer.engine.lsp_recycler.Path", return_value=memory_max),
    ):
        return default_memory_budget()
