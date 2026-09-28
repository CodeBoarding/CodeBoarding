import unittest
from unittest.mock import MagicMock

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.incremental_results import ScopeRelationContext
from diagram_analysis.incremental_update import (
    IncrementalUpdater,
    _patch_file_methods,
    prune_empty_components,
    remove_deleted_files,
)
from static_analyzer.clustering import ClusterResult, ClusterScopeResult


def _component(name: str, component_id: str, source_cluster_ids: list[str] | None = None) -> Component:
    return Component(
        name=name,
        description=f"{name} description",
        key_entities=[],
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


def _clustering() -> ClusterScopeResult:
    return ClusterScopeResult(scope_id="root", leaf_clusters_by_language={"python": ClusterResult()})


def _updater() -> IncrementalUpdater:
    updater = object.__new__(IncrementalUpdater)
    updater.static_analysis = MagicMock()
    updater.static_analysis.get_languages.return_value = []
    updater.reference_resolver = MagicMock()

    def populate(scope, _clustering, touched_ids):
        for component in scope.components:
            if component.component_id in touched_ids:
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

    updater._patch_scope_file_methods = MagicMock(side_effect=populate)
    return updater


class TestIncrementalUpdater(unittest.TestCase):
    def test_moves_reassigned_clusters_between_components(self) -> None:
        first = _component("Core", "1", ["1", "2"])
        second = _component("Parsers", "2", ["3"])
        scope = AnalysisInsights(description="root", components=[first, second], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    cluster_refs=[
                        ScopedClusterRef(scope_id="root", language="python", cluster_id=2),
                        ScopedClusterRef(scope_id="root", language="python", cluster_id=3),
                    ],
                    component_id="2",
                    description="Owns parser behavior.",
                    rationale="Cluster 2 moved.",
                )
            ]
        )

        result = _updater().update_scope("root", scope, decision, _clustering())

        self.assertEqual(first.source_cluster_ids, ["1"])
        self.assertEqual(second.source_cluster_ids, ["2", "3"])
        self.assertEqual(second.description, "Owns parser behavior.")
        self.assertEqual(result.refresh_ids, {"1", "2"})

    def test_noop_preserves_metadata(self) -> None:
        component = _component("API", "1", ["1"])
        scope = AnalysisInsights(description="root", components=[component], components_relations=[])
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.NOOP,
                    cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=1)],
                    component_id="1",
                    name="Ignored",
                    description="Ignored",
                    rationale="Only implementation details changed.",
                )
            ]
        )

        result = _updater().update_scope("root", scope, decision, _clustering())

        self.assertEqual((component.name, component.description), ("API", "API description"))
        self.assertEqual(result.refresh_ids, set())

    def test_create_component_assigns_id_and_membership(self) -> None:
        scope = AnalysisInsights(
            description="root",
            components=[_component("API", "1")],
            components_relations=[],
        )
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.CREATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="root", language="python", cluster_id=7)],
                    name="Worker",
                    description="Runs jobs.",
                    rationale="New responsibility.",
                )
            ]
        )

        result = _updater().update_scope("root", scope, decision, _clustering())

        created = scope.components[1]
        self.assertEqual((created.component_id, created.name), ("2", "Worker"))
        self.assertEqual(created.source_cluster_ids, ["7"])
        self.assertEqual(result.new_component_ids, {"2"})

    def test_delete_component_removes_its_relations(self) -> None:
        first = _component("A", "1")
        second = _component("B", "2")
        scope = AnalysisInsights(
            description="root",
            components=[first, second],
            components_relations=[Relation(relation="calls", src_name="A", dst_name="B", src_id="1", dst_id="2")],
        )
        decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.DELETE_COMPONENT,
                    cluster_refs=[],
                    component_id="1",
                    rationale="The component disappeared.",
                )
            ]
        )

        result = _updater().update_scope("root", scope, decision, _clustering())

        self.assertEqual([component.component_id for component in scope.components], ["2"])
        self.assertEqual(scope.components_relations, [])
        self.assertEqual(result.removed_ids, {"1"})

    def test_generate_all_scope_relations_includes_root(self) -> None:
        updater = object.__new__(IncrementalUpdater)
        updater._generate_scope_relations = MagicMock(return_value=[])
        root = AnalysisInsights(description="root", components=[], components_relations=[])
        context = ScopeRelationContext(clustering=_clustering())

        updater.generate_all_scope_relations(root, {}, {"root": context}, {"pkg.changed"}, {"settings.py"})

        updater._generate_scope_relations.assert_called_once_with(
            root,
            context,
            {"pkg.changed"},
            {"settings.py"},
        )


class TestIncrementalCleanup(unittest.TestCase):
    def test_prunes_empty_component_and_its_relations(self) -> None:
        live = _component_with_method("Live", "1")
        removed = _component("Removed", "2")
        root = AnalysisInsights(
            description="root",
            components=[live, removed],
            components_relations=[
                Relation(relation="calls", src_name="Live", dst_name="Removed", src_id="1", dst_id="2")
            ],
        )

        removed_ids = prune_empty_components(root, {})

        self.assertEqual(removed_ids, {"2"})
        self.assertEqual(root.components, [live])
        self.assertEqual(root.components_relations, [])

    def test_removes_deleted_file_references(self) -> None:
        component = _component_with_method("Root", "1")
        component.file_methods.append(
            FileMethodGroup(
                file_path="deleted.py",
                methods=[MethodEntry(qualified_name="deleted.fn", start_line=1, end_line=2, node_type="FUNCTION")],
            )
        )
        root = AnalysisInsights(description="root", components=[component], components_relations=[])

        dropped = remove_deleted_files(root, {}, {"1.py"})

        self.assertEqual(dropped, {"deleted.py"})
        self.assertEqual([group.file_path for group in component.file_methods], ["1.py"])

    def test_patch_file_methods_moves_method_without_duplicate(self) -> None:
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

    def test_patch_file_methods_keeps_a_live_callback_sharing_the_moved_ones_line(self) -> None:
        """Why: nested arrows on one line (``rAF(() => rAF(() => f()))``) share file, span, type and leaf
        name — they differ only by column, which a ``MethodEntry`` does not carry. Moving the outer one
        used to strip the inner one from its own component too, and the child scope that still owned it
        then failed containment (``1.1 holds N method(s) outside parent 1``)."""
        outer = MethodEntry(qualified_name="App.effect.rAF() callback", start_line=7, end_line=7, node_type="FUNCTION")
        inner = MethodEntry(
            qualified_name="App.effect.rAF() callback.rAF() callback", start_line=7, end_line=7, node_type="FUNCTION"
        )
        keeper = _component("Keeper", "1")
        keeper.file_methods = [FileMethodGroup(file_path="app.tsx", methods=[inner])]
        target = _component("Target", "2")
        scope = AnalysisInsights(description="root", components=[keeper, target], components_relations=[])

        _patch_file_methods(
            scope,
            {"2": [FileMethodGroup(file_path="app.tsx", methods=[outer])]},
            {"2"},
            {outer.qualified_name, inner.qualified_name},
        )

        self.assertEqual([m.qualified_name for m in keeper.file_methods[0].methods], [inner.qualified_name])
        self.assertEqual([m.qualified_name for m in target.file_methods[0].methods], [outer.qualified_name])

    def test_patch_file_methods_still_drops_a_stale_alias_at_the_moved_location(self) -> None:
        """Why: the location match is there for an LSP alias of the moved method — the old spelling is no
        longer live, and leaving it behind would render the same symbol in two components."""
        live = MethodEntry(qualified_name="src.index.funcA", start_line=3, end_line=5, node_type="FUNCTION")
        alias = MethodEntry(qualified_name="src/index.funcA", start_line=3, end_line=5, node_type="FUNCTION")
        old_home = _component("Old", "1")
        old_home.file_methods = [FileMethodGroup(file_path="src/index.ts", methods=[alias])]
        new_home = _component("New", "2")
        scope = AnalysisInsights(description="root", components=[old_home, new_home], components_relations=[])

        _patch_file_methods(
            scope,
            {"2": [FileMethodGroup(file_path="src/index.ts", methods=[live])]},
            {"2"},
            {live.qualified_name},
        )

        self.assertEqual(old_home.file_methods, [])
        self.assertEqual([m.qualified_name for m in new_home.file_methods[0].methods], [live.qualified_name])


if __name__ == "__main__":
    unittest.main()
