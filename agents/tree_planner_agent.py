"""The planner: an LLM folds a scope's candidate groups into components, once per scope."""

import logging
from collections.abc import Sequence
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import TreePlanInsights
from agents.prompts import get_system_message, get_tree_plan_message
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.names import CandidateGroup, GroupingContext, KinshipGrouper
from static_analyzer.clustering.names.frontier import Candidate

logger = logging.getLogger(__name__)

BUDGET = 9
"""Components a scope should not exceed. A preference the planner works toward, never a cap
the partition enforces: budgets are measured to refuse correct answers."""


class TreePlannerAgent(CodeBoardingAgent):
    """A ``Grouper`` that lets the model merge across words, after kinship has merged namesakes.

    It sees candidate groups with their sizes and a few identifiers, never a unit, so a
    wrong answer can only merge boxes. A scope already within the budget is not sent to the
    model at all.
    """

    name = "planner"

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message(), agent_llm, parsing_llm)
        self.prompt = PromptTemplate(template=get_tree_plan_message(), input_variables=["scope", "budget", "groups"])
        self._kinship = KinshipGrouper()

    @trace
    def group(self, candidates: Sequence[Candidate], context: GroupingContext) -> list[CandidateGroup]:
        groups = self._kinship.group(candidates, context)
        if len(groups) <= BUDGET:
            return groups
        labelled = {f"G{index}": group for index, group in enumerate(groups, start=1)}
        insights = self._parse_invoke(
            self.prompt.format(
                scope=context.scope_id,
                budget=BUDGET,
                groups="\n".join(
                    self._describe(label, group, candidates, context) for label, group in labelled.items()
                ),
            ),
            TreePlanInsights,
        )
        folded = self._fold(labelled, insights)
        logger.info("[Planner] %s: %d candidate groups -> %d components", context.scope_id, len(groups), len(folded))
        return folded

    @staticmethod
    def _describe(label: str, group: CandidateGroup, candidates: Sequence[Candidate], context: GroupingContext) -> str:
        by_key = {candidate.key: candidate for candidate in candidates}
        members = [by_key[key] for key in group.keys]
        units = sum(context.sizes.get(key, 0) for key in group.keys)
        names = ", ".join(candidate.label or candidate.kind for candidate in members)
        sample = ", ".join(dict.fromkeys(name for key in group.keys for name in context.samples.get(key, ())))
        return f"{label}: {group.name} [{names}] ({units} files) e.g. {sample}"

    @staticmethod
    def _fold(labelled: dict[str, CandidateGroup], insights: TreePlanInsights) -> list[CandidateGroup]:
        """Every label lands in exactly one component: the first that names it, else its own."""
        taken: set[str] = set()
        folded: list[CandidateGroup] = []
        for planned in insights.groups:
            members = [label for label in planned.members if label in labelled and label not in taken]
            if not members:
                continue
            taken.update(members)
            folded.append(
                CandidateGroup(
                    name=planned.name.strip() or labelled[members[0]].name,
                    keys=tuple(key for label in members for key in labelled[label].keys),
                    terms=tuple(dict.fromkeys(term for label in members for term in labelled[label].terms))
                    + tuple(word for word in planned.owns if word),
                )
            )
        folded.extend(group for label, group in labelled.items() if label not in taken)
        return folded
