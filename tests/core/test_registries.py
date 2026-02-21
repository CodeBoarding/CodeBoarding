from core import Registries, get_registries, reset_registries
from core.registry import Registry


def test_get_registries_returns_singleton():
    reset_registries()
    r1 = get_registries()
    r2 = get_registries()
    assert r1 is r2


def test_reset_registries():
    reset_registries()
    r1 = get_registries()
    reset_registries()
    r2 = get_registries()
    assert r1 is not r2


def test_registries_has_expected_attributes():
    r = Registries()
    assert isinstance(r.health_checks, Registry)
    assert isinstance(r.tools, Registry)


def test_registries_names():
    r = Registries()
    assert r.health_checks.name == "health_checks"
    assert r.tools.name == "tools"


def test_registries_start_empty():
    r = Registries()
    assert len(r.health_checks) == 0
    assert len(r.tools) == 0
