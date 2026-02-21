import pytest

from core.registry import DuplicateRegistrationError, Registry


def test_register_and_get():
    reg: Registry[str] = Registry("test")
    reg.register("foo", "bar")
    assert reg.get("foo") == "bar"


def test_get_missing_returns_none():
    reg: Registry[str] = Registry("test")
    assert reg.get("missing") is None


def test_duplicate_raises():
    reg: Registry[str] = Registry("test")
    reg.register("foo", "bar")
    with pytest.raises(DuplicateRegistrationError, match="duplicate registration"):
        reg.register("foo", "baz")


def test_all_returns_copy():
    reg: Registry[int] = Registry("test")
    reg.register("a", 1)
    reg.register("b", 2)
    items = reg.all()
    assert items == {"a": 1, "b": 2}
    # Mutating the copy does not affect the registry
    items["c"] = 3
    assert "c" not in reg


def test_len():
    reg: Registry[str] = Registry("test")
    assert len(reg) == 0
    reg.register("x", "val")
    assert len(reg) == 1


def test_contains():
    reg: Registry[str] = Registry("test")
    assert "x" not in reg
    reg.register("x", "val")
    assert "x" in reg


def test_repr():
    reg: Registry[str] = Registry("test")
    reg.register("a", "1")
    assert "test" in repr(reg)
    assert "a" in repr(reg)
