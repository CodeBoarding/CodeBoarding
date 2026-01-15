"""
Tests for Java project configuration scanner.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from static_analyzer.java_config_scanner import (
    JavaProjectConfig,
    JavaConfigScanner,
    scan_java_projects,
)
from repo_utils.ignore import RepoIgnoreManager


class TestJavaProjectConfig(unittest.TestCase):
    """Test JavaProjectConfig class."""

    def test_init_simple_project(self):
        """Test initialization of simple project config."""
        root = Path("/project")
        config = JavaProjectConfig(root, "maven", False)

        self.assertEqual(config.root, root)
        self.assertEqual(config.build_system, "maven")
        self.assertFalse(config.is_multi_module)
        self.assertEqual(config.modules, [])

    def test_init_multi_module_project(self):
        """Test initialization of multi-module project config."""
        root = Path("/project")
        modules = [Path("/project/module1"), Path("/project/module2")]
        config = JavaProjectConfig(root, "maven", True, modules)

        self.assertEqual(config.root, root)
        self.assertEqual(config.build_system, "maven")
        self.assertTrue(config.is_multi_module)
        self.assertEqual(len(config.modules), 2)

    def test_repr(self):
        """Test string representation."""
        root = Path("/project")
        config = JavaProjectConfig(root, "gradle", False)

        repr_str = repr(config)

        self.assertIn("JavaProjectConfig", repr_str)
        self.assertIn("/project", repr_str)
        self.assertIn("gradle", repr_str)
        self.assertIn("False", repr_str)


class TestJavaConfigScanner(unittest.TestCase):
    """Test JavaConfigScanner class."""

    def setUp(self):
        """Set up test directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)

    def tearDown(self):
        """Clean up test directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init(self):
        """Test scanner initialization."""
        scanner = JavaConfigScanner(self.repo_path)

        self.assertEqual(scanner.repo_path, self.repo_path)
        self.assertIsNotNone(scanner.ignore_manager)

    def test_init_with_ignore_manager(self):
        """Test scanner initialization with custom ignore manager."""
        mock_ignore = Mock(spec=RepoIgnoreManager)
        scanner = JavaConfigScanner(self.repo_path, mock_ignore)

        self.assertEqual(scanner.ignore_manager, mock_ignore)

    def test_scan_no_projects(self):
        """Test scanning repository with no Java projects."""
        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 0)

    def test_scan_simple_maven_project(self):
        """Test detecting simple Maven project."""
        # Create pom.xml
        pom_file = self.repo_path / "pom.xml"
        pom_file.write_text(
            """
            <project>
                <groupId>com.example</groupId>
                <artifactId>test</artifactId>
            </project>
        """
        )

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "maven")
        self.assertFalse(projects[0].is_multi_module)

    def test_scan_multi_module_maven_project(self):
        """Test detecting multi-module Maven project."""
        # Create parent pom.xml with modules
        pom_file = self.repo_path / "pom.xml"
        pom_file.write_text(
            """
            <project xmlns="http://maven.apache.org/POM/4.0.0">
                <groupId>com.example</groupId>
                <artifactId>parent</artifactId>
                <modules>
                    <module>module1</module>
                    <module>module2</module>
                </modules>
            </project>
        """
        )

        # Create module directories
        (self.repo_path / "module1").mkdir()
        (self.repo_path / "module2").mkdir()

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "maven")
        self.assertTrue(projects[0].is_multi_module)
        self.assertEqual(len(projects[0].modules), 2)

    def test_scan_maven_project_without_namespace(self):
        """Test detecting Maven project without namespace in pom.xml."""
        pom_file = self.repo_path / "pom.xml"
        pom_file.write_text(
            """
            <project>
                <groupId>com.example</groupId>
                <artifactId>parent</artifactId>
                <modules>
                    <module>core</module>
                </modules>
            </project>
        """
        )

        (self.repo_path / "core").mkdir()

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertTrue(projects[0].is_multi_module)
        self.assertEqual(len(projects[0].modules), 1)

    def test_scan_simple_gradle_project(self):
        """Test detecting simple Gradle project."""
        settings_file = self.repo_path / "settings.gradle"
        settings_file.write_text(
            """
            rootProject.name = 'test-project'
        """
        )

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "gradle")
        self.assertFalse(projects[0].is_multi_module)

    def test_scan_multi_project_gradle(self):
        """Test detecting multi-project Gradle build."""
        settings_file = self.repo_path / "settings.gradle"
        settings_file.write_text(
            """
            rootProject.name = 'parent'
            include 'app'
            include 'lib'
        """
        )

        (self.repo_path / "app").mkdir()
        (self.repo_path / "lib").mkdir()

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "gradle")
        self.assertTrue(projects[0].is_multi_module)
        # Module discovery is left to JDTLS for Gradle projects
        self.assertEqual(len(projects[0].modules), 0)

    def test_scan_gradle_kotlin_dsl(self):
        """Test detecting Gradle project with Kotlin DSL."""
        settings_file = self.repo_path / "settings.gradle.kts"
        settings_file.write_text(
            """
            rootProject.name = "test"
            include("app")
        """
        )

        (self.repo_path / "app").mkdir()

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "gradle")

    def test_scan_gradle_nested_modules(self):
        """Test detecting Gradle project with nested modules."""
        settings_file = self.repo_path / "settings.gradle"
        settings_file.write_text(
            """
            include 'services:api'
            include 'services:impl'
        """
        )

        (self.repo_path / "services" / "api").mkdir(parents=True)
        (self.repo_path / "services" / "impl").mkdir(parents=True)

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertTrue(projects[0].is_multi_module)
        # Module discovery is left to JDTLS for Gradle projects
        self.assertEqual(len(projects[0].modules), 0)

    def test_scan_eclipse_project(self):
        """Test detecting Eclipse project."""
        # Create .project and .classpath files
        (self.repo_path / ".project").write_text("<projectDescription/>")
        (self.repo_path / ".classpath").write_text("<classpath/>")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "eclipse")

    def test_scan_eclipse_project_without_classpath(self):
        """Test that Eclipse project requires both .project and .classpath."""
        # Only create .project file
        (self.repo_path / ".project").write_text("<projectDescription/>")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        # Should not detect as Eclipse project
        self.assertEqual(len(projects), 0)

    def test_scan_java_files_no_build_system(self):
        """Test detecting Java files without build system."""
        # Create Java files
        (self.repo_path / "src").mkdir()
        (self.repo_path / "src" / "Main.java").write_text("public class Main {}")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "none")

    def test_scan_maven_takes_precedence_over_gradle(self):
        """Test that Maven projects are preferred when both exist."""
        # Create both Maven and Gradle files
        (self.repo_path / "pom.xml").write_text("<project/>")
        (self.repo_path / "settings.gradle").write_text("rootProject.name = 'test'")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        # Should only detect Maven project
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "maven")

    def test_scan_maven_takes_precedence_over_eclipse(self):
        """Test that Maven projects are preferred over Eclipse."""
        (self.repo_path / "pom.xml").write_text("<project/>")
        (self.repo_path / ".project").write_text("<projectDescription/>")
        (self.repo_path / ".classpath").write_text("<classpath/>")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "maven")

    def test_scan_gradle_takes_precedence_over_eclipse(self):
        """Test that Gradle projects are preferred over Eclipse."""
        (self.repo_path / "settings.gradle").write_text("rootProject.name = 'test'")
        (self.repo_path / ".project").write_text("<projectDescription/>")
        (self.repo_path / ".classpath").write_text("<classpath/>")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "gradle")

    def test_scan_nested_projects(self):
        """Test handling of nested projects (keeps only root)."""
        # Create parent Maven project
        (self.repo_path / "pom.xml").write_text("<project/>")

        # Create nested Maven project
        nested_dir = self.repo_path / "subproject"
        nested_dir.mkdir()
        (nested_dir / "pom.xml").write_text("<project/>")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        # Should detect both as separate projects
        self.assertEqual(len(projects), 2)

    def test_scan_with_ignore_manager(self):
        """Test that ignored directories are skipped."""
        # Create project in ignored directory
        ignored_dir = self.repo_path / "node_modules"
        ignored_dir.mkdir()
        (ignored_dir / "pom.xml").write_text("<project/>")

        # Create .gitignore
        (self.repo_path / ".gitignore").write_text("node_modules/")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        # Should not detect project in ignored directory
        self.assertEqual(len(projects), 0)

    def test_scan_invalid_pom_xml(self):
        """Test handling of invalid pom.xml."""
        (self.repo_path / "pom.xml").write_text("invalid xml content <<<<")

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        # Should handle gracefully
        self.assertEqual(len(projects), 0)

    def test_scan_gradle_parse_error(self):
        """Test handling of Gradle file parse errors."""
        settings_file = self.repo_path / "settings.gradle"
        # Create file that will cause parsing issues
        settings_file.write_bytes(b"\x00\x01\x02")  # Binary content

        scanner = JavaConfigScanner(self.repo_path)
        projects = scanner.scan()

        # Should still create basic Gradle config
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "gradle")

    def test_is_subpath_true(self):
        """Test _is_subpath method when path is subpath."""
        scanner = JavaConfigScanner(self.repo_path)

        parent = Path("/project")
        child = Path("/project/module")

        self.assertTrue(scanner._is_subpath(child, parent))

    def test_is_subpath_false(self):
        """Test _is_subpath method when path is not subpath."""
        scanner = JavaConfigScanner(self.repo_path)

        path1 = Path("/project1")
        path2 = Path("/project2")

        self.assertFalse(scanner._is_subpath(path1, path2))

    def test_is_subpath_same_path(self):
        """Test _is_subpath method with same path."""
        scanner = JavaConfigScanner(self.repo_path)

        path = Path("/project")

        self.assertTrue(scanner._is_subpath(path, path))

    def test_has_java_files_true(self):
        """Test _has_java_files method when Java files exist."""
        (self.repo_path / "src").mkdir()
        (self.repo_path / "src" / "Main.java").write_text("public class Main {}")

        scanner = JavaConfigScanner(self.repo_path)

        self.assertTrue(scanner._has_java_files(self.repo_path))

    def test_has_java_files_false(self):
        """Test _has_java_files method when no Java files exist."""
        (self.repo_path / "README.md").write_text("# Project")

        scanner = JavaConfigScanner(self.repo_path)

        self.assertFalse(scanner._has_java_files(self.repo_path))

    def test_has_java_files_nested(self):
        """Test _has_java_files finds nested Java files."""
        nested = self.repo_path / "src" / "main" / "java" / "com" / "example"
        nested.mkdir(parents=True)
        (nested / "App.java").write_text("package com.example; public class App {}")

        scanner = JavaConfigScanner(self.repo_path)

        self.assertTrue(scanner._has_java_files(self.repo_path))


class TestScanJavaProjects(unittest.TestCase):
    """Test scan_java_projects convenience function."""

    def setUp(self):
        """Set up test directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)

    def tearDown(self):
        """Clean up test directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scan_java_projects(self):
        """Test convenience function."""
        # Create simple Maven project
        (self.repo_path / "pom.xml").write_text("<project/>")

        projects = scan_java_projects(self.repo_path)

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].build_system, "maven")

    def test_scan_java_projects_empty_repo(self):
        """Test scanning empty repository."""
        projects = scan_java_projects(self.repo_path)

        self.assertEqual(len(projects), 0)


if __name__ == "__main__":
    unittest.main()
