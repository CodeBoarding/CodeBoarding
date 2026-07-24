"""Tests for the incremental copy-forward helpers in ``diagram_generator``.

These decide whether an untouched component keeps its methods, its metadata, its
sub-component structure and its relations across an incremental re-analysis. A
regression here produces a plausible-but-wrong ``analysis.json`` rather than a
crash, so each helper is driven directly on hand-built trees.
"""

import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, Relation
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.diagram_generator import (
    DiagramGenerator,
    _capture_baseline_member_keys,
    _capture_membership_baseline,
    _fully_unchanged_component_ids,
    _graft_entered_methods,
    _incremental_changed_component_ids,
    _member_keys,
    _preserve_unchanged_global_relations,
    _restore_unchanged_membership,
    _restore_unchanged_metadata,
    _restore_unchanged_subtrees,
)


def method(qname: str, start: int = 1) -> MethodEntry:
    return MethodEntry(qualified_name=qname, start_line=start, end_line=start + 5, node_type="FUNCTION")


def component(cid: str, name: str, files: dict[str, list[str]], **kwargs) -> Component:
    return Component(
        name=name,
        description=kwargs.pop("description", f"{name} description"),
        key_entities=[],
        component_id=cid,
        file_methods=[
            FileMethodGroup(file_path=path, methods=[method(q, i * 10 + 1) for i, q in enumerate(qnames)])
            for path, qnames in files.items()
        ],
        **kwargs,
    )


def analysis(*components: Component) -> AnalysisInsights:
    return AnalysisInsights(description="d", components=list(components), components_relations=[])


class TestMemberKeys(unittest.TestCase):
    def test_member_keys_pairs_every_method_with_its_file(self):
        comp = component("1", "A", {"a.py": ["a.one", "a.two"], "b.py": ["b.one"]})
        self.assertEqual(_member_keys(comp), {("a.py", "a.one"), ("a.py", "a.two"), ("b.py", "b.one")})

    def test_baseline_member_keys_span_root_and_sub_scopes(self):
        root = analysis(component("1", "A", {"a.py": ["a.one"]}))
        subs = {"1": analysis(component("1.1", "A1", {"a.py": ["a.one"]}))}

        keys = _capture_baseline_member_keys(root, subs)

        self.assertEqual(keys["1"], frozenset({("a.py", "a.one")}))
        self.assertEqual(keys["1.1"], frozenset({("a.py", "a.one")}))


class TestRestoreUnchangedMembership(unittest.TestCase):
    def _baseline_and_live(self):
        """Baseline owns a.one under component 1; the re-partition moved it to component 2."""
        baseline_root = analysis(
            component("1", "A", {"a.py": ["a.one"]}),
            component("2", "B", {"b.py": ["b.one"]}),
        )
        baseline = _capture_membership_baseline(baseline_root, {})
        live_root = analysis(
            component("1", "A", {}),
            component("2", "B", {"b.py": ["b.one"], "a.py": ["a.one"]}),
        )
        return baseline, live_root

    def _owner_of(self, root: AnalysisInsights, qname: str) -> str:
        return next(
            comp.component_id
            for comp in root.components
            for group in comp.file_methods
            for entry in group.methods
            if entry.qualified_name == qname
        )

    def test_unchanged_method_returns_to_its_baseline_owner(self):
        baseline, live_root = self._baseline_and_live()

        _restore_unchanged_membership(live_root, {}, baseline, changed_members=set(), protected_ids=set())

        self.assertEqual(self._owner_of(live_root, "a.one"), "1")

    def test_body_changed_method_keeps_the_new_placement(self):
        baseline, live_root = self._baseline_and_live()

        _restore_unchanged_membership(live_root, {}, baseline, changed_members={"a.one"}, protected_ids=set())

        self.assertEqual(self._owner_of(live_root, "a.one"), "2")

    def test_freshly_created_component_keeps_everything_it_was_given(self):
        baseline, live_root = self._baseline_and_live()

        _restore_unchanged_membership(live_root, {}, baseline, changed_members=set(), protected_ids={"2"})

        self.assertEqual(self._owner_of(live_root, "a.one"), "2")


class TestRestoreUnchangedMetadata(unittest.TestCase):
    def _baseline(self):
        root = analysis(component("1", "Original", {"a.py": ["a.one"]}, source_cluster_ids=["7"]))
        return _capture_membership_baseline(root, {})

    def test_reworded_but_identical_component_is_restored(self):
        baseline = self._baseline()
        live = analysis(component("1", "Renamed", {"a.py": ["a.one"]}, source_cluster_ids=["9"]))

        unchanged = _restore_unchanged_metadata(live, {}, baseline, changed_members=set(), changed_files=set())

        self.assertEqual(unchanged, {"1"})
        self.assertEqual(live.components[0].name, "Original")
        self.assertEqual(live.components[0].source_cluster_ids, ["7"])

    def test_component_owning_a_body_changed_member_is_left_alone(self):
        baseline = self._baseline()
        live = analysis(component("1", "Renamed", {"a.py": ["a.one"]}))

        unchanged = _restore_unchanged_metadata(live, {}, baseline, changed_members={"a.one"}, changed_files=set())

        self.assertEqual(unchanged, set())
        self.assertEqual(live.components[0].name, "Renamed")

    def test_component_owning_a_changed_file_is_left_alone(self):
        baseline = self._baseline()
        live = analysis(component("1", "Renamed", {"a.py": ["a.one"]}))

        unchanged = _restore_unchanged_metadata(live, {}, baseline, changed_members=set(), changed_files={"a.py"})

        self.assertEqual(unchanged, set())
        self.assertEqual(live.components[0].name, "Renamed")

    def test_component_that_gained_a_member_is_left_alone(self):
        baseline = self._baseline()
        live = analysis(component("1", "Renamed", {"a.py": ["a.one", "a.two"]}))

        unchanged = _restore_unchanged_metadata(live, {}, baseline, changed_members=set(), changed_files=set())

        self.assertEqual(unchanged, set())


class TestFullyUnchangedSubtrees(unittest.TestCase):
    def _tree(self):
        root = analysis(component("1", "A", {"a.py": ["a.one", "a.two"]}))
        subs = {
            "1": analysis(
                component("1.1", "A1", {"a.py": ["a.one"]}),
                component("1.2", "A2", {"a.py": ["a.two"]}),
            )
        }
        return root, subs

    def test_untouched_component_is_fully_unchanged(self):
        root, subs = self._tree()
        baseline = _capture_membership_baseline(root, subs)

        ids = _fully_unchanged_component_ids(root, subs, baseline, set(), set(), set())

        self.assertIn("1", ids)

    def test_a_changed_member_disqualifies_the_owner(self):
        root, subs = self._tree()
        baseline = _capture_membership_baseline(root, subs)

        ids = _fully_unchanged_component_ids(root, subs, baseline, {"a.two"}, set(), set())

        self.assertNotIn("1", ids)

    def test_a_freshly_created_descendant_disqualifies_the_ancestor(self):
        root, subs = self._tree()
        baseline = _capture_membership_baseline(root, subs)

        ids = _fully_unchanged_component_ids(root, subs, baseline, set(), set(), protected_ids={"1.2"})

        self.assertNotIn("1", ids)

    def test_restore_puts_a_reshuffled_child_scope_back(self):
        root, subs = self._tree()
        baseline = _capture_membership_baseline(root, subs)
        # The re-partition moved a.two from 1.1's sibling into 1.1 itself.
        subs["1"] = analysis(
            component("1.1", "A1", {"a.py": ["a.one", "a.two"]}),
            component("1.2", "A2", {}),
        )

        preserved = _restore_unchanged_subtrees(root, subs, baseline, set(), set(), set())

        self.assertIn("1", preserved)
        self.assertEqual(_member_keys(subs["1"].components[0]), frozenset({("a.py", "a.one")}))
        self.assertEqual(_member_keys(subs["1"].components[1]), frozenset({("a.py", "a.two")}))


class TestIncrementalChangedComponentIds(unittest.TestCase):
    def _live(self):
        return analysis(
            component("1", "A", {"a.py": ["a.one"]}),
            component("2", "B", {"b.py": ["b.one"]}),
        )

    def _baseline_keys(self):
        return {
            "1": frozenset({("a.py", "a.one")}),
            "2": frozenset({("b.py", "b.one")}),
        }

    def test_nothing_changed_means_no_changed_ids(self):
        changed = _incremental_changed_component_ids(self._live(), {}, {"1", "2"}, self._baseline_keys(), set(), set())
        self.assertEqual(changed, set())

    def test_body_change_marks_only_its_owner(self):
        changed = _incremental_changed_component_ids(
            self._live(), {}, {"1", "2"}, self._baseline_keys(), {"a.one"}, set()
        )
        self.assertEqual(changed, {"1"})

    def test_module_level_edit_marks_the_owning_component(self):
        changed = _incremental_changed_component_ids(
            self._live(), {}, {"1", "2"}, self._baseline_keys(), set(), {"b.py"}
        )
        self.assertEqual(changed, {"2"})

    def test_membership_churn_marks_the_component(self):
        keys = self._baseline_keys() | {"1": frozenset()}
        changed = _incremental_changed_component_ids(self._live(), {}, {"1", "2"}, keys, set(), set())
        self.assertEqual(changed, {"1"})

    def test_component_absent_from_the_baseline_is_changed(self):
        changed = _incremental_changed_component_ids(self._live(), {}, {"1"}, self._baseline_keys(), set(), set())
        self.assertEqual(changed, {"2"})


class TestPreserveUnchangedGlobalRelations(unittest.TestCase):
    @staticmethod
    def _relation(src: str, dst: str, label: str) -> Relation:
        return Relation(relation=label, src_name=src, dst_name=dst, src_id=src, dst_id=dst)

    def test_relabelled_edge_between_untouched_components_is_carried_over(self):
        rebuilt = [self._relation("1", "2", "rebuilt wording")]
        baseline = {("1", "2"): self._relation("1", "2", "baseline wording")}

        kept = _preserve_unchanged_global_relations(rebuilt, baseline, changed_component_ids=set(), live_ids={"1", "2"})

        self.assertEqual([rel.relation for rel in kept], ["baseline wording"])

    def test_edge_touching_a_changed_component_keeps_the_fresh_rebuild(self):
        rebuilt = [self._relation("1", "2", "rebuilt wording")]
        baseline = {("1", "2"): self._relation("1", "2", "baseline wording")}

        kept = _preserve_unchanged_global_relations(rebuilt, baseline, changed_component_ids={"2"}, live_ids={"1", "2"})

        self.assertEqual([rel.relation for rel in kept], ["rebuilt wording"])

    def test_baseline_edge_the_rebuild_dropped_is_restored(self):
        baseline = {("1", "2"): self._relation("1", "2", "baseline wording")}

        kept = _preserve_unchanged_global_relations([], baseline, changed_component_ids=set(), live_ids={"1", "2"})

        self.assertEqual([(rel.src_id, rel.dst_id) for rel in kept], [("1", "2")])

    def test_baseline_edge_to_a_component_that_no_longer_exists_is_dropped(self):
        baseline = {("1", "9"): self._relation("1", "9", "baseline wording")}

        kept = _preserve_unchanged_global_relations([], baseline, changed_component_ids=set(), live_ids={"1", "2"})

        self.assertEqual(kept, [])

    def test_spurious_rebuilt_edge_between_untouched_components_is_discarded(self):
        rebuilt = [self._relation("1", "2", "invented")]

        kept = _preserve_unchanged_global_relations(rebuilt, {}, changed_component_ids=set(), live_ids={"1", "2"})

        self.assertEqual(kept, [])


class TestGraftEnteredMethods(unittest.TestCase):
    def test_entered_method_lands_on_the_child_owning_most_of_its_file(self):
        child_scope = analysis(
            component("1.1", "A1", {"a.py": ["a.one", "a.two"]}),
            component("1.2", "A2", {"b.py": ["b.one"]}),
        )
        parent_methods = {("a.py", "a.three"): method("a.three", 99)}

        _graft_entered_methods(child_scope, {("a.py", "a.three")}, parent_methods)

        self.assertIn(("a.py", "a.three"), _member_keys(child_scope.components[0]))

    def test_method_from_an_unowned_file_falls_back_to_the_largest_child(self):
        child_scope = analysis(
            component("1.1", "A1", {"a.py": ["a.one", "a.two"]}),
            component("1.2", "A2", {"b.py": ["b.one"]}),
        )
        parent_methods = {("c.py", "c.one"): method("c.one", 99)}

        _graft_entered_methods(child_scope, {("c.py", "c.one")}, parent_methods)

        self.assertIn(("c.py", "c.one"), _member_keys(child_scope.components[0]))

    def test_grafting_is_idempotent(self):
        child_scope = analysis(component("1.1", "A1", {"a.py": ["a.one"]}))
        parent_methods = {("a.py", "a.two"): method("a.two", 99)}

        _graft_entered_methods(child_scope, {("a.py", "a.two")}, parent_methods)
        _graft_entered_methods(child_scope, {("a.py", "a.two")}, parent_methods)

        self.assertEqual(len(_member_keys(child_scope.components[0])), 2)


class TestProgressSaveNeverTruncates(unittest.TestCase):
    """A progress save replaces the whole sub-analysis set on disk.

    The incremental path only re-details newly created components, so it has to hand
    its live tree in or every intermediate save would publish an analysis.json with
    the untouched subtrees gone.
    """

    def test_existing_sub_analyses_seed_the_progress_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            generator = DiagramGenerator(
                repo_location=Path(tmp),
                temp_folder=Path(tmp),
                repo_name="test_repo",
                output_dir=Path(tmp),
                depth_level=2,
                run_id="test-run-id",
                log_path="test_repo/test-run-log",
            )
            existing = {"1": analysis(component("1.1", "A1", {"a.py": ["a.one"]}))}

            _expanded, produced = generator._generate_subcomponents(analysis(), [], existing)

            self.assertEqual(set(produced), {"1"})

    def test_omitting_them_starts_empty_for_a_full_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            generator = DiagramGenerator(
                repo_location=Path(tmp),
                temp_folder=Path(tmp),
                repo_name="test_repo",
                output_dir=Path(tmp),
                depth_level=2,
                run_id="test-run-id",
                log_path="test_repo/test-run-log",
            )

            _expanded, produced = generator._generate_subcomponents(analysis(), [])

            self.assertEqual(produced, {})


class TestScopeIdContract(unittest.TestCase):
    def test_root_scope_is_not_treated_as_a_sub_analysis(self):
        root = analysis(component("1", "A", {"a.py": ["a.one"]}))
        baseline = _capture_membership_baseline(root, {})

        self.assertIn(ROOT_SCOPE_ID, baseline.owner_by_scope)
        self.assertEqual(baseline.scope_by_id, {})


if __name__ == "__main__":
    unittest.main()
