"""The service on the names partition: full runs draft, incremental runs replay, partial runs replay one scope."""

import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from agents.file_index_models import FileMethodGroup, MethodEntry
from clustering_ids import ROOT_SCOPE_ID
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.cfg.edge import EdgeKind, ReferenceEdge
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from static_analyzer.clustering.exceptions import IncrementalCacheMissingError, PlannerUnavailableError
from static_analyzer.clustering.names import ComponentRule, ScopeSpec, TreeSpec
from static_analyzer.clustering.service import NEW_SCOPE, ClusteringService, hierarchy_differs, unit_links
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node
from tests.static_analyzer.names.conftest import rule_of, scope_of

REPO = Path("/repo")


def graph(language: str, files: dict[str, list[str]], edges: list[tuple[str, str]] = ()) -> CallGraph:
    """``{relative path: [qualified names]}``; a capitalised last segment is a class, ``_var`` a variable."""
    out = CallGraph(language=language)
    for path, names in files.items():
        for index, name in enumerate(names):
            last = name.rsplit(".", 1)[-1]
            kind = (
                NodeType.VARIABLE
                if last.endswith("_var")
                else NodeType.CLASS if last[:1].isupper() else NodeType.FUNCTION
            )
            out.add_node(Node(name, kind, str(REPO / path), index * 4 + 1, index * 4 + 3))
    for source, target in edges:
        out.add_edge(source, target, call_sites=[{"file": "x.cs", "line": 1}])
    return out


def project(name: str, count: int, *subdirs: str, prefix: str = "") -> dict[str, list[str]]:
    layout: dict[str, list[str]] = {}
    stem = name.split(".")[0]
    for index in range(count):
        sub = subdirs[index % len(subdirs)] if subdirs else ""
        module = ".".join(part for part in (prefix, name, sub) if part)
        layout[f"src/{name}/{sub}/{stem}Type{index}.cs".replace("//", "/")] = [
            f"{module}.{stem}Type{index}",
            f"{module}.{stem}Type{index}.Run()",
        ]
    return layout


def eshop() -> dict[str, list[str]]:
    return (
        project("Ordering.API", 12, "Apis", "Application")
        | project("Ordering.Domain", 4)
        | project("OrderProcessor", 3)
        | project("Catalog.API", 8, "Model", "Apis")
        | project("Basket.API", 4, "Model")
        | project("Webhooks.API", 5)
        | project("WebhookClient", 3)
        | project("PaymentProcessor", 3)
    )


def analysis_for(csharp: CallGraph) -> StaticAnalysisResults:
    results = StaticAnalysisResults()
    results.add_cfg(Language.CSHARP, csharp)
    return results


def persisted_from(hierarchy: ClusterScopeResult) -> dict[str, AnalysisInsights]:
    """What a saved analysis.json would hand back: components with their files and methods."""
    scopes: dict[str, AnalysisInsights] = {}

    def visit(scope: ClusterScopeResult) -> None:
        components = []
        for group in scope.groups:
            by_file: dict[str, list[MethodEntry]] = {}
            for language, names in group.symbol_members_by_language.items():
                nodes = scope.graphs_by_language[language].nodes
                for name in sorted(names):
                    node = nodes[name]
                    by_file.setdefault(str(Path(node.file_path).relative_to(REPO)), []).append(
                        MethodEntry(
                            qualified_name=name, start_line=node.line_start, end_line=node.line_end, node_type="METHOD"
                        )
                    )
            components.append(
                Component(
                    name=group.group_id,
                    description="",
                    key_entities=[],
                    component_id=group.group_id,
                    file_methods=[
                        FileMethodGroup(file_path=path, methods=methods) for path, methods in sorted(by_file.items())
                    ],
                )
            )
            if group.children is not None:
                visit(group.children)
        scopes[scope.scope_id] = AnalysisInsights(description="", components=components, components_relations=[])

    visit(hierarchy)
    return scopes


class TestFullHierarchy(unittest.TestCase):
    def setUp(self):
        self.graph = graph("csharp", eshop() | {"src/Ordering.API/Apis/consts.cs": ["Ordering.API.Apis.limits_var"]})
        self.service = ClusteringService()
        self.hierarchy = self.service.build_full_hierarchy(analysis_for(self.graph), max_depth=2)

    def test_groups_are_the_specification_rules(self):
        root = scope_of(self.service.spec, ROOT_SCOPE_ID)
        self.assertEqual(
            [group.group_id for group in self.hierarchy.groups], [rule.component_id for rule in root.rules]
        )
        self.assertEqual(rule_of(root, "1").name, "Ordering")

    def test_leaves_are_files_and_members_are_callables_and_classes(self):
        leaves = self.hierarchy.leaf_clusters_by_language["csharp"]
        self.assertEqual(len(leaves.clusters), len({node.file_path for node in self.graph.nodes.values()}))
        ordering = self.hierarchy.groups[0]
        self.assertNotIn("Ordering.API.Apis.limits_var", ordering.qualified_names)
        self.assertIn("Ordering.API.Apis.OrderingType0.Run()", ordering.qualified_names)
        self.assertEqual(
            len(ordering.cluster_ids),
            len({node.file_path for name, node in self.graph.nodes.items() if name.startswith("Order")}),
        )

    def test_a_child_scope_replays_over_the_units_its_parent_placed_data_only_files_included(self):
        ordering = self.hierarchy.groups[0]
        assert ordering.children is not None
        consts = next(
            cluster_id
            for cluster_id, files in ordering.children.leaf_clusters_by_language["csharp"].cluster_to_files.items()
            if any(path.endswith("consts.cs") for path in files)
        )
        self.assertIn(consts, [cluster_id for group in ordering.children.groups for cluster_id in group.cluster_ids])

    def test_children_follow_the_ladder_and_expandable_is_a_decision(self):
        ordering, basket = self.hierarchy.groups[0], self.hierarchy.groups[3]
        self.assertTrue(ordering.expandable)
        assert ordering.children is not None
        self.assertEqual([group.group_id for group in ordering.children.groups], ["1.1", "1.2"])
        self.assertFalse(basket.expandable)
        self.assertIsNone(basket.children)
        self.assertIn("1.1", self.service.spec.scopes, "the spec is drafted one level deeper than the tree")
        self.assertTrue(scope_of(self.service.spec, "4").is_leaf)

    def test_the_default_grouper_folds_along_the_graph(self):
        """Fifteen root boxes, Tiny.API calling Ordering: the smallest box joins the one it calls."""
        self.assertEqual(self.service.spec.grouper, "affinity")
        layout = eshop() | project("Tiny.API", 2)
        for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota"):
            layout |= project(name, 3)
        edges = [(f"Tiny.API.TinyType{i}.Run()", "Ordering.API.Apis.OrderingType0.Run()") for i in range(2)]
        service = ClusteringService()
        service.build_full_hierarchy(analysis_for(graph("csharp", layout, edges)), max_depth=1)
        ordering = rule_of(scope_of(service.spec, ROOT_SCOPE_ID), "1")
        self.assertIn(("Tiny",), ordering.prefixes)
        self.assertEqual([part.name for part in ordering.parts], ["OrderProcessor", "Ordering", "Tiny"])

    def test_unit_links_count_calls_and_reference_edges_between_files(self):
        csharp = graph("csharp", {"a.cs": ["A", "A.Run()"], "b.cs": ["B", "B.Run()"]}, [("A.Run()", "B.Run()")])
        csharp.add_edge("A.Run()", "A", call_sites=[{"file": "a.cs", "line": 2}])
        csharp.add_reference_edge(ReferenceEdge("A", "B", EdgeKind.INHERITS))
        csharp.add_reference_edge(ReferenceEdge("B", "B.Run()", EdgeKind.CONTAINS))
        self.assertEqual(unit_links({"csharp": csharp}), {(str(REPO / "a.cs"), str(REPO / "b.cs")): 2})

    def test_connections_come_from_the_graph(self):
        edges = [("Catalog.API.Model.CatalogType0.Run()", "Ordering.API.Apis.OrderingType0.Run()")]
        hierarchy = ClusteringService().build_full_hierarchy(analysis_for(graph("csharp", eshop(), edges)), max_depth=1)
        self.assertEqual([(c.source_group_id, c.target_group_id) for c in hierarchy.connections], [("2", "1")])
        self.assertEqual(hierarchy.connections[0].edges[0].call_sites, [{"file": "x.cs", "line": 1}])

    def test_materialized_groups_explain_every_file_placement(self):
        hierarchy = ClusteringService().build_full_hierarchy(analysis_for(graph("csharp", eshop())), max_depth=1)
        clustered_files = {
            file_path
            for clusters in hierarchy.leaf_clusters_by_language.values()
            for file_path in clusters.file_to_clusters
        }
        reasons = {file_path: reason for group in hierarchy.groups for file_path, reason in group.file_reasons.items()}

        self.assertEqual(set(reasons), clustered_files)
        self.assertTrue(all(reason for reason in reasons.values()))
        self.assertTrue(
            all(
                reason.startswith(("matches namespace prefix", "qualified names match", "falls under", "not claimed"))
                for reason in reasons.values()
            )
        )


class TestIncrementalHierarchy(unittest.TestCase):
    def setUp(self):
        self.layout = eshop()
        first = ClusteringService()
        baseline = first.build_full_hierarchy(analysis_for(graph("csharp", self.layout)), max_depth=2)
        self.spec = TreeSpec.from_dict(first.spec.to_dict())
        self.persisted = persisted_from(baseline)

    def _incremental(self, layout: dict[str, list[str]]) -> tuple[ClusteringService, ClusterScopeResult]:
        analysis = analysis_for(graph("csharp", layout))
        analysis.incremental_base_results = analysis_for(graph("csharp", self.layout))
        service = ClusteringService()
        hierarchy = service.build_incremental_hierarchy(
            analysis, 2, self.spec, self.persisted, REPO, REPO / ".codeboarding"
        )
        return service, hierarchy

    def test_data_only_files_a_layer_falls_back_to_are_not_a_new_scope(self):
        layout: dict[str, list[str]] = {}
        for layer in ("Application", "Domain", "Infrastructure"):
            for feature in ("Incidents", "Teams"):
                for index in range(2):
                    layout[f"src/Beacon.{layer}/{feature}/{feature}{index}.cs"] = [
                        f"Beacon.{layer}.{feature}.{feature}Thing{index}",
                        f"Beacon.{layer}.{feature}.{feature}Thing{index}.Run()",
                    ]
        layout["src/Beacon.Application/Constants/Limits.cs"] = ["Beacon.Application.Constants.Limits.max_var"]
        layout["src/Beacon.Application/Constants/Defaults.cs"] = ["Beacon.Application.Constants.Defaults.min_var"]
        first = ClusteringService()
        baseline = first.build_full_hierarchy(analysis_for(graph("csharp", layout)), max_depth=1)
        analysis = analysis_for(graph("csharp", layout))
        analysis.incremental_base_results = analysis_for(graph("csharp", layout))
        service = ClusteringService()
        hierarchy = service.build_incremental_hierarchy(
            analysis, 1, TreeSpec.from_dict(first.spec.to_dict()), persisted_from(baseline), REPO, REPO
        )
        root = scope_of(service.spec, ROOT_SCOPE_ID)
        self.assertEqual([rule.name for rule in root.rules if rule.origin == NEW_SCOPE], [])
        self.assertFalse(hierarchy_differs(hierarchy, persisted_from(baseline)))

    def test_unchanged_names_replay_to_the_same_components(self):
        _, hierarchy = self._incremental(self.layout)
        self.assertEqual(
            [(g.group_id, g.previous_component_id) for g in hierarchy.groups],
            [(g, g) for g in ["1", "2", "3", "4", "5"]],
        )
        self.assertFalse(hierarchy_differs(hierarchy, self.persisted))

    def test_a_new_file_inside_a_known_scope_joins_it(self):
        layout = self.layout | {"src/Catalog.API/Model/CatalogType99.cs": ["Catalog.API.Model.CatalogType99"]}
        service, hierarchy = self._incremental(layout)
        catalog = next(group for group in hierarchy.groups if group.group_id == "2")
        self.assertIn("Catalog.API.Model.CatalogType99", catalog.qualified_names)
        self.assertEqual(catalog.previous_component_id, "2")
        self.assertEqual(len(scope_of(service.spec, ROOT_SCOPE_ID).rules), 5)
        self.assertTrue(hierarchy_differs(hierarchy, self.persisted))

    def test_a_new_directory_becomes_a_new_component_with_a_fresh_id(self):
        service, hierarchy = self._incremental(self.layout | project("Shipping.API", 3))
        shipping = next(group for group in hierarchy.groups if group.previous_component_id == "")
        self.assertEqual(shipping.group_id, "6")
        self.assertEqual(len(shipping.qualified_names), 6)
        rule = rule_of(scope_of(service.spec, ROOT_SCOPE_ID), "6")
        self.assertEqual((rule.origin, rule.prefixes, rule.terms), (NEW_SCOPE, (("Shipping",),), ()))

    def test_a_single_new_file_nothing_claims_lands_in_the_bucket(self):
        service, hierarchy = self._incremental(self.layout | {"src/Shipping/Ship.cs": ["Shipping.Ship"]})
        root = scope_of(service.spec, ROOT_SCOPE_ID)
        assert root.unplaced_rule is not None
        bucket = next(group for group in hierarchy.groups if group.group_id == root.unplaced_rule.component_id)
        self.assertEqual(bucket.qualified_names, {"Shipping.Ship"})
        self.assertEqual([rule.origin for rule in root.rules if rule.origin == NEW_SCOPE], [])

    def test_a_retired_directory_leaves_no_group_and_no_rule(self):
        layout = {path: names for path, names in self.layout.items() if "Payment" not in path}
        self.assertIn("5", self.spec.scopes, "the baseline drafted the retired component's scope")
        service, hierarchy = self._incremental(layout)
        self.assertEqual([group.group_id for group in hierarchy.groups], ["1", "2", "3", "4"])
        self.assertTrue(hierarchy_differs(hierarchy, self.persisted))
        self.assertIsNone(scope_of(service.spec, ROOT_SCOPE_ID).rule("5"))
        self.assertNotIn("5", service.spec.scopes)

    def test_the_scopes_the_baseline_never_reached_are_drafted_by_the_grouper_that_drew_it(self):
        spec = TreeSpec.from_dict(self.spec.to_dict() | {"grouper": "kinship"})
        analysis = analysis_for(graph("csharp", self.layout))
        analysis.incremental_base_results = analysis_for(graph("csharp", self.layout))
        service = ClusteringService()
        service.build_incremental_hierarchy(analysis, 2, spec, self.persisted, REPO, REPO)
        self.assertEqual(service.grouper.name, "kinship")

    def test_a_baseline_without_a_call_graph_cannot_be_built_on(self):
        analysis = analysis_for(graph("csharp", self.layout))
        analysis.incremental_base_results = StaticAnalysisResults()
        with self.assertRaisesRegex(IncrementalCacheMissingError, "call graph"):
            ClusteringService().build_incremental_hierarchy(analysis, 2, self.spec, self.persisted, REPO, REPO)

    def test_a_new_directory_in_a_scope_drawn_from_words_is_still_a_new_component(self):
        """No rule owns a prefix here, so the new files must be keyed by where they leave the old units."""
        rules = [ComponentRule("1", "Sink", terms=("sink",)), ComponentRule("2", "Logger", terms=("logger",))]
        spec = TreeSpec(scopes={ROOT_SCOPE_ID: ScopeSpec(ROOT_SCOPE_ID, rules, rung="vocabulary")})
        old = {
            f"src/Serilog/Core/{name}.cs": [f"Serilog.Core.{name}", f"Serilog.Core.{name}.Emit()"]
            for name in ("FileSink", "ConsoleSink", "Logger", "LoggerConfiguration")
        }
        new = old | {
            f"src/Serilog/Sampling/{name}.cs": [f"Serilog.Sampling.{name}", f"Serilog.Sampling.{name}.IsEnabled()"]
            for name in ("LevelSampler", "SinkSampler", "LoggerSampler")
        }
        analysis = analysis_for(graph("csharp", new))
        analysis.incremental_base_results = analysis_for(graph("csharp", old))
        service = ClusteringService()
        hierarchy = service.build_incremental_hierarchy(analysis, 1, spec, {}, REPO, REPO)
        sampling = rule_of(scope_of(service.spec, ROOT_SCOPE_ID), "3")
        self.assertEqual((sampling.origin, sampling.prefixes), (NEW_SCOPE, (("Serilog", "Sampling"),)))
        self.assertEqual(len(next(group for group in hierarchy.groups if group.group_id == "3").qualified_names), 6)

    def test_a_new_directory_whose_names_vote_elsewhere_is_still_a_new_component(self):
        """Its names carry ``Order``, which the Ordering rule owns; the directory still wins."""
        layout = self.layout | {
            f"src/Shipping/OrderShipment{i}.cs": [f"Shipping.OrderShipment{i}", f"Shipping.OrderShipment{i}.Run()"]
            for i in range(3)
        }
        service, hierarchy = self._incremental(layout)
        shipping = next(group for group in hierarchy.groups if group.previous_component_id == "")
        self.assertEqual(len(shipping.qualified_names), 6)
        rule = rule_of(scope_of(service.spec, ROOT_SCOPE_ID), shipping.group_id)
        self.assertEqual((rule.origin, rule.prefixes), (NEW_SCOPE, (("Shipping",),)))

    def test_a_baseline_without_a_specification_cannot_be_built_on(self):
        analysis = analysis_for(graph("csharp", self.layout))
        analysis.incremental_base_results = StaticAnalysisResults()
        with self.assertRaisesRegex(IncrementalCacheMissingError, "tree specification"):
            ClusteringService().build_incremental_hierarchy(analysis, 2, TreeSpec(), self.persisted, REPO, REPO)

    def test_a_cold_static_analysis_cannot_be_built_on(self):
        with self.assertRaises(IncrementalCacheMissingError):
            ClusteringService().build_incremental_hierarchy(
                analysis_for(graph("csharp", self.layout)), 2, self.spec, self.persisted, REPO, REPO
            )


class TestScopeHierarchy(unittest.TestCase):
    def setUp(self):
        self.graph = graph("csharp", eshop())
        first = ClusteringService()
        self.hierarchy = first.build_full_hierarchy(analysis_for(self.graph), max_depth=1)
        self.spec = first.spec

    def _scope(self, component_id: str) -> ClusterScopeResult:
        return ClusteringService().build_scope_hierarchy({"csharp": self.graph}, 1, component_id, self.spec)

    def test_a_grouped_component_replays_its_parts(self):
        scope = self._scope("1")
        self.assertEqual([group.group_id for group in scope.groups], ["1.1", "1.2"])

    def test_a_partial_run_holds_the_units_a_full_run_placed_data_only_files_included(self):
        graph_with_consts = graph(
            "csharp", eshop() | {"src/Ordering.API/Apis/consts.cs": ["Ordering.API.Apis.limits_var"]}
        )
        first = ClusteringService()
        full = first.build_full_hierarchy(analysis_for(graph_with_consts), max_depth=2)
        partial = ClusteringService().build_scope_hierarchy({"csharp": graph_with_consts}, 1, "1", first.spec)
        assert full.groups[0].children is not None
        self.assertEqual(
            [(g.group_id, sorted(g.cluster_ids)) for g in partial.groups],
            [(g.group_id, sorted(g.cluster_ids)) for g in full.groups[0].children.groups],
        )

    def test_a_scope_the_planner_never_drew_is_not_drawn_by_another_grouper(self):
        spec = TreeSpec.from_dict(self.spec.to_dict() | {"grouper": "planner"})
        with self.assertRaisesRegex(PlannerUnavailableError, "never drafted"):
            ClusteringService().build_scope_hierarchy({"csharp": self.graph}, 2, "1", spec)

    def test_a_small_component_comes_back_without_groups(self):
        self.assertEqual(self._scope("4").groups, [])


class TestRerootIndexes(unittest.TestCase):
    def test_rejects_group_id_collision(self):
        child_scope = ClusterScopeResult(scope_id="1.1", groups=[ClusterGroup(group_id="1.1.2", cluster_ids=[2])])
        hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[
                ClusterGroup(group_id="1.1", cluster_ids=[1], children=child_scope),
                ClusterGroup(group_id="1.2", cluster_ids=[3]),
            ],
        )
        with self.assertRaisesRegex(ValueError, "duplicate clustering group ID '1.2'"):
            hierarchy.reroot_indexes(["1.1"])

    def test_rejects_scope_id_collision(self):
        hierarchy = ClusterScopeResult(scope_id="root")
        hierarchy.preclustered_scopes = {
            "1.1.2": ClusterScopeResult(scope_id="1.1.2"),
            "1.2": ClusterScopeResult(scope_id="1.2"),
        }
        with self.assertRaisesRegex(ValueError, "duplicate clustering scope ID '1.2'"):
            hierarchy.reroot_indexes(["1.1"])
