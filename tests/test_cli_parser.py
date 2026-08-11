import json
from pathlib import Path
from unittest.mock import patch

import pytest

from codeboarding_cli.render import main as render_main
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
    with patch(command_module, side_effect=lambda *args, **kwargs: events.append("analysis")):
        main([*command_args, "--render", "rst"])

    assert events == ["analysis", "render"]
    mock_render_output.assert_called_once_with(
        "rst",
        analysis_path=analysis_path,
        repo_name="repo",
        output_dir=Path("/tmp/repo/.codeboarding"),
    )
