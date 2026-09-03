import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import PlannedGroup, TreePlanInsights
from agents.tree_planner_agent import BUDGET, TreePlannerAgent
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.names import ROLE_WORDS, CandidateGroup, GroupingContext
from static_analyzer.clustering.names.frontier import BOX, Candidate


def candidates(count: int) -> list[Candidate]:
    return [Candidate(f"box:Feature{i}", BOX, f"Feature{i}", prefixes=((f"Feature{i}",),)) for i in range(count)]


WORDS = (
    "apple",
    "banana",
    "cherry",
    "date",
    "elder",
    "fig",
    "grape",
    "honey",
    "iris",
    "jade",
    "kiwi",
    "lemon",
    "mango",
)


def context(items: list[Candidate], sizes: dict[str, int] | None = None) -> GroupingContext:
    """Sizes fall with the index, so G1 is Feature0; each candidate has one word of its own."""
    sizes = sizes or {c.key: 30 - i for i, c in enumerate(items)}
    return GroupingContext(
        "root",
        ROLE_WORDS,
        sum(sizes.values()),
        "frontier",
        sizes=sizes,
        samples={
            c.key: (f"{c.label}Service", f"{WORDS[i].title()}Client", "__init__", "accepts")
            for i, c in enumerate(items)
        },
    )


def answer(*groups: tuple[str, list[str]]) -> TreePlanInsights:
    return TreePlanInsights(groups=[PlannedGroup(name=name, members=members) for name, members in groups])


class TestTreePlannerAgent(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.repo_dir, ignore_errors=True)

    def _agent(self, draws: int = 1) -> TreePlannerAgent:
        llm = MagicMock()
        llm.model_name = "test-model"
        return TreePlannerAgent(self.repo_dir, StaticAnalysisResults(), llm, llm, draws=draws)

    def test_a_scope_within_the_budget_never_reaches_the_model(self):
        items = candidates(BUDGET)
        with patch.object(TreePlannerAgent, "_ask") as ask:
            groups = self._agent().group(items, context(items))
        self.assertEqual(len(groups), BUDGET)
        ask.assert_not_called()

    def test_the_model_folds_kinship_groups_into_themes(self):
        items = candidates(BUDGET + 3)
        planned = TreePlanInsights(
            groups=[
                PlannedGroup(name="Customer experiences", members=["G1", "G2", "G3"], owns=["apple"]),
                PlannedGroup(name="Everything else", members=[f"G{i}" for i in range(4, BUDGET + 4)]),
            ]
        )
        with patch.object(TreePlannerAgent, "_ask", return_value=planned) as ask:
            groups = self._agent().group(items, context(items))
        self.assertEqual([group.name for group in groups], ["Customer experiences", "Everything else"])
        self.assertEqual(groups[0].keys, ("box:Feature0", "box:Feature1", "box:Feature2"))
        self.assertIn("apple", groups[0].terms)
        prompt = ask.call_args.args[0]
        self.assertIn("G1: Feature0 [Feature0] (30 files) e.g. Feature0Service, AppleClient", prompt)
        self.assertNotIn("__init__", prompt)
        self.assertNotIn("accepts", prompt)

    def test_labels_run_largest_first_and_an_empty_group_is_kept_out_of_the_prompt(self):
        items = candidates(BUDGET + 2)
        sizes = {c.key: 1 for c in items}
        sizes["box:Feature5"] = 9
        sizes["box:Feature7"] = 0
        with patch.object(TreePlannerAgent, "_ask", return_value=answer(("Big", ["G1"]))) as ask:
            groups = self._agent().group(items, context(items, sizes))
        prompt = ask.call_args.args[0]
        self.assertIn("G1: Feature5 [Feature5] (9 files)", prompt)
        self.assertNotIn("Feature7", prompt)
        self.assertEqual(groups[0].keys, ("box:Feature5",))
        self.assertIn(("box:Feature7",), [group.keys for group in groups])
        self.assertEqual(sum(len(group.keys) for group in groups), BUDGET + 2)

    def test_the_floor_mirrors_the_partition_guard_none_at_the_root_five_percent_below(self):
        items = candidates(BUDGET + 1)
        sizes = {c.key: 100 for c in items}
        planned = answer(("All", [f"G{i}" for i in range(1, BUDGET + 2)]))
        with patch.object(TreePlannerAgent, "_ask", return_value=planned) as ask:
            self._agent().group(items, context(items, sizes))
            self.assertIn("at least 2 files", ask.call_args.args[0])
            below = GroupingContext("1", ROLE_WORDS, 1000, "segment", sizes=sizes, samples={})
            self._agent().group(items, below)
            self.assertIn("at least 50 files", ask.call_args.args[0])

    def test_a_label_the_model_forgot_keeps_its_own_group_and_a_repeat_goes_to_the_first(self):
        items = candidates(BUDGET + 1)
        planned = answer(("A", ["G1", "G2"]), ("B", ["G2", "G3", "G99"]))
        with patch.object(TreePlannerAgent, "_ask", return_value=planned):
            groups = self._agent().group(items, context(items))
        self.assertEqual([group.name for group in groups][:2], ["A", "B"])
        self.assertEqual(groups[0].keys, ("box:Feature0", "box:Feature1"))
        self.assertEqual(groups[1].keys, ("box:Feature2",))
        self.assertEqual(len(groups), 2 + (BUDGET + 1 - 3))

    def test_a_component_with_no_known_member_is_dropped(self):
        items = candidates(BUDGET + 1)
        with patch.object(TreePlannerAgent, "_ask", return_value=answer(("Ghost", ["G77"]))):
            groups = self._agent().group(items, context(items))
        self.assertNotIn("Ghost", [group.name for group in groups])
        self.assertEqual(len(groups), BUDGET + 1)

    def test_owned_words_must_be_stems_of_the_members_own_identifiers_and_nobody_elses(self):
        items = candidates(BUDGET + 1)
        planned = TreePlanInsights(
            groups=[
                PlannedGroup(name="A", members=["G1"], owns=["apple", "Apple", "service", "feature", "stream", "a b"]),
                PlannedGroup(name="B", members=["G2"], owns=["banana", "apple", "client"]),
            ]
        )
        with patch.object(TreePlannerAgent, "_ask", return_value=planned):
            groups = self._agent().group(items, context(items))
        self.assertEqual(groups[0].terms[-1:], ("apple",))
        self.assertEqual(groups[1].terms[-1:], ("banana",))
        for word in ("Apple", "service", "feature", "stream", "a b", "client"):
            self.assertNotIn(word, groups[0].terms + groups[1].terms)

    def test_a_word_shared_with_a_forgotten_label_is_not_owned(self):
        items = candidates(BUDGET + 2)
        ctx = context(items)
        shared = {key: samples + ("SharedThing",) for key, samples in ctx.samples.items()}
        ctx = GroupingContext(ctx.scope_id, ctx.role_words, ctx.unit_count, ctx.rung, ctx.sizes, shared)
        planned = TreePlanInsights(
            groups=[
                PlannedGroup(name="Most", members=[f"G{i}" for i in range(1, BUDGET + 2)], owns=["shared", "apple"])
            ]
        )
        with patch.object(TreePlannerAgent, "_ask", return_value=planned):
            groups = self._agent().group(items, ctx)
        most = next(group for group in groups if group.name == "Most")
        self.assertNotIn("shared", most.terms, "the forgotten last label carries the word too")
        self.assertIn("apple", most.terms)

    def test_the_answer_is_read_from_json_with_names_as_labels(self):
        items = candidates(BUDGET + 1)
        labelled = {f"G{i + 1}": CandidateGroup(c.label, (c.key,)) for i, c in enumerate(items)}
        text = 'Sure:\n{"groups": [{"name": "A", "members": ["G1", "feature1", "G3"], "owns": ["x"]}], "notes": ""}'
        insights = TreePlannerAgent._read(text, labelled)
        assert insights is not None
        self.assertEqual(insights.groups[0].members, ["G1", "G2", "G3"])
        self.assertIsNone(TreePlannerAgent._read("no json here", labelled))
        self.assertIsNone(TreePlannerAgent._read('{"components": []}', labelled))

    def test_three_draws_fold_to_the_one_the_others_agree_with(self):
        items = candidates(BUDGET + 3)
        draws = iter(
            [
                answer(("Pair", ["G1", "G2"]), ("Trio", ["G3", "G4", "G5"]), ("Rest", [f"G{i}" for i in range(6, 13)])),
                answer(("Pair", ["G1", "G2"]), ("Trio", ["G3", "G4", "G5"]), ("Rest", [f"G{i}" for i in range(6, 13)])),
                answer(("Odd", ["G1", "G3"]), ("Two", ["G2", "G4", "G5"]), ("Rest", [f"G{i}" for i in range(6, 13)])),
            ]
        )
        with patch.object(TreePlannerAgent, "_ask", side_effect=lambda *_: next(draws)):
            groups = self._agent(draws=3).group(items, context(items))
        self.assertEqual([group.name for group in groups], ["Pair", "Trio", "Rest"])
        self.assertEqual(groups[0].keys, ("box:Feature0", "box:Feature1"))
        self.assertEqual(groups[1].keys, ("box:Feature2", "box:Feature3", "box:Feature4"))

    def test_a_failed_draw_is_dropped_and_no_answer_at_all_raises(self):
        items = candidates(BUDGET + 1)
        draws = iter([RuntimeError("boom"), answer(("All", [f"G{i}" for i in range(1, BUDGET + 2)]))])

        def ask(*_):
            item = next(draws)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(TreePlannerAgent, "_ask", side_effect=ask):
            groups = self._agent(draws=2).group(items, context(items))
        self.assertEqual([group.name for group in groups], ["All"])
        with patch.object(TreePlannerAgent, "_ask", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self._agent(draws=2).group(items, context(items))
