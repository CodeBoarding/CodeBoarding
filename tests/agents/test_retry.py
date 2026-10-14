import unittest
from unittest.mock import patch

from agents.retry import (
    LLM_BACKOFF_MAX_S,
    LLM_MAX_ATTEMPTS,
    RetryAction,
    RetryDecision,
    classify_llm_error,
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
        state = {"invocations": 0, "attempts": 0}

        def fn():
            state["invocations"] += 1
            state["attempts"] += 1
            if state["attempts"] < 3:
                raise ValueError("not yet")
            return "ok"

        with_retries(
            fn,
            max_attempts=5,
            classify=lambda _e, _a: RetryDecision(RetryAction.RETRY, backoff_s=0.1),
        )
        self.assertEqual(state["invocations"], 3)


if __name__ == "__main__":
    unittest.main()


class _StatusError(Exception):
    def __init__(self, message: str, status_code: int, retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = type("Response", (), {"headers": {"retry-after": retry_after} if retry_after else {}})()


THROTTLED = "Error code: 429 - {'Reason': 'ConcurrentInvocationLimitExceeded', 'message': 'Rate Exceeded.'}"


class TestClassifyLLMError(unittest.TestCase):
    def test_gives_up_on_permanent_failures(self):
        for error in (
            _StatusError("Error code: 429 - {'code': 'insufficient_quota'}", 429),
            _StatusError("payment required", 402),
            _StatusError("invalid api key", 401),
            _StatusError("bad request", 400),
        ):
            self.assertEqual(classify_llm_error(error, 0).action, RetryAction.GIVE_UP, error)

    def test_full_jitter_under_a_doubling_ceiling(self):
        with patch("agents.retry.random.uniform", side_effect=lambda low, high: high) as uniform:
            delays = [classify_llm_error(_StatusError(THROTTLED, 429), attempt).backoff_s for attempt in range(6)]
        self.assertEqual(delays, [4.0, 8.0, 16.0, 32.0, 60.0, 60.0])
        self.assertTrue(all(call.args[0] == 0.0 for call in uniform.call_args_list))

    def test_honours_retry_after_up_to_the_cap(self):
        decision = classify_llm_error(_StatusError(THROTTLED, 429, retry_after="7"), 0)
        self.assertEqual(decision, RetryDecision(RetryAction.RETRY, backoff_s=7.0))
        decision = classify_llm_error(_StatusError("Bad gateway", 502, retry_after="600"), 0)
        self.assertEqual(decision.backoff_s, LLM_BACKOFF_MAX_S)


class TestRetryTransientLLMErrors(unittest.TestCase):
    """Why: one throttled call from the hosted proxy used to fail a four-minute run."""

    @patch("agents.retry.time.sleep")
    def test_a_throttled_call_is_retried_and_logged(self, sleep):
        results: list = [_StatusError(THROTTLED, 429), _StatusError("Upstream request failed.", 502), "ok"]

        def fn():
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with self.assertLogs("agents.retry", level="WARNING") as logs:
            self.assertEqual(
                with_retries(fn, max_attempts=LLM_MAX_ATTEMPTS, classify=classify_llm_error, log_prefix="Scope 3"), "ok"
            )
        self.assertEqual(sleep.call_count, 2)
        self.assertIn("Scope 3 failed (attempt 1/5): Error code: 429", logs.output[0])
        self.assertIn("attempt 2/5", logs.output[1])

    @patch("agents.retry.time.sleep")
    def test_a_quota_failure_is_not_retried(self, sleep):
        calls = []

        def fn():
            calls.append(1)
            raise _StatusError("payment required", 402)

        with self.assertRaises(_StatusError):
            with_retries(fn, max_attempts=LLM_MAX_ATTEMPTS, classify=classify_llm_error)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    @patch("agents.retry.time.sleep")
    def test_gives_up_after_the_budget(self, sleep):
        calls = []

        def fn():
            calls.append(1)
            raise _StatusError("Upstream request failed.", 502)

        with self.assertRaises(_StatusError):
            with_retries(fn, max_attempts=LLM_MAX_ATTEMPTS, classify=classify_llm_error)
        self.assertEqual(len(calls), LLM_MAX_ATTEMPTS)
        self.assertEqual(sleep.call_count, LLM_MAX_ATTEMPTS - 1)
