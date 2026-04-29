import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from codeboarding_cli.commands.full_analysis import run_from_args, validate_arguments
from codeboarding_workflows.analysis import run_full, run_partial
from codeboarding_workflows.sources import local_source, onboarding_materials_exist, remote_source


class TestOnboardingMaterialsExist(unittest.TestCase):
    @patch("codeboarding_workflows.sources.remote.requests.get")
    def test_onboarding_materials_exist_true(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = onboarding_materials_exist("test_project")

        self.assertTrue(result)
        mock_get.assert_called_once()
        call_args = mock_get.call_args[0][0]
        self.assertIn("test_project", call_args)

    @patch("codeboarding_workflows.sources.remote.requests.get")
    def test_onboarding_materials_exist_false(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        self.assertFalse(onboarding_materials_exist("test_project"))


class TestGenerateAnalysis(unittest.TestCase):
    @patch("codeboarding_workflows.analysis.DiagramGenerator")
    def test_generate_analysis(self, mock_generator_class):
        mock_generator = MagicMock()
        mock_generator.generate_analysis.return_value = Path("analysis.json")
        mock_generator_class.return_value = mock_generator

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            result = run_full(
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
                static_analyzer=None,
            )
            mock_generator.generate_analysis.assert_called_once()

    @patch("codeboarding_workflows.analysis.DiagramGenerator")
    def test_generate_analysis_with_force_full(self, mock_generator_class):
        mock_generator = MagicMock()
        mock_generator.generate_analysis.return_value = [Path("analysis.json")]
        mock_generator_class.return_value = mock_generator

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            run_full(
                repo_name="test_repo",
                repo_path=repo_path,
                output_dir=output_dir,
                run_id="test-run-id",
                log_path="test_repo/test-run-log",
                force_full=True,
            )

        self.assertTrue(mock_generator.force_full_analysis)


class TestPartialUpdate(unittest.TestCase):
    @patch("codeboarding_workflows.analysis.save_sub_analysis")
    @patch("codeboarding_workflows.analysis.load_full_analysis")
    @patch("codeboarding_workflows.analysis.DiagramGenerator")
    def test_partial_update_success(self, mock_generator_class, mock_load_full, mock_save_sub_analysis):
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

            run_partial(
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

    @patch("codeboarding_workflows.analysis.save_sub_analysis")
    @patch("codeboarding_workflows.analysis.load_full_analysis")
    @patch("codeboarding_workflows.analysis.DiagramGenerator")
    def test_partial_update_nested_component_success(
        self, mock_generator_class, mock_load_full, mock_save_sub_analysis
    ):
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

            run_partial(
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

    @patch("codeboarding_workflows.analysis.DiagramGenerator")
    def test_partial_update_file_not_found(self, mock_generator_class):
        mock_generator = MagicMock()
        mock_generator_class.return_value = mock_generator

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            run_partial(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name="test_project",
                component_id="TestComponent",
                run_id="test-run-id",
                log_path="test_project/test-run-log",
                depth_level=1,
            )

            mock_generator.pre_analysis.assert_called_once()
            mock_generator.process_component.assert_not_called()


class TestRemoteSource(unittest.TestCase):
    """Source-layer contract: clone/cache/cleanup orthogonal to scope."""

    @patch("codeboarding_workflows.sources.remote.clone_repository")
    @patch("codeboarding_workflows.sources.remote.onboarding_materials_exist")
    @patch("codeboarding_workflows.sources.remote.get_repo_name")
    def test_cache_hit_yields_none(self, mock_get_repo_name, mock_materials_exist, mock_clone):
        mock_get_repo_name.return_value = "test_repo"
        mock_materials_exist.return_value = True

        with remote_source("https://github.com/test/repo") as src:
            self.assertIsNone(src)

        mock_clone.assert_not_called()

    @patch("codeboarding_workflows.sources.remote.upload_onboarding_materials")
    @patch("codeboarding_workflows.sources.remote.remove_temp_repo_folder")
    @patch("codeboarding_workflows.sources.remote.create_temp_repo_folder")
    @patch("codeboarding_workflows.sources.remote.clone_repository")
    @patch("codeboarding_workflows.sources.remote.onboarding_materials_exist")
    @patch("codeboarding_workflows.sources.remote.get_repo_name")
    def test_clones_yields_context_and_cleans_up_on_exit(
        self,
        mock_get_repo_name,
        mock_materials_exist,
        mock_clone,
        mock_create_temp,
        mock_remove_temp,
        mock_upload,
    ):
        mock_get_repo_name.return_value = "test_repo"
        mock_materials_exist.return_value = False
        mock_clone.return_value = "test_repo"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_folder = Path(temp_dir)
            mock_create_temp.return_value = temp_folder

            with remote_source("https://github.com/test/repo", upload=True) as src:
                self.assertIsNotNone(src)
                assert src is not None  # narrow for type-checker
                self.assertEqual(src.project_name, "test_repo")
                self.assertEqual(src.artifact_dir, temp_folder)

            mock_clone.assert_called_once()
            mock_remove_temp.assert_called_once_with(str(temp_folder))
            mock_upload.assert_called_once()


class TestLocalSource(unittest.TestCase):
    def test_yields_repo_path_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "out"
            output_dir.mkdir()

            with local_source(repo_path=repo_path, project_name="proj", artifact_dir=output_dir) as src:
                self.assertEqual(src.repo_path, repo_path)
                self.assertEqual(src.project_name, "proj")
                self.assertEqual(src.artifact_dir, output_dir)

    @patch("codeboarding_workflows.analysis.DiagramGenerator")
    @patch("codeboarding_workflows.analysis.load_full_analysis")
    def test_local_source_composes_with_partial_update(self, mock_load_full, mock_generator_class):
        """Axes are orthogonal: the local source composes with any scope, not just full."""
        mock_load_full.return_value = None  # exits early, enough to prove dispatch

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            output_dir = Path(temp_dir) / "out"
            output_dir.mkdir()

            with local_source(repo_path=repo_path, project_name="proj", artifact_dir=output_dir) as src:
                run_partial(
                    repo_path=src.repo_path,
                    output_dir=src.artifact_dir,
                    project_name=src.project_name,
                    component_id="does-not-matter",
                    run_id="r",
                    log_path="l",
                )

            mock_generator_class.return_value.pre_analysis.assert_called_once()


class TestFullCliLocal(unittest.TestCase):
    """CLI-level composition: `full --local` routes to the right scope and passes args through."""

    def _make_args(self, repo_path: Path, **overrides) -> MagicMock:
        args = MagicMock()
        args.local = repo_path
        args.repositories = []
        args.output_dir = None
        args.project_name = None
        args.binary_location = None
        args.depth_level = 1
        args.upload = False
        args.enable_monitoring = False
        args.force = False
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    @patch("codeboarding_cli.commands.full_analysis.run_full")
    @patch("codeboarding_workflows.orchestration.RunContext")
    @patch("codeboarding_cli.commands.full_analysis.bootstrap_environment")
    def test_local_full_calls_run_full(self, _mock_bootstrap, mock_run_context, mock_run_full):
        mock_run_context.resolve.return_value = MagicMock(run_id="r", log_path="l", finalize=lambda: None)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()

            run_from_args(self._make_args(repo_path), MagicMock())

        mock_run_full.assert_called_once()
        kwargs = mock_run_full.call_args.kwargs
        self.assertEqual(kwargs["repo_path"], repo_path.resolve())
        self.assertFalse(kwargs["force_full"])

    @patch("codeboarding_cli.commands.full_analysis.run_full")
    @patch("codeboarding_workflows.orchestration.RunContext")
    @patch("codeboarding_cli.commands.full_analysis.bootstrap_environment")
    def test_force_flag_propagates(self, _mock_bootstrap, mock_run_context, mock_run_full):
        mock_run_context.resolve.return_value = MagicMock(run_id="r", log_path="l", finalize=lambda: None)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()

            run_from_args(self._make_args(repo_path, force=True), MagicMock())

        self.assertTrue(mock_run_full.call_args.kwargs["force_full"])


class TestPartialCliLocal(unittest.TestCase):
    """CLI-level composition: `partial` dispatches to run_partial with the component id."""

    @patch("codeboarding_cli.commands.partial_analysis.run_partial")
    @patch("codeboarding_workflows.orchestration.RunContext")
    @patch("codeboarding_cli.commands.partial_analysis.bootstrap_environment")
    def test_dispatch(self, _mock_bootstrap, mock_run_context, mock_run_partial):
        from codeboarding_cli.commands.partial_analysis import run_from_args as partial_run

        mock_run_context.resolve.return_value = MagicMock(run_id="r", log_path="l", finalize=lambda: None)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()

            args = MagicMock()
            args.local = repo_path
            args.output_dir = None
            args.project_name = None
            args.binary_location = None
            args.depth_level = 1
            args.component_id = "c1"
            args.enable_monitoring = False

            partial_run(args, MagicMock())

        mock_run_partial.assert_called_once()
        self.assertEqual(mock_run_partial.call_args.kwargs["component_id"], "c1")

    def test_requires_local(self):
        from codeboarding_cli.commands.partial_analysis import validate_arguments as partial_validate

        parser = MagicMock()
        args = MagicMock()
        args.local = None

        partial_validate(args, parser)
        parser.error.assert_called_once()


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
    def test_valid_local(self):
        parser = MagicMock()
        args = MagicMock()
        args.repositories = None
        args.local = "/path/to/repo"
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
        args.output_dir = None
        args.project_name = None
        args.upload = True

        validate_arguments(args, parser)
        parser.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
