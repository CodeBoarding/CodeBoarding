"""Generic typed registry for plugin extension points."""

import logging

logger = logging.getLogger(__name__)


class DuplicateRegistrationError(Exception):
    """Raised when a name is registered twice in the same registry."""


class Registry[T]:
    """
    A named, typed dictionary of registered items.

    Thread-safety: Registration is expected at startup only (during load_plugins).
    Reads during pipeline execution are safe without locks.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T) -> None:
        """Register an item. Raises DuplicateRegistrationError on conflict."""
        if name in self._items:
            raise DuplicateRegistrationError(f"Registry '{self.name}': duplicate registration for '{name}'")
        logger.debug(f"Registry '{self.name}': registered '{name}'")
        self._items[name] = item

    def get(self, name: str) -> T | None:
        """Get a registered item by name, or None."""
        return self._items.get(name)

    def all(self) -> dict[str, T]:
        """Return a copy of all registered items."""
        return dict(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __repr__(self) -> str:
        return f"Registry('{self.name}', items={list(self._items.keys())})"
