"""The planner: an LLM folds a scope's candidate groups into components, once per scope."""

import json
import logging
import re
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent, _raise_if_auth_error
from agents.llm_errors import LLMAuthError
from agents.agent_responses import PlannedGroup, TreePlanInsights
from agents.llm_config import MONITORING_CALLBACK, supports_json_mode
from agents.prompts import TREE_PLAN_SYSTEM_MESSAGE, get_tree_plan_message
from agents.retry import RetryAction, RetryDecision, default_backoff, with_retries
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.names import CandidateGroup, GroupingContext, KinshipGrouper, stem, tokenize
from static_analyzer.clustering.names.draft import GUARD_SHARE, MIN_UNITS
from static_analyzer.clustering.names.frontier import Candidate
from static_analyzer.clustering.names.spec import is_root

logger = logging.getLogger(__name__)

BUDGET = 9
"""Components a scope should not exceed. A preference the planner works toward, never a cap
the partition enforces: budgets are measured to refuse correct answers."""

DRAWS = 3
"""Answers drawn per scope; the one the others agree with most is kept. Why: the model is not
deterministic at temperature 0 (0.5-0.7 pair agreement between two draws of one prompt), so a
single draw makes the first run's luck the architecture."""

MAX_OWNS = 5
SAMPLE_NAMES = 5
DRAW_TIMEOUT_SECONDS = 600
_LABEL = re.compile(r"^G\d+$")


class TreePlannerAgent(CodeBoardingAgent):
    """A ``Grouper`` that folds kinship groups across words on the medoid of several JSON draws; it never sees a unit."""

    name = "planner"

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        draws: int = DRAWS,
    ):
        super().__init__(repo_dir, static_analysis, TREE_PLAN_SYSTEM_MESSAGE, agent_llm, parsing_llm)
        self.prompt = PromptTemplate(
            template=get_tree_plan_message(), input_variables=["scope", "units", "count", "budget", "floor", "groups"]
        )
        self.draws = draws
        self._kinship = KinshipGrouper()
        # The request itself is bounded, not only the wait for it: an abandoned draw thread must
        # not hold a connection open past the scope's deadline.
        self._model = (
            agent_llm.bind(response_format={"type": "json_object"}, timeout=DRAW_TIMEOUT_SECONDS)
            if supports_json_mode(agent_llm)
            else agent_llm
        )

    @trace
    def group(self, candidates: Sequence[Candidate], context: GroupingContext) -> list[CandidateGroup]:
        groups = self._kinship.group(candidates, context)
        if len(groups) <= BUDGET:
            return groups
        by_key = {candidate.key: candidate for candidate in candidates}
        size = {group.keys: sum(context.sizes.get(key, 0) for key in group.keys) for group in groups}
        shown = sorted(
            (group for group in groups if size[group.keys]), key=lambda group: (-size[group.keys], group.name)
        )
        labelled = {f"G{index}": group for index, group in enumerate(shown, start=1)}
        prompt = self.prompt.format(
            scope=context.scope_id,
            units=context.unit_count,
            count=len(shown),
            budget=BUDGET,
            floor=MIN_UNITS if is_root(context.scope_id) else max(MIN_UNITS, int(GUARD_SHARE * context.unit_count)),
            groups="\n".join(
                self._describe(label, group, size[group.keys], by_key, context) for label, group in labelled.items()
            ),
        )
        answers = self._draw(prompt, labelled)
        insights = answers[0] if len(answers) == 1 else self._consensus(answers, labelled)
        folded = self._fold(labelled, insights, context)
        folded.extend(group for group in groups if not size[group.keys])
        logger.info(
            "[Planner] %s: %d candidate groups -> %d components from %d draws",
            context.scope_id,
            len(groups),
            len(folded),
            len(answers),
        )
        return folded

    def _draw(self, prompt: str, labelled: dict[str, CandidateGroup]) -> list[TreePlanInsights]:
        """``draws`` answers to one prompt, drawn concurrently; a draw that fails is dropped."""
        # Not ``with``: its ``__exit__`` waits for a stalled draw; a copied context carries the
        # monitoring step into each worker.
        pool = ThreadPoolExecutor(max_workers=self.draws)
        answers: list[TreePlanInsights] = []
        failures: list[Exception] = []
        try:
            futures = [pool.submit(copy_context().run, self._ask, prompt, labelled) for _ in range(self.draws)]
            for future in futures:
                try:
                    answers.append(future.result(timeout=DRAW_TIMEOUT_SECONDS))
                except LLMAuthError:
                    raise
                except Exception as exc:
                    failures.append(exc)
                    logger.warning("[Planner] draw failed: %s", exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if not answers:
            raise RuntimeError("the planner got no answer from the model") from failures[-1]
        return answers

    def _ask(self, prompt: str, labelled: dict[str, CandidateGroup]) -> TreePlanInsights:
        def once() -> TreePlanInsights:
            message = self._model.invoke(
                [SystemMessage(content=self.system_message.content), HumanMessage(content=prompt)],
                config={"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]},
            )
            text = message.content if isinstance(message.content, str) else json.dumps(message.content)
            insights = self._read(text, labelled)
            return insights if insights is not None else self._parse_response(prompt, text, TreePlanInsights)

        def classify(exc: Exception, attempt: int) -> RetryDecision:
            _raise_if_auth_error(exc)
            return RetryDecision(
                action=RetryAction.RETRY, backoff_s=default_backoff(attempt, initial_s=10.0, multiplier=2.0, max_s=60.0)
            )

        return with_retries(once, max_attempts=3, classify=classify, log_prefix="Planner draw")

    @staticmethod
    def _read(text: str, labelled: dict[str, CandidateGroup]) -> TreePlanInsights | None:
        """The answer's JSON, members normalised to labels; None when it is not our shape.

        Why: a model that names a group instead of its label ("Ordering" for "G3") would
        otherwise leave that group out of every component.
        """
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        items = data.get("groups") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        by_name = {group.name.casefold(): label for label, group in labelled.items()}
        groups = []
        for item in items:
            if not isinstance(item, dict):
                return None
            members = [
                member if _LABEL.match(member) else by_name.get(member.casefold(), member)
                for member in map(str, item.get("members") or [])
            ]
            groups.append(
                PlannedGroup(
                    name=str(item.get("name") or ""),
                    members=members,
                    owns=[str(word) for word in item.get("owns") or []],
                )
            )
        return TreePlanInsights(groups=groups)

    @staticmethod
    def _describe(
        label: str, group: CandidateGroup, units: int, by_key: dict[str, Candidate], context: GroupingContext
    ) -> str:
        names = ", ".join(dict.fromkeys(by_key[key].label or by_key[key].kind for key in group.keys))
        sample = ", ".join(_sample(group, context))
        return f"{label}: {group.name} [{names}] ({units} files) e.g. {sample or '-'}"

    @staticmethod
    def _consensus(answers: Sequence[TreePlanInsights], labelled: dict[str, CandidateGroup]) -> TreePlanInsights:
        """The medoid: the draw whose grouping the other draws agree with most, by pair F1.

        Why a draw rather than a vote: a majority over pairs leaves every contested label on
        its own, and names and owned words drawn from several answers do not describe one box.
        """
        together = [_pairs(_placement(answer, labelled)) for answer in answers]

        def agreement(index: int) -> float:
            return sum(_pair_f1(together[index], other) for other in together)

        return answers[max(range(len(answers)), key=lambda index: (agreement(index), -index))]

    @staticmethod
    def _fold(
        labelled: dict[str, CandidateGroup], insights: TreePlanInsights, context: GroupingContext
    ) -> list[CandidateGroup]:
        """Every label lands in exactly one component: the first that names it, else its own.

        A component owns a word only when it is a lowercase stem found among its own members'
        identifiers and nobody else's. Why: replay votes on owned words at full weight, so a
        generic or package-wide word ("stream", the repository's name) drags every file into
        one box.
        """
        taken: set[str] = set()
        members_of: list[tuple[PlannedGroup, list[str]]] = []
        for planned in insights.groups:
            members = [label for label in planned.members if label in labelled and label not in taken]
            if members:
                taken.update(members)
                members_of.append((planned, members))
        stems_of = {label: _stems(labelled, [label], context) for label in labelled}
        folded: list[CandidateGroup] = []
        for planned, members in members_of:
            # Exclusive against every label, the forgotten ones included: a word shared with a
            # box the model left alone would still move that box's files.
            own = set().union(*(stems_of[label] for label in members))
            elsewhere = set().union(*(stems_of[label] for label in labelled if label not in members))
            owns = [
                word
                for word in dict.fromkeys(word.strip().casefold() for word in planned.owns)
                if tokenize(word) == (word,) and stem(word) == word and word in own - elsewhere
            ][:MAX_OWNS]
            folded.append(
                CandidateGroup(
                    name=planned.name.strip() or labelled[members[0]].name,
                    keys=tuple(key for label in members for key in labelled[label].keys),
                    terms=tuple(dict.fromkeys(term for label in members for term in labelled[label].terms))
                    + tuple(owns),
                )
            )
        folded.extend(group for label, group in labelled.items() if label not in taken)
        return folded


def _placement(answer: TreePlanInsights, labelled: dict[str, CandidateGroup]) -> dict[str, str]:
    """label -> the first component naming it; a forgotten label is a component of its own."""
    placement: dict[str, str] = {}
    for index, group in enumerate(answer.groups):
        for label in group.members:
            if label in labelled:
                placement.setdefault(label, str(index))
    for label in labelled:
        placement.setdefault(label, label)
    return placement


def _pairs(placement: dict[str, str]) -> set[frozenset[str]]:
    """Every two labels one component holds."""
    by_component: dict[str, list[str]] = {}
    for label, component in placement.items():
        by_component.setdefault(component, []).append(label)
    return {frozenset((a, b)) for members in by_component.values() for a in members for b in members if a < b}


def _pair_f1(left: set[frozenset[str]], right: set[frozenset[str]]) -> float:
    if not left or not right:
        return 1.0 if left == right else 0.0
    precision, recall = len(left & right) / len(left), len(left & right) / len(right)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _sample(group: CandidateGroup, context: GroupingContext) -> list[str]:
    """A few identifiers of the group's units, skipping dunders and names most groups share."""
    common = Counter(name for samples in context.samples.values() for name in set(samples))
    seen = dict.fromkeys(name for key in group.keys for name in context.samples.get(key, ()))
    return [name for name in seen if not name.startswith("_") and common[name] < 3][:SAMPLE_NAMES]


def _stems(labelled: dict[str, CandidateGroup], members: Sequence[str], context: GroupingContext) -> set[str]:
    return {
        stem(word)
        for label in members
        for key in labelled[label].keys
        for name in context.samples.get(key, ())
        for word in tokenize(name)
    }
