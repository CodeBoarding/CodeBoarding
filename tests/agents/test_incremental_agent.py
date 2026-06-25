import unittest
from unittest.mock import MagicMock

from agents.agent_responses import AnalysisInsights, Component, FileMethodGroup, MethodEntry, Relation
from agents.incremental_agent import prune_empty_components, remove_deleted_files, repopulate_touched_scopes
from static_analyzer.graph import ClusterResult


def _component(name: str, component_id: str, source_cluster_ids: list[str] | None = None) -> Component:
    return Component(
        name=name,
        description=f"{name} description",
        key_entities=[],
        source_group_names=[name.lower()],
        source_cluster_ids=source_cluster_ids or [],
        component_id=component_id,
    )


def _component_with_method(name: str, component_id: str) -> Component:
    component = _component(name, component_id)
    component.file_methods = [
        FileMethodGroup(
            file_path=f"{component_id}.py",
            methods=[
                MethodEntry(qualified_name=f"{component_id}.method", start_line=1, end_line=2, node_type="FUNCTION")
            ],
        )
    ]
    return component


class TestPruneEmptyComponents(unittest.TestCase):
    def test_strips_relations_by_id_not_duplicate_name(self) -> None:
        discovery = _component_with_method("Discovery & Extraction Engine", "1")
        graph = _component_with_method("Graph Synthesis & Normalization", "2")
        removed_root = _component("Removed Root", "9")
        root = AnalysisInsights(
            description="root",
            components=[discovery, graph, removed_root],
            components_relations=[
                Relation(
                    relation="sends raw data to",
                    src_name=discovery.name,
                    dst_name=graph.name,
                    src_id="1",
                    dst_id="2",
                ),
                Relation(
                    relation="obsolete",
                    src_name=discovery.name,
                    dst_name=removed_root.name,
                    src_id="1",
                    dst_id="9",
                ),
            ],
        )
        sub_analyses = {
            "3.2": AnalysisInsights(
                description="sub",
                components=[_component("Graph Synthesis & Normalization", "3.2.4")],
                components_relations=[],
            )
        }

        removed_ids = prune_empty_components(root, sub_analyses)

        self.assertEqual(removed_ids, {"3.2.4", "9"})
        self.assertEqual([(r.src_id, r.dst_id) for r in root.components_relations], [("1", "2")])
        self.assertEqual([c.component_id for c in root.components], ["1", "2"])

    def test_pruning_empty_parent_removes_descendant_subtree(self) -> None:
        parent = _component("Parent", "1")
        child = _component_with_method("Child", "1.1")
        grandchild = _component_with_method("Grandchild", "1.1.1")
        root = AnalysisInsights(description="root", components=[parent], components_relations=[])
        sub_analyses = {
            "1": AnalysisInsights(description="parent scope", components=[child], components_relations=[]),
            "1.1": AnalysisInsights(description="child scope", components=[grandchild], components_relations=[]),
        }

        removed_ids = prune_empty_components(root, sub_analyses)

        self.assertEqual(removed_ids, {"1", "1.1", "1.1.1"})
        self.assertEqual(root.components, [])
        self.assertEqual(sub_analyses, {})


class TestRemoveDeletedFiles(unittest.TestCase):
    def test_scrubs_deleted_file_references_from_root_and_sub_analysis(self) -> None:
        root_component = _component_with_method("Root", "1")
        root_component.file_methods.append(
            FileMethodGroup(
                file_path="deleted.py",
                methods=[MethodEntry(qualified_name="deleted.fn", start_line=1, end_line=2, node_type="FUNCTION")],
            )
        )
        sub_component = _component_with_method("Sub", "1.1")
        root = AnalysisInsights(description="r", components=[root_component], components_relations=[])
        sub = AnalysisInsights(description="s", components=[sub_component], components_relations=[])
        sub_analyses = {"1": sub}

        dropped = remove_deleted_files(root, sub_analyses, {"1.py"})

        self.assertEqual(dropped, {"deleted.py", "1.1.py"})
        self.assertEqual([group.file_path for group in root_component.file_methods], ["1.py"])
        self.assertEqual(sub_component.file_methods, [])


class TestRepopulateTouchedScopes(unittest.TestCase):
    def test_no_refresh_skips_all_helpers(self) -> None:
        helpers = MagicMock()
        touched = repopulate_touched_scopes(
            set(),
            AnalysisInsights(description="r", components=[], components_relations=[]),
            {},
            {},
            helpers,
        )

        self.assertEqual(touched, set())
        helpers.populate_file_methods.assert_not_called()
        helpers.build_static_relations.assert_not_called()

    def test_root_refresh_rebuilds_file_methods_and_static_relations(self) -> None:
        root_component = _component("Root", "1", source_cluster_ids=["1"])
        root = AnalysisInsights(description="r", components=[root_component], components_relations=[])
        cluster_results = {"python": ClusterResult(clusters={1: {"pkg.fn"}}, cluster_to_files={1: {"pkg.py"}})}
        helpers = MagicMock()
        helpers.static_analysis.get_cfg.return_value.filter_by_nodes.return_value = "cfg"

        touched = repopulate_touched_scopes({"1"}, root, {}, cluster_results, helpers)

        self.assertEqual(touched, {""})
        helpers.populate_file_methods.assert_called_once_with(root, cluster_results, {"python": "cfg"})
        helpers.build_static_relations.assert_called_once_with(root, {"python": "cfg"})

    def test_sub_scope_refresh_is_left_unchanged_without_scoped_artifacts(self) -> None:
        sub_component = _component("Sub", "1.1", source_cluster_ids=["1"])
        root = AnalysisInsights(description="r", components=[], components_relations=[])
        sub_analyses = {"1": AnalysisInsights(description="s", components=[sub_component], components_relations=[])}
        helpers = MagicMock()

        with self.assertLogs("agents.incremental_agent", level="WARNING") as logs:
            touched = repopulate_touched_scopes({"1.1"}, root, sub_analyses, {}, helpers)

        self.assertEqual(touched, set())
        self.assertIn("kept scope 1 unchanged", "\n".join(logs.output))
        helpers.populate_file_methods.assert_not_called()
        helpers.build_static_relations.assert_not_called()


if __name__ == "__main__":
    unittest.main()
