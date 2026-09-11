"""A run's diagnostics ride in analysis.json metadata, and never outlive the run."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis
from run_diagnostics import RunDiagnostics
from run_diagnostics.catalog import language_engine_failed


class TestRunDiagnosticsPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.analysis = AnalysisInsights(
            description="root",
            components=[Component(name="A", description="", key_entities=[], component_id="1")],
            components_relations=[],
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _save(self, run_diagnostics):
        return save_analysis(
            analysis=self.analysis,
            output_dir=self.temp_dir,
            sub_analyses={},
            repo_name="repo",
            repo_dir=self.temp_dir,
            source_tree_hash="abc",
            depth_cap=2,
            run_diagnostics=run_diagnostics,
        )

    def _metadata(self) -> dict:
        metadata = load_analysis_metadata(self.temp_dir)
        assert metadata is not None
        return metadata

    def test_a_clean_run_writes_an_empty_report(self):
        self._save(RunDiagnostics().report())
        report = self._metadata()["run_diagnostics"]
        self.assertEqual((report["degraded"], report["notices"], report["entries"]), (0, 0, []))

    def test_a_degradation_reaches_the_document_with_its_wording(self):
        collector = RunDiagnostics()
        collector.record(language_engine_failed("TypeScript", "tsserver exited"))

        self._save(collector.report())

        report = self._metadata()["run_diagnostics"]
        self.assertEqual(report["degraded"], 1)
        entry = report["entries"][0]
        self.assertEqual(entry["code"], "static.language_engine_failed")
        self.assertEqual(entry["subject"], "TypeScript")
        self.assertIn("tsserver exited", entry["detail"])
        self.assertTrue(entry["remedy"])

    def test_a_later_clean_save_clears_the_previous_run_report(self):
        """Unlike the tree spec, diagnostics describe one run: inheriting them would lie."""
        collector = RunDiagnostics()
        collector.record(language_engine_failed("TypeScript", "tsserver exited"))
        self._save(collector.report())

        self._save(RunDiagnostics().report())

        self.assertEqual(self._metadata()["run_diagnostics"]["entries"], [])

    def test_an_intermediate_save_that_omits_the_report_writes_an_empty_one(self):
        self._save(None)
        self.assertEqual(self._metadata()["run_diagnostics"]["entries"], [])

    def test_an_analysis_written_before_the_field_existed_still_parses(self):
        self._save(RunDiagnostics().report())
        path = self.temp_dir / "analysis.json"
        data = json.loads(path.read_text())
        del data["metadata"]["run_diagnostics"]
        path.write_text(json.dumps(data))

        # Reading through the store must not raise on the missing key.
        self.assertNotIn("run_diagnostics", self._metadata())


if __name__ == "__main__":
    unittest.main()
