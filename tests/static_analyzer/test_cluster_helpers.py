import unittest
from unittest.mock import MagicMock

import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import (
    EdgeKind,
    ProgramMapEvidence,
    ProgramMapInformation,
    ProgramMapInformationError,
    ProgramMapInvalidWeightError,
    ProgramMapSnapshot,
    ProgramMapSnapshotError,
    ProgramMapSymbol,
    TOP_LEVEL_COMPONENTS_MAX,
    TOP_LEVEL_COMPONENTS_MIN,
    _build_meta_graph,
    _score_program_partition,
    analyze_program_map_flow,
    analyze_program_map_modules,
    analyze_program_map_packages,
    analyze_program_map_topology,
    assess_program_map_partition,
    build_all_cluster_results,
    build_program_map_information,
    build_program_map,
    build_program_map_for_languages,
    build_program_map_profiles,
    compare_program_map_partitions,
    program_map_projection,
    reconcile_program_map_lineage,
    reindex_across_languages,
    reindex_cluster_result,
    summarize_program_map_delta,
)
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


def _make_cluster_result(prefix: str, count: int) -> ClusterResult:
    clusters = {cluster_id: {f"{prefix}.node_{cluster_id}"} for cluster_id in range(1, count + 1)}
    cluster_to_files = {cluster_id: {f"/repo/{prefix}_{cluster_id}.py"} for cluster_id in range(1, count + 1)}
    file_to_clusters = {f"/repo/{prefix}_{cluster_id}.py": {cluster_id} for cluster_id in range(1, count + 1)}
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=file_to_clusters,
        strategy="test",
    )


def _profile_fixture() -> tuple[ClusterResult, nx.DiGraph, list[set[int]]]:
    clusters = {
        1: {"billing.entry"},
        2: {"billing.invoice"},
        3: {"shipping.dispatch"},
        4: {"shipping.delivery"},
    }
    cluster_to_files = {
        1: {"/repo/billing/entry.py"},
        2: {"/repo/billing/invoice.py"},
        3: {"/repo/shipping/dispatch.py"},
        4: {"/repo/shipping/delivery.py"},
    }
    graph = nx.DiGraph()
    for cluster_id, symbols in clusters.items():
        for symbol in symbols:
            graph.add_node(symbol, file_path=next(iter(cluster_to_files[cluster_id])))
    graph.add_edge("billing.entry", "billing.invoice", weight=3.0)
    graph.add_edge("billing.invoice", "shipping.dispatch", weight=2.0)
    graph.add_edge("shipping.dispatch", "shipping.delivery", weight=5.0)
    graph.add_edge("shipping.delivery", "shipping.dispatch", weight=1.0)
    graph.add_edge("shipping.delivery", "billing.entry", weight=7.0)
    return (
        ClusterResult(
            clusters=clusters,
            cluster_to_files=cluster_to_files,
            file_to_clusters={path: {cluster_id} for cluster_id, paths in cluster_to_files.items() for path in paths},
            strategy="test",
        ),
        graph,
        [{1, 2}, {3, 4}],
    )


def _typed_information_fixture():
    graph = nx.DiGraph()
    for name, file_path, line in (
        ("billing.entry", "/repo/billing/entry.py", 1),
        ("billing.invoice", "/repo/billing/invoice.py", 10),
        ("shipping.dispatch", "/repo/shipping/dispatch.py", 1),
        ("shipping.delivery", "/repo/shipping/delivery.py", 10),
        ("notifications.send", "/repo/notifications/send.py", 1),
    ):
        graph.add_node(name, type=int(NodeType.FUNCTION), file_path=file_path, line_start=line, line_end=line + 4)
    graph.add_edge(
        "billing.entry",
        "billing.invoice",
        evidence=(("call", 3, 1.0), ("contains", 1, 1.0)),
        weight=4.0,
    )
    graph.add_edge(
        "billing.invoice",
        "shipping.dispatch",
        evidence=(("call", 2, 1.0),),
        weight=2.0,
    )
    graph.add_edge(
        "shipping.dispatch",
        "shipping.delivery",
        evidence=(("inherits", 1, 1.0),),
        weight=1.25,
    )
    graph.add_edge(
        "shipping.delivery",
        "shipping.dispatch",
        evidence=(("typeref", 1, 1.0),),
        weight=0.5,
    )
    graph.add_edge(
        "shipping.delivery",
        "notifications.send",
        evidence=(("import", 1, 1.0),),
        weight=0.25,
    )
    return build_program_map_information(graph)


class TestClusterHelpers(unittest.TestCase):
    def test_multi_tech_stack_cluster_ids_are_reindexed_without_overlap(self):
        analysis = MagicMock(spec=StaticAnalysisResults)
        analysis.get_languages.return_value = ["python", "typescript"]

        python_cfg = MagicMock()
        typescript_cfg = MagicMock()
        python_cfg.cluster.return_value = _make_cluster_result("py", 40)
        typescript_cfg.cluster.return_value = _make_cluster_result("ts", 40)
        analysis.get_cfg.side_effect = lambda language: {
            "python": python_cfg,
            "typescript": typescript_cfg,
        }[language]

        result = build_all_cluster_results(analysis)

        python_ids = set(result["python"].clusters.keys())
        typescript_ids = set(result["typescript"].clusters.keys())
        self.assertEqual(python_ids, set(range(1, 41)))
        self.assertTrue(python_ids.isdisjoint(typescript_ids))
        self.assertEqual(len(typescript_ids), 40)

        shifted_ts_ids = set().union(*result["typescript"].file_to_clusters.values())
        self.assertEqual(shifted_ts_ids, typescript_ids)
        self.assertIs(python_cfg._cluster_cache, result["python"])
        self.assertIs(typescript_cfg._cluster_cache, result["typescript"])

    def test_all_clusters_survive_grouping(self):
        """Every leaf cluster keeps its members — nothing is merged away before grouping."""
        analysis = MagicMock(spec=StaticAnalysisResults)
        analysis.get_languages.return_value = ["python"]
        cfg = MagicMock()
        cfg.cluster.return_value = _make_cluster_result("py", 120)
        analysis.get_cfg.return_value = cfg

        result = build_all_cluster_results(analysis)

        self.assertEqual(len(result["python"].clusters), 120)

    def test_reindex_cluster_result_shifts_all_ids(self):
        shifted = reindex_cluster_result(_make_cluster_result("x", 3), 10)

        self.assertEqual(set(shifted.clusters.keys()), {11, 12, 13})
        self.assertEqual(set(shifted.cluster_to_files.keys()), {11, 12, 13})
        for file_ids in shifted.file_to_clusters.values():
            self.assertTrue(file_ids.issubset({11, 12, 13}))

    def test_reindex_across_languages_makes_ids_disjoint(self):
        cluster_results = {
            "javascript": _make_cluster_result("js", 10),
            "python": _make_cluster_result("py", 10),
        }

        reindex_across_languages(cluster_results)

        js_ids = set(cluster_results["javascript"].clusters.keys())
        py_ids = set(cluster_results["python"].clusters.keys())
        self.assertTrue(js_ids.isdisjoint(py_ids), f"Overlap detected: {js_ids & py_ids}")
        self.assertEqual(len(js_ids) + len(py_ids), 20)

    def test_reindex_across_languages_leaves_already_disjoint_ids_alone(self):
        # The seeded incremental path returns the previous run's scoped ids, already
        # disjoint across languages. Re-offsetting them would drift the namespace each run.
        py = _make_cluster_result("py", 3)  # ids 1-3
        ts = ClusterResult(
            clusters={cid: {f"ts.node_{cid}"} for cid in (30, 31, 32)},
            cluster_to_files={cid: {f"/repo/ts_{cid}.ts"} for cid in (30, 31, 32)},
            file_to_clusters={f"/repo/ts_{cid}.ts": {cid} for cid in (30, 31, 32)},
            strategy="test",
        )
        cluster_results = {"python": py, "typescript": ts}

        reindex_across_languages(cluster_results)

        self.assertIs(cluster_results["python"], py)
        self.assertIs(cluster_results["typescript"], ts)
        self.assertEqual(set(cluster_results["typescript"].clusters), {30, 31, 32})

    def test_reindex_across_languages_noop_for_single_language(self):
        cr = _make_cluster_result("py", 10)
        cluster_results = {"python": cr}

        reindex_across_languages(cluster_results)

        self.assertIs(cluster_results["python"], cr)
        self.assertEqual(set(cr.clusters.keys()), set(range(1, 11)))


class TestProgramMap(unittest.TestCase):
    @staticmethod
    def _blocks(n_blocks: int, per_block: int, weak_bridges: bool = True):
        """A ClusterResult + meta-friendly cfg with ``n_blocks`` tight blocks of leaf clusters."""
        clusters, cluster_to_files, file_to_clusters = {}, {}, {}
        graph = nx.DiGraph()
        n = n_blocks * per_block
        for cid in range(1, n + 1):
            block = (cid - 1) // per_block
            nodes = [f"n{cid}_{j}" for j in range(3)]
            clusters[cid] = set(nodes)
            path = f"/repo/block{block}/c{cid}.py"
            cluster_to_files[cid] = {path}
            file_to_clusters[path] = {cid}
            for node in nodes:
                graph.add_node(node, file_path=path)
        # Dense calls within a block, so each block is a tight community.
        for cid in range(1, n + 1):
            block = (cid - 1) // per_block
            for other in range(1, n + 1):
                if other != cid and (other - 1) // per_block == block:
                    graph.add_edge(f"n{cid}_0", f"n{other}_1")
        if weak_bridges:
            for block in range(n_blocks - 1):
                graph.add_edge(f"n{block * per_block + 1}_0", f"n{(block + 1) * per_block + 1}_1")
        cr = ClusterResult(
            clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
        )
        return cr, graph

    def _assert_partition(self, groups, expected_ids):
        assigned = [cid for group in groups for cid in group]
        self.assertEqual(sorted(assigned), sorted(expected_ids))
        self.assertEqual(len(assigned), len(set(assigned)), "clusters must be partitioned, not shared")

    def test_recovers_natural_block_count_within_range(self):
        cr, graph = self._blocks(n_blocks=6, per_block=5)
        program_map = build_program_map(cr, graph)
        self.assertEqual(len(program_map.groups), 6)
        self.assertGreater(program_map.compression, 0.4)
        self._assert_partition(program_map.groups, range(1, 31))

    def test_count_is_clamped_to_range(self):
        # 10 natural blocks, but the count must not exceed the max.
        cr, graph = self._blocks(n_blocks=10, per_block=4)
        groups = build_program_map(cr, graph).groups
        self.assertTrue(TOP_LEVEL_COMPONENTS_MIN <= len(groups) <= TOP_LEVEL_COMPONENTS_MAX)
        self._assert_partition(groups, range(1, 41))

    def test_count_clamping_keeps_infomap_modules_atomic(self):
        cr, graph = self._blocks(n_blocks=10, per_block=4)

        program_map = build_program_map(cr, graph)
        natural_modules: dict[tuple[int, ...], set[int]] = {}
        for cluster_id, path in program_map.module_paths.items():
            natural_modules.setdefault(path, set()).add(cluster_id)

        for module in natural_modules.values():
            self.assertTrue(any(module <= group for group in program_map.groups))

    def test_deterministic_across_runs(self):
        cr, graph = self._blocks(n_blocks=7, per_block=4)
        first = build_program_map(cr, graph)
        second = build_program_map(cr, graph)
        self.assertEqual(sorted(map(sorted, first.groups)), sorted(map(sorted, second.groups)))
        self.assertEqual(first.codelength, second.codelength)

    def test_fewer_clusters_than_floor_returns_singletons(self):
        cr, graph = self._blocks(n_blocks=1, per_block=3, weak_bridges=False)
        groups = build_program_map(cr, graph).groups
        self.assertEqual(len(groups), 3)
        self._assert_partition(groups, range(1, 4))

    def test_isolated_clusters_are_spread_not_piled_onto_one_seed(self):
        # No inter-cluster edges at all: every cluster is a leftover, so only directory
        # affinity and seed size can place them. The old file-overlap rule grew one seed's
        # package set as it absorbed, which made that seed win every later comparison and
        # swallow the repo.
        clusters = {cid: {f"n{cid}"} for cid in range(1, 41)}
        cluster_to_files = {cid: {f"/repo/dir{(cid - 1) // 8}/m{cid}.py"} for cid in range(1, 41)}
        file_to_clusters: dict[str, set[int]] = {}
        for cid, files in cluster_to_files.items():
            for path in files:
                file_to_clusters.setdefault(path, set()).add(cid)
        cr = ClusterResult(
            clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
        )
        graph = nx.DiGraph()
        for cid in range(1, 41):
            graph.add_node(f"n{cid}", file_path=next(iter(cluster_to_files[cid])))

        groups = build_program_map(cr, graph).groups

        self.assertEqual(len(groups), TOP_LEVEL_COMPONENTS_MIN)
        self._assert_partition(groups, range(1, 41))
        biggest = max(len(group) for group in groups)
        self.assertLessEqual(biggest, 12, f"one seed absorbed {biggest}/40 clusters: {sorted(map(len, groups))}")

    def test_large_isolated_cluster_becomes_its_own_component(self):
        # A connected core plus one big, call-isolated module (e.g. a data-model
        # file nothing calls). The big module must stay its own component, not be
        # folded into a seed.
        cr, graph = self._blocks(n_blocks=4, per_block=3)
        big_id = 999
        cr.clusters[big_id] = {f"models.Model{i}" for i in range(60)}  # 60 methods, no call edges
        cr.cluster_to_files[big_id] = {"/repo/models/schema.py"}
        cr.file_to_clusters["/repo/models/schema.py"] = {big_id}
        graph.add_node("models.Model0", file_path="/repo/models/schema.py")

        groups = build_program_map(cr, graph).groups
        owner = next(group for group in groups if big_id in group)
        # It seeded its own group rather than being absorbed into a larger one.
        self.assertEqual(owner, {big_id})

    def test_leaf_ids_combines_languages_into_single_budget(self):
        cr_py, graph = self._blocks(n_blocks=6, per_block=5)
        # Split the same clusters/graph across two languages by cluster id.
        py_clusters = {cid: cr_py.clusters[cid] for cid in range(1, 16)}
        js_clusters = {cid: cr_py.clusters[cid] for cid in range(16, 31)}
        py = ClusterResult(clusters=py_clusters, cluster_to_files=cr_py.cluster_to_files, strategy="t")
        js = ClusterResult(clusters=js_clusters, cluster_to_files=cr_py.cluster_to_files, strategy="t")
        groups = build_program_map_for_languages(
            {"python": py, "javascript": js}, {"python": graph, "javascript": graph}
        ).groups
        self.assertTrue(TOP_LEVEL_COMPONENTS_MIN <= len(groups) <= TOP_LEVEL_COMPONENTS_MAX)
        self._assert_partition(groups, range(1, 31))

    def test_compression_is_zero_without_inter_cluster_edges(self):
        cr, graph = self._blocks(n_blocks=6, per_block=5, weak_bridges=False)
        # Strip every edge so the meta-graph has nothing to separate.
        graph = nx.DiGraph()
        for cid, members in cr.clusters.items():
            for member in members:
                graph.add_node(member, file_path=next(iter(cr.cluster_to_files[cid])))
        program_map = build_program_map(cr, graph)
        self.assertEqual(program_map.compression, 0.0)

    def test_program_map_exposes_normalized_flow_and_hierarchy(self):
        cr, graph = self._blocks(n_blocks=6, per_block=5)

        program_map = build_program_map(cr, graph)

        self.assertAlmostEqual(sum(program_map.node_flow.values()), 1.0)
        self.assertEqual(set(program_map.module_paths), set(cr.clusters))
        self.assertGreaterEqual(program_map.hierarchy_levels, 1)

    def test_published_quality_scores_the_bounded_groups(self):
        cr, graph = self._blocks(n_blocks=10, per_block=4)

        program_map = build_program_map(cr, graph)
        expected_codelength, expected_compression = _score_program_partition(
            _build_meta_graph(cr, graph), program_map.groups, 42
        )

        self.assertAlmostEqual(program_map.codelength, expected_codelength)
        self.assertAlmostEqual(program_map.compression, expected_compression)

    def test_small_directed_graph_still_uses_infomap_flow(self):
        cr, graph = self._blocks(n_blocks=1, per_block=3, weak_bridges=False)
        graph.remove_edges_from(list(graph.edges))
        graph.add_edge("n1_0", "n2_0", weight=20.0)
        graph.add_edge("n2_0", "n3_0", weight=1.0)
        graph.add_edge("n3_0", "n2_0", weight=1.0)

        program_map = build_program_map(cr, graph)

        self.assertEqual(len(program_map.groups), 3)
        self.assertEqual(len(set(round(flow, 6) for flow in program_map.node_flow.values())), 3)

    def test_graph_insertion_order_does_not_change_the_program_map(self):
        cr, graph = self._blocks(n_blocks=6, per_block=5)
        reversed_graph = nx.DiGraph()
        reversed_graph.add_nodes_from(reversed(list(graph.nodes(data=True))))
        reversed_graph.add_edges_from(reversed(list(graph.edges(data=True))))

        first = build_program_map(cr, graph)
        second = build_program_map(cr, reversed_graph)

        self.assertEqual(first.groups, second.groups)
        self.assertEqual(first.codelength, second.codelength)


class TestProgramMapProfiles(unittest.TestCase):
    def test_profiles_keep_the_exact_fitted_group_identity(self):
        cluster_result, graph, groups = _profile_fixture()

        profiles = build_program_map_profiles(cluster_result, graph, groups)

        self.assertEqual([profile.cluster_ids for profile in profiles], [(1, 2), (3, 4)])
        self.assertEqual(profiles[0].symbols, ("billing.entry", "billing.invoice"))
        self.assertEqual(profiles[1].files, ("/repo/shipping/delivery.py", "/repo/shipping/dispatch.py"))
        self.assertEqual(profiles[0].packages, ("/repo/billing",))

    def test_profiles_measure_directed_internal_and_crossing_flow(self):
        cluster_result, graph, groups = _profile_fixture()

        billing, shipping = build_program_map_profiles(cluster_result, graph, groups)

        self.assertEqual((billing.internal_flow, billing.incoming_flow, billing.outgoing_flow), (3.0, 7.0, 2.0))
        self.assertEqual((shipping.internal_flow, shipping.incoming_flow, shipping.outgoing_flow), (6.0, 2.0, 7.0))
        self.assertAlmostEqual(billing.cohesion, 0.25)
        self.assertAlmostEqual(billing.coupling, 0.75)

    def test_profiles_rank_boundary_entries_exits_and_hubs_by_weight(self):
        cluster_result, graph, groups = _profile_fixture()

        billing, shipping = build_program_map_profiles(cluster_result, graph, groups)

        self.assertEqual(billing.entries, ("billing.entry",))
        self.assertEqual(billing.exits, ("billing.invoice",))
        self.assertEqual(billing.hubs, ("billing.entry", "billing.invoice"))
        self.assertEqual(billing.boundary_symbols, ("billing.entry", "billing.invoice"))
        self.assertEqual(shipping.entries, ("shipping.dispatch",))
        self.assertEqual(shipping.exits, ("shipping.delivery",))

    def test_profiles_report_cross_group_flow_in_each_direction(self):
        cluster_result, graph, groups = _profile_fixture()

        billing, shipping = build_program_map_profiles(cluster_result, graph, groups)

        self.assertEqual(billing.outgoing_groups[0].group_id, 1)
        self.assertEqual(billing.outgoing_groups[0].weight, 2.0)
        self.assertEqual(billing.incoming_groups[0].group_id, 1)
        self.assertEqual(billing.incoming_groups[0].weight, 7.0)
        self.assertEqual(shipping.incoming_groups[0].weight, 2.0)
        self.assertEqual(shipping.outgoing_groups[0].weight, 7.0)

    def test_profiles_report_internal_cycle_and_dependency_depth(self):
        cluster_result, graph, groups = _profile_fixture()

        billing, shipping = build_program_map_profiles(cluster_result, graph, groups)

        self.assertEqual(billing.strongly_connected_regions, 2)
        self.assertEqual(billing.cyclic_regions, 0)
        self.assertEqual(billing.maximum_dependency_depth, 1)
        self.assertEqual(shipping.strongly_connected_regions, 1)
        self.assertEqual(shipping.cyclic_regions, 1)
        self.assertEqual(shipping.maximum_dependency_depth, 0)

    def test_profiles_have_zero_safe_metrics_when_no_edges_touch_a_group(self):
        cluster_result, graph, groups = _profile_fixture()
        graph.remove_edges_from(list(graph.edges))

        profiles = build_program_map_profiles(cluster_result, graph, groups)

        for profile in profiles:
            self.assertEqual(profile.internal_flow, 0.0)
            self.assertEqual(profile.coupling, 0.0)
            self.assertEqual(profile.flow_entropy, 0.0)
            self.assertEqual(profile.flow_concentration, 0.0)
            self.assertFalse(profile.entries)
            self.assertFalse(profile.exits)

    def test_profiles_ignore_edges_outside_the_cluster_scope(self):
        cluster_result, graph, groups = _profile_fixture()
        graph.add_node("outside.helper")
        graph.add_edge("billing.entry", "outside.helper", weight=100.0)
        graph.add_edge("outside.helper", "shipping.dispatch", weight=100.0)

        billing, shipping = build_program_map_profiles(cluster_result, graph, groups)

        self.assertEqual((billing.incoming_flow, billing.outgoing_flow), (7.0, 2.0))
        self.assertEqual((shipping.incoming_flow, shipping.outgoing_flow), (2.0, 7.0))

    def test_profiles_reject_non_partitioned_groups(self):
        cluster_result, graph, _ = _profile_fixture()

        with self.assertRaisesRegex(ValueError, "share clusters"):
            build_program_map_profiles(cluster_result, graph, [{1, 2}, {2, 3, 4}])
        with self.assertRaisesRegex(ValueError, "omit clusters"):
            build_program_map_profiles(cluster_result, graph, [{1, 2}, {3}])
        with self.assertRaisesRegex(ValueError, "unknown clusters"):
            build_program_map_profiles(cluster_result, graph, [{1, 2}, {3, 4, 99}])

    def test_profiles_reject_invalid_flow_weights(self):
        cluster_result, graph, groups = _profile_fixture()
        graph["billing.entry"]["billing.invoice"]["weight"] = float("nan")

        with self.assertRaisesRegex(ValueError, "invalid weight"):
            build_program_map_profiles(cluster_result, graph, groups)

    def test_profiles_are_deterministic_when_graph_order_changes(self):
        cluster_result, graph, groups = _profile_fixture()
        reversed_graph = nx.DiGraph()
        reversed_graph.add_nodes_from(reversed(list(graph.nodes(data=True))))
        reversed_graph.add_edges_from(reversed(list(graph.edges(data=True))))

        self.assertEqual(
            build_program_map_profiles(cluster_result, graph, groups),
            build_program_map_profiles(cluster_result, reversed_graph, groups),
        )

    def test_program_map_exposes_profiles_for_each_fitted_group(self):
        cluster_result, graph, _ = _profile_fixture()

        program_map = build_program_map(cluster_result, graph, low=2, high=2)

        self.assertEqual(len(program_map.profiles), len(program_map.groups))
        for group in program_map.groups:
            self.assertEqual(program_map.group_profile(group).cluster_ids, tuple(sorted(group)))
        with self.assertRaisesRegex(KeyError, "Unknown fitted"):
            program_map.group_profile({99})


class TestProgramMapInformation(unittest.TestCase):
    def test_symbols_reject_missing_names_and_invalid_source_locations(self):
        with self.assertRaisesRegex(ProgramMapInformationError, "qualified name"):
            ProgramMapSymbol("", int(NodeType.FUNCTION), "/repo/module.py", 1, 2)
        with self.assertRaisesRegex(ProgramMapInformationError, "invalid location"):
            ProgramMapSymbol("module.bad", int(NodeType.FUNCTION), "/repo/module.py", 4, 3)
        with self.assertRaisesRegex(ProgramMapInformationError, "invalid location"):
            ProgramMapSymbol("module.bad_column", int(NodeType.FUNCTION), "/repo/module.py", 1, 2, -1)

    def test_evidence_rejects_non_integer_counts_and_non_finite_weights(self):
        for count, weight in ((True, 1.0), (1, float("nan")), (1, float("inf"))):
            with self.subTest(count=count, weight=weight), self.assertRaises(ProgramMapInvalidWeightError):
                ProgramMapEvidence("module.source", "module.destination", EdgeKind.CALL, count, weight)

    def test_information_rejects_duplicate_channel_evidence_that_would_corrupt_snapshot_keys(self):
        symbols = (
            ProgramMapSymbol("module.source", int(NodeType.FUNCTION), "/repo/module.py", 1, 2),
            ProgramMapSymbol("module.destination", int(NodeType.FUNCTION), "/repo/module.py", 3, 4),
        )
        evidence = (
            ProgramMapEvidence("module.source", "module.destination", EdgeKind.CALL, 1, 1.0),
            ProgramMapEvidence("module.source", "module.destination", EdgeKind.CALL, 2, 1.0),
        )

        with self.assertRaisesRegex(ProgramMapInformationError, "duplicate typed evidence"):
            ProgramMapInformation(symbols, evidence)

    def test_decodes_typed_evidence_with_channel_weights(self):
        information = _typed_information_fixture()

        self.assertEqual(len(information.symbols), 5)
        self.assertEqual(len(information.evidence), 6)
        weights = {(item.source, item.destination, item.channel): item.weighted_value for item in information.evidence}
        self.assertEqual(weights["billing.entry", "billing.invoice", EdgeKind.CALL], 3.0)
        self.assertEqual(weights["billing.entry", "billing.invoice", EdgeKind.CONTAINS], 1.0)
        self.assertEqual(weights["shipping.dispatch", "shipping.delivery", EdgeKind.INHERITS], 1.25)
        self.assertEqual(weights["shipping.delivery", "shipping.dispatch", EdgeKind.TYPEREF], 0.5)
        self.assertEqual(weights["shipping.delivery", "notifications.send", EdgeKind.IMPORT], 0.25)

    def test_statistics_distinguish_graph_edges_from_evidence(self):
        statistics = _typed_information_fixture().statistics

        self.assertEqual(statistics.symbol_count, 5)
        self.assertEqual(statistics.edge_count, 5)
        self.assertEqual(statistics.evidence_count, 6)
        self.assertAlmostEqual(statistics.total_weight, 8.0)
        self.assertEqual(dict(statistics.channel_counts)[EdgeKind.CALL], 2)
        self.assertEqual(statistics.isolated_symbols, 0)

    def test_symbol_queries_filter_typed_evidence(self):
        information = _typed_information_fixture()

        self.assertEqual(
            information.outgoing("billing.entry", {EdgeKind.CALL})[0].channel,
            EdgeKind.CALL,
        )
        self.assertEqual(
            information.outgoing("billing.entry", {EdgeKind.CONTAINS})[0].channel,
            EdgeKind.CONTAINS,
        )
        self.assertEqual(information.incoming("billing.invoice")[0].source, "billing.entry")
        with self.assertRaisesRegex(KeyError, "Unknown program-map symbol"):
            information.outgoing("missing.symbol")

    def test_symbol_profiles_separate_callers_from_structural_neighbours(self):
        profiles = {profile.qualified_name: profile for profile in _typed_information_fixture().symbol_profiles()}

        invoice = profiles["billing.invoice"]
        self.assertEqual(invoice.caller_count, 1)
        self.assertEqual(invoice.callee_count, 1)
        self.assertEqual(invoice.structural_neighbor_count, 1)
        self.assertEqual(dict(invoice.incoming_channels)[EdgeKind.CALL], 3.0)
        self.assertEqual(dict(invoice.incoming_channels)[EdgeKind.CONTAINS], 1.0)

    def test_channel_analysis_keeps_occurrences_and_weighted_flow_separate(self):
        channels = _typed_information_fixture().channel_analysis()

        calls = channels.profile(EdgeKind.CALL)
        self.assertEqual(calls.evidence_count, 2)
        self.assertEqual(calls.occurrence_count, 5)
        self.assertEqual(calls.weighted_total, 5.0)
        self.assertEqual(calls.source_count, 2)
        self.assertEqual(calls.destination_count, 2)
        self.assertEqual(calls.top_sources[0], ("billing.entry", 3.0))

    def test_channel_analysis_reports_reciprocity_and_self_reference_without_double_counting(self):
        information = _typed_information_fixture()
        graph = program_map_projection(information)
        graph.add_edge(
            "shipping.dispatch",
            "shipping.delivery",
            evidence=(("typeref", 1, 1.0),),
            weight=0.5,
        )
        graph.add_edge(
            "notifications.send",
            "notifications.send",
            evidence=(("import", 1, 1.0),),
            weight=0.25,
        )

        channels = build_program_map_information(graph).channel_analysis()

        self.assertEqual(channels.profile(EdgeKind.TYPEREF).reciprocal_pair_count, 1)
        self.assertEqual(channels.profile(EdgeKind.IMPORT).self_reference_count, 1)
        self.assertEqual(channels.profile(EdgeKind.IMPORT).reciprocal_pair_count, 0)

    def test_channel_analysis_exposes_unreferenced_symbols_and_coverage(self):
        graph = program_map_projection(_typed_information_fixture())
        graph.add_node("reporting.dashboard", type=int(NodeType.FUNCTION), file_path="/repo/reporting/dashboard.py")

        channels = build_program_map_information(graph).channel_analysis()

        self.assertEqual(channels.unreferenced_symbols, ("reporting.dashboard",))
        self.assertAlmostEqual(channels.typed_symbol_coverage, 5 / 6)
        self.assertEqual({profile.channel for profile in channels.profiles}, set(EdgeKind))

    def test_channel_analysis_rejects_non_positive_ranking_limits(self):
        with self.assertRaisesRegex(ValueError, "channel limit"):
            _typed_information_fixture().channel_analysis(0)

    def test_flow_reports_internal_crossing_entropy_and_channel_mix(self):
        information = _typed_information_fixture()

        flow = analyze_program_map_flow(information, {"billing.entry", "billing.invoice"})

        self.assertEqual(flow.internal_weight, 4.0)
        self.assertEqual(flow.crossing_weight, 2.0)
        self.assertAlmostEqual(flow.internal_ratio, 4 / 6)
        self.assertGreater(flow.entropy, 0.0)
        self.assertGreater(flow.concentration, 0.0)
        self.assertEqual(dict(flow.channel_mix)[EdgeKind.CALL], 5.0)
        self.assertEqual(dict(flow.channel_mix)[EdgeKind.CONTAINS], 1.0)

    def test_flow_rejects_unknown_symbols_and_invalid_limits(self):
        information = _typed_information_fixture()

        with self.assertRaisesRegex(KeyError, "Unknown program-map symbols"):
            analyze_program_map_flow(information, {"missing.symbol"})
        with self.assertRaisesRegex(ValueError, "flow limit"):
            analyze_program_map_flow(information, {"billing.entry"}, limit=0)

    def test_topology_reports_cycles_layers_sources_and_sinks(self):
        information = _typed_information_fixture()

        topology = analyze_program_map_topology(
            information,
            {"billing.entry", "billing.invoice", "shipping.dispatch", "shipping.delivery", "notifications.send"},
        )

        cycle = next(
            region for region in topology.regions if set(region.members) == {"shipping.dispatch", "shipping.delivery"}
        )
        self.assertTrue(cycle.cyclic)
        self.assertEqual(topology.sources, ("billing.entry",))
        self.assertEqual(topology.sinks, ("notifications.send",))
        self.assertGreaterEqual(topology.maximum_depth, 2)

    def test_module_analysis_preserves_cross_module_channels(self):
        information = _typed_information_fixture()
        partition = {
            10: {"billing.entry", "billing.invoice"},
            20: {"shipping.dispatch", "shipping.delivery"},
            30: {"notifications.send"},
        }

        analysis = analyze_program_map_modules(information, partition)

        billing = analysis.profiles[0]
        self.assertEqual(billing.module_id, 10)
        self.assertEqual(billing.entry_symbols, ())
        self.assertEqual(billing.exit_symbols, ("billing.invoice",))
        self.assertAlmostEqual(billing.flow.internal_weight, 4.0)
        shipping_to_notification = next(flow for flow in analysis.inter_module_flow if flow.destination_module == 30)
        self.assertEqual(dict(shipping_to_notification.channels)[EdgeKind.IMPORT], 0.25)

    def test_module_analysis_rejects_invalid_or_incomplete_covers(self):
        information = _typed_information_fixture()

        with self.assertRaisesRegex(ProgramMapInformationError, "duplicate"):
            analyze_program_map_modules(
                information,
                {1: {"billing.entry", "billing.invoice"}, 2: {"billing.invoice", "shipping.dispatch"}},
                exact=False,
            )
        with self.assertRaisesRegex(ProgramMapInformationError, "omits"):
            analyze_program_map_modules(information, {1: {"billing.entry"}})
        with self.assertRaisesRegex(ProgramMapInformationError, "unknown"):
            analyze_program_map_modules(
                information, {1: {symbol.qualified_name for symbol in information.symbols} | {"x"}}
            )

    def test_package_analysis_derives_exact_source_tree_membership(self):
        analysis = analyze_program_map_packages(_typed_information_fixture())

        self.assertEqual(
            [profile.package for profile in analysis.profiles],
            ["/repo/billing", "/repo/notifications", "/repo/shipping"],
        )
        billing = analysis.profile("/repo/billing")
        self.assertEqual(billing.symbols, ("billing.entry", "billing.invoice"))
        self.assertEqual(billing.files, ("/repo/billing/entry.py", "/repo/billing/invoice.py"))
        self.assertEqual((billing.flow.internal_weight, billing.flow.crossing_weight), (4.0, 2.0))
        self.assertEqual(billing.exit_symbols, ("billing.invoice",))

    def test_package_analysis_preserves_each_cross_package_evidence_channel(self):
        analysis = analyze_program_map_packages(_typed_information_fixture())

        flow = next(
            item
            for item in analysis.inter_package_flow
            if (item.source_package, item.destination_package) == ("/repo/billing", "/repo/shipping")
        )
        self.assertEqual((flow.weight, flow.channels), (2.0, ((EdgeKind.CALL, 2.0),)))
        import_flow = next(
            item
            for item in analysis.inter_package_flow
            if (item.source_package, item.destination_package) == ("/repo/shipping", "/repo/notifications")
        )
        self.assertEqual((import_flow.weight, import_flow.channels), (0.25, ((EdgeKind.IMPORT, 0.25),)))

    def test_package_analysis_keeps_isolated_packages_visible_to_the_agent_layer(self):
        graph = program_map_projection(_typed_information_fixture())
        graph.add_node("reporting.dashboard", type=int(NodeType.FUNCTION), file_path="/repo/reporting/dashboard.py")

        analysis = analyze_program_map_packages(build_program_map_information(graph))

        reporting = analysis.profile("/repo/reporting")
        self.assertEqual(reporting.symbols, ("reporting.dashboard",))
        self.assertEqual(reporting.flow.total_weight, 0.0)
        self.assertEqual(reporting.topology.sources, ("reporting.dashboard",))
        self.assertEqual(reporting.topology.sinks, ("reporting.dashboard",))

    def test_package_analysis_validates_its_ranking_bound(self):
        with self.assertRaisesRegex(ValueError, "package limit"):
            analyze_program_map_packages(_typed_information_fixture(), 0)

    def test_projection_round_trips_exact_symbols_and_evidence(self):
        information = _typed_information_fixture()

        round_tripped = build_program_map_information(program_map_projection(information))

        self.assertEqual(round_tripped, information)
        self.assertEqual(round_tripped.snapshot(), information.snapshot())

    def test_snapshots_are_stable_for_identical_information(self):
        information = _typed_information_fixture()

        first = information.snapshot()
        second = _typed_information_fixture().snapshot()

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertTrue(first.compare(second).is_empty)

    def test_snapshot_json_round_trips_a_complete_typed_program_map(self):
        snapshot = _typed_information_fixture().snapshot()

        restored = ProgramMapSnapshot.from_json(snapshot.to_json())

        self.assertEqual(restored, snapshot)
        self.assertEqual(restored.to_payload(), snapshot.to_payload())

    def test_snapshot_rejects_stale_fingerprints_and_unknown_payload_content(self):
        payload = _typed_information_fixture().snapshot().to_payload()
        payload["fingerprint"] = "stale"

        with self.assertRaisesRegex(ProgramMapSnapshotError, "fingerprint"):
            ProgramMapSnapshot.from_payload(payload)

        payload = _typed_information_fixture().snapshot().to_payload()
        payload["unexpected"] = True
        with self.assertRaisesRegex(ProgramMapSnapshotError, "fingerprint"):
            ProgramMapSnapshot.from_payload(payload)

    def test_snapshot_rejects_incomplete_and_wrongly_typed_payloads(self):
        payload = _typed_information_fixture().snapshot().to_payload()
        payload["symbols"] = "not-a-list"

        with self.assertRaisesRegex(ProgramMapSnapshotError, "symbols"):
            ProgramMapSnapshot.from_payload(payload)

        with self.assertRaisesRegex(ProgramMapSnapshotError, "valid JSON"):
            ProgramMapSnapshot.from_json("{")
        with self.assertRaisesRegex(ProgramMapSnapshotError, "JSON object"):
            ProgramMapSnapshot.from_json("[]")
        with self.assertRaisesRegex(ProgramMapSnapshotError, "format"):
            ProgramMapSnapshot.from_payload({})

    def test_snapshot_rejects_unknown_evidence_channels_before_cache_use(self):
        payload = _typed_information_fixture().snapshot().to_payload()
        evidence = list(payload["evidence"])
        evidence[0] = {**evidence[0], "channel": "runtime_magic"}
        payload["evidence"] = evidence

        with self.assertRaisesRegex(ProgramMapSnapshotError, "unknown channel"):
            ProgramMapSnapshot.from_payload(payload)

    def test_snapshot_delta_classifies_symbol_and_evidence_changes(self):
        old = _typed_information_fixture()
        graph = program_map_projection(old)
        graph.remove_node("notifications.send")
        graph.add_node("notifications.queue", type=int(NodeType.FUNCTION), file_path="/repo/notifications/queue.py")
        graph.add_edge("shipping.delivery", "notifications.queue", evidence=(("import", 2, 1.0),), weight=0.5)
        new = build_program_map_information(graph)

        delta = old.snapshot().compare(new.snapshot())

        self.assertEqual(delta.added_symbols, ("notifications.queue",))
        self.assertEqual(delta.removed_symbols, ("notifications.send",))
        self.assertIn(("shipping.delivery", "notifications.queue", EdgeKind.IMPORT), delta.added_evidence)
        self.assertIn(("shipping.delivery", "notifications.send", EdgeKind.IMPORT), delta.removed_evidence)
        self.assertFalse(delta.is_empty)

    def test_delta_summary_traces_bounded_bidirectional_impact(self):
        old = _typed_information_fixture()
        graph = program_map_projection(old)
        graph["billing.invoice"]["shipping.dispatch"]["evidence"] = (("call", 5, 1.0),)
        graph["billing.invoice"]["shipping.dispatch"]["weight"] = 5.0
        new = build_program_map_information(graph)

        summary = summarize_program_map_delta(old.snapshot().compare(new.snapshot()), old, new, max_depth=2, limit=4)

        self.assertEqual(summary.changed_evidence_count, 1)
        self.assertEqual(summary.changed_channels, (EdgeKind.CALL,))
        self.assertTrue(summary.impacted_symbols)
        self.assertEqual(summary.surface.file_count, 2)
        self.assertEqual(summary.surface.package_count, 2)
        self.assertEqual(summary.surface.affected_packages, ("/repo/billing", "/repo/shipping"))
        self.assertEqual(summary.surface.channel_weight_delta, ((EdgeKind.CALL, 3.0),))
        self.assertIn("surface: /repo/billing, /repo/shipping", summary.llm_str())
        self.assertIn("program-map delta", summary.llm_str())

    def test_delta_surface_keeps_removed_symbol_locations_after_the_new_map_drops_them(self):
        old = _typed_information_fixture()
        graph = program_map_projection(old)
        graph.remove_node("notifications.send")
        new = build_program_map_information(graph)

        summary = summarize_program_map_delta(old.snapshot().compare(new.snapshot()), old, new)

        self.assertEqual(summary.surface.file_count, 2)
        self.assertEqual(summary.surface.package_count, 2)
        self.assertIn("/repo/notifications/send.py", summary.surface.affected_files)
        self.assertIn("/repo/shipping/delivery.py", summary.surface.affected_files)

    def test_delta_summary_enforces_non_negative_bounds(self):
        information = _typed_information_fixture()
        delta = information.snapshot().compare(information.snapshot())

        for kwargs in ({"max_depth": -1}, {"limit": -1}, {"symbol_limit": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "bounds"):
                summarize_program_map_delta(delta, information, information, **kwargs)

    def test_call_graph_projection_keeps_each_reference_channel(self):
        graph = CallGraph()
        graph.add_node(Node("module.caller", NodeType.FUNCTION, "/repo/module.py", 1, 10))
        graph.add_node(Node("module.target", NodeType.FUNCTION, "/repo/module.py", 20, 25))
        graph.add_edge("module.caller", "module.target", call_sites=[{"file": "/repo/module.py", "line": 3}])
        graph.add_reference_edge("module.caller", "module.target", EdgeKind.CONTAINS)
        graph.add_reference_edge("module.caller", "module.target", EdgeKind.IMPORT)

        information = build_program_map_information(graph.program_map_networkx({EdgeKind.CONTAINS, EdgeKind.IMPORT}))

        self.assertEqual(
            {item.channel for item in information.evidence},
            {EdgeKind.CALL, EdgeKind.CONTAINS, EdgeKind.IMPORT},
        )
        self.assertEqual(information.statistics.evidence_count, 3)

    def test_call_graph_projection_canonicalizes_repeated_reference_evidence(self):
        graph = CallGraph()
        graph.add_node(Node("module.caller", NodeType.FUNCTION, "/repo/module.py", 1, 10))
        graph.add_node(Node("module.target", NodeType.FUNCTION, "/repo/module.py", 20, 25))
        graph.add_reference_edge("module.caller", "module.target", EdgeKind.CONTAINS)
        graph.add_reference_edge("module.caller", "module.target", EdgeKind.CONTAINS)

        projection = graph.program_map_networkx({EdgeKind.CONTAINS})
        information = build_program_map_information(projection)

        self.assertEqual(len(information.evidence), 1)
        item = information.evidence[0]
        self.assertEqual(
            (item.channel, item.count, item.raw_weight, item.weighted_value), (EdgeKind.CONTAINS, 2, 2.0, 2.0)
        )
        self.assertEqual(projection["module.caller"]["module.target"]["weight"], 2.0)


class TestProgramMapHierarchyAndLineage(unittest.TestCase):
    def test_hierarchy_analysis_scores_each_available_infomap_depth(self):
        cluster_result, graph = TestProgramMap._blocks(n_blocks=6, per_block=4)
        program_map = build_program_map(cluster_result, graph)

        self.assertIsNotNone(program_map.hierarchy)
        assert program_map.hierarchy is not None
        self.assertEqual(
            [set(group) for group in program_map.hierarchy.selected.groups],
            program_map.groups,
        )
        self.assertAlmostEqual(program_map.hierarchy.selected.compression, program_map.compression)
        self.assertGreaterEqual(len(program_map.hierarchy.candidates), 1)

    def test_hierarchy_candidate_fitness_accounts_for_all_meta_graph_flow(self):
        cluster_result, graph = TestProgramMap._blocks(n_blocks=6, per_block=4)
        program_map = build_program_map(cluster_result, graph)

        assert program_map.hierarchy is not None
        total_weight = sum(attrs["weight"] for _, _, attrs in _build_meta_graph(cluster_result, graph).edges(data=True))
        for candidate in program_map.hierarchy.candidates:
            fitness = candidate.fitness
            self.assertAlmostEqual(fitness.internal_weight + fitness.crossing_weight, total_weight)
            self.assertGreaterEqual(fitness.internal_ratio, 0.0)
            self.assertLessEqual(fitness.internal_ratio, 1.0)
            self.assertGreaterEqual(fitness.largest_group_fraction, 0.0)
            self.assertLessEqual(fitness.largest_group_fraction, 1.0)

    def test_hierarchy_selection_summary_describes_the_exact_selected_cut(self):
        cluster_result, graph = TestProgramMap._blocks(n_blocks=6, per_block=4)
        program_map = build_program_map(cluster_result, graph)

        assert program_map.hierarchy is not None
        selected = program_map.hierarchy.selected
        summary = program_map.hierarchy.selection_summary
        self.assertIn(f"depth {selected.depth}", summary)
        self.assertIn(f"{selected.group_count} fitted modules", summary)
        self.assertIn(f"{selected.fitness.internal_ratio:.1%}", summary)

    def test_hierarchy_candidate_selection_remains_inside_the_component_budget(self):
        cluster_result, graph = TestProgramMap._blocks(n_blocks=10, per_block=4)
        program_map = build_program_map(cluster_result, graph)

        assert program_map.hierarchy is not None
        selected = program_map.hierarchy.selected
        self.assertTrue(TOP_LEVEL_COMPONENTS_MIN <= selected.group_count <= TOP_LEVEL_COMPONENTS_MAX)
        self.assertEqual(sum(len(group) for group in selected.groups), len(cluster_result.clusters))

    def test_hierarchy_analysis_is_deterministic_for_the_same_module_paths(self):
        cluster_result, graph = TestProgramMap._blocks(n_blocks=6, per_block=4)
        first = build_program_map(cluster_result, graph)
        second = build_program_map(cluster_result, graph)

        assert first.hierarchy is not None
        assert second.hierarchy is not None
        self.assertEqual(first.hierarchy, second.hierarchy)

    def test_hierarchy_analysis_records_the_nearest_level_when_none_fits(self):
        cluster_result, graph = TestProgramMap._blocks(n_blocks=2, per_block=2)
        program_map = build_program_map(cluster_result, graph, low=5, high=8)

        self.assertIsNone(program_map.hierarchy)
        self.assertEqual(len(program_map.groups), 4)

    def test_lineage_assigns_each_predecessor_identity_once(self):
        lineage = reconcile_program_map_lineage(
            [{1, 2}, {3, 4}, {5}],
            {1: "A", 2: "A", 3: "B", 4: "C", 5: "C"},
            {1: 10, 2: 5, 3: 9, 4: 2, 5: 3},
        )

        self.assertEqual(lineage.owners, ("A", "B", "C"))
        self.assertFalse(lineage.retired_owners)
        self.assertEqual(len(lineage.claims), 4)

    def test_lineage_prefers_larger_shared_method_ownership_before_overlap(self):
        lineage = reconcile_program_map_lineage(
            [{1, 2}, {3, 4}],
            {1: "A", 2: "B", 3: "A", 4: "B"},
            {1: 4, 2: 4, 3: 10, 4: 9},
        )

        self.assertEqual(lineage.owners, ("B", "A"))
        self.assertEqual(lineage.claims[0].overlap, 0.5)

    def test_lineage_reports_retired_predecessors_and_unowned_new_groups(self):
        lineage = reconcile_program_map_lineage(
            [{1}, {4}],
            {1: "A", 2: "B", 3: "C"},
            {1: 3, 2: 4, 3: 5, 4: 2},
        )

        self.assertEqual(lineage.owners, ("A", ""))
        self.assertEqual(lineage.retired_owners, ("B", "C"))

    def test_lineage_is_independent_of_previous_owner_mapping_order(self):
        groups = [{1, 2}, {3, 4}]
        counts = {1: 5, 2: 3, 3: 4, 4: 2}
        first = reconcile_program_map_lineage(groups, {1: "A", 2: "A", 3: "B", 4: "B"}, counts)
        second = reconcile_program_map_lineage(groups, {4: "B", 3: "B", 2: "A", 1: "A"}, counts)

        self.assertEqual(first, second)


class TestProgramMapPartitionDiagnostics(unittest.TestCase):
    def test_quality_is_calculated_once_with_the_fitted_program_map(self):
        cluster_result, graph, _ = _profile_fixture()

        program_map = build_program_map(cluster_result, graph, low=2, high=2)

        self.assertIsNotNone(program_map.quality)
        assert program_map.quality is not None
        self.assertEqual(program_map.quality.group_count, len(program_map.groups))
        self.assertEqual(program_map.quality, assess_program_map_partition(program_map))
        self.assertGreaterEqual(program_map.quality.mean_cohesion, 0.0)
        self.assertGreaterEqual(program_map.quality.boundary_symbol_count, 0)
        self.assertIsNotNone(program_map.channels)
        self.assertIsNotNone(program_map.packages)

    def test_empty_partition_has_zero_safe_quality_facts(self):
        empty_map = build_program_map(ClusterResult(), nx.DiGraph())

        self.assertIsNotNone(empty_map.quality)
        assert empty_map.quality is not None
        self.assertEqual(empty_map.quality.group_count, 0)
        self.assertEqual(empty_map.quality.flow_imbalance, 0.0)
        self.assertEqual(empty_map.quality.mean_entropy, 0.0)

    def test_reordered_equivalent_groups_preserve_full_membership_continuity(self):
        drift = compare_program_map_partitions([{1, 2}, {3, 4}], [{3, 4}, {1, 2}])

        self.assertEqual(drift.retained_clusters, 4)
        self.assertEqual(drift.moved_clusters, 0)
        self.assertEqual(drift.unchanged_groups, 2)
        self.assertFalse(drift.has_membership_change)
        self.assertEqual(
            [(overlap.previous_group_id, overlap.current_group_id) for overlap in drift.overlaps],
            [(0, 1), (1, 0)],
        )

    def test_split_and_merge_facts_are_separate_from_membership_retention(self):
        drift = compare_program_map_partitions([{1, 2, 3}, {4, 5, 6}], [{1}, {2, 3, 4}, {5, 6}])

        self.assertEqual(drift.retained_clusters, 6)
        self.assertEqual(drift.moved_clusters, 2)
        self.assertEqual(drift.split_groups, 2)
        self.assertEqual(drift.merged_groups, 1)
        self.assertTrue(drift.has_membership_change)

    def test_added_and_removed_clusters_do_not_imply_partition_movement(self):
        drift = compare_program_map_partitions([{1, 2}], [{2, 3}])

        self.assertEqual(drift.retained_clusters, 1)
        self.assertEqual(drift.moved_clusters, 0)
        self.assertEqual(drift.added_clusters, 1)
        self.assertEqual(drift.removed_clusters, 1)
        self.assertTrue(drift.has_membership_change)


if __name__ == "__main__":
    unittest.main()
