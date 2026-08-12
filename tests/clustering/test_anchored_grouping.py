"""The anchored grouping contract: a change moves only what it touched.

``supercluster_by_modularity_peak`` re-optimizes from scratch, which is deterministic but
not continuous — modularity has many near-equal optima, so a small edit can select a
different one and reshuffle ownership. The incremental path uses ``anchored_grouping``
instead, and these tests pin the properties it exists to provide.
"""

import unittest

import networkx as nx

from clustering.cluster_helpers import (
    SUBCOMPONENTS_MAX,
    SUBCOMPONENTS_MIN,
    _absorb_leftovers,
    _build_meta_graph,
    _method_counts,
    _modularity,
    _seeds_from_partition,
    anchored_grouping,
    supercluster_by_modularity_peak,
)
from static_analyzer.graph import ClusterResult


def blocks(n_blocks: int, per_block: int, members_each: int = 3):
    """A ClusterResult + cfg of ``n_blocks`` tight blocks, weakly bridged."""
    clusters, cluster_to_files, file_to_clusters = {}, {}, {}
    graph = nx.DiGraph()
    n = n_blocks * per_block
    for cid in range(1, n + 1):
        block = (cid - 1) // per_block
        nodes = [f"n{cid}_{j}" for j in range(members_each)]
        clusters[cid] = set(nodes)
        path = f"/repo/block{block}/c{cid}.py"
        cluster_to_files[cid] = {path}
        file_to_clusters[path] = {cid}
        for node in nodes:
            graph.add_node(node, file_path=path)
    for cid in range(1, n + 1):
        block = (cid - 1) // per_block
        for other in range(1, n + 1):
            if other != cid and (other - 1) // per_block == block:
                graph.add_edge(f"n{cid}_0", f"n{other}_1")
    for block in range(n_blocks - 1):
        graph.add_edge(f"n{block * per_block + 1}_0", f"n{(block + 1) * per_block + 1}_1")
    cr = ClusterResult(
        clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
    )
    return cr, graph


def owners_by_member(result, cluster_result) -> dict[str, str]:
    """member qname -> the component label that owns it."""
    out = {}
    for index, group in enumerate(result.groups):
        label = result.owners[index] or f"new{index}"
        for cid in group:
            for member in cluster_result.clusters.get(cid, set()):
                out[member] = label
    return out


class TestAnchoredGrouping(unittest.TestCase):
    def _previous(self, cr, graph):
        """A previous grouping: one component per block."""
        n_per_block = 4
        return {cid: f"C{(cid - 1) // n_per_block}" for cid in cr.clusters}

    def test_unchanged_input_keeps_every_owner(self):
        cr, graph = blocks(n_blocks=5, per_block=4)
        previous = self._previous(cr, graph)

        result = anchored_grouping(cr, graph, previous)

        self.assertFalse(result.regrouped)
        for member, owner in owners_by_member(result, cr).items():
            self.assertEqual(owner, previous[int(member.split("_")[0][1:])], member)

    def test_a_new_subsystem_becomes_its_own_component(self):
        # A whole new package arriving must be its own component, not scattered into the
        # existing ones. The clusters have no previous owner and form a tight island.
        cr, graph = blocks(n_blocks=5, per_block=4)
        previous = self._previous(cr, graph)  # one component per existing block; excludes the new ids

        new_ids = [100, 101, 102, 103]
        for cid in new_ids:
            nodes = [f"nw{cid}_{j}" for j in range(3)]
            cr.clusters[cid] = set(nodes)
            path = f"/repo/newpkg/c{cid}.py"
            cr.cluster_to_files[cid] = {path}
            cr.file_to_clusters[path] = {cid}
            for node in nodes:
                graph.add_node(node, file_path=path)
        for a in new_ids:
            for b in new_ids:
                if a != b:
                    graph.add_edge(f"nw{a}_0", f"nw{b}_1")
        graph.add_edge("n1_0", "nw100_1")  # one weak bridge into the existing graph

        result = anchored_grouping(cr, graph, previous)

        unowned = [group for group, owner in zip(result.groups, result.owners) if not owner]
        self.assertTrue(
            any(group == set(new_ids) for group in unowned),
            f"new subsystem was not promoted to its own component; unowned groups: {unowned}",
        )
        for member, owner in owners_by_member(result, cr).items():
            if member.startswith("nw"):
                continue
            self.assertEqual(owner, previous[int(member.split("_")[0][1:])], member)

    def test_a_new_cluster_is_absorbed_and_moves_nothing_else(self):
        cr, graph = blocks(n_blocks=5, per_block=4)
        previous = self._previous(cr, graph)
        before = anchored_grouping(cr, graph, previous)
        before_owners = owners_by_member(before, cr)

        # A new leaf cluster appears, attached to an existing one.
        cr.clusters[999] = {"fresh.method"}
        cr.cluster_to_files[999] = {"/repo/block0/new.py"}
        graph.add_node("fresh.method", file_path="/repo/block0/new.py")
        graph.add_edge("n1_0", "fresh.method")

        after = anchored_grouping(cr, graph, previous)
        after_owners = owners_by_member(after, cr)

        self.assertFalse(after.regrouped)
        self.assertIn("fresh.method", after_owners)
        for member, owner in before_owners.items():
            self.assertEqual(after_owners[member], owner, f"{member} moved")

    def test_a_removed_cluster_moves_nothing_else(self):
        cr, graph = blocks(n_blocks=5, per_block=4)
        previous = self._previous(cr, graph)
        before_owners = owners_by_member(anchored_grouping(cr, graph, previous), cr)

        doomed = sorted(cr.clusters)[-1]
        for member in cr.clusters.pop(doomed):
            graph.remove_node(member)
        cr.cluster_to_files.pop(doomed, None)

        after_owners = owners_by_member(anchored_grouping(cr, graph, previous), cr)

        for member, owner in before_owners.items():
            if member in after_owners:
                self.assertEqual(after_owners[member], owner, f"{member} moved")

    def test_a_component_losing_every_cluster_disappears(self):
        cr, graph = blocks(n_blocks=5, per_block=4)
        previous = self._previous(cr, graph)

        for cid in [cid for cid in cr.clusters if previous[cid] == "C0"]:
            for member in cr.clusters.pop(cid):
                graph.remove_node(member)
            cr.cluster_to_files.pop(cid, None)

        result = anchored_grouping(cr, graph, previous)

        self.assertNotIn("C0", [owner for owner in result.owners if owner])

    def test_no_previous_grouping_falls_back_to_a_fresh_partition(self):
        cr, graph = blocks(n_blocks=5, per_block=4)

        result = anchored_grouping(cr, graph, {})

        self.assertTrue(result.regrouped)
        self.assertTrue(result.groups)

    def test_drift_past_the_budget_re_derives_the_structure(self):
        cr, graph = blocks(n_blocks=6, per_block=4)
        # A previous grouping deliberately at odds with the graph's real structure:
        # every cluster in one component, which scores far below a fresh optimum.
        previous = {cid: "C0" for cid in cr.clusters}

        result = anchored_grouping(cr, graph, previous, drift_budget=0.05)

        self.assertTrue(result.regrouped)
        self.assertGreater(len(result.groups), 1)

    def test_a_re_derived_structure_still_inherits_identity(self):
        # Regrouping must not rename everything: the component holding most of a
        # predecessor's code keeps its id, so a reader sees a targeted change.
        cr, graph = blocks(n_blocks=6, per_block=4)
        previous = {cid: "C0" for cid in cr.clusters}

        result = anchored_grouping(cr, graph, previous, drift_budget=0.05)

        self.assertTrue(result.regrouped)
        self.assertIn("C0", result.owners)

    def test_ids_are_never_handed_to_two_components(self):
        cr, graph = blocks(n_blocks=6, per_block=4)
        previous = {cid: f"C{(cid - 1) // 4}" for cid in cr.clusters}

        result = anchored_grouping(cr, graph, previous)

        inherited = [owner for owner in result.owners if owner]
        self.assertEqual(len(inherited), len(set(inherited)))

    def test_every_live_cluster_is_owned_exactly_once(self):
        cr, graph = blocks(n_blocks=5, per_block=4)
        previous = self._previous(cr, graph)
        cr.clusters[999] = {"orphan.method"}
        cr.cluster_to_files[999] = {"/repo/elsewhere/x.py"}
        graph.add_node("orphan.method", file_path="/repo/elsewhere/x.py")

        result = anchored_grouping(cr, graph, previous)

        assigned = [cid for group in result.groups for cid in group]
        self.assertEqual(sorted(assigned), sorted(cr.clusters))
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_is_deterministic(self):
        cr, graph = blocks(n_blocks=5, per_block=4)
        previous = self._previous(cr, graph)

        first = anchored_grouping(cr, graph, previous)
        second = anchored_grouping(cr, graph, previous)

        self.assertEqual([sorted(g) for g in first.groups], [sorted(g) for g in second.groups])
        self.assertEqual(first.owners, second.owners)

    def test_works_at_sub_component_range(self):
        cr, graph = blocks(n_blocks=4, per_block=3)
        previous = {cid: f"C{(cid - 1) // 3}" for cid in cr.clusters}

        result = anchored_grouping(cr, graph, previous, SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX)

        self.assertFalse(result.regrouped)
        self.assertEqual(len(result.groups), 4)

    def test_a_new_subsystem_is_promoted_at_the_sub_component_range_too(self):
        # New components can appear at any depth: the promotion runs per scope, so a new
        # island inside an already-expanded component becomes a new sub-component.
        cr, graph = blocks(n_blocks=4, per_block=3)
        previous = {cid: f"C{(cid - 1) // 3}" for cid in cr.clusters}

        new_ids = [200, 201]
        for cid in new_ids:
            nodes = [f"nw{cid}_{j}" for j in range(3)]
            cr.clusters[cid] = set(nodes)
            path = f"/repo/newsub/c{cid}.py"
            cr.cluster_to_files[cid] = {path}
            cr.file_to_clusters[path] = {cid}
            for node in nodes:
                graph.add_node(node, file_path=path)
        graph.add_edge("nw200_0", "nw201_1")
        graph.add_edge("nw201_0", "nw200_1")
        graph.add_edge("n1_0", "nw200_1")

        result = anchored_grouping(cr, graph, previous, SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX)

        unowned = [group for group, owner in zip(result.groups, result.owners) if not owner]
        self.assertTrue(any(group == set(new_ids) for group in unowned), f"unowned groups: {unowned}")

    def test_reported_modularity_scores_the_returned_grouping(self):
        # The score must describe the groups actually returned, never the pre-absorption
        # partition -- the drift gate and the expansion threshold both read it, and a stale
        # score judges a grouping that was never applied.
        cr, graph = blocks(n_blocks=5, per_block=4)

        groups, modularity = supercluster_by_modularity_peak(cr, graph)

        meta = _build_meta_graph(cr, graph)
        self.assertEqual(round(modularity, 10), round(_modularity(meta, groups), 10))

    def test_absorbing_an_edged_leftover_moves_the_score(self):
        # Guards the seam the fix protects: when a non-singleton community is demoted past
        # ``high`` and absorbed into a seed it shares edges with, the merged grouping scores
        # differently from the pre-absorption partition, so the two must not be conflated.
        cr, graph = blocks(n_blocks=9, per_block=2)
        meta = _build_meta_graph(cr, graph)
        method_count = _method_counts(cr)
        # Force the overflow demotion directly: nine 2-cluster communities, capped at high=8.
        communities = [{2 * b + 1, 2 * b + 2} for b in range(9)]
        seeds, leftovers = _seeds_from_partition(communities, method_count, low=5, high=8)
        self.assertTrue(leftovers, "the ninth community must be demoted to a leftover")
        _absorb_leftovers(seeds, leftovers, meta, cr, method_count)

        self.assertNotEqual(
            round(_modularity(meta, [set(c) for c in communities]), 10),
            round(_modularity(meta, seeds), 10),
        )


if __name__ == "__main__":
    unittest.main()
