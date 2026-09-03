import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import PlannedGroup, TreePlanInsights
from agents.tree_planner_agent import BUDGET, TreePlannerAgent
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.names import ROLE_WORDS, GroupingContext
from static_analyzer.clustering.names.frontier import BOX, Candidate


def candidates(count: int) -> list[Candidate]:
    return [Candidate(f"box:Feature{i}", BOX, f"Feature{i}", prefixes=((f"Feature{i}",),)) for i in range(count)]


def context(items: list[Candidate]) -> GroupingContext:
    return GroupingContext(
        "root",
        ROLE_WORDS,
        len(items) * 3,
        "frontier",
        sizes={c.key: 3 for c in items},
        samples={c.key: (f"{c.label}Service", f"{c.label}Repository") for c in items},
    )


class TestTreePlannerAgent(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.repo_dir, ignore_errors=True)

    def _agent(self) -> TreePlannerAgent:
        llm = MagicMock()
        llm.model_name = "test-model"
        return TreePlannerAgent(self.repo_dir, StaticAnalysisResults(), llm, llm)

    def test_a_scope_within_the_budget_never_reaches_the_model(self):
        items = candidates(BUDGET)
        with patch.object(TreePlannerAgent, "_parse_invoke") as parse:
            groups = self._agent().group(items, context(items))
        self.assertEqual(len(groups), BUDGET)
        parse.assert_not_called()

    def test_the_model_folds_kinship_groups_into_themes(self):
        items = candidates(BUDGET + 3)
        answer = TreePlanInsights(
            groups=[
                PlannedGroup(name="Customer experiences", members=["G1", "G2", "G3"], owns=["Customers", "Loyalty"]),
                PlannedGroup(name="Everything else", members=[f"G{i}" for i in range(4, BUDGET + 4)]),
            ]
        )
        with patch.object(TreePlannerAgent, "_parse_invoke", return_value=answer) as parse:
            groups = self._agent().group(items, context(items))
        self.assertEqual([group.name for group in groups], ["Customer experiences", "Everything else"])
        self.assertEqual(groups[0].keys, ("box:Feature0", "box:Feature1", "box:Feature2"))
        self.assertEqual(groups[0].terms[-2:], ("customer", "loyalty"), "owned words vote as the stems replay looks up")
        prompt = parse.call_args.args[0]
        self.assertIn("G1: Feature0 [Feature0] (3 files) e.g. Feature0Service, Feature0Repository", prompt)

    def test_a_label_the_model_forgot_keeps_its_own_group_and_a_repeat_goes_to_the_first(self):
        items = candidates(BUDGET + 1)
        answer = TreePlanInsights(
            groups=[
                PlannedGroup(name="A", members=["G1", "G2"]),
                PlannedGroup(name="B", members=["G2", "G3", "G99"]),
            ]
        )
        with patch.object(TreePlannerAgent, "_parse_invoke", return_value=answer):
            groups = self._agent().group(items, context(items))
        self.assertEqual([group.name for group in groups][:2], ["A", "B"])
        self.assertEqual(groups[0].keys, ("box:Feature0", "box:Feature1"))
        self.assertEqual(groups[1].keys, ("box:Feature2",))
        self.assertEqual(len(groups), 2 + (BUDGET + 1 - 3))

    def test_a_component_with_no_known_member_is_dropped(self):
        items = candidates(BUDGET + 1)
        answer = TreePlanInsights(groups=[PlannedGroup(name="Ghost", members=["G77"])])
        with patch.object(TreePlannerAgent, "_parse_invoke", return_value=answer):
            groups = self._agent().group(items, context(items))
        self.assertNotIn("Ghost", [group.name for group in groups])
        self.assertEqual(len(groups), BUDGET + 1)
