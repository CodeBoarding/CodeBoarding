import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import main as main_module
from diagram_analysis import IncrementalAnalysisRequiresFullError
from diagram_analysis.incremental_models import IncrementalSummary, IncrementalSummaryKind
from main import (
    copy_files,
    generate_analysis,
    generate_markdown_docs,
    onboarding_materials_exist,
    partial_update,
    process_local_repository,
    process_remote_repository,
    validate_arguments,
)


def clean_logging_handlers():
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)


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

    @patch("main.DiagramGenerator")
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
                analysis_path=analysis_file,
                output_dir=output_dir,
                demo_mode=True,
            )

            mock_get_branch.assert_called_once_with(repo_path)
            mock_generate_markdown.assert_called_once()


class TestPartialUpdate(unittest.TestCase):
    @patch("main.save_sub_analysis")
    @patch("main.load_full_analysis")
    @patch("main.DiagramGenerator")
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

    @patch("main.save_sub_analysis")
    @patch("main.load_full_analysis")
    @patch("main.DiagramGenerator")
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

    @patch("main.DiagramGenerator")
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
    @patch("main.log_action")
    @patch("main.upload_onboarding_materials")
    @patch("main.copy_files")
    @patch("main.generate_markdown_docs")
    @patch("main.generate_analysis")
    @patch("main.remove_temp_repo_folder")
    @patch("main.create_temp_repo_folder")
    @patch("main.clone_repository")
    @patch("main.get_repo_name")
    @patch("main.onboarding_materials_exist")
    def test_process_remote_repository_with_cache_hit(
        self,
        mock_materials_exist,
        mock_get_repo_name,
        mock_clone,
        mock_create_temp,
        mock_remove_temp,
        mock_generate_analysis,
        mock_generate_markdown,
        mock_copy_files,
        mock_upload,
        mock_log_action,
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
        mock_log_action.assert_any_call(
            "remote_repository_started",
            cache_check=True,
            depth_level=1,
            output_dir=None,
            repo_name="test_repo",
            repo_url="https://github.com/test/repo",
            run_id="test-run-id",
            upload=False,
        )
        mock_log_action.assert_any_call(
            "remote_cache_hit",
            repo_name="test_repo",
            repo_url="https://github.com/test/repo",
            run_id="test-run-id",
        )

    @patch("main.upload_onboarding_materials")
    @patch("main.copy_files")
    @patch("main.generate_markdown_docs")
    @patch("main.generate_analysis")
    @patch("main.remove_temp_repo_folder")
    @patch("main.create_temp_repo_folder")
    @patch("main.clone_repository")
    @patch("main.get_repo_name")
    def test_process_remote_repository_success(
        self,
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
    @patch("main.log_action")
    @patch("main.generate_analysis")
    def test_process_local_repository_full_analysis(self, mock_generate_analysis, mock_log_action):
        # Test full analysis (no partial update)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            mock_generate_analysis.return_value = Path("analysis.json")

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
            mock_log_action.assert_any_call(
                "local_repository_started",
                depth_level=1,
                force_full=False,
                mode_detail="full_analysis",
                project_name="test_project",
                repo_path=str(repo_path),
                run_id="test-run-id",
            )
            mock_log_action.assert_any_call(
                "full_analysis_completed",
                analysis_path="analysis.json",
                force_full=False,
                project_name="test_project",
                run_id="test-run-id",
            )

    @patch("main.log_action")
    @patch("main.partial_update")
    def test_process_local_repository_partial_update(self, mock_partial_update, mock_log_action):
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
            mock_log_action.assert_any_call(
                "local_repository_started",
                depth_level=2,
                mode_detail="component_analysis",
                project_name="test_project",
                repo_path=str(repo_path),
                run_id="test-run-id",
            )

    @patch("main.log_action")
    @patch("main.DiagramGenerator")
    def test_process_local_repository_incremental_logs_actions(self, mock_generator_class, mock_log_action):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            mock_generator = MagicMock()
            mock_generator.generate_analysis_incremental.return_value = Path("analysis.json")
            mock_generator_class.return_value = mock_generator

            process_local_repository(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=3,
                incremental=True,
            )

            mock_log_action.assert_any_call(
                "local_repository_started",
                depth_level=3,
                mode_detail="incremental_analysis",
                project_name="test_project",
                repo_path=str(repo_path),
                run_id="test-run-id",
            )
            mock_log_action.assert_any_call(
                "incremental_analysis_completed",
                project_name="test_project",
                result=str(Path("analysis.json")),
                run_id="test-run-id",
            )

    @patch("main.log_action")
    @patch("main.DiagramGenerator")
    def test_process_local_repository_incremental_requires_full_logs_action(
        self, mock_generator_class, mock_log_action
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            summary = IncrementalSummary(
                kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
                message="Incremental analysis cannot continue because changed files contain syntax errors. Run a full analysis instead.",
                requires_full_analysis=True,
            )
            mock_generator = MagicMock()
            mock_generator.generate_analysis_incremental.side_effect = IncrementalAnalysisRequiresFullError(
                summary.message,
                summary=summary,
            )
            mock_generator_class.return_value = mock_generator

            with self.assertRaises(SystemExit) as cm:
                process_local_repository(
                    repo_path=repo_path,
                    output_dir=output_dir,
                    project_name="test_project",
                    run_id="test-run-id",
                    log_path="test_project/test-run-log",
                    depth_level=3,
                    incremental=True,
                )

            self.assertEqual(str(cm.exception), summary.message)
            mock_log_action.assert_any_call(
                "incremental_analysis_requires_full",
                incremental_summary=summary.to_dict(),
                project_name="test_project",
                reason=summary.message,
                run_id="test-run-id",
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
    def test_validate_arguments_partial_without_local(self):
        # Test partial update without local mode
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = None
        args.partial_component_id = "test_comp_id"

        validate_arguments(args, parser, is_local=False)
        parser.error.assert_called_once()

    def test_validate_arguments_valid_local(self):
        # Test with valid local arguments
        parser = MagicMock()
        args = MagicMock()
        args.repositories = None
        args.local = "/path/to/repo"
        args.partial_component_id = None

        validate_arguments(args, parser, is_local=True)
        parser.error.assert_not_called()

    def test_validate_arguments_valid_remote(self):
        # Test valid remote arguments (no output-dir requirement anymore)
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = None
        args.partial_component_id = None

        validate_arguments(args, parser, is_local=False)
        parser.error.assert_not_called()

    def test_validate_arguments_both_local_and_remote(self):
        # Test that providing both local and remote raises an error
        parser = MagicMock()
        args = MagicMock()
        args.repositories = ["https://github.com/test/repo"]
        args.local = "/path/to/repo"
        args.partial_component_id = None

        validate_arguments(args, parser, is_local=True)
        parser.error.assert_called_once()

    def test_validate_arguments_rejects_multiple_analysis_modes(self):
        parser = MagicMock()
        args = MagicMock()
        args.repositories = None
        args.local = "/path/to/repo"
        args.partial_component_id = None
        args.full = True
        args.incremental = True
        args.reset_baseline = False

        validate_arguments(args, parser, is_local=True)
        parser.error.assert_called_once_with("Provide at most one of --full, --incremental, or --reset-baseline.")


class TestMainActionLogging(unittest.TestCase):
    def setUp(self):
        clean_logging_handlers()

    def tearDown(self):
        clean_logging_handlers()

    @patch("main.process_local_repository")
    @patch("main.RunContext.resolve")
    @patch("main.initialize_codeboardingignore")
    @patch("main.load_plugins")
    @patch("main.get_registries")
    @patch("main.validate_api_key_provided")
    @patch("main.configure_models")
    @patch("main.load_user_config")
    @patch("main.ensure_config_template")
    @patch("main.env_monitoring_enabled", return_value=False)
    @patch("tool_registry.needs_install", return_value=False)
    def test_main_logs_cli_start_as_first_log_line(
        self,
        mock_needs_install,
        mock_env_monitoring_enabled,
        mock_ensure_config_template,
        mock_load_user_config,
        mock_configure_models,
        mock_validate_api_key,
        mock_get_registries,
        mock_load_plugins,
        mock_initialize_ignore,
        mock_run_context_resolve,
        mock_process_local_repository,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()

            mock_user_cfg = MagicMock()
            mock_user_cfg.llm.agent_model = "agent-model"
            mock_user_cfg.llm.parsing_model = "parsing-model"
            mock_load_user_config.return_value = mock_user_cfg

            run_context = MagicMock()
            run_context.run_id = "run-123"
            run_context.log_path = "repo/run-123"
            mock_run_context_resolve.return_value = run_context

            with patch("sys.argv", ["codeboarding", "--local", str(repo_path)]):
                main_module.main()

            for handler in logging.root.handlers:
                handler.flush()

            log_files = sorted((repo_path / ".codeboarding" / "logs").glob("*.log"))
            self.assertEqual(len(log_files), 1)

            first_line = log_files[0].read_text(encoding="utf-8").splitlines()[0]
            payload = json.loads(first_line[first_line.index("{") :])
            self.assertEqual(payload["action"], "cli_start")
            self.assertEqual(payload["event"], "action")
            self.assertEqual(payload["mode"], "local")
            self.assertEqual(payload["repo_path"], str(repo_path.resolve()))


if __name__ == "__main__":
    unittest.main()
