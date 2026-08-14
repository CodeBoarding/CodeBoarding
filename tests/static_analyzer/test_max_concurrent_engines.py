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
    def test_scales_with_cores(self):
        for cores, expected in ((1, 1), (2, 1), (12, 4), (24, 8)):
            with patch("os.cpu_count", return_value=cores):
                assert recommended_engine_concurrency() == expected

    def test_capped_so_a_big_host_does_not_thrash(self):
        with patch("os.cpu_count", return_value=128):
            assert recommended_engine_concurrency() == 8

    def test_unknown_core_count_still_yields_a_usable_bound(self):
        with patch("os.cpu_count", return_value=None):
            assert recommended_engine_concurrency() == 1
