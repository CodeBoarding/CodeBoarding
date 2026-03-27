"""Nextflow language adapter using the Nextflow Language Server."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.java_utils import get_java_version
from utils import get_config

logger = logging.getLogger(__name__)

# Well-known Nextflow config file names that should always be included
_NEXTFLOW_CONFIG_NAMES = {"nextflow.config"}
# Directory names commonly used for Nextflow config fragments
_NEXTFLOW_CONFIG_DIRS = {"conf", "config", "configs"}


class NextflowAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "Nextflow"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".nf", ".config")

    @property
    def lsp_command(self) -> list[str]:
        return ["java", "-jar", "language-server-all.jar"]

    @property
    def language_id(self) -> str:
        return "nextflow"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Build the Nextflow LS launch command.

        Resolves the JAR path from tool_registry config, then builds
        a ``java -jar /path/to/language-server-all.jar`` command.
        """
        jar_path = self._find_jar_path()
        if jar_path is None:
            raise RuntimeError(
                "Nextflow Language Server JAR not found. "
                "Run `codeboarding-setup` or download language-server-all.jar "
                "from https://github.com/nextflow-io/language-server/releases"
            )

        java_cmd = self._find_java()
        return [str(java_cmd), "-jar", str(jar_path)]

    @staticmethod
    def _find_jar_path() -> Path | None:
        """Locate the Nextflow LS JAR from tool config or well-known paths."""
        lsp_servers = get_config("lsp_servers")
        nf_entry = lsp_servers.get("nextflow", {})
        if jar_str := nf_entry.get("jar_path"):
            jar = Path(jar_str)
            if jar.is_file():
                return jar

        # Fallback to well-known location
        well_known = Path.home() / ".codeboarding" / "servers" / "bin" / "nextflow-lsp" / "language-server-all.jar"
        if well_known.is_file():
            return well_known

        return None

    @staticmethod
    def _find_java() -> str:
        """Find a Java 17+ executable.

        Checks JAVA_HOME, then system PATH. Validates version >= 17
        since the Nextflow Language Server requires it.
        """
        candidates: list[str] = []

        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            java_bin = Path(java_home) / "bin" / "java"
            if java_bin.exists():
                candidates.append(str(java_bin))

        system_java = shutil.which("java")
        if system_java:
            candidates.append(system_java)

        for java_cmd in candidates:
            version = get_java_version(java_cmd)
            if version >= 17:
                return java_cmd

        if candidates:
            logger.warning("Found Java but version < 17. Nextflow LS requires Java 17+.")
            return candidates[0]

        return "java"

    def discover_source_files(self, project_root: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
        """Discover Nextflow source files, filtering .config intelligently.

        All .nf files are included. For .config files, only include:
        - Files named nextflow.config
        - .config files in directories that also contain .nf files
        - .config files in well-known config directories (conf/, config/, configs/)
        """
        project_root = project_root.resolve()
        nf_files: list[Path] = []
        config_files: list[Path] = []
        dirs_with_nf: set[Path] = set()

        for path in self._walk(project_root, ignore_manager):
            if path.suffix == ".nf":
                nf_files.append(path)
                dirs_with_nf.add(path.parent)
            elif path.suffix == ".config":
                config_files.append(path)

        # Filter .config files
        filtered_configs: list[Path] = []
        for cf in config_files:
            if cf.name in _NEXTFLOW_CONFIG_NAMES:
                filtered_configs.append(cf)
            elif cf.parent in dirs_with_nf:
                filtered_configs.append(cf)
            elif cf.parent.name in _NEXTFLOW_CONFIG_DIRS:
                filtered_configs.append(cf)

        all_files = nf_files + filtered_configs
        all_files.sort()

        if all_files:
            logger.info(
                "Found %d Nextflow files in %s (%d .nf, %d .config)",
                len(all_files),
                project_root,
                len(nf_files),
                len(filtered_configs),
            )
        return all_files
