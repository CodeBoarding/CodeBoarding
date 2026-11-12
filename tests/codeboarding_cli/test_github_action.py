import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis import DEFAULT_DEPTH_CAP
from diagram_analysis.analysis_json import build_unified_analysis_json
from diagram_analysis.io_utils import load_analysis_metadata
from codeboarding_cli.github_action import (
    _resolve_depth_cap,
    generate_analysis,
    generate_html,
    generate_markdown,
    generate_mdx,
    generate_rst,
)
from infra.utils import CODEBOARDING_DIR_NAME
from tests.codeboarding_workflows.test_analysis import assert_holds_the_saved_tree, saving_generator

LEGACY_ANALYSIS = Path(__file__).parents[1] / "fixtures" / "legacy_analysis.json"

UNIFIED_ANALYSIS_JSON = json.loads(
    build_unified_analysis_json(
        AnalysisInsights(description="test", components=[], components_relations=[]),
        [],
        "test",
        Path("/repo"),
        source_tree_hash="",
        depth_cap=1,
    )
)


def _write_analysis_file(path: Path) -> Path:
    """Write a minimal unified analysis JSON file and return its path."""
    with open(path, "w") as f:
        json.dump(UNIFIED_ANALYSIS_JSON, f)
    return path


class TestGenerateMarkdown(unittest.TestCase):
    @patch("output_generators.rendering.generate_markdown_file")
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
                output_dir=CODEBOARDING_DIR_NAME,
            )

            # Check that generate_markdown_file was called
            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")
            self.assertEqual(args[1]["repo_ref"], f"https://github.com/test/repo/blob/main/{CODEBOARDING_DIR_NAME}")

    @patch("output_generators.rendering.generate_markdown_file")
    def test_generate_markdown_with_components(self, mock_generate_file):
        # Test with components that produce multiple entries
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            component_a = Component(name="ComponentA", component_id="1", description="Component A", key_entities=[])
            component_b = Component(name="ComponentB", component_id="2", description="Component B", key_entities=[])
            sub_comp = Component(name="SubComp1", component_id="1.1", description="Sub component 1", key_entities=[])
            sub = AnalysisInsights(description="Component A", components=[sub_comp], components_relations=[])
            analysis_file = temp_path / "analysis.json"
            analysis_file.write_text(
                build_unified_analysis_json(
                    AnalysisInsights(
                        description="test", components=[component_a, component_b], components_relations=[]
                    ),
                    [component_a],
                    "test",
                    Path("/repo"),
                    source_tree_hash="",
                    depth_cap=2,
                    sub_analyses={"1": (sub, [])},
                )
            )

            generate_markdown(
                analysis_path=analysis_file,
                repo_name="test_repo",
                repo_url="https://github.com/test/repo",
                target_branch="main",
                temp_repo_folder=temp_path,
                output_dir=CODEBOARDING_DIR_NAME,
            )

            # Should be called for overview + ComponentA sub-analysis
            self.assertEqual(mock_generate_file.call_count, 2)
            first_call = mock_generate_file.call_args_list[0]
            second_call = mock_generate_file.call_args_list[1]
            self.assertEqual(first_call.args[0], "overview")
            self.assertEqual(second_call.args[0], "ComponentA")


class TestGenerateHtml(unittest.TestCase):
    @patch("output_generators.rendering.generate_html_file")
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
    @patch("output_generators.rendering.generate_mdx_file")
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
                output_dir=CODEBOARDING_DIR_NAME,
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")


class TestGenerateRst(unittest.TestCase):
    @patch("output_generators.rendering.generate_rst_file")
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
                output_dir=CODEBOARDING_DIR_NAME,
            )

            mock_generate_file.assert_called_once()
            args = mock_generate_file.call_args
            self.assertEqual(args[0][0], "overview")


class TestGenerateAnalysis(unittest.TestCase):
    @patch("codeboarding_cli.github_action.generate_markdown")
    @patch("codeboarding_cli.github_action.run_incremental_workflow")
    @patch("codeboarding_cli.github_action.DiagramGenerator")
    @patch("codeboarding_cli.github_action.create_temp_repo_folder")
    @patch("codeboarding_cli.github_action.checkout_repo")
    @patch("codeboarding_cli.github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_CAP": "2"})
    def test_generate_analysis_markdown(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_workflow,
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
            mock_generator_class.return_value = mock_generator
            mock_workflow.return_value = temp_path / "analysis.json"

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".md",
                output_dir=CODEBOARDING_DIR_NAME,
            )

            # Check that clone was called
            mock_clone.assert_called_once()

            # Check that checkout was called
            mock_checkout.assert_called_once()

            # Check that generator was created with correct params
            mock_generator_class.assert_called_once()
            args = mock_generator_class.call_args
            self.assertEqual(args[1]["depth_cap"], 2)

            # Check that markdown generation was called with a Path
            mock_generate_markdown.assert_called_once()
            call_kwargs = mock_generate_markdown.call_args
            self.assertIsInstance(
                call_kwargs[1]["analysis_path"] if "analysis_path" in call_kwargs[1] else call_kwargs[0][0], Path
            )

            # Check return value
            self.assertEqual(result, temp_path)

    @patch("codeboarding_cli.github_action.generate_html")
    @patch("codeboarding_cli.github_action.run_incremental_workflow")
    @patch("codeboarding_cli.github_action.DiagramGenerator")
    @patch("codeboarding_cli.github_action.create_temp_repo_folder")
    @patch("codeboarding_cli.github_action.checkout_repo")
    @patch("codeboarding_cli.github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_CAP": "1"})
    def test_generate_analysis_html(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_workflow,
        mock_generate_html,
    ):
        # Test analysis generation with HTML output
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator_class.return_value = mock_generator
            mock_workflow.return_value = temp_path / "analysis.json"

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".html",
                output_dir=CODEBOARDING_DIR_NAME,
            )

            mock_generate_html.assert_called_once()
            self.assertEqual(result, temp_path)

    @patch("codeboarding_cli.github_action.generate_mdx")
    @patch("codeboarding_cli.github_action.run_incremental_workflow")
    @patch("codeboarding_cli.github_action.DiagramGenerator")
    @patch("codeboarding_cli.github_action.create_temp_repo_folder")
    @patch("codeboarding_cli.github_action.checkout_repo")
    @patch("codeboarding_cli.github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_CAP": "1"})
    def test_generate_analysis_mdx(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_workflow,
        mock_generate_mdx,
    ):
        # Test analysis generation with MDX output
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator_class.return_value = mock_generator
            mock_workflow.return_value = temp_path / "analysis.json"

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".mdx",
                output_dir=CODEBOARDING_DIR_NAME,
            )

            mock_generate_mdx.assert_called_once()
            self.assertEqual(result, temp_path)

    @patch("codeboarding_cli.github_action.generate_rst")
    @patch("codeboarding_cli.github_action.run_incremental_workflow")
    @patch("codeboarding_cli.github_action.DiagramGenerator")
    @patch("codeboarding_cli.github_action.create_temp_repo_folder")
    @patch("codeboarding_cli.github_action.checkout_repo")
    @patch("codeboarding_cli.github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_CAP": "1"})
    def test_generate_analysis_rst(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_workflow,
        mock_generate_rst,
    ):
        # Test analysis generation with RST output
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator_class.return_value = mock_generator
            mock_workflow.return_value = temp_path / "analysis.json"

            result = generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="main",
                target_branch="main",
                extension=".rst",
                output_dir=CODEBOARDING_DIR_NAME,
            )

            mock_generate_rst.assert_called_once()
            self.assertEqual(result, temp_path)

    @patch("codeboarding_cli.github_action.run_incremental_workflow")
    @patch("codeboarding_cli.github_action.DiagramGenerator")
    @patch("codeboarding_cli.github_action.create_temp_repo_folder")
    @patch("codeboarding_cli.github_action.checkout_repo")
    @patch("codeboarding_cli.github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_CAP": "1"})
    def test_generate_analysis_unsupported_extension(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_workflow,
    ):
        # Test with unsupported extension
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator_class.return_value = mock_generator
            mock_workflow.return_value = temp_path / "analysis.json"

            with self.assertRaises(ValueError) as context:
                generate_analysis(
                    repo_url="https://github.com/test/repo",
                    source_branch="main",
                    target_branch="main",
                    extension=".unsupported",
                    output_dir=CODEBOARDING_DIR_NAME,
                )

            self.assertIn("Unsupported extension", str(context.exception))

    @patch("codeboarding_cli.github_action.generate_markdown")
    @patch("codeboarding_cli.github_action.run_incremental_workflow")
    @patch("codeboarding_cli.github_action.DiagramGenerator")
    @patch("codeboarding_cli.github_action.create_temp_repo_folder")
    @patch("codeboarding_cli.github_action.checkout_repo")
    @patch("codeboarding_cli.github_action.clone_repository")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "DIAGRAM_DEPTH_CAP": "1"})
    def test_generate_analysis_branch_checkout(
        self,
        mock_clone,
        mock_checkout,
        mock_create_temp,
        mock_generator_class,
        mock_workflow,
        mock_generate_markdown,
    ):
        # Test that branch checkout is called with correct branch
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            mock_create_temp.return_value = temp_path
            mock_clone.return_value = "test_repo"

            mock_generator = MagicMock()
            mock_generator_class.return_value = mock_generator
            mock_workflow.return_value = temp_path / "analysis.json"

            generate_analysis(
                repo_url="https://github.com/test/repo",
                source_branch="feature-branch",
                target_branch="main",
                extension=".md",
                output_dir=CODEBOARDING_DIR_NAME,
            )

            # Check that checkout was called with the source branch
            mock_checkout.assert_called_once()
            args = mock_checkout.call_args[0]
            self.assertEqual(args[1], "feature-branch")


class TestResolveDepthCap(unittest.TestCase):
    """An action incremental over a seeded baseline must reuse the baseline's
    own configured cap, not silently re-cap it at the current default."""

    @patch.dict(os.environ, {"DIAGRAM_DEPTH_CAP": "5"}, clear=True)
    def test_env_var_takes_priority_over_seeded_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_analysis_file(temp_path / "analysis.json")
            self.assertEqual(_resolve_depth_cap(temp_path), 5)

    @patch.dict(os.environ, {}, clear=True)
    def test_seeded_baseline_depth_cap_used_when_env_var_unset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            metadata: dict[str, object] = {
                "generated_at": "2024-01-01T00:00:00Z",
                "repo_name": "test",
                "depth_level": 1,
                "depth_cap": 4,
                "format_version": "v1",
            }
            analysis = {**UNIFIED_ANALYSIS_JSON, "metadata": metadata}
            with open(temp_path / "analysis.json", "w") as f:
                json.dump(analysis, f)

            self.assertEqual(_resolve_depth_cap(temp_path), 4)

    @patch.dict(os.environ, {}, clear=True)
    def test_a_baseline_in_an_older_format_still_gives_its_depth_cap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            shutil.copy(LEGACY_ANALYSIS, temp_path / "analysis.json")

            self.assertEqual(_resolve_depth_cap(temp_path), 2)

    def test_an_unreadable_seeded_analysis_is_rewritten_by_a_full_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            seeded, output = temp_path / "seeded", temp_path / "out"
            seeded.mkdir()
            output.mkdir()
            (temp_path / "repo").mkdir()
            (seeded / "analysis.json").write_text("{not json")

            with (
                patch.dict(os.environ, {"REPO_ROOT": str(temp_path)}, clear=True),
                patch("codeboarding_cli.github_action.clone_repository", return_value="repo"),
                patch("codeboarding_cli.github_action.checkout_repo"),
                patch("codeboarding_cli.github_action.create_temp_repo_folder", return_value=output),
                patch("codeboarding_cli.github_action.DiagramGenerator", side_effect=saving_generator),
                patch("codeboarding_cli.github_action.generate_markdown"),
            ):
                generate_analysis(
                    "https://github.com/o/repo", "feature", "main", ".md", existing_analysis_dir=str(seeded)
                )

            assert_holds_the_saved_tree(output)
            metadata = load_analysis_metadata(output)

        assert metadata is not None
        self.assertEqual(metadata["depth_cap"], DEFAULT_DEPTH_CAP)

    @patch.dict(os.environ, {}, clear=True)
    def test_realized_depth_is_not_a_configuration_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            metadata = {key: value for key, value in UNIFIED_ANALYSIS_JSON["metadata"].items() if key != "depth_cap"}
            (temp_path / "analysis.json").write_text(json.dumps({**UNIFIED_ANALYSIS_JSON, "metadata": metadata}))
            self.assertEqual(_resolve_depth_cap(temp_path), DEFAULT_DEPTH_CAP)

    @patch.dict(os.environ, {"DIAGRAM_DEPTH_LEVEL": "5"}, clear=True)
    def test_old_environment_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "use DIAGRAM_DEPTH_CAP"):
            _resolve_depth_cap(Path("unused"))

    @patch.dict(os.environ, {}, clear=True)
    def test_no_baseline_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(_resolve_depth_cap(Path(temp_dir)), DEFAULT_DEPTH_CAP)


if __name__ == "__main__":
    unittest.main()
