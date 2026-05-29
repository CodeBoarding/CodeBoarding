"""Nextflow adapter backed by the official Nextflow Language Server."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.java_utils import get_java_version
from utils import get_config

logger = logging.getLogger(__name__)

_CONFIG_FILE_NAMES = {"nextflow.config"}
_CONFIG_DIR_NAMES = {"conf", "config", "configs"}
_SERVER_JAR_NAME = "language-server-all.jar"


class NextflowAdapter(LanguageAdapter):
    @property
    def language(self) -> str:
        return "Nextflow"

    @property
    def language_enum(self) -> Language:
        return Language.NEXTFLOW

    @property
    def lsp_command(self) -> list[str]:
        return ["java", "-jar", _SERVER_JAR_NAME]

    @property
    def language_id(self) -> str:
        return "nextflow"

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        return self._settings()

    def get_workspace_settings(self) -> dict | None:
        return self._settings()

    @property
    def indexing_retries(self) -> int:
        return 15

    @property
    def indexing_retry_delay(self) -> float:
        return 2.0

    def get_lsp_command(self, project_root: Path) -> list[str]:
        jar_path = self._find_jar_path()
        if jar_path is None:
            raise RuntimeError(
                "Nextflow Language Server JAR not found. "
                "Run `codeboarding-setup` or install language-server-all.jar under "
                "~/.codeboarding/servers/bin/nextflow-lsp/."
            )
        return [self._find_java(), "-jar", str(jar_path)]

    def discover_source_files(self, project_root: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
        """Include .nf sources plus Nextflow-specific .config files."""
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

        filtered_configs = [
            path
            for path in config_files
            if path.name in _CONFIG_FILE_NAMES or path.parent in dirs_with_nf or path.parent.name in _CONFIG_DIR_NAMES
        ]

        nf_files.sort()
        filtered_configs.sort()
        files = nf_files + filtered_configs
        if files:
            logger.info(
                "Found %d Nextflow files in %s (%d .nf, %d .config)",
                len(files),
                project_root,
                len(nf_files),
                len(filtered_configs),
            )
        return files

    @staticmethod
    def _settings() -> dict:
        return {
            "nextflow": {
                "debug": False,
                "files": {"exclude": [".git", ".nf-test", "work"]},
            }
        }

    @staticmethod
    def _find_jar_path() -> Path | None:
        lsp_servers = get_config("lsp_servers")
        jar_str = lsp_servers.get("nextflow", {}).get("jar_path")
        if jar_str:
            jar_path = Path(jar_str)
            if jar_path.is_file():
                return jar_path

        well_known = Path.home() / ".codeboarding" / "servers" / "bin" / "nextflow-lsp" / _SERVER_JAR_NAME
        return well_known if well_known.is_file() else None

    @staticmethod
    def _find_java() -> str:
        candidates: list[str] = []
        if java_home := os.environ.get("JAVA_HOME"):
            java_bin = Path(java_home) / "bin" / "java"
            if java_bin.exists():
                candidates.append(str(java_bin))

        if system_java := shutil.which("java"):
            candidates.append(system_java)

        for java_cmd in candidates:
            if get_java_version(java_cmd) >= 17:
                return java_cmd

        raise RuntimeError("Java 17+ is required to run the Nextflow Language Server.")
