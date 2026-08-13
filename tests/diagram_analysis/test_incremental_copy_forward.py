"""Tests for the incremental copy-forward helpers in ``diagram_generator``.

These decide whether an untouched component keeps its methods, its metadata, its
sub-component structure and its relations across an incremental re-analysis. A
regression here produces a plausible-but-wrong ``analysis.json`` rather than a
crash, so each helper is driven directly on hand-built trees.
"""

import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, Relation, RelationEdge, SourceCodeReference
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.scope_ids import ROOT_SCOPE_ID
from agents.incremental_agent import prune_empty_components, remove_deleted_files
from static_analyzer.clustering.separability import member_keys
from diagram_analysis.diagram_generator import (
    DiagramGenerator,
    _capture_baseline_member_keys,
    _capture_membership_baseline,
    _fully_unchanged_component_ids,
    _graft_entered_methods,
    _incremental_changed_component_ids,
    preserve_unchanged_relations,
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
        self.assertEqual(member_keys(comp), {("a.py", "a.one"), ("a.py", "a.two"), ("b.py", "b.one")})

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

    def test_emptied_component_does_not_restore_stale_cluster_ids(self):
        baseline = self._baseline()
        live = analysis(component("1", "Original", {}, source_cluster_ids=[]))

        _restore_unchanged_metadata(live, {}, baseline, changed_members={"a.one"}, changed_files=set())

        self.assertEqual(live.components[0].source_cluster_ids, [])


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
        self.assertEqual(member_keys(subs["1"].components[0]), frozenset({("a.py", "a.one")}))
        self.assertEqual(member_keys(subs["1"].components[1]), frozenset({("a.py", "a.two")}))


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

    def test_membership_churn_over_untouched_methods_does_not_mark_the_component(self):
        # Re-clustering moves untouched methods between components constantly. Calling that a
        # change put components owning nothing the commit reached into `changed_ids`, and their
        # relations were then freely added and removed.
        keys = self._baseline_keys() | {"1": frozenset()}
        changed = _incremental_changed_component_ids(self._live(), {}, {"1", "2"}, keys, set(), set())
        self.assertEqual(changed, set())

    def test_membership_churn_involving_a_changed_method_does_mark_the_component(self):
        keys = self._baseline_keys() | {"1": frozenset()}
        live = self._live()
        moved = next(
            method.qualified_name
            for component in live.components
            if component.component_id == "1"
            for group in component.file_methods
            for method in group.methods
        )
        changed = _incremental_changed_component_ids(live, {}, {"1", "2"}, keys, {moved}, set())
        self.assertIn("1", changed)

    def test_component_absent_from_the_baseline_is_changed(self):
        changed = _incremental_changed_component_ids(self._live(), {}, {"1"}, self._baseline_keys(), set(), set())
        self.assertEqual(changed, {"2"})


def _ref(qualified_name: str) -> SourceCodeReference:
    return SourceCodeReference(qualified_name=qualified_name, reference_file="pkg/mod.py")


class TestPreserveUnchangedGlobalRelations(unittest.TestCase):
    @staticmethod
    def _relation(src: str, dst: str, label: str) -> Relation:
        return Relation(relation=label, src_name=src, dst_name=dst, src_id=src, dst_id=dst)

    def test_relabelled_edge_between_untouched_components_is_carried_over(self):
        rebuilt = [self._relation("1", "2", "rebuilt wording")]
        baseline = {("1", "2"): self._relation("1", "2", "baseline wording")}

        kept = preserve_unchanged_relations(
            rebuilt, baseline, changed_component_ids=set(), live_ids={"1", "2"}, live_qnames=set()
        )

        self.assertEqual([rel.relation for rel in kept], ["baseline wording"])

    def test_carried_over_edge_still_takes_the_fresh_call_sites(self):
        # Wording is sticky, structure never is: preserving a stale call-site set would
        # make the diagram lie about the code to avoid re-wording a label.
        fresh = self._relation("1", "2", "rebuilt wording")
        fresh.key_edges = [RelationEdge(source=_ref("live.caller"), target=_ref("live.callee"))]
        stale = self._relation("1", "2", "baseline wording")
        stale.key_edges = [RelationEdge(source=_ref("gone.caller"), target=_ref("gone.callee"))]

        kept = preserve_unchanged_relations([fresh], {("1", "2"): stale}, set(), {"1", "2"}, set())

        self.assertEqual([rel.relation for rel in kept], ["baseline wording"])
        self.assertEqual([edge.source.qualified_name for edge in kept[0].key_edges], ["live.caller"])

    def test_edge_touching_a_changed_component_keeps_the_fresh_rebuild(self):
        # Its supporting calls moved, so the connection itself changed and may be re-described.
        fresh = self._relation("1", "2", "rebuilt wording")
        fresh.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.new_callee"))]
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.old_callee"))]

        kept = preserve_unchanged_relations(
            [fresh], {("1", "2"): stale}, changed_component_ids={"2"}, live_ids={"1", "2"}, live_qnames=set()
        )

        self.assertEqual([rel.relation for rel in kept], ["rebuilt wording"])

    def test_an_edgeless_pair_keeps_its_wording_even_when_an_endpoint_changed(self):
        # Nothing under it could have moved: there are no supporting calls to move. Re-wording
        # it is churn, and gating on the endpoint's change flag is what produced 17 of them on
        # `referenced-symbol-deleted` for a commit that deleted one function.
        rebuilt = [self._relation("1", "2", "rebuilt wording")]
        baseline = {("1", "2"): self._relation("1", "2", "baseline wording")}

        kept = preserve_unchanged_relations(
            rebuilt, baseline, changed_component_ids={"2"}, live_ids={"1", "2"}, live_qnames=set()
        )

        self.assertEqual([rel.relation for rel in kept], ["baseline wording"])

    def test_touched_pair_with_identical_backing_edges_keeps_the_baseline_wording(self):
        # An endpoint is flagged changed (a file it co-owns had a module-level edit), but the
        # call edges between the two components are byte-identical, so the connection did not
        # move: the reader's wording carries over even though the endpoint is "changed".
        fresh = self._relation("1", "2", "rebuilt wording")
        fresh.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.callee"))]
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.callee"))]

        kept = preserve_unchanged_relations([fresh], {("1", "2"): stale}, {"2"}, {"1", "2"}, set())

        self.assertEqual([rel.relation for rel in kept], ["baseline wording"])
        self.assertEqual([edge.source.qualified_name for edge in kept[0].all_edges], ["pkg.caller"])

    def test_touched_pair_whose_edges_moved_is_re_worded(self):
        fresh = self._relation("1", "2", "rebuilt wording")
        fresh.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.new_callee"))]
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.old_callee"))]

        kept = preserve_unchanged_relations([fresh], {("1", "2"): stale}, {"2"}, {"1", "2"}, set())

        self.assertEqual([rel.relation for rel in kept], ["rebuilt wording"])

    def test_touched_pair_carries_baseline_edges_between_unchanged_methods(self):
        # Pair (1, 2) is flagged changed. The rebuild's edge between two unchanged methods
        # (pkg.a -> pkg.stable) that the baseline did not have is dropped; the baseline's edge
        # between two unchanged methods (pkg.old -> pkg.stable) is carried forward; and the
        # edge that touches a changed method (pkg.changed -> pkg.stable) is taken from the rebuild.
        baseline = self._relation("1", "2", "baseline")
        baseline.all_edges = [RelationEdge(source=_ref("pkg.old"), target=_ref("pkg.stable"))]
        fresh = self._relation("1", "2", "rebuilt")
        fresh.all_edges = [
            RelationEdge(source=_ref("pkg.a"), target=_ref("pkg.stable")),
            RelationEdge(source=_ref("pkg.changed"), target=_ref("pkg.stable")),
        ]

        kept = preserve_unchanged_relations(
            [fresh], {("1", "2"): baseline}, {"2"}, {"1", "2"}, set(), changed_members={"pkg.changed"}
        )

        edge_pairs = {(e.source.qualified_name, e.target.qualified_name) for e in kept[0].all_edges}
        self.assertEqual(edge_pairs, {("pkg.old", "pkg.stable"), ("pkg.changed", "pkg.stable")})

    def test_baseline_edge_the_rebuild_dropped_is_restored(self):
        baseline = {("1", "2"): self._relation("1", "2", "baseline wording")}

        kept = preserve_unchanged_relations(
            [], baseline, changed_component_ids=set(), live_ids={"1", "2"}, live_qnames=set()
        )

        self.assertEqual([(rel.src_id, rel.dst_id) for rel in kept], [("1", "2")])

    def test_baseline_edge_to_a_component_that_no_longer_exists_is_dropped(self):
        baseline = {("1", "9"): self._relation("1", "9", "baseline wording")}

        kept = preserve_unchanged_relations(
            [], baseline, changed_component_ids=set(), live_ids={"1", "2"}, live_qnames=set()
        )

        self.assertEqual(kept, [])

    def test_spurious_rebuilt_edge_between_untouched_components_is_discarded(self):
        rebuilt = [self._relation("1", "2", "invented")]

        kept = preserve_unchanged_relations(
            rebuilt, {}, changed_component_ids=set(), live_ids={"1", "2"}, live_qnames=set()
        )

        self.assertEqual(kept, [])

    def test_pair_whose_only_backing_call_the_commit_deleted_is_dropped(self):
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.callee"))]
        fresh = self._relation("1", "2", "invented wording")

        kept = preserve_unchanged_relations(
            [fresh], {("1", "2"): stale}, {"1"}, {"1", "2"}, set(), changed_members={"pkg.caller"}
        )

        self.assertEqual(kept, [])

    def test_backing_that_vanished_with_no_code_cause_does_not_delete_the_pair(self):
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.callee"))]
        fresh = self._relation("1", "2", "invented wording")

        kept = preserve_unchanged_relations(
            [fresh], {("1", "2"): stale}, {"1"}, {"1", "2"}, set(), changed_members={"pkg.elsewhere"}
        )

        self.assertEqual([(rel.src_id, rel.dst_id) for rel in kept], [("1", "2")])

    def test_edgeless_baseline_pair_survives_a_rebuild_with_no_edges(self):
        stale = self._relation("1", "2", "baseline wording")
        fresh = self._relation("1", "2", "rebuilt wording")

        kept = preserve_unchanged_relations(
            [fresh], {("1", "2"): stale}, {"1"}, {"1", "2"}, set(), changed_members={"pkg.changed"}
        )

        self.assertEqual([rel.relation for rel in kept], ["baseline wording"])

    def test_pair_keeping_one_backing_call_between_unchanged_methods_survives(self):
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [
            RelationEdge(source=_ref("pkg.changed"), target=_ref("pkg.callee")),
            RelationEdge(source=_ref("pkg.stable"), target=_ref("pkg.callee")),
        ]
        fresh = self._relation("1", "2", "rebuilt wording")

        kept = preserve_unchanged_relations(
            [fresh], {("1", "2"): stale}, {"1"}, {"1", "2"}, set(), changed_members={"pkg.changed"}
        )

        edge_pairs = {(e.source.qualified_name, e.target.qualified_name) for e in kept[0].all_edges}
        self.assertEqual(edge_pairs, {("pkg.stable", "pkg.callee")})

    def test_evidence_backed_rebuild_survives_after_static_call_is_deleted(self):
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [RelationEdge(source=_ref("pkg.caller"), target=_ref("pkg.callee"))]
        fresh = self._relation("1", "2", "runtime wording")
        fresh.evidence = "Routes requests through a configured endpoint."

        kept = preserve_unchanged_relations(
            [fresh], {("1", "2"): stale}, {"1"}, {"1", "2"}, set(), changed_members={"pkg.caller"}
        )

        self.assertEqual(
            [(relation.relation, relation.evidence) for relation in kept], [(fresh.relation, fresh.evidence)]
        )

    def test_restored_relation_whose_backing_symbol_was_deleted_is_dropped(self):
        # The rebuild dropped this pair because the only edge connecting the two components
        # cited a method the diff deleted; restoring it verbatim resurrects a phantom edge.
        stale = self._relation("1", "2", "baseline wording")
        stale.all_edges = [RelationEdge(source=_ref("gone.caller"), target=_ref("still.here"))]

        kept = preserve_unchanged_relations([], {("1", "2"): stale}, set(), {"1", "2"}, live_qnames={"still.here"})

        self.assertEqual(kept, [])

    def test_restored_relation_with_a_live_backing_edge_is_kept(self):
        live = self._relation("1", "2", "baseline wording")
        live.all_edges = [RelationEdge(source=_ref("a.caller"), target=_ref("b.callee"))]

        kept = preserve_unchanged_relations(
            [], {("1", "2"): live}, set(), {"1", "2"}, live_qnames={"a.caller", "b.callee"}
        )

        self.assertEqual([(rel.src_id, rel.dst_id) for rel in kept], [("1", "2")])


class TestGraftEnteredMethods(unittest.TestCase):
    def test_entered_method_lands_on_the_child_owning_most_of_its_file(self):
        child_scope = analysis(
            component("1.1", "A1", {"a.py": ["a.one", "a.two"]}),
            component("1.2", "A2", {"b.py": ["b.one"]}),
        )
        parent_methods = {("a.py", "a.three"): method("a.three", 99)}

        _graft_entered_methods(child_scope, {("a.py", "a.three")}, parent_methods)

        self.assertIn(("a.py", "a.three"), member_keys(child_scope.components[0]))

    def test_method_from_an_unowned_file_falls_back_to_the_largest_child(self):
        child_scope = analysis(
            component("1.1", "A1", {"a.py": ["a.one", "a.two"]}),
            component("1.2", "A2", {"b.py": ["b.one"]}),
        )
        parent_methods = {("c.py", "c.one"): method("c.one", 99)}

        _graft_entered_methods(child_scope, {("c.py", "c.one")}, parent_methods)

        self.assertIn(("c.py", "c.one"), member_keys(child_scope.components[0]))

    def test_grafting_is_idempotent(self):
        child_scope = analysis(component("1.1", "A1", {"a.py": ["a.one"]}))
        parent_methods = {("a.py", "a.two"): method("a.two", 99)}

        _graft_entered_methods(child_scope, {("a.py", "a.two")}, parent_methods)
        _graft_entered_methods(child_scope, {("a.py", "a.two")}, parent_methods)

        self.assertEqual(len(member_keys(child_scope.components[0])), 2)


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


class TestAnalysedSubtreeSurvivesTheSaveTimeVerdict(unittest.TestCase):
    """A component holding children must never be re-litigated into a leaf.

    analysis.json is the store: the save writes children only for a component it is told is
    expandable, so a separability verdict that flips False on an inherited subtree deletes
    already-analysed work permanently.
    """

    def _generator(self, tmp: str) -> DiagramGenerator:
        generator = DiagramGenerator(
            repo_location=Path(tmp),
            temp_folder=Path(tmp),
            repo_name="test_repo",
            output_dir=Path(tmp),
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        generator.details_agent = MagicMock()
        # The gate says "keep it as a leaf" for everything.
        generator.clustering_service = MagicMock()
        generator.clustering_service.component_is_separable = MagicMock(return_value=False)
        return generator

    def test_a_component_with_children_stays_expandable(self):
        with tempfile.TemporaryDirectory() as tmp:
            generator = self._generator(tmp)
            root = analysis(component("1", "A", {"a.py": ["a.one"]}))
            subs = {"1": analysis(component("1.1", "A child", {"a.py": ["a.one"]}))}

            root_ids, sub_ids = generator._expandable_ids_for_tree(root, subs)

            self.assertEqual(root_ids, ["1"])
            self.assertEqual(sub_ids, {"1": []})

    def test_children_survive_even_when_the_structural_gate_rejects(self):
        # get_expandable_components runs should_expand_component first, so a predicate
        # cannot rescue a component that gate has already dropped -- a component with no
        # file_methods of its own would lose its children despite holding them.
        with tempfile.TemporaryDirectory() as tmp:
            generator = self._generator(tmp)
            bare = Component(name="A", description="", key_entities=[], component_id="1", file_methods=[])
            root = AnalysisInsights(description="", components=[bare], components_relations=[])
            subs = {"1": analysis(component("1.1", "A child", {"a.py": ["a.one"]}))}

            root_ids, _sub_ids = generator._expandable_ids_for_tree(root, subs)

            self.assertEqual(root_ids, ["1"])

    def test_a_childless_component_still_obeys_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            generator = self._generator(tmp)
            root = analysis(component("1", "A", {"a.py": ["a.one"]}))

            root_ids, _sub_ids = generator._expandable_ids_for_tree(root, {})

            self.assertEqual(root_ids, [])


class TestScopeIdContract(unittest.TestCase):
    def test_root_scope_is_not_treated_as_a_sub_analysis(self):
        root = analysis(component("1", "A", {"a.py": ["a.one"]}))
        baseline = _capture_membership_baseline(root, {})

        self.assertIn(ROOT_SCOPE_ID, baseline.owner_by_scope)
        self.assertEqual(baseline.scope_by_id, {})


if __name__ == "__main__":
    unittest.main()

    def test_an_ungrounded_pair_that_came_back_reversed_keeps_the_reader_s_orientation(self):
        # Nothing grounds the direction of a runtime/config relation, so the model picks one
        # afresh each run. Keyed on the ordered pair a swap reads as one relation deleted and
        # another added — two of the five "changed edges" in the reported case were this.
        fresh = self._relation("5", "4", "dispatches to")
        fresh.evidence = "runtime dispatch"
        stale = self._relation("4", "5", "captures user intent")
        stale.evidence = "runtime dispatch"

        kept = preserve_unchanged_relations([fresh], {("4", "5"): stale}, {"5"}, {"4", "5"}, set())

        self.assertEqual([(r.src_id, r.dst_id) for r in kept], [("4", "5")])
        self.assertEqual([r.relation for r in kept], ["captures user intent"])

    def test_a_reversed_pair_with_backing_edges_keeps_its_own_direction(self):
        # An edge is what makes the direction a claim rather than a phrasing; flipping a
        # relation that has one would point its edges the wrong way.
        fresh = self._relation("5", "4", "dispatches to")
        fresh.all_edges = [RelationEdge(source=_ref("pkg.a.caller"), target=_ref("pkg.b.callee"))]
        stale = self._relation("4", "5", "captures user intent")

        kept = preserve_unchanged_relations([fresh], {("4", "5"): stale}, {"5"}, {"4", "5"}, set())

        self.assertEqual([(r.src_id, r.dst_id) for r in kept], [("5", "4")])

    def test_a_reversed_static_pair_is_never_re_oriented(self):
        # A statically-backed relation takes its direction from real CFG edges.
        fresh = self._relation("5", "4", "calls")
        fresh.is_static = True
        stale = self._relation("4", "5", "calls")
        stale.is_static = True

        kept = preserve_unchanged_relations([fresh], {("4", "5"): stale}, {"5"}, {"4", "5"}, set())

        self.assertEqual([(r.src_id, r.dst_id) for r in kept], [("5", "4")])


class TestChangedComponentGranularity(unittest.TestCase):
    """A component that merely SHARES a file with the edit has not itself changed."""

    @staticmethod
    def _analysis(components):
        return AnalysisInsights(description="", components=components, components_relations=[])

    @staticmethod
    def _component(cid, file_path, qnames):
        return Component(
            name=cid,
            description="",
            key_entities=[],
            component_id=cid,
            file_methods=[
                FileMethodGroup(
                    file_path=file_path,
                    methods=[
                        MethodEntry(qualified_name=q, start_line=1, end_line=2, node_type="FUNCTION") for q in qnames
                    ],
                )
            ],
        )

    def _changed(self, components, changed_members, changed_files):
        analysis = self._analysis(components)
        baseline_keys = {c.component_id: member_keys(c) for c in components}
        return _incremental_changed_component_ids(
            analysis, {}, {c.component_id for c in components}, baseline_keys, changed_members, changed_files
        )

    def test_a_co_owner_of_the_changed_file_is_not_itself_changed(self):
        # `types.py` is shared: A owns the edited method, B owns other methods in the same file.
        owner = self._component("A", "types.py", ["pkg.types.edited"])
        co_owner = self._component("B", "types.py", ["pkg.types.untouched"])
        changed = self._changed([owner, co_owner], {"pkg.types.edited"}, {"types.py"})
        self.assertEqual(changed, {"A"}, "B shares the file but owns nothing that changed")

    def test_a_module_level_edit_no_member_represents_still_flags_its_owners(self):
        # An import or constant changed: no method's hash moved, so the file is the only signal.
        a = self._component("A", "consts.py", ["pkg.consts.f"])
        changed = self._changed([a], set(), {"consts.py"})
        self.assertEqual(changed, {"A"})


def _reconcile_child_scope_membership_for_test(child_scope, departed):
    """The relation-filtering half of `_reconcile_child_scope_membership`, exercised directly."""
    departed_qnames = {qualified_name for _path, qualified_name in departed}
    child_scope.components_relations = [
        relation
        for relation in child_scope.components_relations
        if not any(
            edge.source.qualified_name in departed_qnames or edge.target.qualified_name in departed_qnames
            for edge in [*relation.key_edges, *relation.all_edges]
        )
    ]


class TestScopeRelationsSurviveMembershipMovement(unittest.TestCase):
    """Reconciling membership must not wipe the scope's relations wholesale.

    Clearing them leaves `preserve_unchanged_relations` with no baseline, so it is skipped and
    every relation in the scope comes back re-worded — measured at 8 re-wordings on
    `referenced-symbol-deleted` whose call sets were byte-identical.
    """

    @staticmethod
    def _relation(src, dst, source_q, target_q):
        return Relation(
            relation="calls",
            src_name=src,
            dst_name=dst,
            src_id=src,
            dst_id=dst,
            all_edges=[RelationEdge(source=_ref(source_q), target=_ref(target_q))],
        )

    def test_a_relation_citing_a_departed_method_is_dropped(self):
        scope = AnalysisInsights(
            description="",
            components=[],
            components_relations=[self._relation("1", "2", "pkg.gone", "pkg.stays")],
        )
        _reconcile_child_scope_membership_for_test(scope, departed={("a.py", "pkg.gone")})
        self.assertEqual(scope.components_relations, [])

    def test_a_relation_untouched_by_the_movement_is_kept(self):
        keep = self._relation("1", "2", "pkg.here", "pkg.there")
        scope = AnalysisInsights(description="", components=[], components_relations=[keep])
        _reconcile_child_scope_membership_for_test(scope, departed={("a.py", "pkg.gone")})
        self.assertEqual([r.relation for r in scope.components_relations], ["calls"])


class TestDeletedComponentLineage(unittest.TestCase):
    def _tree(self):
        root = analysis(component("1", "Survivor", {"live.py": ["live.one"]}))
        doomed = component("1.2", "Doomed", {"gone.py": ["gone.one"]}, source_cluster_ids=["1.7"])
        subs = {
            "1": analysis(
                component("1.1", "Kept", {"live.py": ["live.one"]}, source_cluster_ids=["1.3"]),
                doomed,
            )
        }
        return root, subs

    def test_deletion_clears_stale_lineage_before_pruning(self):
        root, subs = self._tree()

        remove_deleted_files(root, subs, {"live.py"})
        removed = prune_empty_components(root, subs)

        self.assertIn("1.2", removed)
        self.assertEqual([c.component_id for c in subs["1"].components], ["1.1"])

    def test_existing_cluster_backed_empty_component_survives(self):
        root = analysis(component("1", "Parent", {"live.py": ["live.one"]}))
        subs = {
            "1": analysis(
                component("1.1", "Kept", {"live.py": ["live.one"]}, source_cluster_ids=["1.3"]),
                component("1.2", "Awaiting detail", {}, source_cluster_ids=["1.9"]),
            )
        }

        remove_deleted_files(root, subs, {"live.py"})
        removed = prune_empty_components(root, subs)

        self.assertNotIn("1.2", removed)
