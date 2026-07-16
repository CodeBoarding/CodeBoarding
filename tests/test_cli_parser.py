from unittest.mock import patch

from main import build_parser, main


def test_cli_dispatches_full_by_default() -> None:
    with patch("main.full_analysis.run_from_args") as run_full:
        main(["full", "--local", "/tmp/repo"])

    run_full.assert_called_once()


def test_cli_defaults_to_full_when_leading_arg_is_a_flag() -> None:
    with patch("main.full_analysis.run_from_args") as run_full:
        main(["--local", "/tmp/repo"])

    run_full.assert_called_once()


def test_cli_defaults_to_full_when_leading_arg_is_a_repo_url() -> None:
    with patch("main.full_analysis.run_from_args") as run_full:
        main(["https://github.com/user/repo"])

    run_full.assert_called_once()
    (args, _parser), _kwargs = run_full.call_args
    assert args.repositories == ["https://github.com/user/repo"]


def test_force_flag_registered_and_defaults_false() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo"])
    assert args.force is False


def test_force_flag_sets_true_when_passed() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo", "--force"])
    assert args.force is True
