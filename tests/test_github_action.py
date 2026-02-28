import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from github_action import (
    generate_analysis,
    generate_html,
    generate_markdown,
    generate_mdx,
    generate_rst,
)

UNIFIED_ANALYSIS_JSON = {
    "version": 2,
    "metadata": {"generated_at": "2024-01-01T00:00:00Z", "repo_name": "test", "depth_level": 1},
    "description": "test",
    "components": [],
    "components_relations": [],
}


def _write_analysis_file(path: Path) -> Path:
    """Write a minimal unified analysis JSON file and return its path."""
    with open(path, "w") as f:
        json.dump(UNIFIED_ANALYSIS_JSON, f)
    return path


class TestGenerateMarkdown(unittest.TestCase):
    @patch("github_action.generate_markdown_file")
    def test_generate_markdown_with_analysis_file(self, mock_generate_file):
        # Test markdown generation with a unified analysis file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_file = temp_path / "analysis.json"
            _write_analysis_file(analysis_file)

            generate_markdown(
                analysis_path=analysis_file,
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            # Check that generate_markdown_file was called
            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")
            self.assertEqual(args[1]["repo_ref"], "https://github.com/test/repo/blob/main/.codeboarding")

    @patch("github_action.generate_markdown_file")
    def test_generate_markdown_with_components(self, mock_generate_file):
        # Test with components that produce multiple entries
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_data = {
                "version": 2,
                "metadata": {"generated_at": "2024-01-01T00:00:00Z", "repo_name": "test", "depth_level": 2},
                "description": "test",
                "components": [
                    {
                        "name": "ComponentA",
                        "description": "Component A",
                        "key_entities": [],
                        "components": [
                            {
                                "name": "SubComp1",
                                "description": "Sub component 1",
                                "key_entities": [],
                            }
                        ],
                        "components_relations": [],
                    },
                    {
                        "name": "ComponentB",
                        "description": "Component B",
                        "key_entities": [],
                    },
                ],
                "components_relations": [],
            }

            analysis_file = temp_path / "analysis.json"
            with open(analysis_file, "w") as f:
                json.dump(analysis_data, f)

            generate_markdown(
                analysis_path=analysis_file,
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            # Should be called for overview + ComponentA sub-analysis
            self.assertEqual(mock_generate_file.call_count, 2)
            first_call = mock_generate_file.call_args_list[0]
            second_call = mock_generate_file.call_args_list[1]
            self.assertEqual(first_call.args[0], "overview")
            self.assertEqual(second_call.args[0], "ComponentA")


class TestGenerateHtml(unittest.TestCase):
    @patch("github_action.generate_html_file")
    def test_generate_html_with_analysis_file(self, mock_generate_file):
        # Test HTML generation with a unified analysis file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_file = temp_path / "analysis.json"
            _write_analysis_file(analysis_file)

            generate_html(
                analysis_path=analysis_file,
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")


class TestGenerateMdx(unittest.TestCase):
    @patch("github_action.generate_mdx_file")
    def test_generate_mdx_with_analysis_file(self, mock_generate_file):
        # Test MDX generation with a unified analysis file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_file = temp_path / "analysis.json"
            _write_analysis_file(analysis_file)

            generate_mdx(
                analysis_path=analysis_file,
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")


class TestGenerateRst(unittest.TestCase):
    @patch("github_action.generate_rst_file")
    def test_generate_rst_with_analysis_file(self, mock_generate_file):
        # Test RST generation with a unified analysis file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_file = temp_path / "analysis.json"
            _write_analysis_file(analysis_file)

            generate_rst(
                analysis_path=analysis_file,
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")


class TestGenerateAnalysis(unittest.TestCase):
    @patch("github_action.generate_markdown")
    @patch("github_action.DiagramGenerator")
    @patch("github_action.create_temp_repo_folder")
    @patch("github_action.checkout_repo")
    @patch("github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_LEVEL": "2"})
    def test_generate_analysis_markdown(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_generate_markdown,
    ):
        # Test analysis generation with markdown output
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path

            # Mock clone repository
            mock_clone.return_value = "test_repo"

            # Mock generator
            mock_generator = MagicMock()
            mock_generator.generate_analysis.return_value = [temp_path / "analysis.json"]
            mock_generator_class.return_value = mock_generator

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".md",
                output_dir=".codeboarding",
            )

            # Check that clone was called
            mock_clone.assert_called_once()

            # Check that checkout was called
            mock_checkout.assert_called_once()

            # Check that generator was created with correct params
            mock_generator_class.assert_called_once()
            args = mock_generator_class.call_args
            self.assertEqual(args[1]["depth_level"], 2)

            # Check that markdown generation was called with a Path
            mock_generate_markdown.assert_called_once()
            call_kwargs = mock_generate_markdown.call_args
            self.assertIsInstance(
                call_kwargs[1]["analysis_path"] if "analysis_path" in call_kwargs[1] else call_kwargs[0][0], Path
            )

            # Check return value
            self.assertEqual(result, temp_path)

    @patch("github_action.generate_html")
    @patch("github_action.DiagramGenerator")
    @patch("github_action.create_temp_repo_folder")
    @patch("github_action.checkout_repo")
    @patch("github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_LEVEL": "1"})
    def test_generate_analysis_html(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_generate_html,
    ):
        # Test analysis generation with HTML output
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator.generate_analysis.return_value = [temp_path / "analysis.json"]
            mock_generator_class.return_value = mock_generator

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".html",
                output_dir=".codeboarding",
            )

            mock_generate_html.assert_called_once()
            self.assertEqual(result, temp_path)

    @patch("github_action.generate_mdx")
    @patch("github_action.DiagramGenerator")
    @patch("github_action.create_temp_repo_folder")
    @patch("github_action.checkout_repo")
    @patch("github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_LEVEL": "1"})
    def test_generate_analysis_mdx(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_generate_mdx,
    ):
        # Test analysis generation with MDX output
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator.generate_analysis.return_value = [temp_path / "analysis.json"]
            mock_generator_class.return_value = mock_generator

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".mdx",
                output_dir=".codeboarding",
            )

            mock_generate_mdx.assert_called_once()
            self.assertEqual(result, temp_path)

    @patch("github_action.generate_rst")
    @patch("github_action.DiagramGenerator")
    @patch("github_action.create_temp_repo_folder")
    @patch("github_action.checkout_repo")
    @patch("github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_LEVEL": "1"})
    def test_generate_analysis_rst(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_generate_rst,
    ):
        # Test analysis generation with RST output
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator.generate_analysis.return_value = [temp_path / "analysis.json"]
            mock_generator_class.return_value = mock_generator

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".rst",
                output_dir=".codeboarding",
            )

            mock_generate_rst.assert_called_once()
            self.assertEqual(result, temp_path)

    @patch("github_action.DiagramGenerator")
    @patch("github_action.create_temp_repo_folder")
    @patch("github_action.checkout_repo")
    @patch("github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_LEVEL": "1"})
    def test_generate_analysis_unsupported_extension(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
    ):
        # Test with unsupported extension
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator.generate_analysis.return_value = [temp_path / "analysis.json"]
            mock_generator_class.return_value = mock_generator

            with self.assertRaises(ValueError) as context:
                generate_analysis(
                    repo_url="https://github.com/test/repo",
                    source_branch="main",
                    target_branch="main",
                    extension=".unsupported",
                    output_dir=".codeboarding",
                )

            self.assertIn("Unsupported extension", str(context.exception))

    @patch("github_action.generate_markdown")
    @patch("github_action.DiagramGenerator")
    @patch("github_action.create_temp_repo_folder")
    @patch("github_action.checkout_repo")
    @patch("github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_LEVEL": "1"})
    def test_generate_analysis_branch_checkout(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_generate_markdown,
    ):
        # Test that branch checkout is called with correct branch
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator.generate_analysis.return_value = [temp_path / "analysis.json"]
            mock_generator_class.return_value = mock_generator

            generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="feature-branch",
                target_branch="main",
                extension=".md",
                output_dir=".codeboarding",
            )

            # Check that checkout was called with the source branch
            mock_checkout.assert_called_once()
            args = mock_checkout.call_args[0]
            self.assertEqual(args[1], "feature-branch")


if __name__ == "__main__":
    unittest.main()
