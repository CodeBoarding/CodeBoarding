import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _find_packages_on_disk() -> set[str]:
    """Return all Python packages (directories with __init__.py) under the repo root."""
    exclude_prefixes = {".venv", "tests", "repos", "build", "dist"}
    packages = set()
    for init_file in REPO_ROOT.rglob("__init__.py"):
        parts = init_file.relative_to(REPO_ROOT).parts
        # Skip excluded top-level directories
        if parts[0] in exclude_prefixes:
            continue
        # The package path is everything except the __init__.py filename
        pkg = ".".join(parts[:-1])
        packages.add(pkg)
    return packages


def _declared_packages() -> set[str]:
    """Return the packages listed in pyproject.toml [tool.setuptools] packages."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return set(data["tool"]["setuptools"]["packages"])


class TestPyprojectPackages(unittest.TestCase):
    def test_all_packages_declared_in_pyproject(self):
        on_disk = _find_packages_on_disk()
        declared = _declared_packages()
        missing = on_disk - declared
        self.assertEqual(
            missing,
            set(),
            f"Packages exist on disk but are missing from pyproject.toml [tool.setuptools] packages:\n"
            + "\n".join(f"  - {p}" for p in sorted(missing)),
        )

    def test_no_ghost_packages_in_pyproject(self):
        on_disk = _find_packages_on_disk()
        declared = _declared_packages()
        ghost = declared - on_disk
        self.assertEqual(
            ghost,
            set(),
            f"Packages declared in pyproject.toml do not exist on disk:\n"
            + "\n".join(f"  - {p}" for p in sorted(ghost)),
        )
