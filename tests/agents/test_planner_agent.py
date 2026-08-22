import unittest

from agents.agent_responses import AnalysisInsights, Component
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.planner_agent import get_expandable_components, should_expand_component


def component(name: str, *, clusters: bool = False, files: bool = True) -> Component:
    methods = []
    if files:
        methods = [
            FileMethodGroup(
                file_path=f"{name}.py",
                methods=[
                    MethodEntry(
                        qualified_name=f"{name}.run",
                        start_line=1,
                        end_line=10,
                        node_type="METHOD",
                    )
                ],
            )
        ]
    return Component(
        name=name,
        description=name,
        key_entities=[],
        source_cluster_ids=["1"] if clusters else [],
        file_methods=methods,
    )


class TestShouldExpandComponent(unittest.TestCase):
    def test_clustered_component_with_files_expands(self):
        self.assertTrue(should_expand_component(component("Clustered", clusters=True)))
        self.assertTrue(
            should_expand_component(
                component("Clustered", clusters=True),
                parent_had_clusters=False,
            )
        )

    def test_file_only_component_expands_one_level_below_clustered_parent(self):
        candidate = component("FileOnly")

        self.assertTrue(should_expand_component(candidate, parent_had_clusters=True))
        self.assertFalse(should_expand_component(candidate, parent_had_clusters=False))

    def test_component_without_files_never_expands(self):
        self.assertFalse(should_expand_component(component("Empty", clusters=True, files=False)))
        self.assertFalse(should_expand_component(component("Empty", files=False)))


class TestGetExpandableComponents(unittest.TestCase):
    def test_returns_only_structurally_expandable_components(self):
        clustered = component("Clustered", clusters=True)
        file_only = component("FileOnly")
        empty = component("Empty", clusters=True, files=False)
        analysis = AnalysisInsights(
            description="analysis",
            components=[clustered, file_only, empty],
            components_relations=[],
        )

        self.assertEqual(get_expandable_components(analysis), [clustered, file_only])
        self.assertEqual(
            get_expandable_components(analysis, parent_had_clusters=False),
            [clustered],
        )

    def test_empty_analysis_has_no_expandable_components(self):
        analysis = AnalysisInsights(description="analysis", components=[], components_relations=[])

        self.assertEqual(get_expandable_components(analysis), [])


if __name__ == "__main__":
    unittest.main()
