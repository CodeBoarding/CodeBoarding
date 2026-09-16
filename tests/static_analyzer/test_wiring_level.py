"""Where a resource node is drawn, and what folds when a level would draw more than the cap (§7)."""

import unittest

from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.clustering.names.draft import LIMIT
from static_analyzer.config import NodeType
from static_analyzer.node import Node
from static_analyzer.wiring.emit import WIRING_GRAPH, Placement
from static_analyzer.wiring.level import EXTERNAL, INFRASTRUCTURE, STORES, layout
from static_analyzer.wiring_results import Resource, ResourceChild, ResourceKind


def resource(name: str, kind: ResourceKind = ResourceKind.DB, children: tuple[str, ...] = ()) -> Resource:
    key = f"resource:{kind.value}:{name}"
    return Resource(
        key=key,
        kind=kind,
        name=name,
        children=tuple(ResourceChild(key=f"{key}/{kind.value}:{child}", kind=kind, name=child) for child in children),
    )


def placement_of(homes: dict[str, str], *edges: tuple[str, str, EdgeKind]) -> Placement:
    graph = CallGraph(language=WIRING_GRAPH)
    for source, target, _ in edges:
        for name in (source, target):
            graph.add_node(
                Node(
                    name,
                    NodeType.OBJECT if name.startswith("resource:") else NodeType.FILE,
                    "" if name.startswith("resource:") else f"/repo/{name}",
                    1,
                    1,
                )
            )
    for source, target, kind in edges:
        graph.add_reference_edge(ReferenceEdge(source, target, kind, ()))
    return Placement(graph=graph, homes=dict(homes))


class TestWhereANodeIsDrawn(unittest.TestCase):
    def test_a_shared_resource_is_a_peer_where_its_users_meet(self) -> None:
        """Home at the top is level 1; home inside an expanded box is a peer of that box's children."""
        placed = layout(
            [resource("top"), resource("inner")],
            placement_of({"resource:db:top": "", "resource:db:inner": "1"}),
            {"": ["1", "2"], "1": ["1.1", "1.2"]},
        ).resources

        self.assertEqual([(r.home, r.level, r.badge) for r in placed], [("", 1, False), ("1", 2, False)])

    def test_a_private_resource_is_a_badge_on_a_box_that_holds_no_children_here(self) -> None:
        placed = layout([resource("mine")], placement_of({"resource:db:mine": "2"}), {"": ["1", "2"]}).resources

        self.assertEqual((placed[0].home, placed[0].level, placed[0].badge), ("2", 2, True))

    def test_a_home_the_document_does_not_hold_is_lifted_to_the_deepest_box_it_does(self) -> None:
        """The clustering goes deeper than the depth cap; the node is drawn in the deepest live ancestor."""
        placed = layout(
            [resource("deep", children=("db1",))],
            Placement(homes={"resource:db:deep": "1.2.3", "resource:db:deep/db:db1": "1.2.3"}),
            {"": ["1", "2"], "1": ["1.1", "1.2"]},
        ).resources

        self.assertEqual((placed[0].home, placed[0].level, placed[0].badge), ("1.2", 3, True))
        self.assertEqual(placed[0].children[0].home, "1.2")

    def test_a_child_is_drawn_in_its_owner_s_box(self) -> None:
        placed = layout(
            [resource("pg", children=("a", "b"))],
            Placement(homes={"resource:db:pg": "", "resource:db:pg/db:a": "1", "resource:db:pg/db:b": "2"}),
            {"": ["1", "2"]},
        ).resources

        self.assertEqual([child.home for child in placed[0].children], ["1", "2"])


class TestTheCap(unittest.TestCase):
    def test_under_the_cap_nothing_folds(self) -> None:
        found = layout(
            [resource("a"), resource("b")], placement_of({"resource:db:a": "", "resource:db:b": ""}), {"": ["1", "2"]}
        )

        self.assertEqual([r.group for r in found.resources], ["", ""])
        self.assertEqual(found.levels[""], (2, 2, 0))

    def test_over_the_cap_infrastructure_folds_first_then_third_parties(self) -> None:
        """Fourteen boxes and four resources: the two whose every edge is infrastructure become one
        Infrastructure node; the count is still over, so the two third parties become one External
        services node; the store stays, because there is only one and a group of one saves nothing."""
        boxes = [str(index) for index in range(1, LIMIT)]
        resources = [
            resource("registry", ResourceKind.API),
            resource("collector", ResourceKind.API),
            resource("openai", ResourceKind.API),
            resource("stripe", ResourceKind.API),
            resource("pg"),
        ]
        homes = {r.key: "" for r in resources}
        placement = placement_of(
            homes,
            ("1/pom.xml", "resource:api:registry", EdgeKind.REGISTERS_WITH),
            ("2/pom.xml", "resource:api:registry", EdgeKind.REGISTERS_WITH),
            ("1/pom.xml", "resource:api:collector", EdgeKind.REPORTS_TO),
            ("1/pom.xml", "resource:api:openai", EdgeKind.USES),
            ("1/pom.xml", "resource:api:stripe", EdgeKind.USES),
            ("1/pom.xml", "resource:db:pg", EdgeKind.USES),
        )

        found = layout(resources, placement, {"": boxes})

        self.assertEqual(
            {r.name: r.group for r in found.resources},
            {"registry": INFRASTRUCTURE, "collector": INFRASTRUCTURE, "openai": EXTERNAL, "stripe": EXTERNAL, "pg": ""},
        )
        self.assertEqual(found.levels[""], (LIMIT - 1, 3, 2))

    def test_data_stores_fold_only_when_resources_still_take_more_than_half_the_level(self) -> None:
        boxes = ["1", "2", "3", "4", "5", "6"]
        resources = [resource(f"db{index}") for index in range(10)]
        placement = placement_of({r.key: "" for r in resources})

        found = layout(resources, placement, {"": boxes})

        self.assertEqual({r.group for r in found.resources}, {STORES})
        self.assertEqual(found.levels[""], (6, 1, 0))

    def test_what_the_folds_cannot_settle_is_reported_and_left_to_the_clustering(self) -> None:
        """Eighteen boxes and one shared store: nothing to fold, three over, and the level says so."""
        boxes = [str(index) for index in range(1, LIMIT + 3)]

        found = layout([resource("pg")], placement_of({"resource:db:pg": ""}), {"": boxes})

        self.assertEqual(found.levels[""], (LIMIT + 2, 1, 3))

    def test_a_badge_counts_toward_no_level(self) -> None:
        boxes = [str(index) for index in range(1, LIMIT + 1)]

        found = layout([resource("mine")], placement_of({"resource:db:mine": "3"}), {"": boxes})

        self.assertEqual((found.resources[0].badge, found.levels[""]), (True, (LIMIT, 0, 0)))


if __name__ == "__main__":
    unittest.main()
