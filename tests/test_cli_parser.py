from unittest.mock import patch

from codeboarding_cli.parser import build_parser, main


def test_cli_dispatches_incremental_mode() -> None:
    with (
        patch("codeboarding_cli.parser.incremental.run_from_args") as run_incremental,
        patch("codeboarding_cli.parser.full.run_from_args") as run_full,
    ):
        main(["incremental", "--local", "/tmp/repo"])

    run_incremental.assert_called_once()
    run_full.assert_not_called()


def test_cli_dispatches_full_by_default() -> None:
    with (
        patch("codeboarding_cli.parser.incremental.run_from_args") as run_incremental,
        patch("codeboarding_cli.parser.full.run_from_args") as run_full,
    ):
        main(["full", "--local", "/tmp/repo"])

    run_full.assert_called_once()
    run_incremental.assert_not_called()


def test_force_flag_registered_and_defaults_false() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo"])
    assert args.force is False


def test_force_flag_sets_true_when_passed() -> None:
    args = build_parser().parse_args(["full", "--local", "/tmp/repo", "--force"])
    assert args.force is True
