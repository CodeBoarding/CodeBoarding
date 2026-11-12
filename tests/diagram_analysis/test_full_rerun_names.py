"""A full run over a previous analysis keeps what that analysis said about the components it continues."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scope_analysis_agent import ScopeAnalysisResult, ScopeComponentSemantics
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.io_utils import load_full_analysis
from infra.clustering_ids import ROOT_SCOPE_ID
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node

WORDS = ["Ledger", "Dispatch", "Pricing", "Routing", "Archive", "Audit", "Cache", "Gateway", "Inbox", "Journal"]


class NamingAgent:
    """Names every group it may edit ``<word> <run>``, so a carried name is told apart from a fresh one."""

    def __init__(self, run: str):
        self.run = run
        self.calls: list[tuple[str, set[str], frozenset[str]]] = []

    def analyze(self, scope, analysis, editable_group_ids, locked_name_ids, *args, **kwargs):
        self.calls.append((scope.scope_id, set(editable_group_ids), frozenset(locked_name_ids)))
        return ScopeAnalysisResult(
            description=f"Scope {scope.scope_id} as of {self.run}",
            components=[
                ScopeComponentSemantics(
                    group_id=group_id,
                    name=f"{WORDS[index % len(WORDS)]} {self.run} handling",
                    description=f"{group_id} described in {self.run}",
                )
                for index, group_id in enumerate(sorted(editable_group_ids))
            ],
        )


class TestFullRerunKeepsNames(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.output = self.repo / ".codeboarding"
        self.packages = {"billing": 6, "orders": 5, "shipping": 4}

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, packages: dict[str, int], edited: str = "") -> StaticAnalysisResults:
        graph = CallGraph(language="python")
        files: list[str] = []
        for package, count in packages.items():
            for index in range(count):
                path = self.repo / "src" / package / f"{package}_{index}.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                body = "1" if str(path.relative_to(self.repo)) != edited else "2"
                path.write_text(f"def run_{index}():\n    return {body}\n\n\ndef stop_{index}():\n    return 0\n")
                files.append(str(path))
                module = f"src.{package}.{package}_{index}"
                graph.add_node(Node(f"{module}.run_{index}", NodeType.FUNCTION, str(path), 1, 2))
                graph.add_node(Node(f"{module}.stop_{index}", NodeType.FUNCTION, str(path), 5, 6))
        results = StaticAnalysisResults()
        results.add_cfg(Language.PYTHON, graph)
        results.add_source_files(Language.PYTHON, files)
        return results

    def _owner(self, package: str) -> str:
        loaded = load_full_analysis(self.output)
        assert loaded is not None
        return next(
            component.component_id
            for component in loaded[0].components
            if any(group.file_path.startswith(f"src/{package}/") for group in component.file_methods)
        )

    def _full_run(self, results: StaticAnalysisResults, run: str) -> tuple[NamingAgent, dict[str, tuple[str, str]]]:
        analyzer = MagicMock(collected_diagnostics={})
        analyzer.analyze.return_value = results
        generator = DiagramGenerator(
            repo_location=self.repo,
            temp_folder=Path(self.temp.name),
            repo_name="repo",
            output_dir=self.output,
            depth_cap=1,
            run_id=run,
            log_path=f"repo/{run}",
            static_analyzer=analyzer,
        )
        agent = NamingAgent(run)
        generator.scope_analysis_agent = agent
        generator._agent_llm = MagicMock()
        with patch("diagram_analysis.diagram_generator.ProjectScanner.scan", return_value=[]):
            generator.generate_analysis()
        loaded = load_full_analysis(self.output)
        assert loaded is not None
        root, _subs = loaded
        return agent, {c.component_id: (c.name, c.description) for c in root.components}

    def test_a_rerun_over_unchanged_code_asks_the_model_nothing_and_changes_nothing(self):
        _, first = self._full_run(self._write(self.packages), "first")
        agent, second = self._full_run(self._write(self.packages), "second")
        self.assertGreater(len(first), 1)
        self.assertEqual(second, first)
        self.assertEqual(agent.calls, [])

    def test_a_component_whose_file_changed_keeps_its_name_and_is_described_again(self):
        _, first = self._full_run(self._write(self.packages), "first")
        agent, second = self._full_run(self._write(self.packages, edited="src/orders/orders_0.py"), "second")
        [(scope_id, editable, locked)] = agent.calls
        self.assertEqual(scope_id, ROOT_SCOPE_ID)
        self.assertEqual(len(editable), 1)
        self.assertEqual(locked, frozenset(editable))
        [changed] = editable
        self.assertEqual(second[changed][0], first[changed][0])
        self.assertIn("second", second[changed][1])
        self.assertEqual(
            {key: value for key, value in second.items() if key != changed},
            {key: value for key, value in first.items() if key != changed},
        )

    def test_ids_follow_the_code_when_the_size_order_changes(self):
        """Shipping grows from the smallest package to the largest; 4 of its 7 files are the old ones."""
        _, first = self._full_run(self._write(self.packages), "first")
        before = {package: self._owner(package) for package in self.packages}
        agent, second = self._full_run(self._write(self.packages | {"shipping": 7}), "second")
        self.assertEqual({package: self._owner(package) for package in self.packages}, before)
        [(_, editable, locked)] = agent.calls
        self.assertEqual(editable, {before["shipping"]})
        self.assertEqual(locked, frozenset(), "4 of 7 files is too little of the old component to lock its name")
        for package in ("billing", "orders"):
            self.assertEqual(second[before[package]], first[before[package]])

    def test_a_new_package_gets_a_fresh_id_and_a_fresh_name(self):
        _, first = self._full_run(self._write(self.packages), "first")
        agent, second = self._full_run(self._write(self.packages | {"returns": 5}), "second")
        [new_id] = second.keys() - first.keys()
        self.assertEqual(int(new_id), max(int(component_id) for component_id in first) + 1)
        self.assertIn("second", second[new_id][0])
        self.assertTrue(all(second[component_id] == first[component_id] for component_id in first))

    def test_no_previous_analysis_names_everything(self):
        agent, first = self._full_run(self._write(self.packages), "first")
        [(scope_id, editable, locked)] = agent.calls
        self.assertEqual(editable, set(first))
        self.assertEqual(locked, frozenset())


if __name__ == "__main__":
    unittest.main()
