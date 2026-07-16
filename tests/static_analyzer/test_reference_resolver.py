import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import AnalysisInsights, Component, Relation, RelationEdge, SourceCodeReference
from agents.file_index_models import FileMethodGroup, MethodEntry
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import Edge
from static_analyzer.node import Node
from static_analyzer.program_graph import ProgramNode, ProgramNodeKind
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

    def _node(self, qname: str, rel_file: str, start: int = 1, end: int = 2) -> ProgramNode:
        return ProgramNode(
            node_id=qname,
            kind=ProgramNodeKind.SYMBOL,
            language="python",
            name=qname.rsplit(".", 1)[-1],
            file_path=str(self.repo_dir / rel_file),
            symbol_type=NodeType.FUNCTION,
            line_start=start,
            line_end=end,
            reference_worthy=True,
        )

    def _file_methods(self, qname: str, rel_file: str) -> list[FileMethodGroup]:
        return [
            FileMethodGroup(
                file_path=rel_file,
                methods=[MethodEntry(qualified_name=qname, start_line=1, end_line=2, node_type="FUNCTION")],
            )
        ]

    def test_fix_source_code_reference_lines_resolves_key_entities_and_returns_relative_paths(self):
        node = self._node("service.OCR.extract_text", "service.py", 3, 4)
        self.static_analysis.get_reference.return_value = node
        component = Component(
            name="Service",
            description="",
            key_entities=[SourceCodeReference(qualified_name="service.OCR.extract_text")],
            file_methods=self._file_methods("service.OCR.extract_text", "service.py"),
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

        self.assertEqual(reference.qualified_name, node.id)
        self.assertEqual(reference.reference_file, str(self.repo_dir / "service.py"))

    def test_resolve_reference_uses_matching_file_candidate(self):
        self.static_analysis.get_reference.side_effect = ValueError("not found")
        self.static_analysis.get_loose_reference.side_effect = Exception("not found")
        reference = SourceCodeReference(qualified_name="module.file.test_function")

        self.resolver.resolve_reference(reference, ["module/file.py"])

        self.assertEqual(reference.reference_file, str(self.repo_dir / "module" / "file.py"))

    def test_resolve_reference_uses_repo_relative_path_without_candidates(self) -> None:
        self.static_analysis.get_reference.side_effect = ValueError("not found")
        self.static_analysis.get_loose_reference.side_effect = ValueError("not found")
        reference = SourceCodeReference(qualified_name="module.file")

        self.resolver.resolve_reference(reference)

        self.assertEqual(reference.reference_file, str(self.repo_dir / "module" / "file.py"))

    def test_repair_key_entity_references_canonicalizes_deduplicates_and_drops_unresolved(self) -> None:
        canonical_qname = "service.OCR.extract_text"
        node = self._node(canonical_qname, "service.py", 3, 4)

        def get_reference(_language: object, qname: str) -> ProgramNode:
            if qname == canonical_qname:
                return node
            raise ValueError("not found")

        def get_loose_reference(_language: object, qname: str) -> tuple[str | None, ProgramNode | None]:
            if qname == "OCR.extract_text":
                return canonical_qname, node
            return None, None

        self.static_analysis.get_reference.side_effect = get_reference
        self.static_analysis.get_loose_reference.side_effect = get_loose_reference
        self.static_analysis.iter_reference_nodes.return_value = [node]
        references = [
            SourceCodeReference(qualified_name="OCR.extract_text"),
            SourceCodeReference(qualified_name=canonical_qname),
            SourceCodeReference(qualified_name="hallucinated.missing"),
        ]

        repair = self.resolver.repair_key_entity_references(references)

        self.assertEqual([reference.qualified_name for reference in repair.references], [canonical_qname])
        self.assertEqual(repair.canonicalized_count, 1)
        self.assertEqual(repair.unresolved_qnames, {"hallucinated.missing"})

    def test_key_entity_resolution_refreshes_persisted_location(self):
        node = self._node("service.OCR.extract_text", "service.py", 30, 40)
        self.static_analysis.get_reference.return_value = node
        reference = SourceCodeReference(
            qualified_name="service.OCR.extract_text",
            reference_file="service.py",
            reference_start_line=3,
            reference_end_line=4,
        )
        component = Component(
            name="Service",
            description="",
            key_entities=[reference],
            component_id="1",
            file_methods=self._file_methods("service.OCR.extract_text", "service.py"),
        )
        analysis = AnalysisInsights(description="", components=[component], components_relations=[])

        self.resolver.fix_key_entities_refs(analysis, {"1"})

        self.assertEqual(reference.reference_start_line, 30)
        self.assertEqual(reference.reference_end_line, 40)

    def test_key_entity_resolution_drops_deleted_symbol_even_when_file_still_exists(self):
        self.static_analysis.get_reference.side_effect = ValueError("not found")
        self.static_analysis.get_loose_reference.return_value = ("", None)
        reference = SourceCodeReference(
            qualified_name="service.deleted",
            reference_file="service.py",
            reference_start_line=3,
            reference_end_line=4,
        )

        repair = self.resolver.repair_key_entity_references([reference])

        self.assertEqual(repair.references, [])
        self.assertEqual(repair.unresolved_qnames, {"service.deleted"})

    def test_component_scope_prevents_fuzzy_resolution_into_another_component(self):
        scoped = self._node("a.Handler.run", "service.py")
        other = self._node("b.Worker.run", "module/file.py")
        self.static_analysis.get_reference.side_effect = ValueError("not found")
        self.static_analysis.iter_reference_nodes.return_value = [other, scoped]
        reference = SourceCodeReference(qualified_name="Handler.run")
        component = Component(
            name="Service",
            description="",
            key_entities=[reference],
            component_id="1",
            file_methods=self._file_methods("a.Handler.run", "service.py"),
        )
        analysis = AnalysisInsights(description="", components=[component], components_relations=[])

        self.resolver.fix_key_entities_refs(analysis, {"1"})

        self.assertEqual([entity.qualified_name for entity in component.key_entities], ["a.Handler.run"])

    def test_component_scope_rejects_exact_reference_owned_by_another_component(self):
        scoped = self._node("a.Handler.run", "service.py")
        other = self._node("b.Worker.run", "module/file.py")
        self.static_analysis.get_reference.return_value = other
        self.static_analysis.iter_reference_nodes.return_value = [other, scoped]
        reference = SourceCodeReference(qualified_name="b.Worker.run")
        component = Component(
            name="Service",
            description="",
            key_entities=[reference],
            component_id="1",
            file_methods=self._file_methods("a.Handler.run", "service.py"),
        )
        analysis = AnalysisInsights(description="", components=[component], components_relations=[])

        self.resolver.fix_key_entities_refs(analysis, {"1"})

        self.assertEqual(component.key_entities, [])

    def test_fix_source_code_reference_lines_resolves_relation_edges_and_call_sites(self):
        source_node = self._node("service.OCR.extract_text", "service.py", 1, 2)
        target_node = self._node("module.file.test_function", "module/file.py", 3, 4)
        self.static_analysis.get_reference.side_effect = [source_node, target_node]
        static_edge = Edge(
            Node(source_node.id, NodeType.FUNCTION, source_node.file_path, 1, 2),
            Node(target_node.id, NodeType.FUNCTION, target_node.file_path, 3, 4),
            [{"line": 7, "column": 8}],
        )
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
        self.assertEqual([site.model_dump() for site in edge.call_sites], [{"line": 7, "column": 8}])

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
