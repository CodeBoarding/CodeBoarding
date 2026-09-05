"""Expanded components write one document each, so their names must not collide."""

import unittest

from agents.agent_responses import AnalysisInsights, Component, Relation
from diagram_analysis.diagram_generator import distinguish_expanded_component_names
from utils import sanitize


def _component(component_id: str, name: str) -> Component:
    return Component(name=name, description=name, key_entities=[], component_id=component_id)


class TestDistinguishExpandedComponentNames(unittest.TestCase):
    def test_a_child_named_after_its_parent_gets_its_id_and_its_relations_follow(self) -> None:
        root = AnalysisInsights(
            description="",
            components=[_component("1", "Static Analysis Engine"), _component("2", "Agents")],
            components_relations=[
                Relation(relation="feeds", src_name="Static Analysis Engine", dst_name="Agents", src_id="1", dst_id="2")
            ],
        )
        one = AnalysisInsights(
            description="",
            components=[_component("1.1", "Static Analysis Engine"), _component("1.2", "Adapters")],
            components_relations=[
                Relation(
                    relation="uses", src_name="Static Analysis Engine", dst_name="Adapters", src_id="1.1", dst_id="1.2"
                )
            ],
        )
        one_one = AnalysisInsights(
            description="", components=[_component("1.1.1", "Static Analysis Engine")], components_relations=[]
        )

        renamed = distinguish_expanded_component_names(root, {"1": one, "1.1": one_one})

        self.assertEqual(renamed, [("1.1", "Static Analysis Engine", "Static Analysis Engine (1.1)")])
        self.assertEqual(root.components[0].name, "Static Analysis Engine")
        self.assertEqual(one.components[0].name, "Static Analysis Engine (1.1)")
        self.assertEqual(one.components_relations[0].src_name, "Static Analysis Engine (1.1)")
        # A leaf may share the name: it writes no document of its own.
        self.assertEqual(one_one.components[0].name, "Static Analysis Engine")

    def test_names_that_differ_only_in_case_collide_as_the_renderer_sees_them(self) -> None:
        root = AnalysisInsights(
            description="", components=[_component("1", "API"), _component("2", "api")], components_relations=[]
        )
        subs = {key: AnalysisInsights(description="", components=[], components_relations=[]) for key in ("1", "2")}

        distinguish_expanded_component_names(root, subs)

        self.assertEqual([c.name for c in root.components], ["API", "api (2)"])

    def test_a_suffixed_name_that_is_itself_taken_is_suffixed_again(self) -> None:
        root = AnalysisInsights(
            description="",
            components=[_component("1", "Core"), _component("2", "Core"), _component("3", "Core (2)")],
            components_relations=[],
        )
        subs = {key: AnalysisInsights(description="", components=[], components_relations=[]) for key in "123"}

        distinguish_expanded_component_names(root, subs)

        names = [c.name for c in root.components]
        self.assertEqual(names, ["Core", "Core (2)", "Core (2) (3)"])
        self.assertEqual(len({sanitize(n).casefold() for n in names}), 3)

    def test_ids_of_mixed_shapes_still_order(self) -> None:
        root = AnalysisInsights(
            description="",
            components=[_component("legacy_component_a", "Core"), _component("2", "Core")],
            components_relations=[],
        )
        subs = {
            key: AnalysisInsights(description="", components=[], components_relations=[])
            for key in ("legacy_component_a", "2")
        }

        renamed = distinguish_expanded_component_names(root, subs)

        self.assertEqual([r[0] for r in renamed], ["legacy_component_a"])

    def test_the_root_document_names_are_taken_before_any_component(self) -> None:
        root = AnalysisInsights(
            description="",
            components=[_component("1", "Overview"), _component("2", "On-Boarding")],
            components_relations=[],
        )
        subs = {key: AnalysisInsights(description="", components=[], components_relations=[]) for key in ("1", "2")}

        distinguish_expanded_component_names(root, subs)

        self.assertEqual([c.name for c in root.components], ["Overview (1)", "On-Boarding (2)"])

    def test_names_that_differ_only_in_punctuation_still_collide(self) -> None:
        root = AnalysisInsights(
            description="", components=[_component("1", "Foo Bar"), _component("2", "Foo-Bar")], components_relations=[]
        )
        subs = {
            "1": AnalysisInsights(description="", components=[], components_relations=[]),
            "2": AnalysisInsights(description="", components=[], components_relations=[]),
        }

        renamed = distinguish_expanded_component_names(root, subs)

        self.assertEqual([r[0] for r in renamed], ["2"])
        self.assertEqual(root.components[1].name, "Foo-Bar (2)")


if __name__ == "__main__":
    unittest.main()
