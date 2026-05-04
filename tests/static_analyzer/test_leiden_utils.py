"""Tests for the NetworkX <-> igraph conversion + seeded-Leiden helpers."""

import networkx as nx
import pytest

from static_analyzer.leiden_utils import (
    find_partition,
    find_partition_seeded,
    nx_to_ig,
    partition_to_clusters,
)


class TestNxToIg:
    def test_round_trip_undirected(self):
        nx_g = nx.Graph()
        nx_g.add_edges_from([("a", "b"), ("b", "c"), ("a", "c")])
        idx_to_qname: list[str]
        ig_g, idx_to_qname = nx_to_ig(nx_g)

        assert ig_g.vcount() == 3
        assert ig_g.ecount() == 3
        assert set(idx_to_qname) == {"a", "b", "c"}
        assert not ig_g.is_directed()

    def test_round_trip_directed(self):
        nx_g = nx.DiGraph()
        nx_g.add_edges_from([("a", "b"), ("b", "c"), ("c", "a")])
        ig_g, _ = nx_to_ig(nx_g)

        assert ig_g.is_directed()
        assert ig_g.ecount() == 3

    def test_weight_attribute_survives(self):
        nx_g = nx.Graph()
        nx_g.add_weighted_edges_from([("a", "b", 5.0), ("b", "c", 0.1)])
        ig_g, _ = nx_to_ig(nx_g)

        # Why: the seeded path passes weights="weight" to leidenalg, which
        # only works if the attribute round-tripped through from_networkx.
        assert "weight" in ig_g.es.attributes()
        assert sorted(ig_g.es["weight"]) == [0.1, 5.0]

    def test_idx_to_qname_aligned_with_vertex_order(self):
        nx_g = nx.Graph()
        nx_g.add_nodes_from(["x", "y", "z"])
        idx_to_qname: list[str]
        ig_g, idx_to_qname = nx_to_ig(nx_g)

        for i, v in enumerate(ig_g.vs):
            assert v["_nx_name"] == idx_to_qname[i]


class TestPartitionToClusters:
    def test_covers_all_nodes(self):
        nx_g = nx.complete_graph(["a", "b", "c", "d"])
        clusters: list[set[str]] = find_partition(nx_g, seed=42)
        covered: set[str] = set()
        for c in clusters:
            covered |= c
        assert covered == {"a", "b", "c", "d"}

    def test_no_overlap_between_clusters(self):
        nx_g = nx.karate_club_graph()
        clusters: list[set[int]] = find_partition(nx_g, seed=42)
        seen: set[int] = set()
        for c in clusters:
            assert c.isdisjoint(seen)
            seen |= c


class TestFindPartition:
    def test_empty_graph_returns_empty_list(self):
        assert find_partition(nx.Graph(), seed=42) == []

    def test_single_node_returns_one_singleton(self):
        nx_g = nx.Graph()
        nx_g.add_node("only")
        clusters: list[set[str]] = find_partition(nx_g, seed=42)
        assert clusters == [{"only"}]

    def test_deterministic_with_seed(self):
        nx_g = nx.karate_club_graph()
        a: list[set[int]] = find_partition(nx_g, seed=42)
        b: list[set[int]] = find_partition(nx_g, seed=42)
        assert sorted(sorted(c) for c in a) == sorted(sorted(c) for c in b)

    def test_resolution_parameter_changes_partition_count(self):
        # Higher resolution -> more, smaller communities (RB Configuration model).
        nx_g = nx.karate_club_graph()
        low: list[set[int]] = find_partition(nx_g, resolution=0.5, seed=42)
        high: list[set[int]] = find_partition(nx_g, resolution=2.0, seed=42)
        assert len(high) >= len(low)

    def test_directed_graph_handled_natively(self):
        nx_d = nx.DiGraph()
        nx_d.add_edges_from([("a", "b"), ("b", "c"), ("c", "a"), ("a", "d")])
        clusters: list[set[str]] = find_partition(nx_d, seed=42)
        covered: set[str] = set()
        for c in clusters:
            covered |= c
        assert covered == {"a", "b", "c", "d"}


class TestFindPartitionSeeded:
    def test_all_locked_preserves_input_membership(self):
        # Why: the load-bearing property of the lock guarantee.
        nx_g = nx.karate_club_graph()
        n = nx_g.number_of_nodes()
        # Two-cluster prior using compact IDs (max < n).
        init = [0] * (n // 2) + [1] * (n - n // 2)
        fixed = [True] * n
        membership = find_partition_seeded(nx_g, initial_membership_compact=init, is_membership_fixed=fixed, seed=42)
        assert membership == init

    def test_locked_vertex_does_not_move_under_modularity_pressure(self):
        # 3-clique + bridge + 2-clique. Deliberately bad initial: one big cluster.
        # Lock everything; output must equal input.
        nx_g = nx.Graph()
        nx_g.add_nodes_from(range(5))
        nx_g.add_edges_from([(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)])
        init = [0] * 5
        fixed = [True] * 5
        membership = find_partition_seeded(nx_g, initial_membership_compact=init, is_membership_fixed=fixed, seed=42)
        assert membership == init

    def test_unlocked_vertices_rebalance_under_modularity_pressure(self):
        # Same graph, deliberately bad init, but free everyone.
        # Leiden should split into 2 communities.
        nx_g = nx.Graph()
        nx_g.add_nodes_from(range(5))
        nx_g.add_edges_from([(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)])
        init = [0] * 5
        fixed = [False] * 5
        membership = find_partition_seeded(nx_g, initial_membership_compact=init, is_membership_fixed=fixed, seed=42)
        assert len(set(membership)) >= 2

    def test_partial_lock_preserves_locked_assignments(self):
        nx_g = nx.karate_club_graph()
        n = nx_g.number_of_nodes()
        init = [0] * (n // 2) + [1] * (n - n // 2)
        fixed = [True] * (n // 2) + [False] * (n - n // 2)
        membership = find_partition_seeded(nx_g, initial_membership_compact=init, is_membership_fixed=fixed, seed=42)
        # Locked vertices must keep their initial cluster.
        for i in range(n // 2):
            assert membership[i] == 0

    def test_empty_graph_returns_empty_membership(self):
        assert find_partition_seeded(nx.Graph(), initial_membership_compact=[], is_membership_fixed=[], seed=42) == []

    def test_id_above_n_vertices_raises(self):
        # Why: leidenalg requires max(initial_membership) < n_vertices. Calling
        # this helper with arbitrary prior IDs (e.g. 100) would silently
        # corrupt; we surface it as a clear error.
        nx_g = nx.complete_graph(["a", "b", "c"])
        with pytest.raises(ValueError, match="value >= n_vertices"):
            find_partition_seeded(
                nx_g,
                initial_membership_compact=[0, 100, 0],
                is_membership_fixed=[True, True, True],
                seed=42,
            )

    def test_length_mismatch_raises(self):
        nx_g = nx.complete_graph(["a", "b", "c"])
        with pytest.raises(ValueError, match="length"):
            find_partition_seeded(
                nx_g,
                initial_membership_compact=[0, 0],
                is_membership_fixed=[True, True, True],
                seed=42,
            )

    def test_deterministic_with_seed(self):
        nx_g = nx.karate_club_graph()
        n = nx_g.number_of_nodes()
        init = [0] * n
        fixed = [False] * n
        a = find_partition_seeded(nx_g, initial_membership_compact=init, is_membership_fixed=fixed, seed=42)
        b = find_partition_seeded(nx_g, initial_membership_compact=init, is_membership_fixed=fixed, seed=42)
        assert a == b
