"""Two expanded components that sanitise to one document name overwrite each other."""

import unittest

from agents.agent_responses import AnalysisInsights, Component, Relation
from diagram_analysis.diagram_generator import distinguish_expanded_component_names


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
