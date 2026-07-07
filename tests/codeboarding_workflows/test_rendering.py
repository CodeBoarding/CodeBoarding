"""Render-time projection of global leaf-only relations to per-level views.

Catches the bug Devin flagged on PR #246: if ``components_relations`` is
serialized as a single global leaf set at root, the mermaid render at root
(or at any non-leaf sub-analysis level) must not emit edges referencing
component names that are not declared as nodes in that diagram.
"""

import json
import re
from pathlib import Path

from agents.agent_responses import Relation
from codeboarding_workflows.rendering import (
    _ancestor_in_level,
    _load_entries,
    project_relations_to_level,
    render_docs,
)


# ---------------------------------------------------------------------------
# project_relations_to_level — unit tests
# ---------------------------------------------------------------------------


def _rel(src_id: str, dst_id: str, label: str = "calls", edge_count: int = 1) -> Relation:
    return Relation(
        relation=label,
        src_name=src_id,
        dst_name=dst_id,
        src_id=src_id,
        dst_id=dst_id,
        edge_count=edge_count,
    )


def test_ancestor_in_level_direct_hit():
    assert _ancestor_in_level("1.1.1", {"1.1.1", "2"}) == "1.1.1"


def test_ancestor_in_level_rolls_up():
    assert _ancestor_in_level("1.1.1", {"1", "2"}) == "1"
    assert _ancestor_in_level("1.1.1", {"1.1", "2"}) == "1.1"


def test_ancestor_in_level_outside():
    assert _ancestor_in_level("3.5", {"1", "2"}) is None


def test_project_root_only():
    rels = [_rel("1.1.1", "2.1.2"), _rel("3", "4.1")]
    out = project_relations_to_level(rels, {"1", "2", "3", "4"}, {})
    pairs = {(r.src_id, r.dst_id) for r in out}
    assert pairs == {("1", "2"), ("3", "4")}


def test_project_drops_self_edges_after_rollup():
    # Both endpoints collapse to "1" — would render as 1 -> 1, dropped.
    rels = [_rel("1.1.1", "1.2.1")]
    out = project_relations_to_level(rels, {"1", "2"}, {})
    assert out == []


def test_project_aggregates_duplicates():
    rels = [_rel("1.1.1", "2.1.1", edge_count=3), _rel("1.1.2", "2.1.2", edge_count=2)]
    out = project_relations_to_level(rels, {"1", "2"}, {})
    assert len(out) == 1
    assert out[0].src_id == "1" and out[0].dst_id == "2"
    assert out[0].edge_count == 5


def test_project_uses_id_to_name_map():
    rels = [_rel("1.1.1", "2.1.2")]
    out = project_relations_to_level(rels, {"1", "2"}, {"1": "API", "2": "Core"})
    assert out[0].src_name == "API" and out[0].dst_name == "Core"


def test_project_rolledup_label_is_input_order_deterministic():
    # Two differently-labeled leaf relations collapse to the same level pair (1->2).
    # project_relations_to_level keeps the first label seen, so the chosen label
    # depends entirely on input order. build_global_relations sorts its output by
    # (src_id, dst_id, relation), which is what makes this deterministic in the
    # real pipeline — assert that the label matches the sorted-first leaf ("reads"
    # < "writes") regardless of the order the caller happens to pass them in.
    a = _rel("1.1.1", "2.1.1", label="reads")
    b = _rel("1.1.2", "2.1.2", label="writes")
    sorted_input = sorted([b, a], key=lambda r: (r.src_id, r.dst_id, r.relation))

    forward = project_relations_to_level(sorted_input, {"1", "2"}, {})
    backward = project_relations_to_level(list(reversed(sorted_input)), {"1", "2"}, {})

    # The pipeline always feeds the (src_id, dst_id, relation)-sorted set, so the
    # winning label must be stable — pin it to the sorted-first leaf's label.
    assert len(forward) == 1
    assert forward[0].relation == "reads"
    # Same sorted input in reverse would flip the label — proving the sort in
    # build_global_relations is what guarantees determinism, not projection itself.
    assert backward[0].relation == "writes"


def test_project_drops_relations_outside_level():
    # 5.x is not part of the level at all — drop the edge.
    rels = [_rel("5.1", "5.2"), _rel("1.1", "5.1")]
    out = project_relations_to_level(rels, {"1", "2"}, {})
    assert out == []


def test_project_passes_through_existing_level_ids():
    # No roll-up needed when src/dst already match.
    rels = [_rel("1.1.1", "1.1.2"), _rel("1.1.1", "1.2.1")]
    out = project_relations_to_level(rels, {"1.1.1", "1.1.2", "1.2.1"}, {})
    pairs = {(r.src_id, r.dst_id) for r in out}
    assert pairs == {("1.1.1", "1.1.2"), ("1.1.1", "1.2.1")}


# ---------------------------------------------------------------------------
# End-to-end: render_docs from a fabricated depth-3 analysis.json
# ---------------------------------------------------------------------------


def _make_depth3_unified_json() -> dict:
    """Synthetic depth-3 analysis with only leaf-level relations at root.

    Mirrors the post-rebuild_global_relations shape: root carries the global
    leaf set; every nested ``components`` is a plain tree with no per-level
    relations.
    """
    return {
        "metadata": {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "commit_hash": "deadbeef",
            "repo_name": "fake",
            "depth_level": 3,
            "file_coverage_summary": {
                "total_files": 0,
                "analyzed": 0,
                "not_analyzed": 0,
                "not_analyzed_by_reason": {},
            },
        },
        "description": "fake project",
        "files": {},
        "methods_index": {},
        "components": [
            {
                "name": "API",
                "component_id": "1",
                "description": "API root",
                "key_entities": [],
                "source_cluster_ids": [],
                "file_methods": [],
                "can_expand": True,
                "components": [
                    {
                        "name": "Public",
                        "component_id": "1.1",
                        "description": "Public",
                        "key_entities": [],
                        "source_cluster_ids": [],
                        "file_methods": [],
                        "can_expand": True,
                        "components": [
                            {
                                "name": "REST",
                                "component_id": "1.1.1",
                                "description": "REST",
                                "key_entities": [],
                                "source_cluster_ids": [],
                                "file_methods": [],
                                "can_expand": False,
                            },
                            {
                                "name": "GraphQL",
                                "component_id": "1.1.2",
                                "description": "GraphQL",
                                "key_entities": [],
                                "source_cluster_ids": [],
                                "file_methods": [],
                                "can_expand": False,
                            },
                        ],
                    },
                ],
            },
            {
                "name": "Core",
                "component_id": "2",
                "description": "Core",
                "key_entities": [],
                "source_cluster_ids": [],
                "file_methods": [],
                "can_expand": True,
                "components": [
                    {
                        "name": "Auth",
                        "component_id": "2.1",
                        "description": "Auth",
                        "key_entities": [],
                        "source_cluster_ids": [],
                        "file_methods": [],
                        "can_expand": False,
                    },
                ],
            },
            {
                "name": "Storage",
                "component_id": "3",
                "description": "Storage",
                "key_entities": [],
                "source_cluster_ids": [],
                "file_methods": [],
                "can_expand": False,
            },
        ],
        # Only leaf-level relations — what rebuild_global_relations produces.
        "components_relations": [
            # Sibling edge inside Public (1.1.1 -> 1.1.2) — survives at the Public level.
            {
                "relation": "delegates to",
                "src_name": "REST",
                "dst_name": "GraphQL",
                "src_id": "1.1.1",
                "dst_id": "1.1.2",
                "edge_count": 1,
                "is_static": True,
            },
            {
                "relation": "calls",
                "src_name": "REST",
                "dst_name": "Auth",
                "src_id": "1.1.1",
                "dst_id": "2.1",
                "edge_count": 5,
                "is_static": True,
            },
            {
                "relation": "queries",
                "src_name": "REST",
                "dst_name": "Storage",
                "src_id": "1.1.1",
                "dst_id": "3",
                "edge_count": 2,
                "is_static": True,
            },
            {
                "relation": "authenticates",
                "src_name": "GraphQL",
                "dst_name": "Auth",
                "src_id": "1.1.2",
                "dst_id": "2.1",
                "edge_count": 1,
                "is_static": False,
            },
        ],
    }


def _extract_mermaid_nodes_and_edges(md_text: str) -> tuple[set[str], list[tuple[str, str]]]:
    """Return (declared node keys, [(src_key, dst_key), ...]) from a mermaid block."""
    nodes = set(re.findall(r"^\s*([A-Za-z0-9_]+)\[", md_text, flags=re.MULTILINE))
    edges = re.findall(r"^\s*([A-Za-z0-9_]+)\s*--\s*\"[^\"]*\"\s*-->\s*([A-Za-z0-9_]+)", md_text, flags=re.MULTILINE)
    return nodes, edges


def test_render_docs_root_has_no_phantom_nodes(tmp_path: Path):
    """The root overview.md must not reference component names that aren't declared as nodes."""
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps(_make_depth3_unified_json()))

    render_docs(
        analysis_path,
        repo_name="fake",
        repo_ref="",
        temp_dir=tmp_path,
        format=".md",
        root_name="overview",
    )

    md = (tmp_path / "overview.md").read_text()
    # Just the first mermaid block — at the top of the file.
    nodes, edges = _extract_mermaid_nodes_and_edges(md.split("```")[1])
    # The root entry must declare API, Core, Storage as nodes.
    assert {"API", "Core", "Storage"}.issubset(nodes), nodes
    # Every edge endpoint must be a declared node — no phantoms.
    for src, dst in edges:
        assert src in nodes, f"Phantom src '{src}' not in declared nodes {nodes}"
        assert dst in nodes, f"Phantom dst '{dst}' not in declared nodes {nodes}"
    # And specifically — at least one edge must exist (rolled-up from the leaf set).
    assert edges, "Root mermaid has no edges; expected leaf relations to roll up to 1->2, 1->3"


def test_render_docs_sub_level_renders_sibling_edges(tmp_path: Path):
    """The Public sub-analysis ({1.1.1, 1.1.2}) must render the leaf sibling edge 1.1.1->1.1.2."""
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps(_make_depth3_unified_json()))

    render_docs(
        analysis_path,
        repo_name="fake",
        repo_ref="",
        temp_dir=tmp_path,
        format=".md",
        root_name="overview",
    )

    public_md = (tmp_path / "Public.md").read_text()
    nodes, edges = _extract_mermaid_nodes_and_edges(public_md.split("```")[1])
    assert {"REST", "GraphQL"}.issubset(nodes)
    assert ("REST", "GraphQL") in edges, edges
    # No phantoms at sub-level either.
    for src, dst in edges:
        assert src in nodes and dst in nodes, (src, dst, nodes)


def test_load_entries_projects_per_level(tmp_path: Path):
    """``_load_entries`` should replace each entry's ``components_relations`` with the projected set."""
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps(_make_depth3_unified_json()))

    entries = _load_entries(analysis_path)

    # Root entry is first, then sub-analyses in some order.
    fname, root_analysis, _ = entries[0]
    assert fname == "__root__"
    root_pairs = {(r.src_id, r.dst_id) for r in root_analysis.components_relations}
    # Leaf relations roll up at root: 1.1.1->2.1 and 1.1.2->2.1 both collapse to
    # 1->2 (aggregated); 1.1.1->3 collapses to 1->3; 1.1.1->1.1.2 collapses to a
    # 1->1 self-loop and is dropped.
    assert root_pairs == {("1", "2"), ("1", "3")}
