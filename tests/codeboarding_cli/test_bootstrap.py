"""``bootstrap_environment`` installs the base tools and turns on per-analysis installs, unless binaries are given."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from codeboarding_cli.bootstrap import bootstrap_environment


def _bootstrap(tmp_path: Path, binary_location: Path | None) -> tuple[MagicMock, MagicMock, MagicMock]:
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
        on_demand = stack.enter_context(patch("codeboarding_cli.bootstrap.enable_on_demand_install"))
        update = stack.enter_context(patch("codeboarding_cli.bootstrap.update_config"))
        bootstrap_environment(tmp_path, binary_location)
    return ensure, on_demand, update


def test_a_run_installs_the_base_tools_and_leaves_the_servers_to_the_analysis(tmp_path: Path):
    ensure, on_demand, update = _bootstrap(tmp_path, None)
    assert ensure.call_args.kwargs["languages"] == ()
    on_demand.assert_called_once()
    update.assert_not_called()


def test_given_binaries_install_nothing(tmp_path: Path):
    ensure, on_demand, update = _bootstrap(tmp_path, tmp_path / "bin")
    ensure.assert_not_called()
    on_demand.assert_not_called()
    update.assert_called_once_with(tmp_path / "bin")
