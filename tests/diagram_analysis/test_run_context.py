import unittest
from pathlib import Path
from unittest.mock import patch

from diagram_analysis.run_context import finalize_run_context, resolve_run_context


class TestRunContext(unittest.TestCase):
    @patch("diagram_analysis.run_context.generate_log_path", return_value="project/2026-03-18_09-00-00")
    @patch("diagram_analysis.run_context.generate_run_id", return_value="fresh-run-id")
    def test_resolve_run_context_generates_fresh_run_by_default(self, mock_generate_run_id, mock_generate_log_path):
        result = resolve_run_context(
            repo_dir=Path("/tmp/repo"),
            project_name="project",
        )

        self.assertEqual(result.run_id, "fresh-run-id")
        self.assertEqual(result.log_path, "project/2026-03-18_09-00-00")
        mock_generate_run_id.assert_called_once_with()
        mock_generate_log_path.assert_called_once_with("project")

    @patch("diagram_analysis.run_context.generate_log_path", return_value="project/2026-03-18_09-00-00")
    @patch("diagram_analysis.run_context.generate_run_id", return_value="fresh-run-id")
    @patch("diagram_analysis.run_context.load_existing_run_id", return_value="cached-run-id")
    def test_resolve_run_context_reuses_existing_run_id_when_requested(
        self,
        mock_load_existing_run_id,
        mock_generate_run_id,
        mock_generate_log_path,
    ):
        result = resolve_run_context(
            repo_dir=Path("/tmp/repo"),
            project_name="project",
            reuse_latest_run_id=True,
        )

        self.assertEqual(result.run_id, "cached-run-id")
        self.assertEqual(result.log_path, "project/2026-03-18_09-00-00")
        mock_load_existing_run_id.assert_called_once_with(Path("/tmp/repo"))
        mock_generate_run_id.assert_not_called()
        mock_generate_log_path.assert_called_once_with("project")

    @patch("diagram_analysis.run_context.generate_log_path", return_value="project/2026-03-18_09-00-00")
    @patch("diagram_analysis.run_context.generate_run_id", return_value="fresh-run-id")
    @patch("diagram_analysis.run_context.load_existing_run_id", return_value=None)
    def test_resolve_run_context_falls_back_to_fresh_run_id(
        self,
        mock_load_existing_run_id,
        mock_generate_run_id,
        mock_generate_log_path,
    ):
        result = resolve_run_context(
            repo_dir=Path("/tmp/repo"),
            project_name="project",
            reuse_latest_run_id=True,
        )

        self.assertEqual(result.run_id, "fresh-run-id")
        self.assertEqual(result.log_path, "project/2026-03-18_09-00-00")
        mock_load_existing_run_id.assert_called_once_with(Path("/tmp/repo"))
        mock_generate_run_id.assert_called_once_with()
        mock_generate_log_path.assert_called_once_with("project")

    @patch("diagram_analysis.run_context.prune_details_caches")
    def test_finalize_run_context_prunes_to_single_run_id(self, mock_prune_details_caches):
        finalize_run_context(repo_dir=Path("/tmp/repo"), run_id="run-123")

        mock_prune_details_caches.assert_called_once_with(
            repo_dir=Path("/tmp/repo"),
            only_keep_run_id="run-123",
        )


if __name__ == "__main__":
    unittest.main()
