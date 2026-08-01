"""Tests for the no-single-child invariant in ``diagram_analysis.tree_shape``.

A level holding one component is the shape measured on ~10% of stored analyses
(120 occurrences over 574 documents), in two flavours: a component whose only
child is a leaf, and a chain where each level restates the one below. Absorption
has to fix both without breaking the dotted-path identity everything else keys on
— ``sub_analyses`` keys, scoped cluster ids and relation endpoints.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from agents.incremental_agent import prune_empty_components
from agents.file_index_models import FileMethodGroup, MethodEntry
from diagram_analysis.analysis_json import build_unified_analysis_json, parse_unified_analysis
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.tree_shape import (
    absorb_single_child_components,
    drop_dangling_key_entities,
    single_child_scopes,
)


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
    """The 114-of-120 case: a component whose single child has nothing under it."""

    def _tree(self):
        root = analysis(
            component("1", "Kept", {"a.py": ["a.one"]}),
            component("2", "Degenerate", {"b.py": ["b.one", "b.two"]}),
        )
        subs = {"2": analysis(component("2.1", "Restates Its Parent", {"b.py": ["b.one", "b.two"]}))}
        return root, subs

    def test_the_parent_becomes_a_leaf(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertEqual(shape(root, subs), {"": ["1", "2"]})

    def test_the_parent_keeps_its_own_identity_and_membership(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        parent = root.components[1]
        self.assertEqual(parent.name, "Degenerate")
        self.assertEqual(
            {(g.file_path, m.qualified_name) for g in parent.file_methods for m in g.methods},
            {("b.py", "b.one"), ("b.py", "b.two")},
        )

    def test_the_absorption_names_the_child_that_disappeared(self):
        root, subs = self._tree()

        absorbed = absorb_single_child_components(root, subs)

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

    def test_grandchildren_become_the_parents_children(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertEqual(shape(root, subs), {"": ["1", "9"], "1": ["1.1", "1.2"]})
        self.assertEqual([c.name for c in subs["1"].components], ["Real One", "Real Two"])

    def test_scoped_cluster_ids_follow_the_components(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertEqual([c.source_cluster_ids for c in subs["1"].components], [["1.3"], ["1.5"]])

    def test_the_promoted_siblings_relations_come_with_them(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertEqual([(r.src_id, r.dst_id) for r in subs["1"].components_relations], [("1.1", "1.2")])

    def test_the_absorbed_scope_key_is_gone(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertNotIn("1.1.1", subs)
        self.assertEqual(set(subs), {"1"})


class TestDeepSubtreeIsRerooted(unittest.TestCase):
    """A promoted grandchild that is itself expanded keeps its whole subtree."""

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
        """Absorbing it would leave an analysis with no components at all."""
        root = analysis(component("1", "Everything", {"a.py": ["a.one"]}))

        absorbed = absorb_single_child_components(root, {})

        self.assertEqual(absorbed, [])
        self.assertEqual(shape(root, {}), {"": ["1"]})

    def test_the_root_keeps_its_global_relation_list(self):
        """The root's relations are the global cross-boundary set, not a sibling set."""
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
    """The markitdown shape: three nested boxes describing the same two methods."""

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

    def test_edges_into_the_absorbed_child_land_on_the_absorber(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertIn(("1", "2"), [(r.src_id, r.dst_id) for r in root.components_relations])
        self.assertIn(("2", "1"), [(r.src_id, r.dst_id) for r in root.components_relations])

    def test_an_edge_that_collapses_into_a_self_loop_is_dropped(self):
        root, subs = self._tree()

        absorb_single_child_components(root, subs)

        self.assertNotIn(("2", "2"), [(r.src_id, r.dst_id) for r in root.components_relations])


class TestInvariantReporting(unittest.TestCase):
    def test_single_child_scopes_names_every_violation(self):
        root = analysis(component("1", "A", {"a.py": ["a.one"]}), component("2", "B", {"b.py": ["b.one"]}))
        subs = {
            "1": analysis(component("1.1", "Only", {"a.py": ["a.one"]})),
            "2": analysis(
                component("2.1", "One", {"b.py": ["b.one"]}),
                component("2.2", "Two", {"b.py": ["b.one"]}),
            ),
        }

        self.assertEqual(single_child_scopes(root, subs), ["1"])

    def test_a_healthy_tree_is_untouched_and_reports_nothing(self):
        root = analysis(component("1", "A", {"a.py": ["a.one"]}), component("2", "B", {"b.py": ["b.one"]}))
        subs = {
            "1": analysis(
                component("1.1", "One", {"a.py": ["a.one"]}),
                component("1.2", "Two", {"a.py": ["a.one"]}),
            )
        }
        before = shape(root, subs)

        absorbed = absorb_single_child_components(root, subs)

        self.assertEqual(absorbed, [])
        self.assertEqual(shape(root, subs), before)
        self.assertEqual(single_child_scopes(root, subs), [])

    def test_absorption_is_idempotent(self):
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
        after_first = shape(root, subs)
        second = absorb_single_child_components(root, subs)

        self.assertEqual(second, [])
        self.assertEqual(shape(root, subs), after_first)


class TestAbsorbedTreeSurvivesTheStore(unittest.TestCase):
    """analysis.json IS the store, and it only writes children for an expandable component.

    Why this is worth a test of its own: a component holding a sub-analysis it was not
    declared expandable for loses that whole subtree at save, permanently. Absorption
    rewrites both the ids and the ``sub_analyses`` keys, so the two must still agree
    afterwards or the collapse silently takes a level more than it meant to.
    """

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
    """The shared pre-save chokepoint is where every flow — full, incremental, partial — meets it."""

    def test_a_degenerate_level_does_not_survive_to_the_save(self):
        generator = DiagramGenerator.__new__(DiagramGenerator)
        generator.static_analysis = None
        generator._baseline_global_relations = None
        root = analysis(
            component("1", "Kept", {"a.py": ["a.one"]}),
            component("2", "Degenerate", {"b.py": ["b.one"]}),
        )
        subs = {"2": analysis(component("2.1", "Restates Its Parent", {"b.py": ["b.one"]}))}

        with patch.object(DiagramGenerator, "_strip_ignored"):
            generator.finalize_for_save(root, subs)

        self.assertEqual(shape(root, subs), {"": ["1", "2"]})


if __name__ == "__main__":
    unittest.main()


class TestDanglingKeyEntities(unittest.TestCase):
    """A deleted symbol must take its pointers with it.

    Measured over the eval baselines and corpus: 557 of 557 key entities in healthy
    documents name a method some component owns, so dropping the ones that do not cannot
    remove a legitimate reference. The failure this prevents is indirect and was found by
    an e2e test: `prune_empty_components` reads a non-empty `key_entities` as a component
    still having something to describe, so a component emptied of every method survived as
    a box held up by pointers into code the commit had deleted.
    """

    def _entity(self, qname: str) -> SourceCodeReference:
        return SourceCodeReference(qualified_name=qname, reference_file="a.py")

    def test_an_entity_naming_a_deleted_symbol_is_dropped(self):
        emptied = component("1.1", "Emptied", {})
        emptied.key_entities = [self._entity("a.gone"), self._entity("a.also_gone")]
        root = analysis(component("1", "Parent", {"a.py": ["a.kept"]}))
        subs = {"1": analysis(component("1.2", "Kept", {"a.py": ["a.kept"]}), emptied)}

        dropped = drop_dangling_key_entities(root, subs)

        self.assertEqual(sorted(dropped), ["a.also_gone", "a.gone"])
        self.assertEqual(emptied.key_entities, [])

    def test_an_entity_naming_a_live_symbol_is_kept(self):
        kept = component("1.1", "Kept", {"a.py": ["a.one"]})
        kept.key_entities = [self._entity("a.one")]
        root = analysis(component("1", "Parent", {"a.py": ["a.one"]}))
        subs = {"1": analysis(kept, component("1.2", "Other", {"a.py": ["a.one"]}))}

        self.assertEqual(drop_dangling_key_entities(root, subs), [])
        self.assertEqual([e.qualified_name for e in kept.key_entities], ["a.one"])

    def test_a_symbol_another_component_owns_still_counts_as_live(self):
        """The rule is "this document indexes it", not "this component owns it".

        Narrowing it to the component's own methods is `fix_key_entities_refs`'s job and
        its call sites decide when that runs. This pass only removes what the document no
        longer describes anywhere, so it can run unconditionally without second-guessing
        a cross-component citation that was already there.
        """
        citing = component("1.1", "Citing", {"a.py": ["a.one"]})
        citing.key_entities = [self._entity("b.two")]
        root = analysis(component("1", "Parent", {"a.py": ["a.one"], "b.py": ["b.two"]}))
        subs = {"1": analysis(citing, component("1.2", "Owner", {"b.py": ["b.two"]}))}

        self.assertEqual(drop_dangling_key_entities(root, subs), [])
        self.assertEqual([e.qualified_name for e in citing.key_entities], ["b.two"])

    def test_the_emptied_component_can_then_be_pruned_and_its_sibling_absorbed(self):
        """The end-to-end shape the e2e test `last-sibling-is-absorbed-into-its-parent` hits.

        One of two children is emptied by cutting symbols out of a file the parent shares,
        so no file is deleted and nothing scrubs the emptied component's entities. With them
        gone the prune can remove it, which leaves the parent holding one child, which is
        what absorption is for. Each pass is ordinary; only the order makes them work.
        """
        emptied = component("2.1.2", "Emptied", {})
        emptied.key_entities = [self._entity("t.deleted")]
        # Siblings at every level above the one under test: a single-child scope anywhere
        # else collapses too, and the assertion could no longer say which level moved.
        root = analysis(
            component("2", "Top", {"t.py": ["t.kept"], "u.py": ["u.one"]}),
            component("9", "Elsewhere", {"z.py": ["z.one"]}),
        )
        subs = {
            "2": analysis(
                component("2.1", "Parent", {"t.py": ["t.kept"]}),
                component("2.2", "Other", {"u.py": ["u.one"]}),
                component("2.3", "Third", {"u.py": ["u.one"]}),
            ),
            "2.1": analysis(component("2.1.1", "Survivor", {"t.py": ["t.kept"]}), emptied),
        }

        drop_dangling_key_entities(root, subs)
        removed = prune_empty_components(root, subs, set())
        absorb_single_child_components(root, subs)

        self.assertIn("2.1.2", removed)
        self.assertEqual(shape(root, subs), {"": ["2", "9"], "2": ["2.1", "2.2", "2.3"]})
