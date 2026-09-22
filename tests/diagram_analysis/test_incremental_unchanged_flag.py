"""The incremental early exit says so in the metadata, so a consumer can tell its zero from a model's."""

import shutil
import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis


class TestIncrementalUnchangedFlag(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.analysis = AnalysisInsights(
            description="root",
            components=[Component(name="A", description="", key_entities=[], component_id="1")],
            components_relations=[],
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _save(self, **kwargs):
        return save_analysis(
            analysis=self.analysis,
            output_dir=self.temp_dir,
            sub_analyses={},
            repo_name="repo",
            repo_dir=self.temp_dir,
            source_tree_hash="abc",
            depth_cap=2,
            **kwargs,
        )

    def _metadata(self) -> dict:
        metadata = load_analysis_metadata(self.temp_dir)
        assert metadata is not None
        return metadata

    def test_a_plain_save_is_not_marked(self):
        self._save()
        self.assertIs(self._metadata()["incremental_unchanged"], False)

    def test_the_early_exit_marks_its_save(self):
        self._save(incremental_unchanged=True)
        self.assertIs(self._metadata()["incremental_unchanged"], True)

    def test_the_mark_describes_one_write_and_is_never_carried_forward(self):
        """Why: a later run that did re-detail must not inherit a verdict it did not earn."""
        self._save(incremental_unchanged=True)
        self._save()
        self.assertIs(self._metadata()["incremental_unchanged"], False)


if __name__ == "__main__":
    unittest.main()
