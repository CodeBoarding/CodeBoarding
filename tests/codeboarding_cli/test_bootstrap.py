"""``bootstrap_environment`` installs the tools of the repository it is given, and none when binaries are given."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from codeboarding_cli.bootstrap import bootstrap_environment


def _bootstrap(tmp_path: Path, binary_location: Path | None, repo_path: Path | None) -> tuple[MagicMock, MagicMock]:
    with ExitStack() as stack:
        for name in (
            "setup_logging",
            "ensure_config_template",
            "load_user_config",
            "configure_models",
            "validate_api_key_provided",
            "load_plugins",
            "get_registries",
        ):
            stack.enter_context(patch(f"codeboarding_cli.bootstrap.{name}"))
        ensure = stack.enter_context(patch("codeboarding_cli.bootstrap.ensure_tools"))
        update = stack.enter_context(patch("codeboarding_cli.bootstrap.update_config"))
        bootstrap_environment(tmp_path, binary_location, repo_path)
    return ensure, update


def test_a_local_run_installs_the_tools_its_repository_needs(tmp_path: Path):
    ensure, update = _bootstrap(tmp_path, None, tmp_path / "repo")
    assert ensure.call_args.kwargs["repo_path"] == tmp_path / "repo"
    update.assert_not_called()


def test_given_binaries_install_nothing(tmp_path: Path):
    ensure, update = _bootstrap(tmp_path, tmp_path / "bin", tmp_path / "repo")
    ensure.assert_not_called()
    update.assert_called_once_with(tmp_path / "bin")


def test_a_run_without_a_repository_yet_installs_nothing(tmp_path: Path):
    ensure, _ = _bootstrap(tmp_path, None, None)
    ensure.assert_not_called()
