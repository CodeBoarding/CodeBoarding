"""The tree specification rides in analysis.json metadata."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis
from diagram_analysis.analysis_json import FileCoverageSummary
from static_analyzer.clustering.exceptions import IncrementalCacheMissingError
from static_analyzer.clustering.names.spec import SPEC_VERSION

NO_COVERAGE = FileCoverageSummary(total_files=0, analyzed=0, not_analyzed=0)


class TestTreeSpecPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.analysis = AnalysisInsights(
            description="root",
            components=[Component(name="A", description="", key_entities=[], component_id="1")],
            components_relations=[],
        )
        self.spec = {"version": SPEC_VERSION, "grouper": "kinship", "machinery": [], "scopes": {"root": {"rules": []}}}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _save(self, tree_spec):
        return save_analysis(
            analysis=self.analysis,
            output_dir=self.temp_dir,
            sub_analyses={},
            repo_name="repo",
            repo_dir=self.temp_dir,
            source_tree_hash="abc",
            depth_cap=2,
            tree_spec=tree_spec,
            file_coverage_summary=NO_COVERAGE,
        )

    def _metadata(self) -> dict:
        metadata = load_analysis_metadata(self.temp_dir)
        assert metadata is not None
        return metadata

    def test_the_specification_is_written_and_read_back(self):
        self._save(self.spec)
        self.assertEqual(self._metadata()["tree_spec"], self.spec)

    def test_a_specification_of_another_version_is_not_replayed(self):
        """Why: the tokenizer, stemmer and role words behind a version would move units silently."""
        self._save(self.spec | {"version": SPEC_VERSION + 1})
        generator = DiagramGenerator(
            repo_location=self.temp_dir,
            temp_folder=self.temp_dir,
            repo_name="repo",
            output_dir=self.temp_dir,
            depth_cap=1,
            run_id="run",
            log_path="log",
        )
        with self.assertRaisesRegex(IncrementalCacheMissingError, "version"):
            generator._stored_tree_spec()
