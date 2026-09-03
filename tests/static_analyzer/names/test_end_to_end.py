"""The public path the pipeline will use: CallGraph -> units -> draft -> replay."""

from clustering_ids import ROOT_SCOPE_ID
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering.names import KinshipGrouper, draft_tree, replay, role_words_for, units_from_graphs
from static_analyzer.config import NodeType
from static_analyzer.node import Node
from tests.static_analyzer.names.conftest import rule_of, scope_of


def graph(language: str, files: dict[str, list[tuple[str, NodeType]]]) -> CallGraph:
    out = CallGraph(language=language)
    for path, symbols in files.items():
        for index, (name, kind) in enumerate(symbols):
            out.add_node(Node(name, kind, path, index * 3 + 1, index * 3 + 2))
    return out


def test_a_two_language_repo_drafts_replays_and_survives_json():
    python = graph(
        "python",
        {
            "/repo/backend/orders/models.py": [
                ("backend.orders.models.Order", NodeType.CLASS),
                ("backend.orders.models.Order.total", NodeType.METHOD),
            ],
            "/repo/backend/orders/views.py": [("backend.orders.views.list_orders", NodeType.FUNCTION)],
            "/repo/backend/catalog/models.py": [("backend.catalog.models.Item", NodeType.CLASS)],
            "/repo/backend/catalog/views.py": [("backend.catalog.views.list_items", NodeType.FUNCTION)],
            "/repo/backend/main.py": [("backend.main.run", NodeType.FUNCTION)],
        },
    )
    typescript = graph(
        "typescript",
        {
            "/repo/frontend/orders/OrderPage.tsx": [("frontend.orders.OrderPage.OrderPage", NodeType.FUNCTION)],
            "/repo/frontend/orders/OrderRow.tsx": [("frontend.orders.OrderRow.OrderRow", NodeType.FUNCTION)],
            "/repo/frontend/catalog/ItemPage.tsx": [("frontend.catalog.ItemPage.ItemPage", NodeType.FUNCTION)],
            "/repo/frontend/catalog/ItemCard.tsx": [("frontend.catalog.ItemCard.ItemCard", NodeType.FUNCTION)],
            "/repo/frontend/catalog/ItemList.tsx": [("frontend.catalog.ItemList.ItemList", NodeType.FUNCTION)],
        },
    )
    units = units_from_graphs({"python": python, "typescript": typescript})
    assert [unit.language for unit in units] == ["python"] * 5 + ["typescript"] * 5

    spec = draft_tree(units, KinshipGrouper(), 2)
    root = scope_of(spec, ROOT_SCOPE_ID)
    assert sorted(rule.name for rule in root.components if not rule.is_fallback_only) == ["catalog", "orders"]
    orders = next(rule for rule in root.components if rule.name == "orders")
    assert orders.prefixes == (("backend", "orders"), ("frontend", "orders"))

    partition = replay(units, root, role_words_for(spec.machinery))
    by_unit = {unit_id: rule_of(root, component_id).name for unit_id, component_id in partition.assignment.items()}
    assert by_unit["/repo/frontend/orders/OrderRow.tsx"] == "orders"
    assert by_unit["/repo/backend/catalog/views.py"] == "catalog"
    assert by_unit["/repo/backend/main.py"] == "Loose files in backend"

    reloaded = spec.__class__.from_dict(spec.to_dict())
    again = replay(units, scope_of(reloaded, ROOT_SCOPE_ID), role_words_for(reloaded.machinery))
    assert again.assignment == partition.assignment
    assert scope_of(reloaded, orders.component_id).rung == "unmerge"
