"""TypeScript and JavaScript language adapter using typescript-language-server."""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
from pathlib import Path

from infra.repo_utils.ignore import RepoIgnoreManager
from static_analyzer.config import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.exceptions import WorkspaceSetupError
from static_analyzer.typescript_workspace import PLUGIN_LOCATION, PLUGIN_NAME, workspace_members

logger = logging.getLogger(__name__)


class TypeScriptAdapter(LanguageAdapter):

    @property
    def results_language(self) -> Language:
        return Language.TYPESCRIPT

    @property
    def language(self) -> str:
        return "TypeScript"

    @property
    def language_enum(self) -> Language:
        return Language.TYPESCRIPT

    @property
    def lsp_command(self) -> list[str]:
        return ["typescript-language-server", "--stdio"]

    @property
    def language_id(self) -> str:
        return "typescript"

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        # Why: by default a syntax-only server answers while the project still loads, and a definition
        # that needs an inferred type -- ``.then(t => t.serialize())`` -- comes back empty on a cold start.
        # Why the plugin: a call into a sibling workspace package of an uninstalled, unbuilt checkout resolves nowhere;
        # the plugin resolves it to the member's sources (see ``typescript_workspace``).
        return {
            "tsserver": {"useSyntaxServer": "never"},
            "plugins": [{"name": PLUGIN_NAME, "location": str(PLUGIN_LOCATION)}],
        }

    def get_lsp_commands(self, project_root: Path) -> list[dict]:
        """Hands the workspace members to the plugin; tsserver gives the configuration to each project it loads."""
        members = workspace_members(project_root)
        if not members:
            return []
        if not (PLUGIN_LOCATION / "node_modules" / PLUGIN_NAME / "index.js").is_file():
            raise WorkspaceSetupError(f"{PLUGIN_NAME} is missing from this install; reinstall CodeBoarding")
        logger.info(f"tsserver resolves {len(members)} workspace members of {project_root} to their sources")
        configuration = {"root": str(project_root), "members": {name: str(path) for name, path in members.items()}}
        return [{"command": "_typescript.configurePlugin", "arguments": [PLUGIN_NAME, configuration]}]

    def rebuilds_for(self, changed_files: Collection[Path], project_root: Path) -> bool:
        """Whether a change alters how imports resolve: a tsconfig, a workspace declaration, or a workspace's manifest."""
        names = {path.name for path in changed_files}
        if "pnpm-workspace.yaml" in names or any(re.fullmatch(r"[tj]sconfig.*\.json", name) for name in names):
            return True
        return "package.json" in names and bool(workspace_members(project_root))

    def get_lsp_heap_options(self, heap_bytes: int) -> dict:
        # Why: at Node's default heap tsserver runs out of memory on a large tsconfig project and answers nothing.
        return {"maxTsServerMemory": heap_bytes // 1024**2}

    def extract_package(self, qualified_name: str) -> str:
        return self._extract_deep_package(qualified_name)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        return self._get_hierarchical_packages(source_files, project_root)


class JavaScriptAdapter(TypeScriptAdapter):

    @property
    def language(self) -> str:
        return "JavaScript"

    @property
    def language_enum(self) -> Language:
        return Language.JAVASCRIPT

    @property
    def language_id(self) -> str:
        return "javascript"

    @property
    def config_key(self) -> str:
        return "typescript"
