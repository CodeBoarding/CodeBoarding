import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import AnalysisInsights, Component, Relation, RelationEdge, SourceCodeReference
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import Edge
from static_analyzer.node import Node
from static_analyzer.reference_resolver import StaticReferenceResolver


class TestStaticReferenceResolver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name)
        (self.repo_dir / "service.py").write_text("class OCR:\n    def extract_text(self):\n        pass\n")
        (self.repo_dir / "module").mkdir()
        (self.repo_dir / "module" / "file.py").write_text("def test_function():\n    pass\n")

        self.static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.static_analysis.get_languages.return_value = ["python"]
        self.static_analysis.iter_reference_nodes.return_value = []
        self.resolver = StaticReferenceResolver(self.repo_dir, self.static_analysis)

    def tearDown(self):
        self.tmp.cleanup()

    def _node(self, qname: str, rel_file: str, start: int = 1, end: int = 2) -> Node:
        return Node(qname, NodeType.FUNCTION, str(self.repo_dir / rel_file), start, end)

    def test_fix_source_code_reference_lines_resolves_key_entities_and_returns_relative_paths(self):
        node = self._node("service.OCR.extract_text", "service.py", 3, 4)
        self.static_analysis.get_reference.return_value = node
        component = Component(
            name="Service",
            description="",
            key_entities=[SourceCodeReference(qualified_name="service.OCR.extract_text")],
        )
        analysis = AnalysisInsights(description="", components=[component], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        reference = result.components[0].key_entities[0]
        self.assertEqual(reference.qualified_name, "service.OCR.extract_text")
        self.assertEqual(reference.reference_file, "service.py")
        self.assertEqual(reference.reference_start_line, 3)
        self.assertEqual(reference.reference_end_line, 4)

    def test_resolve_reference_prefers_symbol_match_across_languages_over_file_fallback(self):
        (self.repo_dir / "src").mkdir()
        (self.repo_dir / "src" / "widget.ts").write_text("export class Widget {}\n")
        self.static_analysis.get_languages.return_value = ["python", "typescript"]
        ts_node = self._node("src.widget.Widget", "src/widget.ts", 12, 34)
        self.static_analysis.get_reference.side_effect = [ValueError("not python"), ts_node]
        reference = SourceCodeReference(qualified_name="src.widget.Widget", reference_file="src/widget.ts")

        self.resolver.resolve_reference(reference)

        self.assertEqual(reference.reference_file, str(self.repo_dir / "src" / "widget.ts"))
        self.assertEqual(reference.reference_start_line, 12)
        self.assertEqual(reference.reference_end_line, 34)
        self.static_analysis.get_loose_reference.assert_not_called()

    def test_resolve_reference_uses_unique_token_match(self):
        node = self._node(
            "packages.markitdown.src.markitdown._markitdown.MarkItDown.enable_builtins",
            "service.py",
            10,
            20,
        )
        self.static_analysis.get_reference.side_effect = ValueError("not found")
        self.static_analysis.get_loose_reference.return_value = ("", None)
        self.static_analysis.iter_reference_nodes.return_value = [node]
        reference = SourceCodeReference(qualified_name="markitdown.MarkItDown:enable_builtins")

        self.resolver.resolve_reference(reference)

        self.assertEqual(reference.qualified_name, node.fully_qualified_name)
        self.assertEqual(reference.reference_file, str(self.repo_dir / "service.py"))

    def test_resolve_reference_uses_matching_file_candidate(self):
        self.static_analysis.get_reference.side_effect = ValueError("not found")
        self.static_analysis.get_loose_reference.side_effect = Exception("not found")
        reference = SourceCodeReference(qualified_name="module.file.test_function")

        self.resolver.resolve_reference(reference, ["module/file.py"])

        self.assertEqual(reference.reference_file, str(self.repo_dir / "module" / "file.py"))

    def test_fix_source_code_reference_lines_resolves_relation_edges_and_call_sites(self):
        source_node = self._node("service.OCR.extract_text", "service.py", 1, 2)
        target_node = self._node("module.file.test_function", "module/file.py", 3, 4)
        self.static_analysis.get_reference.side_effect = [source_node, target_node]
        static_edge = Edge(source_node, target_node, [{"line": 7, "column": 8}])
        cfg = MagicMock()
        cfg.edges = [static_edge]
        self.static_analysis.get_cfg.return_value = cfg
        relation = Relation(
            relation="calls",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="service.OCR.extract_text"),
                    target=SourceCodeReference(qualified_name="module.file.test_function"),
                )
            ],
        )
        analysis = AnalysisInsights(
            description="",
            components=[
                Component(name="Source", description="", key_entities=[]),
                Component(name="Target", description="", key_entities=[]),
            ],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        edge = result.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.reference_file, "service.py")
        self.assertEqual(edge.target.reference_file, "module/file.py")
        self.assertEqual(edge.call_sites, [{"line": 7, "column": 8}])

    def test_fix_source_code_reference_lines_keeps_external_target_edge(self):
        source_node = self._node("service.OCR.extract_text", "service.py", 1, 2)
        self.static_analysis.get_reference.side_effect = [source_node, ValueError("not found")]
        self.static_analysis.get_loose_reference.return_value = ("", None)
        self.static_analysis.iter_reference_nodes.return_value = [source_node]
        relation = Relation(
            relation="calls external LLM API",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="service.OCR.extract_text"),
                    target=SourceCodeReference(qualified_name="openai.OpenAI"),
                    description="External OpenAI client call",
                )
            ],
        )
        analysis = AnalysisInsights(
            description="",
            components=[Component(name="Source", description="", key_entities=[])],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(len(result.components_relations), 1)
        self.assertEqual(len(result.components_relations[0].key_edges), 1)

    def test_fix_source_code_reference_lines_drops_same_endpoint_key_edge(self):
        node = self._node("service.OCR.extract_text", "service.py", 1, 2)
        self.static_analysis.get_reference.side_effect = [node, node]
        relation = Relation(
            relation="self call",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="service.OCR.extract_text"),
                    target=SourceCodeReference(qualified_name="service.OCR.extract_text"),
                )
            ],
        )
        analysis = AnalysisInsights(
            description="",
            components=[Component(name="Source", description="", key_entities=[])],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(result.components_relations, [])


if __name__ == "__main__":
    unittest.main()
