import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.dependency_discovery import (
    Ecosystem,
    FileRole,
    DiscoveredDependencyFile,
    discover_dependency_files,
)
from repo_utils.ignore import RepoIgnoreManager


class TestDependencyDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.ignore_manager = RepoIgnoreManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _relative_results(self, **kwargs) -> list[str]:
        return [
            discovered.path.relative_to(self.temp_dir).as_posix()
            for discovered in discover_dependency_files(self.temp_dir, self.ignore_manager, **kwargs)
        ]

    # ── Root-level discovery ──

    def test_discovers_root_dependency_candidates(self):
        (self.temp_dir / "requirements.txt").touch()
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "README.md").touch()

        discovered = self._relative_results()

        self.assertIn("requirements.txt", discovered)
        self.assertIn("package.json", discovered)
        self.assertNotIn("README.md", discovered)

    def test_skips_default_ignored_subdirectories(self):
        env_dir = self.temp_dir / "env"
        env_dir.mkdir()
        (env_dir / "environment.yml").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, [])

    def test_ignores_non_registry_files(self):
        (self.temp_dir / "notes.md").touch()
        (self.temp_dir / "random.json").touch()
        (self.temp_dir / "data.csv").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, [])

    # ── Per-ecosystem root discovery ──

    def test_discovers_go_dependency_files(self):
        (self.temp_dir / "go.mod").touch()
        (self.temp_dir / "go.sum").touch()
        (self.temp_dir / "go.work").touch()
        (self.temp_dir / "go.work.sum").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["go.mod", "go.sum", "go.work", "go.work.sum"])

    def test_discovers_java_dependency_files(self):
        (self.temp_dir / "pom.xml").touch()
        (self.temp_dir / "build.gradle").touch()
        (self.temp_dir / "settings.gradle").touch()
        (self.temp_dir / "gradle.properties").touch()

        discovered = self._relative_results()

        self.assertEqual(
            discovered,
            ["build.gradle", "gradle.properties", "pom.xml", "settings.gradle"],
        )

    def test_discovers_java_kotlin_dsl_files(self):
        (self.temp_dir / "build.gradle.kts").touch()
        (self.temp_dir / "settings.gradle.kts").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["build.gradle.kts", "settings.gradle.kts"])

    def test_discovers_php_dependency_files(self):
        (self.temp_dir / "composer.json").touch()
        (self.temp_dir / "composer.lock").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["composer.json", "composer.lock"])

    def test_discovers_rust_dependency_files(self):
        (self.temp_dir / "Cargo.toml").touch()
        (self.temp_dir / "Cargo.lock").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["Cargo.lock", "Cargo.toml"])

    def test_discovers_ruby_dependency_files(self):
        (self.temp_dir / "Gemfile").touch()
        (self.temp_dir / "Gemfile.lock").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["Gemfile", "Gemfile.lock"])

    def test_discovers_dotnet_dependency_files(self):
        (self.temp_dir / "Directory.Build.props").touch()
        (self.temp_dir / "Directory.Packages.props").touch()
        (self.temp_dir / "global.json").touch()

        discovered = self._relative_results()

        self.assertEqual(
            discovered,
            ["Directory.Build.props", "Directory.Packages.props", "global.json"],
        )

    def test_discovers_dart_dependency_files(self):
        (self.temp_dir / "pubspec.yaml").touch()
        (self.temp_dir / "pubspec.lock").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["pubspec.lock", "pubspec.yaml"])

    # ── Nested / monorepo discovery (bounded walk) ──

    def test_discovers_nested_dependency_files(self):
        service_dir = self.temp_dir / "services" / "auth"
        service_dir.mkdir(parents=True)
        (service_dir / "go.mod").touch()
        (service_dir / "go.sum").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["services/auth/go.mod", "services/auth/go.sum"])

    def test_discovers_java_monorepo_submodules(self):
        (self.temp_dir / "pom.xml").touch()
        sub = self.temp_dir / "module-a"
        sub.mkdir()
        (sub / "pom.xml").touch()

        discovered = self._relative_results()

        self.assertEqual(discovered, ["module-a/pom.xml", "pom.xml"])

    def test_respects_max_depth(self):
        deep = self.temp_dir / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "go.mod").touch()

        self.assertEqual(self._relative_results(max_depth=3), [])
        self.assertEqual(self._relative_results(max_depth=4), ["a/b/c/d/go.mod"])

    def test_max_depth_zero_is_root_only(self):
        (self.temp_dir / "go.mod").touch()
        sub = self.temp_dir / "sub"
        sub.mkdir()
        (sub / "go.mod").touch()

        discovered = self._relative_results(max_depth=0)

        self.assertEqual(discovered, ["go.mod"])

    # ── Filtering ──

    def test_filter_by_role_manifest_only(self):
        (self.temp_dir / "go.mod").touch()
        (self.temp_dir / "go.sum").touch()

        discovered = self._relative_results(roles={FileRole.MANIFEST})

        self.assertEqual(discovered, ["go.mod"])

    def test_filter_by_role_lock_only(self):
        (self.temp_dir / "go.mod").touch()
        (self.temp_dir / "go.sum").touch()

        discovered = self._relative_results(roles={FileRole.LOCK})

        self.assertEqual(discovered, ["go.sum"])

    def test_filter_by_ecosystem(self):
        (self.temp_dir / "go.mod").touch()
        (self.temp_dir / "requirements.txt").touch()
        (self.temp_dir / "pom.xml").touch()

        discovered = self._relative_results(ecosystems={Ecosystem.GO})

        self.assertEqual(discovered, ["go.mod"])

    def test_filter_by_ecosystem_and_role(self):
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "package-lock.json").touch()
        (self.temp_dir / "tsconfig.json").touch()
        (self.temp_dir / "requirements.txt").touch()

        discovered = self._relative_results(
            ecosystems={Ecosystem.NODE},
            roles={FileRole.MANIFEST},
        )

        self.assertEqual(discovered, ["package.json"])

    # ── Classified discovery ──

    def test_classified_returns_spec_metadata(self):
        (self.temp_dir / "go.mod").touch()

        classified = discover_dependency_files(self.temp_dir, self.ignore_manager)

        self.assertEqual(len(classified), 1)
        self.assertIsInstance(classified[0], DiscoveredDependencyFile)
        self.assertEqual(classified[0].spec.ecosystem, Ecosystem.GO)
        self.assertEqual(classified[0].spec.role, FileRole.MANIFEST)
        self.assertEqual(classified[0].spec.filename, "go.mod")

    def test_classified_multiple_ecosystems(self):
        (self.temp_dir / "pom.xml").touch()
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "go.mod").touch()

        classified = discover_dependency_files(self.temp_dir, self.ignore_manager)

        ecosystems_found = {d.spec.ecosystem for d in classified}
        self.assertEqual(ecosystems_found, {Ecosystem.JAVA, Ecosystem.NODE, Ecosystem.GO})

    # ── Ignore manager integration ──

    def test_respects_ignore_manager_for_files_and_subdirectories(self):
        (self.temp_dir / "requirements.txt").touch()

        sub = self.temp_dir / "module"
        sub.mkdir()
        (sub / "pom.xml").touch()

        ignored_sub = self.temp_dir / "vendor"
        ignored_sub.mkdir()
        (ignored_sub / "go.mod").touch()

        def should_ignore(path: Path) -> bool:
            return path.name in {"requirements.txt", "vendor"}

        with patch.object(self.ignore_manager, "should_ignore", side_effect=should_ignore):
            discovered = self._relative_results()

        self.assertEqual(discovered, ["module/pom.xml"])

    def test_no_duplicates_across_walk(self):
        (self.temp_dir / "go.mod").touch()

        discovered = self._relative_results()

        self.assertEqual(len(discovered), 1)


if __name__ == "__main__":
    unittest.main()
