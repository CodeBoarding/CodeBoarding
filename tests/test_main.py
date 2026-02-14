import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch, ANY

from main import (
    copy_files,
    generate_analysis,
    generate_markdown_docs,
    onboarding_materials_exist,
    partial_update,
    process_local_repository,
    process_remote_repository,
    validate_arguments,
    validate_env_vars,
)


class TestValidateEnvVars(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}, clear=True)
    def test_validate_env_vars_single_key(self):
        # Should not raise any exception when exactly one key is set
        try:
            validate_env_vars()
        except SystemExit:
            self.fail("validate_env_vars raised SystemExit unexpectedly")

    @patch.dict(os.environ, {}, clear=True)
    def test_validate_env_vars_no_keys(self):
        # Should exit when no keys are set
        with self.assertRaises(SystemExit) as cm:
            validate_env_vars()
        self.assertEqual(cm.exception.code, 1)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "key1", "ANTHROPIC_API_KEY": "key2"}, clear=True)
    def test_validate_env_vars_multiple_keys(self):
        # Should exit when multiple keys are set
        with self.assertRaises(SystemExit) as cm:
            validate_env_vars()
        self.assertEqual(cm.exception.code, 2)

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}, clear=True)
    def test_validate_env_vars_google_key(self):
        # Test with Google API key
        try:
            validate_env_vars()
        except SystemExit:
            self.fail("validate_env_vars raised SystemExit unexpectedly")

    @patch.dict(os.environ, {"CEREBRAS_API_KEY": "test_key"}, clear=True)
    def test_validate_env_vars_cerebras_key(self):
        # Test with Cerebras API key
        try:
            validate_env_vars()
        except SystemExit:
            self.fail("validate_env_vars raised SystemExit unexpectedly")


class TestOnboardingMaterialsExist(unittest.TestCase):
    @patch("main.requests.get")
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

    @patch("main.requests.get")
    def test_onboarding_materials_exist_false(self, mock_get):
        # Test when materials don't exist (status 404)
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = onboarding_materials_exist("test_project")

        self.assertFalse(result)


class TestGenerateAnalysis(unittest.TestCase):
    @patch("main.DiagramGenerator")
    def test_generate_analysis(self, mock_generator_class):
        # Test generate_analysis function
        mock_generator = MagicMock()
        mock_generator.generate_analysis.return_value = [
            Path("analysis1.json"),
            Path("analysis2.json"),
        ]
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
                depth_level=2,
            )

            self.assertEqual(len(result), 2)
            mock_generator_class.assert_called_once_with(
                repo_location=repo_path,
                temp_folder=output_dir,
                repo_name="test_repo",
                output_dir=output_dir,
                depth_level=2,
                run_id=None,
                monitoring_enabled=False,
            )
            mock_generator.generate_analysis.assert_called_once()


class TestGenerateMarkdownDocs(unittest.TestCase):
    @patch("main.generate_markdown_file")
    @patch("main.get_branch")
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
                analysis_files=[analysis_file],
                output_dir=output_dir,
                demo_mode=True,
            )

            mock_get_branch.assert_called_once_with(repo_path)
            mock_generate_markdown.assert_called_once()


class TestPartialUpdate(unittest.TestCase):
    @patch("main.save_sub_analysis")
    @patch("main.load_analysis")
    @patch("main.DiagramGenerator")
    def test_partial_update_success(self, mock_generator_class, mock_load_analysis, mock_save_sub_analysis):
        # Test successful partial update
        from agents.agent_responses import AnalysisInsights, Component

        mock_generator = MagicMock()
        mock_generator_class.return_value = mock_generator

        # Mock process_component to return a valid tuple
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

        mock_load_analysis.return_value = AnalysisInsights(
            description="test",
            components=[
                Component(
                    name="TestComponent",
                    component_id="test_comp_id",
                    description="Test",
                    key_entities=[],
                    source_cluster_ids=[],
                )
            ],
            components_relations=[],
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
                depth_level=1,
            )

            mock_generator.pre_analysis.assert_called_once()
            mock_generator.process_component.assert_called_once()
            mock_save_sub_analysis.assert_called_once_with(mock_sub_analysis, output_dir, "test_comp_id")

    @patch("main.load_analysis")
    @patch("main.DiagramGenerator")
    def test_partial_update_file_not_found(self, mock_generator_class, mock_load_analysis):
        # Test when analysis.json doesn't exist
        mock_generator = MagicMock()
        mock_generator_class.return_value = mock_generator
        mock_load_analysis.return_value = None

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
                depth_level=1,
            )

            # pre_analysis should be called, but process_component should not
            mock_generator.pre_analysis.assert_called_once()
            mock_generator.process_component.assert_not_called()


class TestProcessRemoteRepository(unittest.TestCase):
    @patch("main.upload_onboarding_materials")
    @patch("main.copy_files")
    @patch("main.generate_markdown_docs")
    @patch("main.generate_analysis")
    @patch("main.remove_temp_repo_folder")
    @patch("main.create_temp_repo_folder")
    @patch("main.clone_repository")
    @patch("main.get_repo_name")
    @patch("main.onboarding_materials_exist")
    @patch("main.caching_enabled")
    def test_process_remote_repository_with_cache_hit(
        self,
        mock_caching_enabled,
        mock_materials_exist,
        mock_get_repo_name,
        mock_clone,
        mock_create_temp,
        mock_remove_temp,
        mock_generate_analysis,
        mock_generate_markdown,
        mock_copy_files,
        mock_upload,
    ):
        # Test with cache hit
        mock_caching_enabled.return_value = True
        mock_materials_exist.return_value = True
        mock_get_repo_name.return_value = "test_repo"

        with patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos", "ROOT_RESULT": "/tmp/results"}):
            process_remote_repository(
                repo_url="https://github.com/test/repo",
                cache_check=True,
            )

            # Should return early due to cache hit
            mock_clone.assert_not_called()
            mock_generate_analysis.assert_not_called()

    @patch("main.upload_onboarding_materials")
    @patch("main.copy_files")
    @patch("main.generate_markdown_docs")
    @patch("main.generate_analysis")
    @patch("main.remove_temp_repo_folder")
    @patch("main.create_temp_repo_folder")
    @patch("main.clone_repository")
    @patch("main.get_repo_name")
    @patch("main.caching_enabled")
    def test_process_remote_repository_success(
        self,
        mock_caching_enabled,
        mock_get_repo_name,
        mock_clone,
        mock_create_temp,
        mock_remove_temp,
        mock_generate_analysis,
        mock_generate_markdown,
        mock_copy_files,
        mock_upload,
    ):
        # Test successful processing
        mock_caching_enabled.return_value = False
        mock_get_repo_name.return_value = "test_repo"
        mock_clone.return_value = "test_repo"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_folder = Path(temp_dir)
            mock_create_temp.return_value = temp_folder
            mock_generate_analysis.return_value = [Path("analysis.json")]

            with patch.dict(os.environ, {"REPO_ROOT": temp_dir, "ROOT_RESULT": temp_dir}):
                # Create the repo directory
                repo_path = Path(temp_dir) / "test_repo"
                repo_path.mkdir(parents=True, exist_ok=True)

                process_remote_repository(
                    repo_url="https://github.com/test/repo",
                    upload=True,
                    cache_check=False,
                )

                mock_clone.assert_called_once()
                mock_generate_analysis.assert_called_once()
                mock_generate_markdown.assert_called_once()
                mock_remove_temp.assert_called_once()


class TestProcessLocalRepository(unittest.TestCase):
    @patch("main.generate_analysis")
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
                depth_level=1,
            )

            mock_generate_analysis.assert_called_once_with(
                repo_name="test_project",
                repo_path=repo_path,
                output_dir=output_dir,
                depth_level=1,
                monitoring_enabled=False,
            )
            self.assertTrue(output_dir.exists())

    @patch("main.partial_update")
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
                depth_level=2,
                component_id="TestComponent",
            )

            mock_partial_update.assert_called_once_with(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                component_id="TestComponent",
                depth_level=2,
            )


class TestCopyFiles(unittest.TestCase):
    def test_copy_files_success(self):
        # Test copying markdown and JSON files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_folder = Path(temp_dir) / "temp"
            temp_folder.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Create test files
            (temp_folder / "test.md").write_text("# Test")
            (temp_folder / "data.json").write_text('{"key": "value"}')
            (temp_folder / "ignore.txt").write_text("ignore me")

            copy_files(temp_folder, output_dir)

            # Check that only .md and .json files were copied
            self.assertTrue((output_dir / "test.md").exists())
            self.assertTrue((output_dir / "data.json").exists())
            self.assertFalse((output_dir / "ignore.txt").exists())


class TestValidateArguments(unittest.TestCase):
    def test_validate_arguments_local_without_project_name(self):
        # Test local mode without project_name
        parser = MagicMock()
        args = MagicMock()
        args.repositories = None
        args.local = "/path/to/repo"
        args.project_name = None
        args.partial_component_id = None
        args.output_dir = None

        validate_arguments(args, parser, is_local=True)
        parser.error.assert_called_once()

    def test_validate_arguments_partial_without_local(self):
        # Test partial update without local mode
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = None
        args.project_name = "test"
        args.partial_component_id = "test_comp_id"
        args.output_dir = Path("./analysis")

        validate_arguments(args, parser, is_local=False)
        parser.error.assert_called_once()

    def test_validate_arguments_valid(self):
        # Test with valid arguments
        parser = MagicMock()
        args = MagicMock()
        args.repositories = None
        args.local = "/path/to/repo"
        args.project_name = "test"
        args.partial_component_id = None
        args.output_dir = None

        validate_arguments(args, parser, is_local=True)
        parser.error.assert_not_called()

    def test_validate_arguments_remote_without_output_dir(self):
        # Test remote mode requires output_dir
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = None
        args.project_name = None
        args.partial_component_id = None
        args.output_dir = None

        validate_arguments(args, parser, is_local=False)
        parser.error.assert_called_once_with("--output-dir is required when using remote repositories")

    def test_validate_arguments_remote_with_output_dir(self):
        # Test remote mode with output_dir is valid
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = None
        args.project_name = None
        args.partial_component_id = None
        args.output_dir = Path("./analysis")

        validate_arguments(args, parser, is_local=False)
        parser.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
