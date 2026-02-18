import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.dependency_discovery import discover_dependency_files
from repo_utils.ignore import RepoIgnoreManager


class TestDependencyDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.ignore_manager = RepoIgnoreManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _relative_results(self) -> list[str]:
        return [
            path.relative_to(self.temp_dir).as_posix()
            for path in discover_dependency_files(self.temp_dir, self.ignore_manager)
        ]

    def test_discovers_root_dependency_candidates(self):
        (self.temp_dir / "requirements.txt").touch()
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "README.md").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["requirements.txt", "package.json"])

    def test_discovers_known_dependency_subdirectories(self):
        requirements_dir = self.temp_dir / "requirements"
        requirements_dir.mkdir()
        (requirements_dir / "base.txt").touch()

        deps_dir = self.temp_dir / "deps"
        deps_dir.mkdir()
        (deps_dir / "prod.yaml").touch()

        dependencies_dir = self.temp_dir / "dependencies"
        dependencies_dir.mkdir()
        (dependencies_dir / "stack.toml").touch()

        discovered = self._relative_results()

        self.assertEqual(
            discovered,
            [
                "requirements/base.txt",
                "deps/prod.yaml",
                "dependencies/stack.toml",
            ],
        )

    def test_skips_default_ignored_subdirectories(self):
        env_dir = self.temp_dir / "env"
        env_dir.mkdir()
        (env_dir / "runtime.yml").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, [])

    def test_ignores_non_matching_files_in_dependency_subdirectories(self):
        requirements_dir = self.temp_dir / "requirements"
        requirements_dir.mkdir()
        (requirements_dir / "notes.md").touch()
        (requirements_dir / "deps.json").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, [])

    def test_respects_ignore_manager_for_files_and_subdirectories(self):
        (self.temp_dir / "requirements.txt").touch()

        requirements_dir = self.temp_dir / "requirements"
        requirements_dir.mkdir()
        (requirements_dir / "base.txt").touch()

        deps_dir = self.temp_dir / "deps"
        deps_dir.mkdir()
        (deps_dir / "prod.txt").touch()

        def should_ignore(path: Path) -> bool:
            return path.name in {"requirements.txt", "deps"}

        with patch.object(self.ignore_manager, "should_ignore", side_effect=should_ignore):
            discovered = self._relative_results()

        self.assertEqual(discovered, ["requirements/base.txt"])


if __name__ == "__main__":
    unittest.main()
