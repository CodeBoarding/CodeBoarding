"""Integration test for the gate-then-arbitrate naming flow.

These tests compose the deterministic Jaccard gate (PriorClusterIndex) with
the LLM arbiter (NameArbiterAgent) and verify the end-to-end behavior the
real cache-miss path would have. The LLM call itself is stubbed via
``_validation_invoke``: the seam under test is the *composition*, not the
LLM's reasoning. Real-LLM end-to-end tests live under tests/integration/
and require API keys.

Why an integration test for a building block: the arbiter and the gate are
each unit-tested in isolation, but their composition is what callers will
actually use. The composition has its own correctness properties (correct
gate threshold → correct arbiter input → correct downstream name) that
neither unit test covers.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import NameDecision
from agents.name_arbiter import ArbiterCache, NameArbiterAgent
from agents.prior_cluster_index import PriorClusterIndex
from static_analyzer.analysis_result import StaticAnalysisResults


def _resolve_name(
    new_members: list[str],
    *,
    index: PriorClusterIndex,
    agent: NameArbiterAgent,
    full_naming_fn,
    arbiter_cache: ArbiterCache,
    threshold: float = 0.5,
) -> str:
    """The composition under test.

    Mirrors the pseudo-flow from the design sketch: deterministic gate first,
    LLM arbiter on near-match, full re-derivation as the last-resort fallback.
    A real cache-miss handler in the codebase would call exactly this shape.
    """
    match = index.find_best_match(new_members, threshold=threshold)
    if match is None:
        return full_naming_fn(new_members)

    prior, _score = match
    decision = agent.arbitrate(
        prior_name=prior.name,
        prior_members=sorted(prior.members),
        new_members=new_members,
        cache=arbiter_cache,
    )
    if decision.event == "NOOP":
        return prior.name
    assert decision.new_name is not None  # narrowed by NameDecision validator
    return decision.new_name


class TestGateAndArbiterComposition(unittest.TestCase):
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
        self.full_naming = MagicMock(side_effect=lambda members: f"Fresh<{','.join(sorted(members))}>")
        self.cache: ArbiterCache = {}
        self.index = PriorClusterIndex.from_pairs(
            [
                ("Authentication", ["auth.login", "auth.logout", "auth.verify_token"]),
                ("Data Pipeline", ["pipe.ingest", "pipe.transform", "pipe.export"]),
            ]
        )

    def _stub_decision(self, decision: NameDecision) -> None:
        self.agent._validation_invoke = MagicMock(return_value=decision)  # type: ignore[method-assign]

    def test_high_jaccard_with_noop_keeps_prior_name(self) -> None:
        self._stub_decision(
            NameDecision(
                event="NOOP",
                prior_name="Authentication",
                rationale="closely related addition",
            )
        )
        new_members = ["auth.login", "auth.logout", "auth.verify_token", "auth.refresh_token"]
        chosen = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
        )
        self.assertEqual(chosen, "Authentication")
        # Why we assert the full naming path is untouched: the whole point of
        # the gate-then-arbitrate path is to avoid full re-derivation when a
        # near-match exists. If full naming is invoked here, we've defeated it.
        self.full_naming.assert_not_called()

    def test_high_jaccard_with_update_uses_new_name(self) -> None:
        self._stub_decision(
            NameDecision(
                event="UPDATE",
                prior_name="Authentication",
                new_name="Identity Service",
                rationale="purpose broadened",
            )
        )
        new_members = ["auth.login", "auth.logout", "auth.verify_token", "auth.refresh_token"]
        chosen = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
        )
        self.assertEqual(chosen, "Identity Service")
        self.full_naming.assert_not_called()

    def test_low_jaccard_falls_through_to_full_naming(self) -> None:
        # No prior cluster overlaps meaningfully with these members → arbiter
        # must NOT be invoked and full naming must take over.
        new_members = ["unrelated.alpha", "unrelated.beta", "unrelated.gamma"]
        # Why we stub the arbiter to a value that would be wrong: if the gate
        # doesn't reject, this stub would leak into the chosen name. Asserting
        # ``Fresh<...>`` proves the gate, not the arbiter, drove the path.
        self._stub_decision(
            NameDecision(
                event="UPDATE",
                prior_name="Authentication",
                new_name="WrongRoute",
                rationale="should not be invoked",
            )
        )
        chosen = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
        )
        self.assertTrue(chosen.startswith("Fresh<"))
        self.assertIn("unrelated.alpha", chosen)
        self.agent._validation_invoke.assert_not_called()  # type: ignore[attr-defined]

    def test_repeat_call_within_run_uses_cache(self) -> None:
        self._stub_decision(NameDecision(event="NOOP", prior_name="Authentication", rationale="first call"))
        new_members = ["auth.login", "auth.logout", "auth.verify_token", "auth.refresh_token"]
        first = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
        )
        self.assertEqual(first, "Authentication")
        self.assertEqual(self.agent._validation_invoke.call_count, 1)  # type: ignore[attr-defined]

        # Second call with identical inputs must skip the LLM via the arbiter cache.
        self._stub_decision(
            NameDecision(
                event="UPDATE",
                prior_name="Authentication",
                new_name="WrongIfInvoked",
                rationale="should not run",
            )
        )
        second = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
        )
        self.assertEqual(second, "Authentication")  # cached NOOP holds.

    def test_high_jaccard_to_a_different_prior_picks_correct_one(self) -> None:
        # Confirms the gate dispatches to the *right* prior when the index has
        # multiple candidates. The arbiter's stub will receive the correct
        # prior_name only if the gate selected the right cluster.
        self._stub_decision(NameDecision(event="NOOP", prior_name="Data Pipeline", rationale="ok"))
        new_members = ["pipe.ingest", "pipe.transform", "pipe.export", "pipe.normalize"]
        chosen = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
        )
        self.assertEqual(chosen, "Data Pipeline")

    def test_threshold_gates_borderline_cases(self) -> None:
        # 2 of 4 prior members survive in a 4-member new set ⇒ Jaccard 2/6 ≈ 0.33.
        # At default threshold 0.5: rejected → full naming.
        # At threshold 0.3: accepted → arbiter invoked.
        new_members = ["auth.login", "auth.logout", "stranger.alpha", "stranger.beta"]
        self._stub_decision(
            NameDecision(
                event="UPDATE",
                prior_name="Authentication",
                new_name="Mixed Cluster",
                rationale="lowered threshold caught it",
            )
        )

        # Default threshold rejects.
        chosen_default = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
        )
        self.assertTrue(chosen_default.startswith("Fresh<"))

        # Lowered threshold accepts.
        chosen_loose = _resolve_name(
            new_members,
            index=self.index,
            agent=self.agent,
            full_naming_fn=self.full_naming,
            arbiter_cache=self.cache,
            threshold=0.3,
        )
        self.assertEqual(chosen_loose, "Mixed Cluster")


if __name__ == "__main__":
    unittest.main()
