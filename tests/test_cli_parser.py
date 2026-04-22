from unittest.mock import patch

from codeboarding_cli.parser import build_parser, main


def test_cli_dispatches_incremental_mode() -> None:
    with (
        patch("codeboarding_cli.parser.incremental.run_from_args") as run_incremental,
        patch("codeboarding_cli.parser.full.run_from_args") as run_full,
    ):
        main(["--local", "/tmp/repo", "--incremental"])

    run_incremental.assert_called_once()
    run_full.assert_not_called()


def test_cli_dispatches_full_by_default() -> None:
    with (
        patch("codeboarding_cli.parser.incremental.run_from_args") as run_incremental,
        patch("codeboarding_cli.parser.full.run_from_args") as run_full,
    ):
        main(["--local", "/tmp/repo"])

    run_full.assert_called_once()
    run_incremental.assert_not_called()


def test_full_flag_registered_and_defaults_false() -> None:
    args = build_parser().parse_args(["--local", "/tmp/repo"])
    assert args.full is False


def test_full_flag_sets_true_when_passed() -> None:
    args = build_parser().parse_args(["--local", "/tmp/repo", "--full"])
    assert args.full is True
