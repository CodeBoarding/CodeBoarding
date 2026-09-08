import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest

import diagram_analysis
from codeboarding_cli.render import main as render_main
from codeboarding_workflows.analysis import build_generator, run_full
from codeboarding_workflows.sources import SourceContext
from diagram_analysis import DEFAULT_DEPTH_CAP, DiagramGenerator, RunContext, RunPaths
from main import build_parser, main
from output_generators import SUPPORTED_FORMATS


def test_cli_dispatches_incremental_mode() -> None:
    with (
        patch("main.incremental_analysis.run_from_args") as run_incremental,
        patch("main.full_analysis.run_from_args") as run_full,
    ):
        main(["incremental", "--local", "/tmp/repo"])

    run_incremental.assert_called_once()
    run_full.assert_not_called()


def test_cli_dispatches_full_by_default() -> None:
    with (
        patch("main.incremental_analysis.run_from_args") as run_incremental,
        patch("main.full_analysis.run_from_args") as run_full,
    ):
        main(["full", "--local", "/tmp/repo"])

    run_full.assert_called_once()
    run_incremental.assert_not_called()


def test_cli_defaults_to_full_when_leading_arg_is_a_flag() -> None:
    with (
        patch("main.incremental_analysis.run_from_args") as run_incremental,
        patch("main.full_analysis.run_from_args") as run_full,
    ):
        main(["--local", "/tmp/repo"])

    run_full.assert_called_once()
    run_incremental.assert_not_called()


def test_cli_defaults_to_full_when_leading_arg_is_a_repo_url() -> None:
    with (
        patch("main.incremental_analysis.run_from_args") as run_incremental,
        patch("main.full_analysis.run_from_args") as run_full,
    ):
        main(["https://github.com/user/repo"])

    run_full.assert_called_once()
    run_incremental.assert_not_called()
    (args, _parser), _kwargs = run_full.call_args
    assert args.repositories == ["https://github.com/user/repo"]


def test_cli_incremental_subcommand_is_not_swallowed_as_positional() -> None:
    with (
        patch("main.incremental_analysis.run_from_args") as run_incremental,
        patch("main.full_analysis.run_from_args") as run_full,
    ):
        main(["incremental", "--local", "/tmp/repo"])

    run_incremental.assert_called_once()
    run_full.assert_not_called()


def test_force_flag_registered_and_defaults_false() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo"])
    assert args.force is False


def test_force_flag_sets_true_when_passed() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo", "--force"])
    assert args.force is True


def test_depth_cap_default_is_unchanged() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo"])
    assert args.depth_cap == DEFAULT_DEPTH_CAP == 3
    assert not hasattr(args, "depth_level")
    assert not hasattr(diagram_analysis, "DEFAULT_DEPTH_LEVEL")


@pytest.mark.parametrize("command", [[], ["full"]])
def test_depth_cap_has_canonical_destination(command: list[str]) -> None:
    with patch("main.full_analysis.run_from_args") as run_full:
        main([*command, "--local", "/tmp/repo", "--depth-cap", "5"])

    args = run_full.call_args.args[0]
    assert args.depth_cap == 5
    assert not hasattr(args, "depth_level")


@pytest.mark.parametrize("command", [[], ["full"]])
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("extra", [[], ["--depth-cap", "4"]])
def test_old_depth_input_is_rejected(command, remote, extra, capsys) -> None:
    target = ["https://github.com/user/repo"] if remote else ["--local", "/tmp/repo"]
    with patch("main.full_analysis.run_from_args") as run_full:
        with pytest.raises(SystemExit) as exc:
            main([*command, *target, "--depth-level", "5", *extra])
    assert exc.value.code == 2
    assert "unrecognized arguments: --depth-level" in capsys.readouterr().err
    run_full.assert_not_called()


@pytest.mark.parametrize("factory", [run_full, build_generator])
def test_workflow_rejects_old_python_keyword(factory, tmp_path) -> None:
    with pytest.raises(TypeError, match="depth_level"):
        factory(RunPaths(tmp_path, tmp_path, "repo"), RunContext("run", "log"), **{"depth_level": 3})


def test_generator_rejects_old_python_keyword(tmp_path) -> None:
    with pytest.raises(TypeError, match="depth_level"):
        DiagramGenerator(tmp_path, tmp_path, "repo", tmp_path, run_id="run", log_path="log", **{"depth_level": 3})


def test_depth_cap_requires_integer() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["full", "--local", "/tmp/repo", "--depth-cap", "invalid"])
    assert exc.value.code == 2


def test_full_help_names_cap_and_distinguishes_realized_depth(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["full", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--depth-cap DEPTH_CAP" in help_text
    assert "--depth-level" not in help_text
    assert "metadata.depth_cap" in help_text
    assert "metadata.depth_level" in help_text


@pytest.mark.parametrize("remote", [False, True])
def test_depth_cap_maps_to_workflow_parameter(tmp_path: Path, monkeypatch, remote: bool) -> None:
    monkeypatch.chdir(tmp_path)
    source = SourceContext(repo_path=tmp_path, artifact_dir=tmp_path, project_name="repo")
    target = ["https://github.com/user/repo"] if remote else ["--local", str(tmp_path)]
    with (
        patch("codeboarding_cli.commands.full_analysis.bootstrap_environment"),
        patch("codeboarding_workflows.orchestration.RunContext.resolve"),
        patch("codeboarding_cli.commands.full_analysis.remote_source", return_value=nullcontext(source)),
        patch("codeboarding_cli.commands.full_analysis.monitor_execution"),
        patch("codeboarding_cli.commands.full_analysis.render_docs"),
        patch("codeboarding_cli.commands.full_analysis.get_branch", return_value="main"),
        patch("codeboarding_cli.commands.full_analysis.get_current_commit", return_value="sha"),
        patch("codeboarding_cli.commands.full_analysis.run_full") as run_full,
    ):
        main(["full", *target, "--depth-cap", "5"])

    run_full.assert_called_once()
    assert run_full.call_args.kwargs["depth_cap"] == 5
    assert "depth_level" not in run_full.call_args.kwargs


def test_render_flag_registered_and_defaults_none() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo"])
    assert args.render is None


@pytest.mark.parametrize("output_format", SUPPORTED_FORMATS)
@pytest.mark.parametrize(
    "command_args",
    [
        ["full", "--local", "/tmp/repo"],
        ["incremental", "--local", "/tmp/repo"],
        ["partial", "--local", "/tmp/repo", "--component-id", "1"],
    ],
)
def test_render_flag_accepts_all_formats(command_args: list[str], output_format: str) -> None:
    args = build_parser().parse_args([*command_args, "--render", output_format])
    assert args.render == output_format


def test_render_flag_rejects_remote_full() -> None:
    with pytest.raises(SystemExit):
        main(["full", "https://github.com/user/repo", "--render", "md"])


@patch("codeboarding_cli.render.setup_logging")
@patch("codeboarding_cli.render.render_output")
def test_standalone_render_uses_existing_analysis(mock_render_output, _mock_setup_logging, tmp_path: Path) -> None:
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps({"metadata": {"repo_name": "demo"}}))
    output_dir = tmp_path / "rendered"

    render_main([str(analysis_path), "--format", "html", "--output-dir", str(output_dir)])

    mock_render_output.assert_called_once_with(
        "html",
        analysis_path=analysis_path.resolve(),
        repo_name="demo",
        output_dir=output_dir.resolve(),
    )


@pytest.mark.parametrize("analysis", ["not json", json.dumps({"metadata": {}})])
@patch("codeboarding_cli.render.setup_logging")
@patch("codeboarding_cli.render.render_output")
def test_standalone_render_rejects_invalid_analysis(
    mock_render_output,
    mock_setup_logging,
    tmp_path: Path,
    analysis: str,
) -> None:
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(analysis)

    with pytest.raises(SystemExit):
        render_main([str(analysis_path)])

    mock_render_output.assert_not_called()
    mock_setup_logging.assert_not_called()


@pytest.mark.parametrize(
    ("command_module", "command_args"),
    [
        ("main.full_analysis.run_from_args", ["full", "--local", "/tmp/repo"]),
        ("main.incremental_analysis.run_from_args", ["incremental", "--local", "/tmp/repo"]),
        ("main.partial_analysis.run_from_args", ["partial", "--local", "/tmp/repo", "--component-id", "1"]),
    ],
)
@patch("main.render_output")
def test_main_renders_after_any_successful_analysis(
    mock_render_output,
    command_module: str,
    command_args: list[str],
) -> None:
    analysis_path = Path("/tmp/repo/.codeboarding/analysis.json")
    events: list[str] = []
    mock_render_output.side_effect = lambda *args, **kwargs: events.append("render")
    with (
        patch(command_module, side_effect=lambda *args, **kwargs: events.append("analysis")),
        patch("main.Path.is_file", return_value=True),
    ):
        main([*command_args, "--render", "rst"])

    assert events == ["analysis", "render"]
    mock_render_output.assert_called_once_with(
        "rst",
        analysis_path=analysis_path,
        repo_name="repo",
        output_dir=Path("/tmp/repo/.codeboarding"),
    )


@patch("main.render_output")
@patch("main.incremental_analysis.run_from_args")
def test_main_skips_render_when_analysis_does_not_exist(
    _mock_run_incremental,
    mock_render_output,
    tmp_path: Path,
) -> None:
    main(["incremental", "--local", str(tmp_path), "--render", "md"])

    mock_render_output.assert_not_called()
