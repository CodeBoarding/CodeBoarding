import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch, ANY

from codeboarding_workflows.full_analysis import generate_analysis
from codeboarding_workflows.local_analysis import process_local_repository
from codeboarding_workflows.markdown import generate_markdown_docs
from codeboarding_workflows.partial_analysis import partial_update
from codeboarding_workflows.remote_analysis import onboarding_materials_exist, process_remote_repository
from codeboarding_cli.commands.full_analysis import run_from_args, validate_arguments


class TestOnboardingMaterialsExist(unittest.TestCase):
    @patch("codeboarding_workflows.remote_analysis.requests.get")
    def test_onboarding_materials_exist_true(self, mock_get):
        # Test when materials exist (status 200)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = onboarding_materials_exist("test_project")

        self.assertTrue(result)
        mock_get.assert_called_once()
        call_args = mock_get.call_args[0][0]
        self.assertIn("test_project", call_args)

    @patch("codeboarding_workflows.remote_analysis.requests.get")
    def test_onboarding_materials_exist_false(self, mock_get):
        # Test when materials don't exist (status 404)
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = onboarding_materials_exist("test_project")

        self.assertFalse(result)


class TestGenerateAnalysis(unittest.TestCase):
    @patch("codeboarding_workflows.full_analysis.DiagramGenerator")
    def test_generate_analysis(self, mock_generator_class):
        # Test generate_analysis function
        mock_generator = MagicMock()
        mock_generator.generate_analysis.return_value = Path("analysis.json")
        mock_generator_class.return_value = mock_generator

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            result = generate_analysis(
                repo_name="test_repo",
                repo_path=repo_path,
                output_dir=output_dir,
                run_id="test-run-id",
                log_path="test_repo/test-run-log",
                depth_level=2,
            )

            self.assertEqual(result, Path("analysis.json"))
            mock_generator_class.assert_called_once_with(
                repo_location=repo_path,
                temp_folder=output_dir,
                repo_name="test_repo",
                output_dir=output_dir,
                depth_level=2,
                run_id="test-run-id",
                log_path="test_repo/test-run-log",
                monitoring_enabled=False,
            )
            mock_generator.generate_analysis.assert_called_once()

    @patch("codeboarding_workflows.full_analysis.DiagramGenerator")
    def test_generate_analysis_with_force_full(self, mock_generator_class):
        mock_generator = MagicMock()
        mock_generator.generate_analysis.return_value = [Path("analysis.json")]
        mock_generator_class.return_value = mock_generator

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            generate_analysis(
                repo_name="test_repo",
                repo_path=repo_path,
                output_dir=output_dir,
                run_id="test-run-id",
                log_path="test_repo/test-run-log",
                force_full=True,
            )

        self.assertTrue(mock_generator.force_full_analysis)


class TestGenerateMarkdownDocs(unittest.TestCase):
    @patch("codeboarding_workflows.markdown.generate_markdown_file")
    @patch("codeboarding_workflows.markdown.get_branch")
    @patch("builtins.open", create=True)
    def test_generate_markdown_docs(self, mock_open, mock_get_branch, mock_generate_markdown):
        # Test generate_markdown_docs function
        mock_get_branch.return_value = "main"

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            # Create a test analysis file
            analysis_file = output_dir / "test_analysis.json"
            analysis_json = '{"description": "test", "components": [], "components_relations": []}'

            # Mock file reading
            mock_file = MagicMock()
            mock_file.read.return_value = analysis_json
            mock_file.__enter__.return_value = mock_file
            mock_open.return_value = mock_file

            generate_markdown_docs(
                repo_name="test_repo",
                repo_path=repo_path,
                repo_url="https://github.com/test/repo",
                analysis_path=analysis_file,
                output_dir=output_dir,
                demo_mode=True,
            )

            mock_get_branch.assert_called_once_with(repo_path)
            mock_generate_markdown.assert_called_once()


class TestPartialUpdate(unittest.TestCase):
    @patch("codeboarding_workflows.partial_analysis.save_sub_analysis")
    @patch("codeboarding_workflows.partial_analysis.load_full_analysis")
    @patch("codeboarding_workflows.partial_analysis.DiagramGenerator")
    def test_partial_update_success(self, mock_generator_class, mock_load_full, mock_save_sub_analysis):
        # Test successful partial update for a root-level component
        from agents.agent_responses import AnalysisInsights, Component

        mock_generator = MagicMock()
        mock_generator_class.return_value = mock_generator

        mock_sub_analysis = AnalysisInsights(
            description="test sub-analysis",
            components=[
                Component(
                    name="SubComponent",
                    description="Sub",
                    key_entities=[],
                    source_cluster_ids=[],
                )
            ],
            components_relations=[],
        )
        mock_generator.process_component.return_value = (
            "test_comp_id",
            mock_sub_analysis,
            [],
        )

        root_component = Component(
            name="TestComponent",
            component_id="test_comp_id",
            description="Test",
            key_entities=[],
            source_cluster_ids=[],
        )
        mock_load_full.return_value = (
            AnalysisInsights(description="test", components=[root_component], components_relations=[]),
            {},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            partial_update(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                component_id="test_comp_id",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=1,
            )

            mock_generator.pre_analysis.assert_called_once()
            mock_generator.process_component.assert_called_once()
            mock_save_sub_analysis.assert_called_once_with(mock_sub_analysis, output_dir, "test_comp_id")

    @patch("codeboarding_workflows.partial_analysis.save_sub_analysis")
    @patch("codeboarding_workflows.partial_analysis.load_full_analysis")
    @patch("codeboarding_workflows.partial_analysis.DiagramGenerator")
    def test_partial_update_nested_component_success(
        self, mock_generator_class, mock_load_full, mock_save_sub_analysis
    ):
        # Test that partial_update finds components nested inside sub-analyses
        from agents.agent_responses import AnalysisInsights, Component

        mock_generator = MagicMock()
        mock_generator_class.return_value = mock_generator

        mock_sub_analysis_result = AnalysisInsights(
            description="nested sub-analysis result",
            components=[],
            components_relations=[],
        )
        mock_generator.process_component.return_value = (
            "nested_comp_id",
            mock_sub_analysis_result,
            [],
        )

        root_component = Component(
            name="RootComponent",
            component_id="root_comp_id",
            description="Root",
            key_entities=[],
            source_cluster_ids=[],
        )
        nested_component = Component(
            name="NestedComponent",
            component_id="nested_comp_id",
            description="Nested",
            key_entities=[],
            source_cluster_ids=[],
        )
        sub_analysis_of_root = AnalysisInsights(
            description="sub of root",
            components=[nested_component],
            components_relations=[],
        )
        mock_load_full.return_value = (
            AnalysisInsights(description="root", components=[root_component], components_relations=[]),
            {"root_comp_id": sub_analysis_of_root},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            partial_update(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                component_id="nested_comp_id",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=1,
            )

            mock_generator.pre_analysis.assert_called_once()
            mock_generator.process_component.assert_called_once_with(nested_component)
            mock_save_sub_analysis.assert_called_once_with(mock_sub_analysis_result, output_dir, "nested_comp_id")

    @patch("codeboarding_workflows.partial_analysis.DiagramGenerator")
    def test_partial_update_file_not_found(self, mock_generator_class):
        # Test when analysis.json doesn't exist
        mock_generator = MagicMock()
        mock_generator_class.return_value = mock_generator

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            # Should not raise exception, just log error and return
            partial_update(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                component_id="TestComponent",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=1,
            )

            # pre_analysis should be called, but process_component should not
            mock_generator.pre_analysis.assert_called_once()
            mock_generator.process_component.assert_not_called()


class TestProcessRemoteRepository(unittest.TestCase):
    @patch("codeboarding_workflows.remote_analysis.upload_onboarding_materials")
    @patch("codeboarding_workflows.remote_analysis.copy_files")
    @patch("codeboarding_workflows.remote_analysis.generate_markdown_docs")
    @patch("codeboarding_workflows.remote_analysis.generate_analysis")
    @patch("codeboarding_workflows.remote_analysis.remove_temp_repo_folder")
    @patch("codeboarding_workflows.remote_analysis.create_temp_repo_folder")
    @patch("codeboarding_workflows.remote_analysis.clone_repository")
    @patch("codeboarding_workflows.remote_analysis.get_repo_name")
    @patch("codeboarding_workflows.remote_analysis.onboarding_materials_exist")
    def test_process_remote_repository_with_cache_hit(
        self,
        mock_materials_exist,
        mock_get_repo_name,
        mock_clone,
        mock_create_temp,
        mock_remove_temp,
        mock_generate_analysis,
        mock_generate_markdown,
        mock_copy_artifacts,
        mock_upload,
    ):
        # Test with cache hit
        mock_materials_exist.return_value = True
        mock_get_repo_name.return_value = "test_repo"

        process_remote_repository(
            repo_url="https://github.com/test/repo",
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
            cache_check=True,
        )

        # Should return early due to cache hit
        mock_clone.assert_not_called()
        mock_generate_analysis.assert_not_called()

    @patch("codeboarding_workflows.remote_analysis.upload_onboarding_materials")
    @patch("codeboarding_workflows.remote_analysis.copy_files")
    @patch("codeboarding_workflows.remote_analysis.generate_markdown_docs")
    @patch("codeboarding_workflows.remote_analysis.generate_analysis")
    @patch("codeboarding_workflows.remote_analysis.remove_temp_repo_folder")
    @patch("codeboarding_workflows.remote_analysis.create_temp_repo_folder")
    @patch("codeboarding_workflows.remote_analysis.clone_repository")
    @patch("codeboarding_workflows.remote_analysis.get_repo_name")
    def test_process_remote_repository_success(
        self,
        mock_get_repo_name,
        mock_clone,
        mock_create_temp,
        mock_remove_temp,
        mock_generate_analysis,
        mock_generate_markdown,
        mock_copy_artifacts,
        mock_upload,
    ):
        # Test successful processing
        mock_get_repo_name.return_value = "test_repo"
        mock_clone.return_value = "test_repo"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_folder = Path(temp_dir)
            mock_create_temp.return_value = temp_folder
            mock_generate_analysis.return_value = [Path("analysis.json")]

            # Create the repo directory
            repo_path = Path("repos") / "test_repo"
            repo_path.mkdir(parents=True, exist_ok=True)

            process_remote_repository(
                repo_url="https://github.com/test/repo",
                run_id="test-run-id",
                log_path="test_repo/test-run-log",
                upload=True,
                cache_check=False,
            )

            mock_clone.assert_called_once()
            mock_generate_analysis.assert_called_once()
            mock_generate_markdown.assert_called_once()
            mock_remove_temp.assert_called_once()


class TestProcessLocalRepository(unittest.TestCase):
    @patch("codeboarding_workflows.local_analysis.generate_analysis")
    def test_process_local_repository_full_analysis(self, mock_generate_analysis):
        # Test full analysis (no partial update)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            process_local_repository(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=1,
            )

            mock_generate_analysis.assert_called_once_with(
                repo_name="test_project",
                repo_path=repo_path,
                output_dir=output_dir,
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=1,
                monitoring_enabled=False,
                force_full=False,
            )
            self.assertTrue(output_dir.exists())

    @patch("codeboarding_workflows.local_analysis.partial_update")
    def test_process_local_repository_partial_update(self, mock_partial_update):
        # Test partial update
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"

            process_local_repository(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=2,
                component_id="TestComponent",
            )

            mock_partial_update.assert_called_once_with(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                component_id="TestComponent",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=2,
            )


class TestFullCliForceFull(unittest.TestCase):
    @patch("codeboarding_cli.commands.full_analysis.RunContext")
    @patch("codeboarding_cli.commands.full_analysis.bootstrap_environment")
    @patch("codeboarding_cli.commands.full_analysis.process_local_repository")
    def test_force_flag_propagates_force_full(self, mock_process, _mock_bootstrap, mock_run_context):
        mock_run_context.resolve.return_value = MagicMock(run_id="r", log_path="l", finalize=lambda: None)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()

            args = MagicMock()
            args.local = repo_path
            args.repositories = []
            args.output_dir = None
            args.project_name = None
            args.partial_component_id = None
            args.binary_location = None
            args.depth_level = 1
            args.upload = False
            args.enable_monitoring = False
            args.force = True

            run_from_args(args, MagicMock())

        mock_process.assert_called_once()
        self.assertTrue(mock_process.call_args.kwargs["force_full"])


class TestCopyFiles(unittest.TestCase):
    def test_copy_files_copies_each_file_to_target(self):
        from utils import copy_files

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "src"
            source.mkdir()
            target = Path(temp_dir) / "dst"

            (source / "test.md").write_text("# Test")
            (source / "data.json").write_text('{"key": "value"}')
            (source / "ignore.txt").write_text("ignore me")

            copy_files([source / "test.md", source / "data.json"], target)

            self.assertTrue((target / "test.md").exists())
            self.assertTrue((target / "data.json").exists())
            self.assertFalse((target / "ignore.txt").exists())

    def test_copy_files_creates_missing_target_dir(self):
        from utils import copy_files

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "src"
            source.mkdir()
            (source / "x.md").write_text("x")

            target = Path(temp_dir) / "not-yet-existing" / "dst"
            copy_files([source / "x.md"], target)

            self.assertTrue((target / "x.md").exists())


class TestValidateArguments(unittest.TestCase):
    def test_partial_without_local_errors(self):
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = None
        args.partial_component_id = "test_comp_id"
        args.output_dir = None
        args.project_name = None
        args.upload = False

        validate_arguments(args, parser)
        parser.error.assert_called_once()

    def test_valid_local(self):
        parser = MagicMock()
        args = MagicMock()
        args.repositories = None
        args.local = "/path/to/repo"
        args.partial_component_id = None
        args.output_dir = None
        args.project_name = None
        args.upload = False

        validate_arguments(args, parser)
        parser.error.assert_not_called()

    def test_valid_remote(self):
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = None
        args.partial_component_id = None
        args.output_dir = None
        args.project_name = None
        args.upload = False

        validate_arguments(args, parser)
        parser.error.assert_not_called()

    def test_both_local_and_remote_errors(self):
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = "/path/to/repo"
        args.partial_component_id = None
        args.output_dir = None
        args.project_name = None
        args.upload = False

        validate_arguments(args, parser)
        parser.error.assert_called_once()

    def test_upload_with_local_errors(self):
        parser = MagicMock()
        args = MagicMock()
        args.repositories = None
        args.local = "/path/to/repo"
        args.partial_component_id = None
        args.output_dir = None
        args.project_name = None
        args.upload = True

        validate_arguments(args, parser)
        parser.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
