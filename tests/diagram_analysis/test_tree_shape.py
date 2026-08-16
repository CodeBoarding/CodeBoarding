"""Tests for single-child component absorption."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from agents.file_index_models import FileMethodGroup, MethodEntry
from diagram_analysis.analysis_json import build_unified_analysis_json, parse_unified_analysis
from diagram_analysis.diagram_generator import DiagramGenerator, _member_keys, _reconcile_child_scope
from diagram_analysis.exceptions import ScopeContainmentError
from static_analyzer.graph import CallGraph
from static_analyzer.clustering.method_cluster_paths import MethodClusterPaths
from diagram_analysis.tree_shape import absorb_single_child_components


def method(qname: str, start: int = 1) -> MethodEntry:
    return MethodEntry(qualified_name=qname, start_line=start, end_line=start + 5, node_type="FUNCTION")


def component(cid: str, name: str, files: dict[str, list[str]], clusters: list[str] | None = None) -> Component:
    return Component(
        name=name,
        description=f"{name} description",
        key_entities=[],
        component_id=cid,
        source_cluster_ids=clusters or [],
        file_methods=[
            FileMethodGroup(file_path=path, methods=[method(q, i * 10 + 1) for i, q in enumerate(qnames)])
            for path, qnames in files.items()
        ],
    )


def relation(src_id: str, dst_id: str) -> Relation:
    return Relation(
        relation="calls",
        src_name=f"c{src_id}",
        dst_name=f"c{dst_id}",
        key_edges=[],
        src_id=src_id,
        dst_id=dst_id,
    )


def analysis(*components: Component, relations: list[Relation] | None = None) -> AnalysisInsights:
    return AnalysisInsights(description="d", components=list(components), components_relations=relations or [])


def shape(root: AnalysisInsights, subs: dict[str, AnalysisInsights]) -> dict[str, list[str]]:
    """The tree as ``scope id -> child component ids``, root under ``''``."""
    return {"": [c.component_id for c in root.components]} | {
        scope_id: [c.component_id for c in scope.components] for scope_id, scope in subs.items()
    }


class TestChildlessOnlyChild(unittest.TestCase):
    def _tree(self):
        root = analysis(
            component("1", "Kept", {"a.py": ["a.one"]}),
            component("2", "Degenerate", {"b.py": ["b.one", "b.two"]}),
        )
        subs = {"2": analysis(component("2.1", "Restates Its Parent", {"b.py": ["b.one", "b.two"]}))}
        return root, subs

    def test_the_parent_becomes_a_leaf_without_losing_identity(self):
        root, subs = self._tree()

        absorbed = absorb_single_child_components(root, subs)

        self.assertEqual(shape(root, subs), {"": ["1", "2"]})
        parent = root.components[1]
        self.assertEqual(parent.name, "Degenerate")
        self.assertEqual(
            {(g.file_path, m.qualified_name) for g in parent.file_methods for m in g.methods},
            {("b.py", "b.one"), ("b.py", "b.two")},
        )
        self.assertEqual(absorbed, ["2.1"])


class TestGrandchildrenArePromoted(unittest.TestCase):
    def _tree(self):
        root = analysis(
            component("1", "Top", {"a.py": ["a.one", "a.two", "a.three"]}),
            component("9", "Sibling", {"z.py": ["z.one"]}),
        )
        subs = {
            "1": analysis(component("1.1", "Only Child", {"a.py": ["a.one", "a.two", "a.three"]})),
            "1.1": analysis(
                component("1.1.1", "Real One", {"a.py": ["a.one"]}, clusters=["1.1.3"]),
                component("1.1.2", "Real Two", {"a.py": ["a.two", "a.three"]}, clusters=["1.1.5"]),
                relations=[relation("1.1.1", "1.1.2")],
            ),
        }
        return root, subs

    def test_grandchildren_and_their_metadata_move_to_the_parent(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertEqual(shape(root, subs), {"": ["1", "9"], "1": ["1.1", "1.2"]})
        self.assertEqual([c.name for c in subs["1"].components], ["Real One", "Real Two"])
        self.assertEqual([c.source_cluster_ids for c in subs["1"].components], [["1.3"], ["1.5"]])
        self.assertEqual([(r.src_id, r.dst_id) for r in subs["1"].components_relations], [("1.1", "1.2")])
        self.assertEqual(set(subs), {"1"})


class TestDeepSubtreeIsRerooted(unittest.TestCase):
    def test_every_descendant_scope_and_id_moves_up_one_level(self):
        root = analysis(
            component("3", "Top", {"a.py": ["a.one", "a.two"]}),
            component("9", "Sibling", {"z.py": ["z.one"]}),
        )
        subs = {
            "3": analysis(component("3.2", "Only Child", {"a.py": ["a.one", "a.two"]})),
            "3.2": analysis(
                component("3.2.1", "Branch", {"a.py": ["a.one"]}),
                component("3.2.2", "Other", {"a.py": ["a.two"]}),
            ),
            "3.2.1": analysis(
                component("3.2.1.1", "Deep A", {"a.py": ["a.one"]}, clusters=["3.2.1.4"]),
                component("3.2.1.2", "Deep B", {"a.py": ["a.one"]}, clusters=["3.2.1.9"]),
            ),
        }

        absorb_single_child_components(root, subs)

        self.assertEqual(
            shape(root, subs),
            {"": ["3", "9"], "3": ["3.1", "3.2"], "3.1": ["3.1.1", "3.1.2"]},
        )
        self.assertEqual([c.source_cluster_ids for c in subs["3.1"].components], [["3.1.4"], ["3.1.9"]])


class TestRerootIsIndependentOfScopeOrder(unittest.TestCase):
    def _chain(self, keys_deepest_first: bool):
        root = analysis(component("1", "Top", {"a.py": ["a.one"]}), component("9", "Sibling", {"z.py": ["z.one"]}))
        levels = [
            ("1", [component("1.1", "Only child", {"a.py": ["a.one"]})]),
            ("1.1", [component("1.1.1", "G", {"a.py": ["a.one"]}), component("1.1.2", "G2", {"a.py": ["a.one"]})]),
            (
                "1.1.1",
                [component("1.1.1.1", "GG", {"a.py": ["a.one"]}), component("1.1.1.2", "GG2", {"a.py": ["a.one"]})],
            ),
            (
                "1.1.1.1",
                [
                    component("1.1.1.1.1", "GGG", {"a.py": ["a.one"]}),
                    component("1.1.1.1.2", "GGG2", {"a.py": ["a.one"]}),
                ],
            ),
        ]
        if keys_deepest_first:
            levels = list(reversed(levels))
        return root, {key: analysis(*comps) for key, comps in levels}

    def test_no_scope_is_lost_when_children_are_inserted_first(self):
        root, subs = self._chain(keys_deepest_first=True)

        absorb_single_child_components(root, subs)

        self.assertEqual(
            shape(root, subs),
            {
                "": ["1", "9"],
                "1": ["1.1", "1.2"],
                "1.1": ["1.1.1", "1.1.2"],
                "1.1.1": ["1.1.1.1", "1.1.1.2"],
            },
        )
        for scope_id, scope in subs.items():
            for owned in scope.components:
                self.assertTrue(
                    owned.component_id.startswith(f"{scope_id}."), f"{owned.component_id} not under {scope_id}"
                )


class TestRootScope(unittest.TestCase):
    def test_a_lone_top_level_component_hands_its_children_to_the_root(self):
        root = analysis(component("1", "Everything", {"a.py": ["a.one", "a.two"]}))
        subs = {
            "1": analysis(
                component("1.1", "First", {"a.py": ["a.one"]}),
                component("1.2", "Second", {"a.py": ["a.two"]}),
            )
        }

        absorb_single_child_components(root, subs)

        self.assertEqual(shape(root, subs), {"": ["1", "2"]})
        self.assertEqual([c.name for c in root.components], ["First", "Second"])

    def test_a_lone_childless_top_level_component_is_left_alone(self):
        root = analysis(component("1", "Everything", {"a.py": ["a.one"]}))

        absorbed = absorb_single_child_components(root, {})

        self.assertEqual(absorbed, [])
        self.assertEqual(shape(root, {}), {"": ["1"]})

    def test_the_root_keeps_its_global_relation_list(self):
        root = analysis(
            component("1", "Everything", {"a.py": ["a.one", "a.two"]}),
            relations=[relation("1.1.1", "1.2.1")],
        )
        subs = {
            "1": analysis(
                component("1.1", "First", {"a.py": ["a.one"]}),
                component("1.2", "Second", {"a.py": ["a.two"]}),
            ),
            "1.1": analysis(
                component("1.1.1", "Leaf", {"a.py": ["a.one"]}),
                component("1.1.2", "Leaf Sibling", {"a.py": ["a.one"]}),
            ),
            "1.2": analysis(
                component("1.2.1", "Leaf Too", {"a.py": ["a.two"]}),
                component("1.2.2", "Leaf Too Sibling", {"a.py": ["a.two"]}),
            ),
        }

        absorb_single_child_components(root, subs)

        self.assertEqual([(r.src_id, r.dst_id) for r in root.components_relations], [("1.1", "2.1")])


class TestDegenerateChain(unittest.TestCase):
    def test_a_chain_of_only_children_collapses_to_one_component(self):
        root = analysis(
            component("5", "Input & Stream Handling", {"uri.py": ["uri.parse", "uri.resolve"]}),
            component("9", "Sibling", {"z.py": ["z.one"]}),
        )
        subs = {
            "5": analysis(component("5.1", "URI Resolver", {"uri.py": ["uri.parse", "uri.resolve"]})),
            "5.1": analysis(component("5.1.1", "URI Scheme Handlers", {"uri.py": ["uri.parse", "uri.resolve"]})),
        }

        absorbed = absorb_single_child_components(root, subs)

        self.assertEqual(shape(root, subs), {"": ["5", "9"]})
        self.assertEqual(root.components[0].name, "Input & Stream Handling")
        self.assertEqual(absorbed, ["5.1", "5.1"])


class TestRelationsAcrossAnAbsorption(unittest.TestCase):
    def _tree(self):
        root = analysis(
            component("1", "Kept", {"a.py": ["a.one"]}),
            component("2", "Absorber", {"b.py": ["b.one"]}),
            relations=[relation("1", "2.1"), relation("2.1", "1"), relation("2", "2.1")],
        )
        subs = {"2": analysis(component("2.1", "Gone", {"b.py": ["b.one"]}))}
        return root, subs

    def test_edges_are_rerooted_and_self_loops_are_dropped(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertIn(("1", "2"), [(r.src_id, r.dst_id) for r in root.components_relations])
        self.assertIn(("2", "1"), [(r.src_id, r.dst_id) for r in root.components_relations])
        self.assertNotIn(("2", "2"), [(r.src_id, r.dst_id) for r in root.components_relations])


class TestAbsorbedTreeSurvivesTheStore(unittest.TestCase):
    def test_the_promoted_subtree_is_still_there_after_a_save_and_a_load(self):
        root = analysis(
            component("1", "Top", {"a.py": ["a.one", "a.two"]}),
            component("9", "Sibling", {"z.py": ["z.one"]}),
        )
        subs = {
            "1": analysis(component("1.1", "Only Child", {"a.py": ["a.one", "a.two"]})),
            "1.1": analysis(
                component("1.1.1", "A", {"a.py": ["a.one"]}),
                component("1.1.2", "B", {"a.py": ["a.two"]}),
            ),
        }

        absorb_single_child_components(root, subs)
        collapsed = shape(root, subs)
        expandable = [c for scope in [root, *subs.values()] for c in scope.components if c.component_id in subs]
        document = build_unified_analysis_json(
            analysis=root,
            repo_name="r",
            repo_dir=Path("."),
            sub_analyses={cid: (scope, []) for cid, scope in subs.items()},
            expandable_components=expandable,
            source_tree_hash="",
            depth_cap=3,
        )
        reloaded_root, reloaded_subs = parse_unified_analysis(json.loads(document))

        self.assertEqual(shape(reloaded_root, reloaded_subs), collapsed)


class TestFinalizeForSaveEnforcesTheInvariant(unittest.TestCase):
    def test_a_degenerate_level_does_not_survive_to_the_save(self):
        generator = DiagramGenerator.__new__(DiagramGenerator)
        generator.static_analysis = None
        generator.repo_location = Path(".")
        generator._baseline_global_relations = None
        root = analysis(
            component("1", "Kept", {"a.py": ["a.one"]}),
            component("2", "Degenerate", {"b.py": ["b.one"]}),
            relations=[relation("1", "2.1")],
        )
        subs = {"2": analysis(component("2.1", "Restates Its Parent", {"b.py": ["b.one"]}))}

        with patch.object(DiagramGenerator, "_strip_ignored"):
            generator.finalize_for_save(root, subs)

        self.assertEqual(shape(root, subs), {"": ["1", "2"]})
        self.assertEqual([(edge.src_id, edge.dst_id) for edge in root.components_relations], [("1", "2")])

    def test_relations_are_rebuilt_against_the_pre_absorption_ids(self):
        generator = DiagramGenerator.__new__(DiagramGenerator)
        generator.static_analysis = None
        generator.repo_location = Path(".")
        root = analysis(component("1", "Kept", {"a.py": ["a.one"]}), component("2", "Parent", {}))
        subs = {"2": analysis(component("2.1", "Child", {}))}

        def rebuild(current_root, current_subs):
            self.assertIn("2", current_subs)
            current_root.components_relations = [relation("1", "2.1")]
            return current_root.components_relations

        with (
            patch.object(DiagramGenerator, "_strip_ignored"),
            patch.object(generator, "rebuild_global_relations", side_effect=rebuild),
        ):
            generator.finalize_for_save(root, subs)

        self.assertEqual([(edge.src_id, edge.dst_id) for edge in root.components_relations], [("1", "2")])


class TestMembershipReconciliation(unittest.TestCase):
    def test_departed_methods_take_their_key_entities_and_cluster_lineage(self):
        parent = component("1", "Parent", {"a.py": ["a.kept"]})
        child = component("1.1", "Child", {"a.py": ["a.kept", "a.gone"]}, clusters=["1.7"])
        child.key_entities = [
            SourceCodeReference(qualified_name="a.kept", reference_file="a.py"),
            SourceCodeReference(qualified_name="a.gone", reference_file="a.py"),
        ]
        child_scope = analysis(child)

        _reconcile_child_scope(
            parent,
            child_scope,
            set(_member_keys(parent)),
            set(_member_keys(child)),
            Path("."),
        )

        self.assertEqual([entity.qualified_name for entity in child.key_entities], ["a.kept"])
        self.assertEqual(child.source_cluster_ids, ["1.7"])

    def test_an_emptied_child_loses_stale_metadata(self):
        parent = component("1", "Parent", {})
        child = component("1.1", "Child", {"a.py": ["a.gone"]}, clusters=["1.7"])
        child.key_entities = [SourceCodeReference(qualified_name="a.gone", reference_file="a.py")]

        _reconcile_child_scope(
            parent,
            analysis(child),
            set(),
            set(_member_keys(child)),
            Path("."),
        )

        self.assertEqual(child.key_entities, [])
        self.assertEqual(child.source_cluster_ids, [])


class TestClusterLineageMovesWithTheTree(unittest.TestCase):
    def test_the_absorbed_scopes_clusters_take_the_parents_path(self):
        paths = MethodClusterPaths({"pkg.one": {"1.2.3", "1.2.7"}, "pkg.two": {"4.1"}})

        paths.reroot_scope("1.2", "1")

        self.assertEqual(paths.snapshot_dict(), {"pkg.one": {"1.3", "1.7"}, "pkg.two": {"4.1"}})

    def test_the_parents_own_partition_is_dropped_rather_than_merged(self):
        paths = MethodClusterPaths({"a": {"1.0"}, "b": {"1.1.0", "1.1"}})

        paths.reroot_scope("1.1", "1")

        self.assertEqual(paths.snapshot_dict(), {"a": set(), "b": {"1.0"}})

    def test_a_descendant_scope_keeps_its_depth_below_the_new_path(self):
        paths = MethodClusterPaths({"deep": {"1.2.5.4"}})

        paths.reroot_scope("1.2", "1")

        self.assertEqual(paths.snapshot_dict(), {"deep": {"1.5.4"}})

    def test_root_absorption_leaves_bare_leaf_ids(self):
        paths = MethodClusterPaths({"pkg.one": {"1.3"}})

        paths.reroot_scope("1", "")

        self.assertEqual(paths.snapshot_dict(), {"pkg.one": {"3"}})

    def test_absorption_carries_the_lineage_with_it(self):
        root = analysis(component("1", "Top", {"a.py": ["a.one"]}), component("9", "Sib", {"z.py": ["z.one"]}))
        subs = {
            "1": analysis(component("1.1", "Only child", {"a.py": ["a.one"]})),
            "1.1": analysis(
                component("1.1.1", "G", {"a.py": ["a.one"]}), component("1.1.2", "G2", {"a.py": ["a.one"]})
            ),
        }
        cfg = CallGraph()
        cfg.method_cluster_paths = MethodClusterPaths({"a.one": {"1.0", "1.1.4"}})

        absorb_single_child_components(root, subs, [cfg])

        self.assertEqual(cfg.method_cluster_paths.snapshot_dict(), {"a.one": {"1.4"}})


class TestAbsorptionKeepsTheDocumentRenderable(unittest.TestCase):
    def test_a_relation_naming_the_absorbed_child_takes_the_parents_name(self):
        root = analysis(
            component("1", "Kept", {"a.py": ["a.one"]}),
            component("2", "Absorber", {"b.py": ["b.one"]}),
            relations=[relation("1", "2.1")],
        )
        root.components_relations[0].dst_name = "Gone"
        subs = {"2": analysis(component("2.1", "Gone", {"b.py": ["b.one"]}))}

        absorb_single_child_components(root, subs)

        edge = root.components_relations[0]
        self.assertEqual((edge.dst_id, edge.dst_name), ("2", "Absorber"))

    def test_a_promoted_components_relations_name_their_new_ids(self):
        root = analysis(component("1", "Top", {"a.py": ["a.one"]}), component("9", "Sib", {"z.py": ["z.one"]}))
        promoted = analysis(
            component("1.1.1", "First", {"a.py": ["a.one"]}),
            component("1.1.2", "Second", {"a.py": ["a.one"]}),
            relations=[relation("1.1.1", "1.1.2")],
        )
        promoted.components_relations[0].src_name = "First"
        promoted.components_relations[0].dst_name = "Second"
        subs = {"1": analysis(component("1.1", "Only child", {"a.py": ["a.one"]})), "1.1": promoted}

        absorb_single_child_components(root, subs)

        edge = subs["1"].components_relations[0]
        self.assertEqual((edge.src_id, edge.src_name), ("1.1", "First"))
        self.assertEqual((edge.dst_id, edge.dst_name), ("1.2", "Second"))


class TestAbsorptionNeverHidesAContainmentViolation(unittest.TestCase):
    def test_a_child_owning_what_its_parent_does_not_fails_before_the_collapse(self):
        generator = DiagramGenerator.__new__(DiagramGenerator)
        generator.static_analysis = None
        generator.repo_location = Path(".")
        generator._baseline_global_relations = None
        root = analysis(component("1", "Parent", {"a.py": ["a.one"]}))
        subs = {"1": analysis(component("1.1", "Child owning more", {"a.py": ["a.one", "a.stray"]}))}

        with patch.object(DiagramGenerator, "_strip_ignored"):
            with self.assertRaises(ScopeContainmentError):
                generator.finalize_for_save(root, subs)

        self.assertIn("1", subs, "the violating scope must still be there for a human to look at")


if __name__ == "__main__":
    unittest.main()
