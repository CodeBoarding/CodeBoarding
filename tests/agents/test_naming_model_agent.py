import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import ComponentNaming, NamingModelInsights
from agents.naming_model_agent import NamingModelAgent
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


def _analysis(nodes: list[tuple[str, str]]) -> StaticAnalysisResults:
    graph = CallGraph(language="csharp")
    for index, (qualified_name, file_path) in enumerate(nodes):
        graph.add_node(Node(qualified_name, NodeType.CLASS, file_path, index + 1, index + 2))
    results = StaticAnalysisResults()
    results.add_cfg(Language.CSHARP, graph)
    results.add_references(Language.CSHARP, list(graph.nodes.values()))
    return results


class TestNamingModelAgentEvidence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "eShop"
        (self.repo_dir / "src" / "Ordering.API").mkdir(parents=True)
        (self.repo_dir / "src" / "Catalog.API").mkdir(parents=True)

        self.static_analysis = _analysis(
            [
                ("Ordering.API.Apis.OrdersApi", str(self.repo_dir / "src/Ordering.API/OrdersApi.cs")),
                ("Catalog.API.Apis.CatalogApi", str(self.repo_dir / "src/Catalog.API/CatalogApi.cs")),
            ]
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _agent(self) -> NamingModelAgent:
        llm = MagicMock()
        llm.model_name = "test-model"
        return NamingModelAgent(self.repo_dir, self.static_analysis, llm, llm)

    def test_scopes_are_read_against_the_repo_root(self):
        """Nodes carry absolute paths; without the root every scope collapses to a filesystem
        component and the model is shown one scope for the whole repo."""
        scopes, _, _ = self._agent()._evidence()
        self.assertEqual(scopes, ["Catalog.API", "Ordering.API"])

    def test_evidence_carries_vocabulary_and_identifiers(self):
        scopes, vocabulary, identifiers = self._agent()._evidence()
        self.assertIn("OrdersApi", identifiers)
        self.assertIn("Api", dict(vocabulary))
        self.assertTrue(scopes)


class TestNamingModelAgentParsing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_empty_repository_yields_an_empty_model(self):
        llm = MagicMock()
        llm.model_name = "test-model"
        model = NamingModelAgent(self.repo_dir, _analysis([]), llm, llm).read_naming_model()
        self.assertEqual(model.components, ())
        self.assertEqual(model.machinery, frozenset())

    def test_insights_become_a_naming_model(self):
        analysis = _analysis([("Ordering.Order", str(self.repo_dir / "src/Ordering/Order.cs"))])
        llm = MagicMock()
        llm.model_name = "test-model"
        agent = NamingModelAgent(self.repo_dir, analysis, llm, llm)
        agent._parse_invoke = MagicMock(
            return_value=NamingModelInsights(
                components=[ComponentNaming(name="Ordering", owns=["Order", "Payment"])],
                machinery=["Handler", "Repository"],
            )
        )
        model = agent.read_naming_model()
        self.assertEqual([c.name for c in model.components], ["Ordering"])
        self.assertEqual(model.components[0].owns, ("Order", "Payment"))
        self.assertEqual(model.machinery, frozenset({"Handler", "Repository"}))


if __name__ == "__main__":
    unittest.main()
