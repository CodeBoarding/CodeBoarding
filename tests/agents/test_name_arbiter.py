"""Tests for ``NameDecision`` schema, ``validate_name_decision`` validator,
and ``NameArbiterAgent.arbitrate``."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from agents.agent_responses import NameDecision
from agents.name_arbiter import NameArbiterAgent
from agents.validation import (
    ValidationContext,
    validate_name_decision,
)
from static_analyzer.analysis_result import StaticAnalysisResults


class TestNameDecisionSchema(unittest.TestCase):
    def test_noop_with_new_name_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            NameDecision(event="NOOP", prior_name="X", new_name="Y", rationale="bad")

    def test_update_without_new_name_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            NameDecision(event="UPDATE", prior_name="X", new_name=None, rationale="bad")

    def test_update_with_same_new_name_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            NameDecision(event="UPDATE", prior_name="X", new_name="X", rationale="bad")

    def test_noop_minimal_valid(self) -> None:
        d = NameDecision(event="NOOP", prior_name="Authentication", new_name=None, rationale="no change")
        self.assertEqual(d.event, "NOOP")
        self.assertIsNone(d.new_name)

    def test_update_minimal_valid(self) -> None:
        d = NameDecision(event="UPDATE", prior_name="Authentication", new_name="Cryptography", rationale="shifted")
        self.assertEqual(d.event, "UPDATE")
        self.assertEqual(d.new_name, "Cryptography")


class TestValidateNameDecision(unittest.TestCase):
    def test_no_expected_prior_name_passes(self) -> None:
        decision = NameDecision(event="NOOP", prior_name="Whatever", rationale="ok")
        result = validate_name_decision(decision, ValidationContext())
        self.assertTrue(result.is_valid)

    def test_prior_name_match_passes(self) -> None:
        decision = NameDecision(event="NOOP", prior_name="Authentication", rationale="ok")
        result = validate_name_decision(decision, ValidationContext(expected_prior_name="Authentication"))
        self.assertTrue(result.is_valid)

    def test_prior_name_drift_rejected(self) -> None:
        decision = NameDecision(event="NOOP", prior_name="Auth Service", rationale="paraphrased")
        result = validate_name_decision(decision, ValidationContext(expected_prior_name="Authentication"))
        self.assertFalse(result.is_valid)
        joined = "\n".join(result.feedback_messages)
        self.assertIn("Authentication", joined)
        self.assertIn("Auth Service", joined)

    def test_prior_name_case_drift_rejected(self) -> None:
        # Case sensitivity is intentional — paraphrased capitalization is still drift.
        decision = NameDecision(event="NOOP", prior_name="authentication", rationale="case")
        result = validate_name_decision(decision, ValidationContext(expected_prior_name="Authentication"))
        self.assertFalse(result.is_valid)


class TestArbitrate(unittest.TestCase):
    def setUp(self) -> None:
        self.static_analysis = MagicMock(spec=StaticAnalysisResults)
        with patch("agents.agent.create_agent") as mock_create_agent:
            mock_create_agent.return_value = MagicMock()
            self.agent = NameArbiterAgent(
                repo_dir=Path("/tmp/fake"),
                static_analysis=self.static_analysis,
                agent_llm=MagicMock(),
                parsing_llm=MagicMock(),
            )

    def _stub_decision(self, decision: NameDecision) -> None:
        self.agent._validation_invoke = MagicMock(return_value=decision)  # type: ignore[method-assign]

    def test_returns_noop_on_small_addition(self) -> None:
        self._stub_decision(NameDecision(event="NOOP", prior_name="Authentication", rationale="closely related"))
        result = self.agent.arbitrate(
            prior_name="Authentication",
            prior_members=["auth.login", "auth.logout", "auth.verify_token"],
            new_members=["auth.login", "auth.logout", "auth.verify_token", "auth.refresh_token"],
        )
        self.assertEqual(result.event, "NOOP")
        self.assertIsNone(result.new_name)

    def test_returns_update_on_purpose_shift(self) -> None:
        self._stub_decision(
            NameDecision(
                event="UPDATE",
                prior_name="Authentication",
                new_name="Cryptography",
                rationale="purpose shifted",
            )
        )
        result = self.agent.arbitrate(
            prior_name="Authentication",
            prior_members=["auth.login", "auth.logout"],
            new_members=["crypto.encrypt", "crypto.decrypt"],
        )
        self.assertEqual(result.event, "UPDATE")
        self.assertEqual(result.new_name, "Cryptography")

    def test_cache_hit_skips_llm(self) -> None:
        self._stub_decision(NameDecision(event="NOOP", prior_name="Authentication", rationale="should not be called"))
        cache = {("Authentication", ("a",), ("a", "b")): "Authentication"}
        result = self.agent.arbitrate(
            prior_name="Authentication",
            prior_members=["a"],
            new_members=["a", "b"],
            cache=cache,
        )
        self.assertEqual(result.event, "NOOP")
        # Why we assert this directly: caching is the whole point of the param;
        # if the LLM is invoked on a cache hit, we've reintroduced cost the
        # callers expect to avoid.
        self.agent._validation_invoke.assert_not_called()  # type: ignore[attr-defined]

    def test_cache_miss_then_hit_records_chosen_name(self) -> None:
        self._stub_decision(
            NameDecision(
                event="UPDATE",
                prior_name="Authentication",
                new_name="Cryptography",
                rationale="shifted",
            )
        )
        cache: dict = {}
        first = self.agent.arbitrate(
            prior_name="Authentication",
            prior_members=["a"],
            new_members=["a", "b"],
            cache=cache,
        )
        self.assertEqual(first.new_name, "Cryptography")

        # Second call with the same triple uses the cache; LLM mock should not be re-invoked.
        # Why we re-stub to a different value: if the cache reads, the result
        # must come from the cache and ignore whatever the LLM would return.
        self._stub_decision(NameDecision(event="NOOP", prior_name="Authentication", rationale="ignored"))
        second = self.agent.arbitrate(
            prior_name="Authentication",
            prior_members=["a"],
            new_members=["a", "b"],
            cache=cache,
        )
        self.assertEqual(second.event, "UPDATE")
        self.assertEqual(second.new_name, "Cryptography")

    def test_drift_in_final_response_coerced_to_noop(self) -> None:
        """If the LLM's prior_name still drifts after retries (validator returned best-of-N),
        the arbiter must not silently pass the drifted name downstream."""
        self._stub_decision(
            NameDecision(
                event="NOOP",
                prior_name="Auth Service",  # drifted vs input "Authentication"
                rationale="LLM paraphrased after retries",
            )
        )
        result = self.agent.arbitrate(
            prior_name="Authentication",
            prior_members=["a"],
            new_members=["a"],
        )
        self.assertEqual(result.event, "NOOP")
        self.assertEqual(result.prior_name, "Authentication")
        self.assertIsNone(result.new_name)


if __name__ == "__main__":
    unittest.main()
