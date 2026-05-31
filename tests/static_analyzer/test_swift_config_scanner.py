"""Tests for Swift / SwiftPM package configuration scanner."""

from pathlib import Path

from static_analyzer.swift_config_scanner import SwiftConfigScanner


class TestSwiftConfigScanner:
    def test_no_packages_no_swift_files(self, tmp_path: Path):
        assert SwiftConfigScanner(tmp_path).scan() == []

    def test_root_package(self, tmp_path: Path):
        (tmp_path / "Package.swift").write_text("// swift-tools-version:5.9\n")
        configs = SwiftConfigScanner(tmp_path).scan()
        assert [c.root for c in configs] == [tmp_path]

    def test_nested_package_in_subdir(self, tmp_path: Path):
        """Package.swift in a subdirectory (e.g. ios/) is detected."""
        ios = tmp_path / "ios"
        ios.mkdir()
        (ios / "Package.swift").write_text("// swift-tools-version:5.9\n")
        configs = SwiftConfigScanner(tmp_path).scan()
        assert [c.root for c in configs] == [ios]

    def test_workspace_with_sibling_packages(self, tmp_path: Path):
        """Sibling packages (no outer manifest) both surface."""
        pkg_a = tmp_path / "PackageA"
        pkg_b = tmp_path / "PackageB"
        pkg_a.mkdir()
        pkg_b.mkdir()
        (pkg_a / "Package.swift").write_text("")
        (pkg_b / "Package.swift").write_text("")
        configs = SwiftConfigScanner(tmp_path).scan()
        roots = sorted(c.root for c in configs)
        assert roots == [pkg_a, pkg_b]

    def test_nested_package_dropped_when_outer_present(self, tmp_path: Path):
        """An outer Package.swift owns inner packages — don't double-root."""
        (tmp_path / "Package.swift").write_text("")
        inner = tmp_path / "Subpackages" / "Inner"
        inner.mkdir(parents=True)
        (inner / "Package.swift").write_text("")
        configs = SwiftConfigScanner(tmp_path).scan()
        assert [c.root for c in configs] == [tmp_path]

    def test_vendored_dependencies_ignored(self, tmp_path: Path):
        """Package.swift files inside .build/Checkouts/ are SwiftPM-fetched deps."""
        (tmp_path / "Package.swift").write_text("")
        dep_dir = tmp_path / ".build" / "checkouts" / "SomeDep"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Package.swift").write_text("")
        configs = SwiftConfigScanner(tmp_path).scan()
        assert [c.root for c in configs] == [tmp_path]

    def test_fallback_when_swift_files_without_manifest(self, tmp_path: Path):
        """Xcode-only project: swift files exist but no Package.swift -> repo root."""
        (tmp_path / "App.swift").write_text("import Foundation\n")
        configs = SwiftConfigScanner(tmp_path).scan()
        assert [c.root for c in configs] == [tmp_path]
