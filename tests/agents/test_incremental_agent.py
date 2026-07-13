import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import (
    AnalysisInsights,
    ComponentApiSurface,
    ComponentApiSurfaces,
    ComponentRelations,
    Component,
    Relation,
    RelationEdge,
    SourceCodeReference,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from agents.incremental_agent import (
    IncrementalAgent,
    _cluster_analysis_for_scope,
    _patch_file_methods,
    prune_empty_components,
    remove_deleted_files,
)
from agents.incremental_results import ScopeRelationContext
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


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

    def test_pruning_preserves_cluster_backed_empty_components(self) -> None:
        empty = _component("Prior Empty Leaf", "1.1", source_cluster_ids=["1.1.0"])
        root = AnalysisInsights(description="root", components=[empty], components_relations=[])

        removed_ids = prune_empty_components(root, {}, protected_empty_ids={"1.1"})

        self.assertEqual(removed_ids, set())
        self.assertEqual(root.components, [empty])


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


class TestUpdateScope(unittest.TestCase):
    def _agent(self) -> IncrementalAgent:
        agent = object.__new__(IncrementalAgent)
        agent.static_analysis = MagicMock()
        agent.static_analysis.get_languages.return_value = []
        agent.static_analysis.get_cfg.return_value.filter_by_nodes.return_value = "cfg"
        agent.reference_resolver = MagicMock()

        def populate(scope, _cluster_results, _cfg_graphs, _touched_ids, source_cluster_id_prefix=""):
            for component in scope.components:
                if component.source_cluster_ids:
                    component.file_methods = [
                        FileMethodGroup(
                            file_path=f"{component.component_id}.py",
                            methods=[
                                MethodEntry(
                                    qualified_name=f"{component.component_id}.method",
                                    start_line=1,
                                    end_line=2,
                                    node_type="FUNCTION",
                                )
                            ],
                        )
                    ]

        agent._patch_scope_file_methods = MagicMock(side_effect=populate)
        agent.build_static_relations = MagicMock()
        return agent

    def test_update_without_selected_key_entities_does_not_synthesize_them(self) -> None:
        component = _component("API", "1", source_cluster_ids=["1"])
        scope = AnalysisInsights(description="root", components=[component], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=2)],
                    component_id="1",
                    description="New",
                    rationale="API gained a cluster.",
                )
            ]
        )

        agent = self._agent()
        reference_resolver = MagicMock()
        agent.reference_resolver = reference_resolver
        result = agent.update_scope("root", scope, decision, {"python": ClusterResult()})

        self.assertEqual(component.description, "New")
        self.assertEqual(component.source_cluster_ids, ["1", "2"])
        self.assertEqual(component.key_entities, [])
        self.assertEqual(result.refresh_ids, {"1"})
        self.assertEqual(result.new_component_ids, set())
        reference_resolver.fix_key_entities_refs.assert_called_once_with(scope, {"1"})

    def test_update_preserves_planner_selected_key_entities(self) -> None:
        component = _component("API", "1", source_cluster_ids=["1"])
        selected = SourceCodeReference(qualified_name="alias.method", reference_file="selected.py")
        expected = selected.model_copy(deep=True)
        scope = AnalysisInsights(description="root", components=[component], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=2)],
                    component_id="1",
                    key_entities=[selected],
                    rationale="The API responsibility changed.",
                )
            ]
        )

        agent = self._agent()
        reference_resolver = MagicMock()
        agent.reference_resolver = reference_resolver

        def resolve_selected_key_entity(_analysis: AnalysisInsights, _component_ids: set[str]) -> None:
            self.assertIs(_analysis, scope)
            self.assertEqual(_component_ids, {"1"})

        reference_resolver.fix_key_entities_refs.side_effect = resolve_selected_key_entity

        agent.update_scope("root", scope, decision, {"python": ClusterResult()})

        self.assertEqual(component.key_entities, [expected])

    def test_update_scope_moves_reassigned_clusters_between_siblings(self) -> None:
        first = _component("Core", "1.1", source_cluster_ids=["1.1", "1.2"])
        second = _component("Parsers", "1.2", source_cluster_ids=["1.3"])
        scope = AnalysisInsights(description="nested", components=[first, second], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="1", language="python", cluster_id=2)],
                    component_id="1.2",
                    description="Parsers now own the parser cluster.",
                    rationale="Cluster 2 moved under parser responsibility.",
                )
            ]
        )

        result = self._agent().update_scope("1", scope, decision, {"python": ClusterResult()})

        self.assertEqual(first.source_cluster_ids, ["1.1"])
        self.assertEqual(second.source_cluster_ids, ["1.2", "1.3"])
        self.assertEqual(result.refresh_ids, {"1.1", "1.2"})

    def test_noop_preserves_metadata_but_refreshes_derived_scope_data(self) -> None:
        component = _component("API", "1", source_cluster_ids=["1"])
        scope = AnalysisInsights(description="root", components=[component], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.NOOP,
                    cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                    component_id="1",
                    name="Ignored rename",
                    description="Ignored description",
                    rationale="Only implementation details changed.",
                )
            ]
        )

        agent = self._agent()
        result = agent.update_scope("root", scope, decision, {"python": ClusterResult()})

        self.assertEqual(component.name, "API")
        self.assertEqual(component.description, "API description")
        self.assertEqual(result.refresh_ids, {"1"})
        self.assertEqual(result.relation_context.cluster_results, {"python": ClusterResult()})
        self.assertEqual(result.relation_context.cfg_graphs, {})

    def test_create_component_assigns_id_clusters_methods_and_key_entities(self) -> None:
        existing = Component(name="API", description="", key_entities=[], component_id="1")
        scope = AnalysisInsights(description="root", components=[existing], components_relations=[])
        selected = SourceCodeReference(qualified_name="worker.run")
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.CREATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=7)],
                    name="Worker",
                    description="Runs jobs.",
                    key_entities=[selected],
                    rationale="New isolated responsibility.",
                )
            ]
        )

        result = self._agent().update_scope("root", scope, decision, {"python": ClusterResult()})

        created = scope.components[1]
        self.assertEqual(created.component_id, "2")
        self.assertEqual(created.name, "Worker")
        self.assertEqual(created.source_cluster_ids, ["7"])
        self.assertEqual(created.file_methods[0].methods[0].qualified_name, "2.method")
        self.assertEqual(created.key_entities, [selected])
        self.assertEqual(result.refresh_ids, {"2"})
        self.assertEqual(result.new_component_ids, {"2"})

    def test_create_component_with_mismatched_scope_is_skipped(self) -> None:
        scope = AnalysisInsights(description="root", components=[], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.CREATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="1.3", language="python", cluster_id=8)],
                    name="Nested Worker",
                    description="Runs nested jobs.",
                    rationale="Wrong scope for root apply.",
                )
            ]
        )

        result = self._agent().update_scope("root", scope, decision, {"python": ClusterResult()})

        self.assertEqual(scope.components, [])
        self.assertEqual(result.new_component_ids, set())

    def test_delete_component_removes_relations(self) -> None:
        first = _component("A", "1")
        second = _component("B", "2")
        relation = Relation(relation="calls", src_name="A", dst_name="B", src_id="1", dst_id="2")
        scope = AnalysisInsights(description="root", components=[first, second], components_relations=[relation])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.DELETE_COMPONENT,
                    cluster_refs=[],
                    component_id="1",
                    rationale="Removed cluster emptied component.",
                )
            ]
        )

        result = self._agent().update_scope("root", scope, decision, {})

        self.assertEqual([component.component_id for component in scope.components], ["2"])
        self.assertEqual(scope.components_relations, [])
        self.assertEqual(result.removed_ids, {"1"})

    def test_delete_component_keeps_live_cfg_methods(self) -> None:
        first = _component_with_method("A", "1")
        second = _component_with_method("B", "2")
        scope = AnalysisInsights(description="root", components=[first, second], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.DELETE_COMPONENT,
                    cluster_refs=[],
                    component_id="1",
                    rationale="Planner thought the component was removed.",
                )
            ]
        )
        agent = self._agent()
        cfg = MagicMock()
        cfg.nodes = {"1.method": object(), "2.method": object()}
        agent.static_analysis.get_languages = MagicMock(return_value=["python"])  # type: ignore[method-assign]
        agent.static_analysis.get_cfg = MagicMock(return_value=cfg)  # type: ignore[method-assign]

        result = agent.update_scope("root", scope, decision, {})

        self.assertEqual([component.component_id for component in scope.components], ["1", "2"])
        self.assertEqual(result.removed_ids, set())
        self.assertEqual(result.refresh_ids, {"1"})

    def test_delete_component_keeps_live_cfg_key_entities(self) -> None:
        first = _component("A", "1")
        first.key_entities = [SourceCodeReference(qualified_name="1.method", reference_file="a.py")]
        second = _component_with_method("B", "2")
        scope = AnalysisInsights(description="root", components=[first, second], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.DELETE_COMPONENT,
                    cluster_refs=[],
                    component_id="1",
                    rationale="Planner thought the component was removed.",
                )
            ]
        )
        agent = self._agent()
        cfg = MagicMock()
        cfg.nodes = {"1.method": object(), "2.method": object()}
        agent.static_analysis.get_languages = MagicMock(return_value=["python"])  # type: ignore[method-assign]
        agent.static_analysis.get_cfg = MagicMock(return_value=cfg)  # type: ignore[method-assign]

        result = agent.update_scope("root", scope, decision, {})

        self.assertEqual([component.component_id for component in scope.components], ["1", "2"])
        self.assertEqual(result.removed_ids, set())
        self.assertEqual(result.refresh_ids, {"1"})


class TestIncrementalRelations(unittest.TestCase):
    def test_reconstructs_nested_cluster_context_from_persisted_components(self) -> None:
        component = _component("Worker", "1.1", source_cluster_ids=["1.2", "9.9"])
        component.source_group_names = []
        scope = AnalysisInsights(description="nested", components=[component], components_relations=[])

        cluster_analysis = _cluster_analysis_for_scope(
            scope,
            "1",
            {"python": ClusterResult(clusters={2: {"worker.run"}})},
        )

        self.assertEqual(component.source_group_names, ["Worker"])
        self.assertEqual(cluster_analysis.cluster_components[0].cluster_ids, [2])

    def test_uses_api_surface_relation_pipeline_and_attaches_static_call_sites(self) -> None:
        source = Node("pkg.api.run", NodeType.FUNCTION, "api.py", 1, 5)
        target = Node("pkg.worker.load", NodeType.FUNCTION, "worker.py", 10, 15)
        cfg = CallGraph(nodes={source.fully_qualified_name: source, target.fully_qualified_name: target})
        cfg.add_edge(source.fully_qualified_name, target.fully_qualified_name, [{"line": 3, "column": 7}])
        static_analysis = MagicMock(spec=StaticAnalysisResults)
        static_analysis.get_languages.return_value = ["python"]
        static_analysis.get_cfg.return_value = cfg
        static_analysis.available_cfgs.return_value = {"python": cfg}

        api = _component("API", "1", source_cluster_ids=["1"])
        api.file_methods = [
            FileMethodGroup(
                file_path="api.py",
                methods=[
                    MethodEntry(
                        qualified_name=source.fully_qualified_name, start_line=1, end_line=5, node_type="FUNCTION"
                    )
                ],
            )
        ]
        worker = _component("Worker", "2", source_cluster_ids=["2"])
        worker.file_methods = [
            FileMethodGroup(
                file_path="worker.py",
                methods=[
                    MethodEntry(
                        qualified_name=target.fully_qualified_name, start_line=10, end_line=15, node_type="FUNCTION"
                    )
                ],
            )
        ]
        scope = AnalysisInsights(
            description="root",
            files={
                "api.py": FileEntry(methods=[api.file_methods[0].methods[0].model_copy(deep=True)]),
                "worker.py": FileEntry(methods=[worker.file_methods[0].methods[0].model_copy(deep=True)]),
            },
            components=[api, worker],
            components_relations=[Relation(relation="legacy relation", src_name="API", dst_name="Worker")],
        )
        api_surfaces = ComponentApiSurfaces(
            api_surfaces=[
                ComponentApiSurface(component_name="API", outgoing_api_paths=["dispatches jobs"]),
                ComponentApiSurface(component_name="Worker", incoming_api_paths=["receives jobs"]),
            ]
        )
        key_edge = RelationEdge(
            source=SourceCodeReference(qualified_name=source.fully_qualified_name),
            target=SourceCodeReference(qualified_name=target.fully_qualified_name),
            description="API dispatches work.",
        )
        relation_result = ComponentRelations(
            components_relations=[
                Relation(
                    relation="dispatches jobs to",
                    src_name="API",
                    dst_name="Worker",
                    evidence="Queue-backed job dispatch.",
                    key_edges=[key_edge],
                )
            ]
        )
        cluster_results = {
            "python": ClusterResult(clusters={1: {source.fully_qualified_name}, 2: {target.fully_qualified_name}})
        }

        with patch("agents.agent.create_agent", return_value=MagicMock()):
            agent = IncrementalAgent(
                repo_dir=Path("/tmp/fake-repo"),
                static_analysis=static_analysis,
                project_name="Test",
                meta_context=None,
                agent_llm=MagicMock(),
                parsing_llm=MagicMock(),
            )
        agent._parse_invoke = MagicMock(return_value=api_surfaces)
        agent._invoke_validate = MagicMock(return_value=relation_result)
        agent.reference_resolver = MagicMock()
        agent.reference_resolver.fix_source_code_reference_lines.side_effect = lambda analysis: analysis

        system_message = str(agent.system_message.content)
        self.assertIn("Test", system_message)
        self.assertIn("unknown", system_message)
        self.assertNotIn("{project_name}", system_message)

        generated = agent.generate_scope_relations(
            scope,
            "root",
            ScopeRelationContext(cluster_results=cluster_results, cfg_graphs={"python": cfg}),
        )

        self.assertEqual(generated, relation_result.components_relations)
        self.assertEqual(agent._parse_invoke.call_args.args[1], ComponentApiSurfaces)
        self.assertNotIn("legacy relation", agent._parse_invoke.call_args.args[0])
        relation_call = agent._invoke_validate.call_args
        self.assertEqual(relation_call.args[1], ComponentRelations)
        self.assertNotIn("legacy relation", relation_call.args[0])
        self.assertEqual(relation_call.kwargs["validators"][0].__name__, "validate_relations")
        self.assertEqual(len(scope.components_relations), 1)
        relation = scope.components_relations[0]
        self.assertEqual(relation.evidence, "Queue-backed job dispatch.")
        self.assertEqual(relation.key_edges, [key_edge])
        self.assertTrue(relation.is_static)
        self.assertTrue(any(edge.call_sites[0].line == 3 for edge in relation.all_edges if edge.call_sites))
        self.assertEqual(scope.files["api.py"].methods[0].qualified_name, source.fully_qualified_name)
        self.assertEqual(scope.files["api.py"].methods[0].node_type, NodeType.FUNCTION.name)

    def test_generate_all_scope_relations_includes_root_scope_id(self) -> None:
        agent = object.__new__(IncrementalAgent)
        agent.generate_scope_relations = MagicMock(return_value=[])
        root = AnalysisInsights(description="root", components=[], components_relations=[])
        context = ScopeRelationContext(cluster_results={}, cfg_graphs={})

        agent.generate_all_scope_relations(root, {}, {"root": context})

        agent.generate_scope_relations.assert_called_once_with(root, "root", context)

    def test_single_component_scope_clears_stale_relations_and_resolves_references(self) -> None:
        agent = object.__new__(IncrementalAgent)
        agent.reference_resolver = MagicMock()
        component = _component("Only", "1")
        scope = AnalysisInsights(
            description="root",
            components=[component],
            components_relations=[Relation(relation="stale", src_name="Only", dst_name="Removed")],
        )

        result = agent.generate_scope_relations(
            scope,
            "root",
            ScopeRelationContext(cluster_results={}, cfg_graphs={}),
        )

        self.assertEqual(result, [])
        self.assertEqual(scope.components_relations, [])
        agent.reference_resolver.fix_source_code_reference_lines.assert_called_once_with(scope)


class TestPatchFileMethods(unittest.TestCase):
    def test_preserves_untouched_methods_while_replacing_represented_slice(self) -> None:
        component = _component("API", "1")
        component.file_methods = [
            FileMethodGroup(
                file_path="api.py",
                methods=[
                    MethodEntry(qualified_name="api.keep", start_line=1, end_line=2, node_type="FUNCTION"),
                    MethodEntry(qualified_name="api.changed", start_line=4, end_line=5, node_type="FUNCTION"),
                ],
            )
        ]
        scope = AnalysisInsights(description="root", components=[component], components_relations=[])
        updated = FileMethodGroup(
            file_path="api.py",
            methods=[MethodEntry(qualified_name="api.changed", start_line=4, end_line=6, node_type="FUNCTION")],
        )

        _patch_file_methods(scope, {"1": [updated]}, {"1"}, {"api.keep", "api.changed"})

        methods = scope.components[0].file_methods[0].methods
        self.assertEqual([method.qualified_name for method in methods], ["api.keep", "api.changed"])
        self.assertEqual(methods[1].end_line, 6)

    def test_moves_represented_method_between_siblings_without_duplicate(self) -> None:
        first = _component("Old", "1")
        second = _component("New", "2")
        first.file_methods = [
            FileMethodGroup(
                file_path="shared.py",
                methods=[MethodEntry(qualified_name="pkg.moved", start_line=10, end_line=12, node_type="FUNCTION")],
            )
        ]
        scope = AnalysisInsights(description="root", components=[first, second], components_relations=[])
        moved = FileMethodGroup(
            file_path="shared.py",
            methods=[MethodEntry(qualified_name="pkg.moved", start_line=10, end_line=12, node_type="FUNCTION")],
        )

        _patch_file_methods(scope, {"2": [moved]}, {"1", "2"}, {"pkg.moved"})

        self.assertEqual(first.file_methods, [])
        self.assertEqual(second.file_methods[0].methods[0].qualified_name, "pkg.moved")

    def test_removes_deleted_methods_only_from_touched_components(self) -> None:
        touched = _component("Touched", "1")
        untouched = _component("Untouched", "2")
        stale = MethodEntry(qualified_name="pkg.deleted", start_line=1, end_line=2, node_type="FUNCTION")
        live = MethodEntry(qualified_name="pkg.live", start_line=3, end_line=4, node_type="FUNCTION")
        touched.file_methods = [FileMethodGroup(file_path="a.py", methods=[stale, live])]
        untouched.file_methods = [FileMethodGroup(file_path="b.py", methods=[stale])]
        scope = AnalysisInsights(description="root", components=[touched, untouched], components_relations=[])

        _patch_file_methods(scope, {}, {"1"}, {"pkg.live"})

        self.assertEqual([method.qualified_name for method in touched.file_methods[0].methods], ["pkg.live"])
        self.assertEqual(untouched.file_methods[0].methods[0].qualified_name, "pkg.deleted")


if __name__ == "__main__":
    unittest.main()
