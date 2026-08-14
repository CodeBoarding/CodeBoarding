"""Tests for the engine-concurrency bound.

The bound exists because a monorepo emits one engine per sub-project and
starting them all at once costs peak memory for work that then runs one engine
at a time. See ``max_concurrent_engines`` for the measurements behind it.
"""

from unittest.mock import patch

from static_analyzer import (
    MAX_CONCURRENT_ENGINES_ENV_VAR,
    max_concurrent_engines,
    recommended_engine_concurrency,
)


class TestMaxConcurrentEngines:
    def test_unset_stays_unbounded(self):
        with patch.dict("os.environ", {}, clear=True):
            assert max_concurrent_engines() == 0

    def test_explicit_value_is_honoured(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "3"}):
            assert max_concurrent_engines() == 3

    def test_zero_disables_the_bound(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "0"}):
            assert max_concurrent_engines() == 0

    def test_negative_is_clamped_to_unbounded(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "-4"}):
            assert max_concurrent_engines() == 0

    def test_non_integer_is_treated_as_unset(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "lots"}):
            assert max_concurrent_engines() == 0


class TestRecommendedEngineConcurrency:
    """The bound is the smallest of work, CPU and memory. Each test pins one."""

    def test_cpu_binds_on_a_modest_host(self):
        # 12 cores / 3 = 4, which is where the abp sweep actually peaked.
        with patch("static_analyzer.default_memory_budget", return_value=64 * 1024**3):
            with patch("os.cpu_count", return_value=12):
                assert recommended_engine_concurrency(28) == 4

    def test_memory_binds_on_a_large_host(self):
        # 128 cores would allow 42 on CPU alone; a 12GB budget supports 6.
        with patch("static_analyzer.default_memory_budget", return_value=12 * 1024**3):
            with patch("os.cpu_count", return_value=128):
                assert recommended_engine_concurrency(28) == 6

    def test_available_work_binds_for_an_ordinary_repo(self):
        with patch("static_analyzer.default_memory_budget", return_value=64 * 1024**3):
            with patch("os.cpu_count", return_value=128):
                assert recommended_engine_concurrency(2) == 2

    def test_never_drops_below_one(self):
        with patch("static_analyzer.default_memory_budget", return_value=1024**3):
            with patch("os.cpu_count", return_value=2):
                assert recommended_engine_concurrency(28) == 1

    def test_unknown_core_count_still_yields_a_usable_bound(self):
        with patch("static_analyzer.default_memory_budget", return_value=64 * 1024**3):
            with patch("os.cpu_count", return_value=None):
                assert recommended_engine_concurrency(28) == 1
