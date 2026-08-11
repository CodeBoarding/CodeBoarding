"""End-to-end tests for ``cluster_viz.export`` over a hand-built artifact directory."""

import json
import pickle
import tempfile
import unittest
from pathlib import Path

from cluster_viz.export import export_clustering
from cluster_viz.render import render_html
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.graph import CallGraph, ClusterResult, EdgeKind
from static_analyzer.node import Node

_FILES = {0: "engine/parse.py", 1: "engine/write.py", 2: "cli/main.py"}


def _cluster_result(clusters: dict[int, set[str]], cfg: CallGraph) -> ClusterResult:
    cluster_to_files = {cid: {cfg.nodes[qname].file_path for qname in members} for cid, members in clusters.items()}
    file_to_clusters: dict[str, set[int]] = {}
    for cid, files in cluster_to_files.items():
        for file_path in files:
            file_to_clusters.setdefault(file_path, set()).add(cid)
    return ClusterResult(
        clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="test"
    )


def _build_cfg() -> tuple[CallGraph, dict[int, set[str]], dict[int, set[str]]]:
    """Six methods in three tight groups, clustered once at the root and once inside component 1."""
    cfg = CallGraph(language="python")
    names: dict[int, list[str]] = {}
    for group in range(3):
        names[group] = []
        for index in range(2):
            qname = f"pkg{group}.mod.fn{index}"
            names[group].append(qname)
            cfg.add_node(
                Node(
                    fully_qualified_name=qname,
                    node_type=NodeType.FUNCTION,
                    file_path=_FILES[group],
                    line_start=1 + index * 10,
                    line_end=9 + index * 10,
                )
            )
    for group, members in names.items():
        cfg.add_edge(members[0], members[1], [{"file": _FILES[group], "line": 3}])
    cfg.add_edge(names[0][1], names[1][0])
    cfg.add_reference_edge(names[0][0], names[1][1], EdgeKind.TYPEREF)

    root_clusters = {index: set(members) for index, members in names.items()}
    sub_clusters = {1: set(names[0]), 2: set(names[1])}
    return cfg, root_clusters, sub_clusters


def _write_artifacts(directory: Path) -> None:
    cfg, root_clusters, sub_clusters = _build_cfg()
    cfg.record_cluster_paths(_cluster_result(root_clusters, cfg))
    cfg.record_cluster_paths(_cluster_result(sub_clusters, cfg), "1")
    cfg._cluster_cache = _cluster_result(root_clusters, cfg)

    static_analysis = StaticAnalysisResults()
    static_analysis.add_cfg(Language.PYTHON, cfg)
    (directory / "static_analysis.pkl").write_bytes(pickle.dumps(static_analysis))

    analysis = {
        "metadata": {"repo_name": "demo", "depth_level": 2, "generated_at": "2026-01-01T00:00:00+00:00"},
        "description": "a demo repository",
        "components": [
            {
                "component_id": "1",
                "name": "Engine",
                "description": "parses and writes",
                "source_cluster_ids": ["0", "1"],
                "key_entities": [{"qualified_name": "pkg0.mod.fn0"}],
                "components": [
                    {"component_id": "1.1", "name": "Parser", "source_cluster_ids": ["1.1"], "components": []},
                    {"component_id": "1.2", "name": "Writer", "source_cluster_ids": ["1.2"], "components": []},
                ],
            },
            {"component_id": "2", "name": "CLI", "source_cluster_ids": ["2"], "components": []},
        ],
    }
    (directory / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")


class TestExportClustering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        directory = Path(cls._tmp.name)
        _write_artifacts(directory)
        cls.payload = export_clustering(directory, directory)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_method_is_exported_with_its_lineage(self):
        nodes = self.payload["nodes"]
        self.assertEqual(len(nodes), 6)
        by_name = {node["q"]: node for node in nodes}
        self.assertEqual(by_name["pkg0.mod.fn0"]["path"], ["0", "1.1"])
        self.assertEqual(by_name["pkg0.mod.fn0"]["comp"], "1.1")
        self.assertEqual(by_name["pkg2.mod.fn0"]["path"], ["2", ""])
        self.assertEqual(by_name["pkg2.mod.fn0"]["f"], "cli/main.py")

    def test_call_and_reference_edges_are_kept_apart(self):
        kinds = {self.payload["edge_kinds"][edge[2]] for edge in self.payload["edges"]}
        self.assertEqual(kinds, {"call", "typeref"})

    def test_scopes_cover_the_root_and_every_expanded_component(self):
        scopes = {scope["scope_id"]: scope for scope in self.payload["scopes"]}
        self.assertEqual(sorted(scopes), ["", "1"])
        self.assertEqual(scopes[""]["cluster_count"], 3)
        self.assertEqual(scopes[""]["level"], 1)
        self.assertEqual([group["component_id"] for group in scopes[""]["groups"]], ["1", "2"])
        self.assertEqual(scopes["1"]["level"], 2)

    def test_cluster_entries_carry_the_evidence_for_their_grouping(self):
        cluster = {scope["scope_id"]: scope for scope in self.payload["scopes"]}[""]["clusters"]["0"]
        self.assertEqual(cluster["size"], 2)
        self.assertEqual(cluster["component"], "1")
        self.assertEqual(cluster["files"], ["engine/parse.py"])
        self.assertIn(cluster["role"], {"seed", "promoted_seed", "absorbed"})
        self.assertGreaterEqual(cluster["internal_edges"], 1)

    def test_levels_summarize_the_hierarchy(self):
        self.assertEqual(
            self.payload["levels"],
            [
                {"level": 1, "scopes": [""], "clusters": 3, "components": 2},
                {"level": 2, "scopes": ["1"], "clusters": 2, "components": 2},
            ],
        )

    def test_components_report_how_much_code_they_own(self):
        components = {entry["component_id"]: entry for entry in self.payload["components"]}
        self.assertEqual(components["1"]["subtree_methods"], 4)
        self.assertEqual(components["1"]["own_methods"], 0)
        self.assertEqual(components["1.1"]["own_methods"], 2)
        self.assertEqual(components["2"]["parent_id"], "")

    def test_layout_places_every_method_inside_its_component_circle(self):
        layout = self.payload["layout"]
        self.assertEqual(len(layout["nodes"]), 6)
        self.assertIn("1", layout["circles"])
        self.assertEqual(layout["labels"]["1"], "Engine")
        node_index = next(i for i, node in enumerate(self.payload["nodes"]) if node["q"] == "pkg0.mod.fn0")
        x, y = layout["nodes"][node_index]
        cx, cy, radius = layout["circles"]["1.1"]
        self.assertLessEqual(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5, radius)

    def test_metadata_records_the_clustering_configuration(self):
        meta = self.payload["meta"]
        self.assertEqual(meta["project"], "demo")
        self.assertEqual(meta["languages"], ["python"])
        self.assertEqual(meta["node_count"], 6)
        self.assertEqual(meta["clustering"]["seed"], 42)
        self.assertEqual(meta["clustering"]["root_strategy"], {"python": "test"})

    def test_viewer_embeds_the_payload_and_keeps_the_script_intact(self):
        html = render_html(self.payload)
        self.assertNotIn("/*__CLUSTERING_PAYLOAD__*/null", html)
        self.assertIn("pkg0.mod.fn0", html)
        self.assertNotIn("</script>", html.split("<script>")[1].split("</script>")[0])


class TestUnclusteredMethods(unittest.TestCase):
    def test_a_method_with_no_lineage_is_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            _write_artifacts(directory)
            static_analysis = pickle.loads((directory / "static_analysis.pkl").read_bytes())
            cfg = static_analysis.get_cfg(Language.PYTHON)
            cfg.add_node(
                Node(
                    fully_qualified_name="pkg9.lonely.fn",
                    node_type=NodeType.FUNCTION,
                    file_path="stray/lonely.py",
                    line_start=1,
                    line_end=2,
                )
            )
            (directory / "static_analysis.pkl").write_bytes(pickle.dumps(static_analysis))

            payload = export_clustering(directory, directory)

            self.assertEqual(payload["meta"]["node_count"], 7)
            self.assertTrue(any("no cluster lineage" in warning for warning in payload["meta"]["warnings"]))
            lonely = next(node for node in payload["nodes"] if node["q"] == "pkg9.lonely.fn")
            self.assertEqual(lonely["comp"], "")
