from unittest.mock import MagicMock, patch

from core import Registries
from core.plugin_loader import ENTRY_POINT_GROUP, load_plugins


def test_load_plugins_no_plugins():
    """When no entry points are found, returns empty list."""
    registries = Registries()
    with patch("core.plugin_loader.entry_points", return_value=[]):
        loaded = load_plugins(registries)
    assert loaded == []


def test_load_plugins_calls_init():
    """Plugin init function is called with registries."""
    registries = Registries()
    mock_init = MagicMock()

    mock_ep = MagicMock()
    mock_ep.name = "test_plugin"
    mock_ep.value = "test_module:init"
    mock_ep.load.return_value = mock_init

    with patch("core.plugin_loader.entry_points", return_value=[mock_ep]):
        loaded = load_plugins(registries)

    mock_init.assert_called_once_with(registries)
    assert loaded == ["test_plugin"]


def test_load_plugins_handles_import_failure():
    """A plugin that fails to import does not crash loading."""
    registries = Registries()

    mock_ep = MagicMock()
    mock_ep.name = "bad_plugin"
    mock_ep.value = "bad_module:init"
    mock_ep.load.side_effect = ImportError("no such module")

    with patch("core.plugin_loader.entry_points", return_value=[mock_ep]):
        loaded = load_plugins(registries)

    assert loaded == []


def test_load_plugins_handles_init_failure():
    """A plugin whose init function raises does not crash loading."""
    registries = Registries()

    mock_init = MagicMock(side_effect=RuntimeError("init failed"))
    mock_ep = MagicMock()
    mock_ep.name = "broken_plugin"
    mock_ep.value = "broken_module:init"
    mock_ep.load.return_value = mock_init

    with patch("core.plugin_loader.entry_points", return_value=[mock_ep]):
        loaded = load_plugins(registries)

    assert loaded == []


def test_load_plugins_multiple_plugins():
    """Multiple plugins are loaded in order."""
    registries = Registries()

    ep1 = MagicMock()
    ep1.name = "plugin_a"
    ep1.value = "mod_a:init"
    ep1.load.return_value = MagicMock()

    ep2 = MagicMock()
    ep2.name = "plugin_b"
    ep2.value = "mod_b:init"
    ep2.load.return_value = MagicMock()

    with patch("core.plugin_loader.entry_points", return_value=[ep1, ep2]):
        loaded = load_plugins(registries)

    assert loaded == ["plugin_a", "plugin_b"]


def test_entry_point_group_constant():
    """The entry point group is 'codeboarding.plugins'."""
    assert ENTRY_POINT_GROUP == "codeboarding.plugins"
