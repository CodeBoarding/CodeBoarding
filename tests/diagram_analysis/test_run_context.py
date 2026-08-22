import unittest
from pathlib import Path
from unittest.mock import patch

from diagram_analysis.run_context import RunContext


class TestRunContext(unittest.TestCase):
    @patch("diagram_analysis.run_context.generate_log_path", return_value="project/2026-03-18_09-00-00")
    @patch("diagram_analysis.run_context.generate_run_id", return_value="fresh-run-id")
    def test_resolve_generates_fresh_run_by_default(self, mock_generate_run_id, mock_generate_log_path):
        result = RunContext.resolve(
            repo_dir=Path("/tmp/repo"),
            project_name="project",
        )

        self.assertEqual(result.run_id, "fresh-run-id")
        self.assertEqual(result.log_path, "project/2026-03-18_09-00-00")
        self.assertEqual(result.repo_dir, Path("/tmp/repo"))
        mock_generate_run_id.assert_called_once_with()
        mock_generate_log_path.assert_called_once_with("project")


if __name__ == "__main__":
    unittest.main()
