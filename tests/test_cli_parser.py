from unittest.mock import patch

from codeboarding_cli.parser import main


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
