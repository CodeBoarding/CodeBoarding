"""An incremental prompt details the pairs the commit touched and counts the rest.

The gate and the wording-restore path share one predicate on purpose: a pair the model was
shown as a bare count must never end up carrying a model-authored label.
"""

import unittest

from agents.agent_responses import Relation, RelationEdge, SourceCodeReference
from agents.relation_edges import edge_touches_change, pair_untouched_by_change


def edge(src: str, dst: str) -> RelationEdge:
    return RelationEdge(
        source=SourceCodeReference(qualified_name=src, reference_file="pkg/mod.py"),
        target=SourceCodeReference(qualified_name=dst, reference_file="pkg/mod.py"),
    )


def relation(*edges: RelationEdge) -> Relation:
    return Relation(relation="calls", src_name="1", dst_name="2", src_id="1", dst_id="2", all_edges=list(edges))


class TestEdgeTouchesChange(unittest.TestCase):
    def test_a_changed_source_makes_the_edge_evidence(self):
        self.assertTrue(edge_touches_change("pkg.caller", "pkg.callee", {"pkg.caller"}))

    def test_a_changed_target_also_makes_the_edge_evidence(self):
        # Asymmetric with the deletion predicate on purpose: either end moving is a reason
        # to re-describe the connection.
        self.assertTrue(edge_touches_change("pkg.caller", "pkg.callee", {"pkg.callee"}))

    def test_an_untouched_edge_is_not_evidence(self):
        self.assertFalse(edge_touches_change("pkg.caller", "pkg.callee", {"pkg.elsewhere"}))

    def test_a_full_run_has_no_changed_members(self):
        self.assertFalse(edge_touches_change("pkg.caller", "pkg.callee", None))
        self.assertFalse(edge_touches_change("pkg.caller", "pkg.callee", set()))


class TestPairUntouchedByChange(unittest.TestCase):
    def test_a_pair_with_no_touched_edge_is_untouched(self):
        rel = relation(edge("pkg.a", "pkg.b"), edge("pkg.c", "pkg.d"))
        self.assertTrue(pair_untouched_by_change(rel, {"pkg.elsewhere"}))

    def test_one_touched_edge_makes_the_whole_pair_touched(self):
        rel = relation(edge("pkg.a", "pkg.b"), edge("pkg.c", "pkg.d"))
        self.assertFalse(pair_untouched_by_change(rel, {"pkg.c"}))

    def test_an_edgeless_pair_is_not_claimed_either_way(self):
        # It has no supporting calls, so the existing edgeless branch decides its wording.
        self.assertFalse(pair_untouched_by_change(relation(), {"pkg.a"}))

    def test_a_full_run_never_reports_a_pair_untouched(self):
        rel = relation(edge("pkg.a", "pkg.b"))
        self.assertFalse(pair_untouched_by_change(rel, None))
        self.assertFalse(pair_untouched_by_change(rel, set()))


if __name__ == "__main__":
    unittest.main()
