"""The tree specification rides in analysis.json metadata and survives a save that omits it."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis


class TestTreeSpecPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.analysis = AnalysisInsights(
            description="root",
            components=[Component(name="A", description="", key_entities=[], component_id="1")],
            components_relations=[],
        )
        self.spec = {"version": 1, "grouper": "kinship", "machinery": [], "scopes": {"root": {"rules": []}}}

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
        )

    def _metadata(self) -> dict:
        metadata = load_analysis_metadata(self.temp_dir)
        assert metadata is not None
        return metadata

    def test_the_specification_is_written_and_read_back(self):
        self._save(self.spec)
        self.assertEqual(self._metadata()["tree_spec"], self.spec)

    def test_a_save_without_a_specification_keeps_the_one_on_disk(self):
        """Dropping it would strand every later incremental and partial run."""
        self._save(self.spec)
        self._save(None)
        self.assertEqual(self._metadata()["tree_spec"], self.spec)

    def test_an_analysis_written_before_the_specification_has_an_empty_one(self):
        self._save(None)
        payload = json.loads((self.temp_dir / "analysis.json").read_text())
        self.assertEqual(payload["metadata"]["tree_spec"], {})
