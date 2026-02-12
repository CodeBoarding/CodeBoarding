import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agents.agent_responses import MetaAnalysisInsights
from diagram_analysis.meta_context_resolver import resolve_meta_context


class DummyLLM:
    model_name = "dummy-model"


class TestMetaContextResolver(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "README.md").write_text("Sample project", encoding="utf-8")
        (self.repo_dir / "requirements.txt").write_text("pydantic==2.0.0", encoding="utf-8")

        self.meta_result = MetaAnalysisInsights(
            project_type="library",
            domain="software",
            architectural_patterns=["modular"],
            expected_components=["core"],
            technology_stack=["python"],
            architectural_bias="Prefer module boundaries",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_resolve_meta_context_uses_cache_after_first_run(self):
        meta_agent = Mock()
        meta_agent.analyze_project_metadata.return_value = self.meta_result
        llm = DummyLLM()

        first = resolve_meta_context(self.repo_dir, meta_agent, llm)
        second = resolve_meta_context(self.repo_dir, meta_agent, llm)

        self.assertEqual(first.project_type, self.meta_result.project_type)
        self.assertEqual(second.project_type, self.meta_result.project_type)
        self.assertEqual(meta_agent.analyze_project_metadata.call_count, 1)


if __name__ == "__main__":
    unittest.main()
