"""Tests for global cross-boundary relation building.

Validates that build_global_relations() correctly finds relationships between
components at every combination of hierarchy levels using a depth-3 simulated project.

Hierarchy:
    1  (API)            -- expanded to depth 3
      1.1  (Public)
        1.1.1  (REST)         - api/public/rest.py
        1.1.2  (GraphQL)      - api/public/graphql.py
      1.2  (Internal)
        1.2.1  (Admin)        - api/internal/admin.py
    2  (Core)           -- expanded to depth 3
      2.1  (Users)
        2.1.1  (Auth)         - core/users/auth.py
        2.1.2  (Profiles)     - core/users/profiles.py
      2.2  (Billing)
        2.2.1  (Payments)     - core/billing/payments.py
        2.2.2  (Invoices)     - core/billing/invoices.py
    3  (Storage)        -- NOT expanded (stays at lvl 1)
                              - storage/cache.py
    4  (Messaging)      -- expanded to depth 2 only
      4.1  (Email)            - msg/email.py
      4.2  (Push)             - msg/push.py

Tested level combinations (src -> dst):
    lvl3 -> lvl3  cross-root              1.1.1 -> 2.1.2, 1.1.1 -> 2.1.1, 1.1.2 -> 2.2.1, 1.2.1 -> 2.1.1
    lvl3 -> lvl3  same root, diff parent  1.2.1 -> 1.1.1
    lvl3 -> lvl3  sibling                 2.2.2 -> 2.2.1, 2.1.1 -> 2.1.2
    lvl3 -> lvl2                          1.1.1 -> 4.1, 2.2.2 -> 4.2
    lvl3 -> lvl1                          1.1.1 -> 3, 2.1.1 -> 3, 2.2.2 -> 3
    lvl2 -> lvl3                          4.1 -> 2.1.1, 4.2 -> 2.2.2
    lvl2 -> lvl2  sibling                 4.1 -> 4.2
    lvl2 -> lvl1                          4.1 -> 3
    lvl1 -> lvl3                          3 -> 2.1.1
    lvl1 -> lvl2                          3 -> 4.2
"""

import unittest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    MethodEntry,
    Relation,
    assign_component_ids,
)
from static_analyzer.cluster_relations import (
    build_component_relations,
    build_global_node_to_component_map,
    build_global_relations,
)
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, Edge
from static_analyzer.node import Node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(name: str, file_path: str) -> Node:
    return Node(name, NodeType.FUNCTION, file_path, 1, 10)


def _method(name: str) -> MethodEntry:
    return MethodEntry(qualified_name=name, start_line=1, end_line=10, node_type="FUNCTION")


def _comp(name: str, methods: list[tuple[str, str]], cid: str = "") -> Component:
    groups: dict[str, list[MethodEntry]] = {}
    for qname, fpath in methods:
        groups.setdefault(fpath, []).append(_method(qname))
    return Component(
        name=name,
        description=f"{name} component",
        key_entities=[],
        component_id=cid,
        file_methods=[FileMethodGroup(file_path=fp, methods=m) for fp, m in groups.items()],
    )


# ---------------------------------------------------------------------------
# All project nodes
# ---------------------------------------------------------------------------

ALL_NODES: dict[str, Node] = {
    # 1 / 1.1 / 1.1.1  (API > Public > REST)
    "api.rest.list": _node("api.rest.list", "api/public/rest.py"),
    "api.rest.get": _node("api.rest.get", "api/public/rest.py"),
    # 1 / 1.1 / 1.1.2  (API > Public > GraphQL)
    "api.gql.query": _node("api.gql.query", "api/public/graphql.py"),
    # 1 / 1.2 / 1.2.1  (API > Internal > Admin)
    "api.admin.reset": _node("api.admin.reset", "api/internal/admin.py"),
    # 2 / 2.1 / 2.1.1  (Core > Users > Auth)
    "core.auth.login": _node("core.auth.login", "core/users/auth.py"),
    "core.auth.verify": _node("core.auth.verify", "core/users/auth.py"),
    # 2 / 2.1 / 2.1.2  (Core > Users > Profiles)
    "core.profiles.get": _node("core.profiles.get", "core/users/profiles.py"),
    # 2 / 2.2 / 2.2.1  (Core > Billing > Payments)
    "core.pay.charge": _node("core.pay.charge", "core/billing/payments.py"),
    # 2 / 2.2 / 2.2.2  (Core > Billing > Invoices)
    "core.inv.create": _node("core.inv.create", "core/billing/invoices.py"),
    "core.inv.send": _node("core.inv.send", "core/billing/invoices.py"),
    # 3  (Storage -- flat)
    "storage.cache.get": _node("storage.cache.get", "storage/cache.py"),
    "storage.cache.set": _node("storage.cache.set", "storage/cache.py"),
    # 4 / 4.1  (Messaging > Email)
    "msg.email.send": _node("msg.email.send", "msg/email.py"),
    # 4 / 4.2  (Messaging > Push)
    "msg.push.notify": _node("msg.push.notify", "msg/push.py"),
}

# ---------------------------------------------------------------------------
# CFG edges -- one per level-combination
# ---------------------------------------------------------------------------

CFG_EDGE_SPECS: list[tuple[str, str, str]] = [
    # (src_qname, dst_qname, description)
    # --- lvl3 -> lvl3 (cross-root) ---
    ("api.rest.list", "core.profiles.get", "1.1.1 -> 2.1.2"),
    ("api.rest.get", "core.auth.verify", "1.1.1 -> 2.1.1"),
    ("api.gql.query", "core.pay.charge", "1.1.2 -> 2.2.1"),
    ("api.admin.reset", "core.auth.login", "1.2.1 -> 2.1.1"),
    # --- lvl3 -> lvl3 (same root, different lvl2 parent) ---
    ("api.admin.reset", "api.rest.list", "1.2.1 -> 1.1.1"),
    # --- lvl3 -> lvl3 (sibling -- same lvl2 parent) ---
    ("core.inv.create", "core.pay.charge", "2.2.2 -> 2.2.1"),
    ("core.auth.login", "core.profiles.get", "2.1.1 -> 2.1.2"),
    # --- lvl3 -> lvl2 ---
    ("api.rest.list", "msg.email.send", "1.1.1 -> 4.1"),
    ("core.inv.send", "msg.push.notify", "2.2.2 -> 4.2"),
    # --- lvl3 -> lvl1 ---
    ("api.rest.list", "storage.cache.get", "1.1.1 -> 3"),
    ("core.auth.verify", "storage.cache.get", "2.1.1 -> 3"),
    ("core.inv.send", "storage.cache.set", "2.2.2 -> 3"),
    # --- lvl2 -> lvl3 ---
    ("msg.email.send", "core.auth.verify", "4.1 -> 2.1.1"),
    ("msg.push.notify", "core.inv.create", "4.2 -> 2.2.2"),
    # --- lvl2 -> lvl2 (cross-root) ---
    ("msg.email.send", "msg.push.notify", "4.1 -> 4.2 (sibling)"),
    # --- lvl2 -> lvl1 ---
    ("msg.email.send", "storage.cache.set", "4.1 -> 3"),
    # --- lvl1 -> lvl3 ---
    ("storage.cache.get", "core.auth.login", "3 -> 2.1.1"),
    # --- lvl1 -> lvl2 ---
    ("storage.cache.set", "msg.push.notify", "3 -> 4.2"),
    # --- lvl1 -> lvl1 ---
    # (Storage has no outbound edge to another lvl1 naturally, but we can
    #  test this in the partial-expansion scenario where Core is not expanded.)
]


def _build_cfg() -> CallGraph:
    nodes = dict(ALL_NODES)
    edges = [Edge(nodes[s], nodes[d]) for s, d, _ in CFG_EDGE_SPECS]
    return CallGraph(nodes=nodes, edges=edges)


def _methods_for(*file_paths: str) -> list[tuple[str, str]]:
    return [(n.fully_qualified_name, n.file_path) for n in ALL_NODES.values() if n.file_path in file_paths]


def _build_root_analysis() -> AnalysisInsights:
    analysis = AnalysisInsights(
        description="Test app",
        components=[
            _comp("API", _methods_for("api/public/rest.py", "api/public/graphql.py", "api/internal/admin.py")),
            _comp(
                "Core",
                _methods_for(
                    "core/users/auth.py",
                    "core/users/profiles.py",
                    "core/billing/payments.py",
                    "core/billing/invoices.py",
                ),
            ),
            _comp("Storage", _methods_for("storage/cache.py")),
            _comp("Messaging", _methods_for("msg/email.py", "msg/push.py")),
        ],
        components_relations=[
            Relation(relation="calls", src_name="API", dst_name="Core", src_id="1", dst_id="2"),
            Relation(relation="caches via", src_name="Core", dst_name="Storage", src_id="2", dst_id="3"),
            Relation(relation="notifies via", src_name="Core", dst_name="Messaging", src_id="2", dst_id="4"),
        ],
    )
    assign_component_ids(analysis)
    return analysis


def _build_sub_analyses() -> dict[str, AnalysisInsights]:
    """Full expansion: 1 and 2 to depth 3, 4 to depth 2, 3 stays flat."""

    # --- API depth 2 ---
    api_d2 = AnalysisInsights(
        description="API",
        components=[
            _comp("Public", _methods_for("api/public/rest.py", "api/public/graphql.py")),
            _comp("Internal", _methods_for("api/internal/admin.py")),
        ],
        components_relations=[
            Relation(relation="falls back to", src_name="Internal", dst_name="Public", src_id="1.2", dst_id="1.1"),
        ],
    )
    assign_component_ids(api_d2, parent_id="1")

    # --- API > Public depth 3 ---
    public_d3 = AnalysisInsights(
        description="Public API",
        components=[
            _comp("REST", _methods_for("api/public/rest.py")),
            _comp("GraphQL", _methods_for("api/public/graphql.py")),
        ],
        components_relations=[],
    )
    assign_component_ids(public_d3, parent_id="1.1")

    # --- API > Internal depth 3 ---
    internal_d3 = AnalysisInsights(
        description="Internal API",
        components=[
            _comp("Admin", _methods_for("api/internal/admin.py")),
        ],
        components_relations=[],
    )
    assign_component_ids(internal_d3, parent_id="1.2")

    # --- Core depth 2 ---
    core_d2 = AnalysisInsights(
        description="Core",
        components=[
            _comp("Users", _methods_for("core/users/auth.py", "core/users/profiles.py")),
            _comp("Billing", _methods_for("core/billing/payments.py", "core/billing/invoices.py")),
        ],
        components_relations=[
            Relation(relation="bills", src_name="Users", dst_name="Billing", src_id="2.1", dst_id="2.2"),
        ],
    )
    assign_component_ids(core_d2, parent_id="2")

    # --- Core > Users depth 3 ---
    users_d3 = AnalysisInsights(
        description="Users",
        components=[
            _comp("Auth", _methods_for("core/users/auth.py")),
            _comp("Profiles", _methods_for("core/users/profiles.py")),
        ],
        components_relations=[
            Relation(relation="populates", src_name="Auth", dst_name="Profiles", src_id="2.1.1", dst_id="2.1.2"),
        ],
    )
    assign_component_ids(users_d3, parent_id="2.1")

    # --- Core > Billing depth 3 ---
    billing_d3 = AnalysisInsights(
        description="Billing",
        components=[
            _comp("Payments", _methods_for("core/billing/payments.py")),
            _comp("Invoices", _methods_for("core/billing/invoices.py")),
        ],
        components_relations=[
            Relation(relation="triggers", src_name="Invoices", dst_name="Payments", src_id="2.2.2", dst_id="2.2.1"),
        ],
    )
    assign_component_ids(billing_d3, parent_id="2.2")

    # --- Messaging depth 2 (NOT expanded further) ---
    msg_d2 = AnalysisInsights(
        description="Messaging",
        components=[
            _comp("Email", _methods_for("msg/email.py")),
            _comp("Push", _methods_for("msg/push.py")),
        ],
        components_relations=[],
    )
    assign_component_ids(msg_d2, parent_id="4")

    return {
        "1": api_d2,
        "1.1": public_d3,
        "1.2": internal_d3,
        "2": core_d2,
        "2.1": users_d3,
        "2.2": billing_d3,
        "4": msg_d2,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGlobalNodeMap(unittest.TestCase):

    def test_full_expansion(self):
        gmap = build_global_node_to_component_map(_build_root_analysis(), _build_sub_analyses())
        # depth-3 leaves
        self.assertEqual(gmap["api.rest.list"], "1.1.1")
        self.assertEqual(gmap["api.gql.query"], "1.1.2")
        self.assertEqual(gmap["api.admin.reset"], "1.2.1")
        self.assertEqual(gmap["core.auth.login"], "2.1.1")
        self.assertEqual(gmap["core.profiles.get"], "2.1.2")
        self.assertEqual(gmap["core.pay.charge"], "2.2.1")
        self.assertEqual(gmap["core.inv.create"], "2.2.2")
        # depth-2 leaves (Messaging)
        self.assertEqual(gmap["msg.email.send"], "4.1")
        self.assertEqual(gmap["msg.push.notify"], "4.2")
        # depth-1 leaf (Storage)
        self.assertEqual(gmap["storage.cache.get"], "3")

    def test_depth_1(self):
        gmap = build_global_node_to_component_map(_build_root_analysis(), {})
        self.assertEqual(gmap["api.rest.list"], "1")
        self.assertEqual(gmap["core.auth.login"], "2")
        self.assertEqual(gmap["storage.cache.get"], "3")
        self.assertEqual(gmap["msg.email.send"], "4")


class TestAllLevelCombinations(unittest.TestCase):
    """Every pair of hierarchy levels is reachable via CFG edges."""

    def setUp(self):
        gmap = build_global_node_to_component_map(_build_root_analysis(), _build_sub_analyses())
        self.rels = build_component_relations(gmap, {"python": _build_cfg()})
        self.pairs = {(r.src_cluster_id, r.dst_cluster_id) for r in self.rels}

    # -- lvl3 -> lvl3 (cross-root) ------------------------------------
    def test_lvl3_to_lvl3_cross_root(self):
        self.assertIn(("1.1.1", "2.1.2"), self.pairs, "REST -> Profiles")
        self.assertIn(("1.1.1", "2.1.1"), self.pairs, "REST -> Auth")
        self.assertIn(("1.1.2", "2.2.1"), self.pairs, "GraphQL -> Payments")
        self.assertIn(("1.2.1", "2.1.1"), self.pairs, "Admin -> Auth")

    # -- lvl3 -> lvl3 (same root, different lvl2 parent) ---------------
    def test_lvl3_to_lvl3_same_root(self):
        self.assertIn(("1.2.1", "1.1.1"), self.pairs, "Admin -> REST")

    # -- lvl3 -> lvl3 (sibling) ----------------------------------------
    def test_lvl3_to_lvl3_sibling(self):
        self.assertIn(("2.2.2", "2.2.1"), self.pairs, "Invoices -> Payments")
        self.assertIn(("2.1.1", "2.1.2"), self.pairs, "Auth -> Profiles")

    # -- lvl3 -> lvl2 --------------------------------------------------
    def test_lvl3_to_lvl2(self):
        self.assertIn(("1.1.1", "4.1"), self.pairs, "REST -> Email")
        self.assertIn(("2.2.2", "4.2"), self.pairs, "Invoices -> Push")

    # -- lvl3 -> lvl1 --------------------------------------------------
    def test_lvl3_to_lvl1(self):
        self.assertIn(("1.1.1", "3"), self.pairs, "REST -> Storage")
        self.assertIn(("2.1.1", "3"), self.pairs, "Auth -> Storage")
        self.assertIn(("2.2.2", "3"), self.pairs, "Invoices -> Storage")

    # -- lvl2 -> lvl3 --------------------------------------------------
    def test_lvl2_to_lvl3(self):
        self.assertIn(("4.1", "2.1.1"), self.pairs, "Email -> Auth")
        self.assertIn(("4.2", "2.2.2"), self.pairs, "Push -> Invoices")

    # -- lvl2 -> lvl2 (sibling) ----------------------------------------
    def test_lvl2_to_lvl2_sibling(self):
        self.assertIn(("4.1", "4.2"), self.pairs, "Email -> Push")

    # -- lvl2 -> lvl1 --------------------------------------------------
    def test_lvl2_to_lvl1(self):
        self.assertIn(("4.1", "3"), self.pairs, "Email -> Storage")

    # -- lvl1 -> lvl3 --------------------------------------------------
    def test_lvl1_to_lvl3(self):
        self.assertIn(("3", "2.1.1"), self.pairs, "Storage -> Auth")

    # -- lvl1 -> lvl2 --------------------------------------------------
    def test_lvl1_to_lvl2(self):
        self.assertIn(("3", "4.2"), self.pairs, "Storage -> Push")

    # -- sanity --------------------------------------------------------
    def test_no_self_edges(self):
        for src, dst in self.pairs:
            self.assertNotEqual(src, dst)

    def test_total_edges_match_spec(self):
        self.assertEqual(len(self.pairs), len(CFG_EDGE_SPECS))


class TestLabelInheritance(unittest.TestCase):
    """LLM labels propagate from ancestor relations to finer-grained static edges."""

    def setUp(self):
        self.rels = build_global_relations(
            _build_root_analysis(),
            _build_sub_analyses(),
            {"python": _build_cfg()},
        )
        self.by_pair = {(r.src_id, r.dst_id): r for r in self.rels}

    def test_root_label_inherited(self):
        # Root LLM "1"->"2" label "calls" -> inherited by 1.1.1->2.1.2
        r = self.by_pair[("1.1.1", "2.1.2")]
        self.assertEqual(r.relation, "calls")

    def test_depth2_label_inherited(self):
        # Depth-2 LLM "2.2.2"->"2.2.1" label "triggers"
        r = self.by_pair[("2.2.2", "2.2.1")]
        self.assertEqual(r.relation, "triggers")

    def test_depth3_label_direct(self):
        # Depth-3 LLM "2.1.1"->"2.1.2" label "populates"
        r = self.by_pair[("2.1.1", "2.1.2")]
        self.assertEqual(r.relation, "populates")

    def test_fallback_to_ancestor_label(self):
        # "2.1.1"->"3" has no direct LLM relation, ancestor "2"->"3" is "caches via"
        r = self.by_pair[("2.1.1", "3")]
        self.assertEqual(r.relation, "caches via")

    def test_no_label_defaults_to_calls(self):
        # "3"->"2.1.1" -- no LLM relation in 3->2 direction. Default is "calls".
        r = self.by_pair[("3", "2.1.1")]
        self.assertEqual(r.relation, "calls")

    def test_root_relations_superseded(self):
        ids = {(r.src_id, r.dst_id) for r in self.rels}
        self.assertNotIn(("1", "2"), ids, "Superseded by finer static edges")
        self.assertNotIn(("2", "3"), ids, "Superseded by finer static edges")

    def test_no_duplicates(self):
        pairs = [(r.src_id, r.dst_id) for r in self.rels]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_all_metadata_present(self):
        for r in self.rels:
            self.assertTrue(r.src_id and r.dst_id and r.src_name and r.dst_name and r.relation)


class TestIncrementalExpansion(unittest.TestCase):
    """Simulate partial updates: start from depth 1, expand one component at a time.

    This mirrors the --partial-component-id CLI flow where an existing analysis
    is loaded, one component is expanded, and build_global_relations is re-run
    with the updated sub_analyses dict.
    """

    def test_expand_single_root_component(self):
        """Start at depth 1 (no subs), expand component 2 (Core) to depth 2."""
        root = _build_root_analysis()
        cfg = {"python": _build_cfg()}

        # Before expansion: all relations are at root level
        before = build_global_relations(root, {}, cfg)
        before_ids = {(r.src_id, r.dst_id) for r in before}
        self.assertIn(("1", "2"), before_ids)
        self.assertIn(("2", "3"), before_ids)
        for src, dst in before_ids:
            self.assertNotIn(".", src)
            self.assertNotIn(".", dst)

        # Expand Core (component "2") to depth 2
        all_subs = _build_sub_analyses()
        subs_after = {"2": all_subs["2"]}

        after = build_global_relations(root, subs_after, cfg)
        after_ids = {(r.src_id, r.dst_id) for r in after}

        # Root "1" -> "2" should now be replaced by finer edges to 2.1 and 2.2
        self.assertNotIn(("1", "2"), after_ids, "Root 1->2 superseded")
        self.assertIn(("1", "2.1"), after_ids, "API -> Users")
        self.assertIn(("1", "2.2"), after_ids, "API -> Billing")
        # Core sub -> Storage
        self.assertIn(("2.1", "3"), after_ids, "Users -> Storage")
        self.assertIn(("2.2", "3"), after_ids, "Billing -> Storage")
        # Sibling within Core
        self.assertIn(("2.1", "2.2"), after_ids, "Users -> Billing (sibling)")
        # Unchanged: Storage -> Messaging still at root level
        self.assertIn(("3", "4"), after_ids, "Storage -> Messaging unchanged")

    def test_expand_second_component_after_first(self):
        """Expand Core to depth 2, then expand Messaging to depth 2."""
        root = _build_root_analysis()
        cfg = {"python": _build_cfg()}
        all_subs = _build_sub_analyses()

        # Step 1: Expand Core
        subs = {"2": all_subs["2"]}
        step1 = build_global_relations(root, subs, cfg)
        step1_ids = {(r.src_id, r.dst_id) for r in step1}
        # Messaging still at root level
        self.assertIn(("2.2", "4"), step1_ids, "Billing -> Messaging (root)")

        # Step 2: Also expand Messaging
        subs["4"] = all_subs["4"]
        step2 = build_global_relations(root, subs, cfg)
        step2_ids = {(r.src_id, r.dst_id) for r in step2}
        # Now Billing -> Messaging should be refined to Billing -> Push
        self.assertNotIn(("2.2", "4"), step2_ids, "Billing -> Messaging superseded")
        self.assertIn(("2.2", "4.2"), step2_ids, "Billing -> Push (refined)")
        # Cross-root lvl2: Email -> Users
        self.assertIn(("4.1", "2.1"), step2_ids, "Email -> Users")

    def test_expand_leaf_to_depth3(self):
        """Start with Core at depth 2, then expand Users (2.1) to depth 3."""
        root = _build_root_analysis()
        cfg = {"python": _build_cfg()}
        all_subs = _build_sub_analyses()

        # Step 1: Core at depth 2 only
        subs = {"2": all_subs["2"]}
        step1 = build_global_relations(root, subs, cfg)
        step1_ids = {(r.src_id, r.dst_id) for r in step1}
        self.assertIn(("1", "2.1"), step1_ids, "API -> Users at depth 2")
        self.assertIn(("2.1", "3"), step1_ids, "Users -> Storage at depth 2")

        # Step 2: Expand Users (2.1) to depth 3 -> Auth (2.1.1), Profiles (2.1.2)
        subs["2.1"] = all_subs["2.1"]
        step2 = build_global_relations(root, subs, cfg)
        step2_ids = {(r.src_id, r.dst_id) for r in step2}

        # "1" -> "2.1" should now be refined to "1" -> "2.1.1" and "1" -> "2.1.2"
        self.assertNotIn(("1", "2.1"), step2_ids, "API -> Users superseded by finer edges")
        self.assertIn(("1", "2.1.1"), step2_ids, "API -> Auth")
        self.assertIn(("1", "2.1.2"), step2_ids, "API -> Profiles")
        # Users sub -> Storage refined
        self.assertNotIn(("2.1", "3"), step2_ids, "Users -> Storage superseded")
        self.assertIn(("2.1.1", "3"), step2_ids, "Auth -> Storage")
        # Sibling within Users
        self.assertIn(("2.1.1", "2.1.2"), step2_ids, "Auth -> Profiles (sibling)")


class TestPartialExpansion(unittest.TestCase):

    def test_only_api_expanded(self):
        """API to depth 3, everything else at lvl 1."""
        subs = {k: v for k, v in _build_sub_analyses().items() if k.startswith("1")}
        rels = build_global_relations(_build_root_analysis(), subs, {"python": _build_cfg()})
        ids = {(r.src_id, r.dst_id) for r in rels}

        # lvl3 -> lvl1 (API leaf -> unexpanded root)
        self.assertIn(("1.1.1", "2"), ids, "REST -> Core (Core not expanded)")
        self.assertIn(("1.1.1", "3"), ids, "REST -> Storage")
        self.assertIn(("1.1.1", "4"), ids, "REST -> Messaging (not expanded)")
        # lvl3 -> lvl3 within API
        self.assertIn(("1.2.1", "1.1.1"), ids, "Admin -> REST")
        # lvl1 -> lvl1
        self.assertIn(("2", "3"), ids, "Core -> Storage (both at root)")
        self.assertIn(("3", "2"), ids, "Storage -> Core (both at root)")
        self.assertIn(("3", "4"), ids, "Storage -> Messaging")

    def test_core_depth2_only(self):
        """Core to depth 2, Messaging to depth 2, API and Storage at lvl 1."""
        subs = {"2": _build_sub_analyses()["2"], "4": _build_sub_analyses()["4"]}
        rels = build_global_relations(_build_root_analysis(), subs, {"python": _build_cfg()})
        ids = {(r.src_id, r.dst_id) for r in rels}

        # lvl1 -> lvl2
        self.assertIn(("1", "2.1"), ids, "API -> Users")
        self.assertIn(("1", "2.2"), ids, "API -> Billing")
        # lvl2 -> lvl2 (sibling)
        self.assertIn(("4.1", "4.2"), ids, "Email -> Push (sibling)")
        # lvl2 -> lvl1
        self.assertIn(("4.1", "3"), ids, "Email -> Storage")
        self.assertIn(("2.1", "3"), ids, "Users -> Storage")
        # lvl2 -> lvl2 (cross-root)
        self.assertIn(("2.2", "4.2"), ids, "Billing -> Push")
        # lvl1 -> lvl2
        self.assertIn(("3", "4.2"), ids, "Storage -> Push")
        self.assertIn(("3", "2.1"), ids, "Storage -> Users")
