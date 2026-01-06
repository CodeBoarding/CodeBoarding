"""
Java project configuration scanner.

Detects Maven, Gradle, and multi-module Java projects.
"""

from pathlib import Path
from typing import List, Optional
import xml.etree.ElementTree as ET
import logging

from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)


class JavaProjectConfig:
    """Configuration for a Java project."""

    def __init__(
        self,
        root: Path,
        build_system: str,  # "maven", "gradle", "eclipse", or "none"
        is_multi_module: bool = False,
        modules: Optional[List[Path]] = None,
    ):
        self.root = root
        self.build_system = build_system
        self.is_multi_module = is_multi_module
        self.modules = modules or []

    def __repr__(self):
        return (
            f"JavaProjectConfig(root={self.root}, "
            f"build_system={self.build_system}, "
            f"is_multi_module={self.is_multi_module}, "
            f"modules={len(self.modules)})"
        )


class JavaConfigScanner:
    """Scanner for Java project configurations."""

    def __init__(self, repo_path: Path, ignore_manager: RepoIgnoreManager | None = None):
        self.repo_path = repo_path
        self.ignore_manager = ignore_manager if ignore_manager else RepoIgnoreManager(repo_path)

    def scan(self) -> List[JavaProjectConfig]:
        """
        Scan repository for Java projects.

        Returns list of JavaProjectConfig objects, one per root project.
        For multi-module projects, returns single config with submodules.
        """
        projects = []

        # Find all potential project roots
        maven_roots = self._find_maven_projects()
        gradle_roots = self._find_gradle_projects()
        eclipse_roots = self._find_eclipse_projects()

        # Process Maven projects
        for root in maven_roots:
            if not self.ignore_manager.should_ignore(root):
                config = self._analyze_maven_project(root)
                if config:
                    projects.append(config)

        # Process Gradle projects (exclude if already covered by Maven)
        for root in gradle_roots:
            if not self.ignore_manager.should_ignore(root):
                if not any(self._is_subpath(root, p.root) for p in projects):
                    config = self._analyze_gradle_project(root)
                    if config:
                        projects.append(config)

        # Process Eclipse projects (only if no Maven/Gradle)
        for root in eclipse_roots:
            if not self.ignore_manager.should_ignore(root):
                if not any(self._is_subpath(root, p.root) for p in projects):
                    config = JavaProjectConfig(root, "eclipse", False)
                    projects.append(config)

        # If no build system found but Java files exist, create basic config
        if not projects and self._has_java_files(self.repo_path):
            logger.warning(
                f"No Maven/Gradle/Eclipse project found in {self.repo_path}, "
                f"but Java files detected. Analysis will be limited."
            )
            projects.append(JavaProjectConfig(self.repo_path, "none", False))

        return projects

    def _find_maven_projects(self) -> List[Path]:
        """Find all directories containing pom.xml."""
        return [p.parent for p in self.repo_path.rglob("pom.xml") if p.is_file()]

    def _find_gradle_projects(self) -> List[Path]:
        """Find all directories containing settings.gradle."""
        gradle_roots = []

        # settings.gradle indicates a project root
        gradle_roots.extend(p.parent for p in self.repo_path.rglob("settings.gradle") if p.is_file())
        gradle_roots.extend(p.parent for p in self.repo_path.rglob("settings.gradle.kts") if p.is_file())

        return gradle_roots

    def _find_eclipse_projects(self) -> List[Path]:
        """Find all directories containing .project file."""
        return [
            p.parent for p in self.repo_path.rglob(".project") if p.is_file() and (p.parent / ".classpath").exists()
        ]

    def _analyze_maven_project(self, pom_dir: Path) -> Optional[JavaProjectConfig]:
        """Analyze a Maven project to determine if it's multi-module."""
        pom_file = pom_dir / "pom.xml"

        try:
            tree = ET.parse(pom_file)
            root = tree.getroot()

            # Define namespace
            ns = {"maven": "http://maven.apache.org/POM/4.0.0"}

            # Check for <modules> section
            modules_elem = root.find("maven:modules", ns)
            if modules_elem is None:
                # Also try without namespace (some POMs don't use it)
                modules_elem = root.find("modules")

            if modules_elem is not None and len(modules_elem) > 0:
                # Multi-module project
                module_paths = []
                for module in modules_elem.findall("maven:module", ns) or modules_elem.findall("module"):
                    module_name = module.text.strip()
                    module_path = pom_dir / module_name
                    if module_path.exists():
                        module_paths.append(module_path)

                return JavaProjectConfig(pom_dir, "maven", is_multi_module=True, modules=module_paths)
            else:
                # Single-module Maven project
                return JavaProjectConfig(pom_dir, "maven", False)

        except ET.ParseError as e:
            logger.warning(f"Failed to parse {pom_file}: {e}")
            return None

    def _analyze_gradle_project(self, gradle_dir: Path) -> Optional[JavaProjectConfig]:
        """Analyze a Gradle project to determine if it's multi-project."""
        settings_file = gradle_dir / "settings.gradle"
        if not settings_file.exists():
            settings_file = gradle_dir / "settings.gradle.kts"

        if not settings_file.exists():
            return JavaProjectConfig(gradle_dir, "gradle", False)

        try:
            content = settings_file.read_text()

            # Look for include statements
            # Examples: include 'app', 'lib'
            #           include 'services:api', 'services:impl'
            import re

            includes = re.findall(r"include\s+['\"]([^'\"]+)['\"]", content)
            includes.extend(re.findall(r"include\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", content))

            if includes:
                # Multi-project Gradle build
                module_paths = []
                for include in includes:
                    # Convert Gradle path to file path
                    # 'services:api' -> 'services/api'
                    module_rel_path = include.replace(":", "/")
                    module_path = gradle_dir / module_rel_path
                    if module_path.exists():
                        module_paths.append(module_path)

                return JavaProjectConfig(gradle_dir, "gradle", is_multi_module=True, modules=module_paths)
            else:
                return JavaProjectConfig(gradle_dir, "gradle", False)

        except Exception as e:
            logger.warning(f"Failed to parse {settings_file}: {e}")
            return JavaProjectConfig(gradle_dir, "gradle", False)

    def _has_java_files(self, directory: Path) -> bool:
        """Check if directory contains any .java files."""
        try:
            next(directory.rglob("*.java"))
            return True
        except StopIteration:
            return False

    def _is_subpath(self, path: Path, parent: Path) -> bool:
        """Check if path is a subpath of parent."""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False


def scan_java_projects(repo_path: Path) -> List[JavaProjectConfig]:
    """
    Convenience function to scan for Java projects.

    Args:
        repo_path: Root directory of repository

    Returns:
        List of JavaProjectConfig objects
    """
    scanner = JavaConfigScanner(repo_path)
    return scanner.scan()
