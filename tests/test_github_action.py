import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from github_action import (
    generate_analysis,
    generate_html,
    generate_markdown,
    generate_mdx,
    generate_rst,
)


class TestGenerateMarkdown(unittest.TestCase):
    @patch("github_action.generate_markdown_file")
    @patch("builtins.open", create=True)
    def test_generate_markdown_with_analysis_files(self, mock_open, mock_generate_file):
        # Test markdown generation with analysis files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test analysis files
            analysis_file = temp_path / "component_analysis.json"
            analysis_json = '{"description": "test", "components": [], "components_relations": []}'

            # Mock file reading
            mock_file = MagicMock()
            mock_file.read.return_value = analysis_json
            mock_file.__enter__.return_value = mock_file
            mock_open.return_value = mock_file

            generate_markdown(
                analysis_files=[str(analysis_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            # Check that generate_markdown_file was called
            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")  # fname should be changed to 'overview'
            self.assertEqual(args[1]["repo_ref"], "https://github.com/test/repo/blob/main/.codeboarding")

    @patch("github_action.generate_markdown_file")
    @patch("builtins.open", create=True)
    def test_generate_markdown_skip_version_file(self, mock_open, mock_generate_file):
        # Test that codeboarding_version.json is skipped
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create version file (should be skipped)
            version_file = temp_path / "codeboarding_version.json"

            generate_markdown(
                analysis_files=[str(version_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            # Should not call generate_markdown_file for version file
            mock_generate_file.assert_not_called()

    @patch("github_action.generate_markdown_file")
    @patch("builtins.open", create=True)
    def test_generate_markdown_multiple_files(self, mock_open, mock_generate_file):
        # Test with multiple analysis files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_files = [
                temp_path / "analysis1.json",
                temp_path / "analysis2.json",
            ]

            analysis_json = '{"description": "test", "components": [], "components_relations": []}'
            mock_file = MagicMock()
            mock_file.read.return_value = analysis_json
            mock_file.__enter__.return_value = mock_file
            mock_open.return_value = mock_file

            generate_markdown(
                analysis_files=[str(f) for f in analysis_files],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            # Should be called twice
            self.assertEqual(mock_generate_file.call_count, 2)


class TestGenerateHtml(unittest.TestCase):
    @patch("github_action.generate_html_file")
    @patch("builtins.open", create=True)
    def test_generate_html_with_analysis_files(self, mock_open, mock_generate_file):
        # Test HTML generation with analysis files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_file = temp_path / "component_analysis.json"
            analysis_json = '{"description": "test", "components": [], "components_relations": []}'

            mock_file = MagicMock()
            mock_file.read.return_value = analysis_json
            mock_file.__enter__.return_value = mock_file
            mock_open.return_value = mock_file

            generate_html(
                analysis_files=[str(analysis_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")

    @patch("github_action.generate_html_file")
    @patch("builtins.open", create=True)
    def test_generate_html_skip_version_file(self, mock_open, mock_generate_file):
        # Test that version file is skipped
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            version_file = temp_path / "codeboarding_version.json"

            generate_html(
                analysis_files=[str(version_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
            )

            mock_generate_file.assert_not_called()


class TestGenerateMdx(unittest.TestCase):
    @patch("github_action.generate_mdx_file")
    @patch("builtins.open", create=True)
    def test_generate_mdx_with_analysis_files(self, mock_open, mock_generate_file):
        # Test MDX generation with analysis files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_file = temp_path / "component_analysis.json"
            analysis_json = '{"description": "test", "components": [], "components_relations": []}'

            mock_file = MagicMock()
            mock_file.read.return_value = analysis_json
            mock_file.__enter__.return_value = mock_file
            mock_open.return_value = mock_file

            generate_mdx(
                analysis_files=[str(analysis_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")

    @patch("github_action.generate_mdx_file")
    @patch("builtins.open", create=True)
    def test_generate_mdx_skip_version_file(self, mock_open, mock_generate_file):
        # Test that version file is skipped
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            version_file = temp_path / "codeboarding_version.json"

            generate_mdx(
                analysis_files=[str(version_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            mock_generate_file.assert_not_called()


class TestGenerateRst(unittest.TestCase):
    @patch("github_action.generate_rst_file")
    @patch("builtins.open", create=True)
    def test_generate_rst_with_analysis_files(self, mock_open, mock_generate_file):
        # Test RST generation with analysis files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            analysis_file = temp_path / "component_analysis.json"
            analysis_json = '{"description": "test", "components": [], "components_relations": []}'

            mock_file = MagicMock()
            mock_file.read.return_value = analysis_json
            mock_file.__enter__.return_value = mock_file
            mock_open.return_value = mock_file

            generate_rst(
                analysis_files=[str(analysis_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")

    @patch("github_action.generate_rst_file")
    @patch("builtins.open", create=True)
    def test_generate_rst_skip_version_file(self, mock_open, mock_generate_file):
        # Test that version file is skipped
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            version_file = temp_path / "codeboarding_version.json"

            generate_rst(
                analysis_files=[str(version_file)],
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=".codeboarding",
            )

            mock_generate_file.assert_not_called()


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

            # Check that markdown generation was called
            mock_generate_markdown.assert_called_once()

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
    ):
        # Test that branch checkout is called with correct branch
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            repo_path = Path("/tmp/repos/test_repo")

            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator.generate_analysis.return_value = []
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
