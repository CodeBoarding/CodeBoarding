"""NameArbiterAgent: NOOP-or-UPDATE decision for cluster names.

Narrow LLM call that decides whether a near-match cluster should keep its
prior name (NOOP) or be renamed (UPDATE). Caller is responsible for the
similarity gate — the arbiter assumes the inputs are recognizable as
"the same cluster."
"""

import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import NameDecision
from agents.prompts import get_system_message
from agents.prompts.name_arbiter import get_name_arbiter_message
from agents.validation import ValidationContext, validate_name_decision
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


# Cache key: (prior_name, sorted prior members tuple, sorted new members tuple).
# Value: the chosen name (prior_name when NOOP, new_name when UPDATE).
ArbiterCache = dict[tuple[str, tuple[str, ...], tuple[str, ...]], str]


class NameArbiterAgent(CodeBoardingAgent):
    """One LLM call: decide NOOP vs UPDATE for a single cluster's name."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        # No tools: arbitration is a pure judgment call over the inputs in the
        # prompt. Source reads would just slow it down.
        super().__init__(
            repo_dir,
            static_analysis,
            get_system_message(),
            agent_llm,
            parsing_llm,
            tool_names=[],
        )
        self._prompt_template = PromptTemplate(
            template=get_name_arbiter_message(),
            input_variables=[
                "prior_name",
                "prior_members",
                "new_members",
                "added_count",
                "added_members",
                "removed_count",
                "removed_members",
            ],
        )

    @trace
    def arbitrate(
        self,
        prior_name: str,
        prior_members: list[str],
        new_members: list[str],
        cache: ArbiterCache | None = None,
    ) -> NameDecision:
        """Return the NOOP-or-UPDATE decision for this cluster.

        Why the cache parameter: a single analysis run may arbitrate the
        same (prior_name, prior_members, new_members) triple multiple times
        across redetail passes. Memoizing avoids redundant LLM calls without
        introducing cross-run state — the cache is per-run and ephemeral.
        """
        cache_key = (
            prior_name,
            tuple(sorted(prior_members)),
            tuple(sorted(new_members)),
        )
        if cache is not None and cache_key in cache:
            cached_name = cache[cache_key]
            return _decision_from_cached_name(prior_name, cached_name)

        prior_set, new_set = set(prior_members), set(new_members)
        added = sorted(new_set - prior_set)
        removed = sorted(prior_set - new_set)

        prompt = self._prompt_template.format(
            prior_name=prior_name,
            prior_members=_render_members(sorted(prior_members)),
            new_members=_render_members(sorted(new_members)),
            added_count=len(added),
            added_members=_render_members(added),
            removed_count=len(removed),
            removed_members=_render_members(removed),
        )

        decision: NameDecision = self._validation_invoke(
            prompt,
            NameDecision,
            validators=[validate_name_decision],
            context=ValidationContext(expected_prior_name=prior_name),
            max_validation_attempts=3,
        )

        # Defense in depth: even after retries, the validator may return the
        # best-scoring (still-imperfect) result. Treat a final-attempt drift as
        # NOOP rather than letting a paraphrased prior_name leak downstream.
        if decision.prior_name != prior_name:
            logger.warning(
                "[NameArbiter] Final response had prior_name=%r (expected %r); coercing to NOOP.",
                decision.prior_name,
                prior_name,
            )
            decision = NameDecision(
                event="NOOP",
                prior_name=prior_name,
                new_name=None,
                rationale="Validator-rejected drift coerced to NOOP",
            )

        if cache is not None:
            cache[cache_key] = decision.new_name if decision.event == "UPDATE" else prior_name

        return decision


def _render_members(members: list[str]) -> str:
    if not members:
        return "(none)"
    return "\n".join(f"  - {m}" for m in members)


def _decision_from_cached_name(prior_name: str, cached_name: str) -> NameDecision:
    if cached_name == prior_name:
        return NameDecision(
            event="NOOP",
            prior_name=prior_name,
            new_name=None,
            rationale="cache hit",
        )
    return NameDecision(
        event="UPDATE",
        prior_name=prior_name,
        new_name=cached_name,
        rationale="cache hit",
    )
