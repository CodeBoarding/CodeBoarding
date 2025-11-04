import importlib
import pkgutil
import sys
from types import ModuleType


def mirror_package(alias: str, target: str) -> ModuleType:
    """Register an alias package that mirrors another package (including submodules).

    This lets code import `alias.subpkg.mod` while the actual code lives at `target.subpkg.mod`.
    """
    target_pkg = importlib.import_module(target)
    sys.modules[alias] = target_pkg

    if hasattr(target_pkg, "__path__"):
        prefix = target_pkg.__name__ + "."
        for _finder, fullname, _ispkg in pkgutil.walk_packages(target_pkg.__path__, prefix):
            alias_fullname = alias + fullname[len(target_pkg.__name__):]
            sys.modules[alias_fullname] = importlib.import_module(fullname)

    return target_pkg


