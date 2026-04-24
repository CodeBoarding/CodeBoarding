import unittest
from unittest.mock import patch

from agents.retry import (
    RetryAction,
    RetryDecision,
    default_backoff,
    with_retries,
)


class TestDefaultBackoff(unittest.TestCase):
    def test_exponential_growth(self):
        self.assertEqual(default_backoff(0, initial_s=10, multiplier=2.0, max_s=None), 10)
        self.assertEqual(default_backoff(1, initial_s=10, multiplier=2.0, max_s=None), 20)
        self.assertEqual(default_backoff(3, initial_s=10, multiplier=2.0, max_s=None), 80)

    def test_clamps_to_max(self):
        self.assertEqual(default_backoff(4, initial_s=10, multiplier=2.0, max_s=120), 120)
        self.assertEqual(default_backoff(10, initial_s=30, multiplier=2.0, max_s=300), 300)


class TestWithRetries(unittest.TestCase):
    def test_returns_on_first_success(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        self.assertEqual(with_retries(fn, max_attempts=5), "ok")
        self.assertEqual(len(calls), 1)

    @patch("agents.retry.time.sleep")
    def test_retries_with_default_classify_then_succeeds(self, mock_sleep):
        results: list = [ValueError("fail1"), ValueError("fail2"), "ok"]

        def fn():
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        self.assertEqual(
            with_retries(
                fn,
                max_attempts=5,
                classify=lambda _e, a: RetryDecision(RetryAction.RETRY, backoff_s=1.0 * (2**a)),
            ),
            "ok",
        )
        # Two retries; backoff was 1 then 2
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertEqual(mock_sleep.call_args_list[0].args, (1.0,))
        self.assertEqual(mock_sleep.call_args_list[1].args, (2.0,))

    @patch("agents.retry.time.sleep")
    def test_give_up_raises_immediately_no_sleep(self, mock_sleep):
        class Fatal(Exception):
            pass

        def fn():
            raise Fatal("permanent")

        with self.assertRaises(Fatal):
            with_retries(
                fn,
                max_attempts=5,
                classify=lambda e, _a: RetryDecision(
                    RetryAction.GIVE_UP if isinstance(e, Fatal) else RetryAction.RETRY
                ),
            )
        mock_sleep.assert_not_called()

    @patch("agents.retry.time.sleep")
    def test_retry_now_does_not_sleep(self, mock_sleep):
        results: list = [ValueError("fail"), "ok"]

        def fn():
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        self.assertEqual(
            with_retries(
                fn,
                max_attempts=5,
                classify=lambda _e, _a: RetryDecision(RetryAction.RETRY_NOW),
            ),
            "ok",
        )
        mock_sleep.assert_not_called()

    @patch("agents.retry.time.sleep")
    def test_exhausted_raises_by_default(self, mock_sleep):
        def fn():
            raise RuntimeError("always fails")

        with self.assertRaises(RuntimeError):
            with_retries(
                fn,
                max_attempts=3,
                classify=lambda _e, _a: RetryDecision(RetryAction.RETRY, backoff_s=0.1),
            )
        # 3 attempts, so 2 sleeps (no sleep after final failure)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("agents.retry.time.sleep")
    def test_on_exhausted_returns_fallback(self, mock_sleep):
        def fn():
            raise RuntimeError("always fails")

        result = with_retries(
            fn,
            max_attempts=3,
            classify=lambda _e, _a: RetryDecision(RetryAction.RETRY, backoff_s=0.1),
            on_exhausted=lambda _: "fallback",
        )
        self.assertEqual(result, "fallback")
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("agents.retry.time.sleep")
    def test_fn_is_invoked_fresh_each_attempt(self, mock_sleep):
        """fn() is a closure; callers relying on per-attempt construction (e.g. recreating
        an LLM extractor on retry) depend on this being called fresh each time."""
        state = {"constructions": 0, "attempts": 0}

        def fn():
            state["constructions"] += 1  # simulating "construct extractor" here
            state["attempts"] += 1
            if state["attempts"] < 3:
                raise ValueError("not yet")
            return "ok"

        with_retries(
            fn,
            max_attempts=5,
            classify=lambda _e, _a: RetryDecision(RetryAction.RETRY, backoff_s=0.1),
        )
        self.assertEqual(state["constructions"], 3)


if __name__ == "__main__":
    unittest.main()
