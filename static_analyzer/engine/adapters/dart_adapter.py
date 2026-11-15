"""Dart and Flutter analysis using the SDK's language server."""

import subprocess
from pathlib import Path

import yaml

from infra.repo_utils.ignore import RepoIgnoreManager
from infra.tool_registry.installers import archive_launcher_path
from infra.tool_registry.paths import get_servers_dir, native_binary_ok
from infra.tool_registry.registry import TOOL_REGISTRY
from infra.vscode_constants import VSCODE_CONFIG
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap


class DartAdapter(LanguageAdapter):
    @property
    def language(self) -> str:
        return "Dart"

    @property
    def language_enum(self) -> Language:
        return Language.DART

    @property
    def lsp_command(self) -> list[str]:
        return ["dart", "language-server"]

    @property
    def fail_on_empty_symbols(self) -> bool:
        return True

    def get_lsp_command(self, project_root: Path) -> list[str]:
        dependency = next(dep for dep in TOOL_REGISTRY if dep.key == "dart")
        configured = Path(VSCODE_CONFIG["lsp_servers"]["dart"]["command"][0])
        binary = configured if configured.is_absolute() else archive_launcher_path(get_servers_dir(), dependency)
        assert binary is not None
        if not native_binary_ok(binary):
            raise RuntimeError("Managed Dart SDK not found. Run codeboarding-setup.")
        return [str(binary), "language-server"]

    def declared_apart(self, a: SymbolInfo, b: SymbolInfo) -> bool:
        """Named and unnamed constructors are siblings despite their qualified-name prefixes."""
        return a.kind == b.kind == NodeType.CONSTRUCTOR and a.definition_location != b.definition_location

    def refine_document_symbols(
        self,
        file_path: Path,
        symbols: list[dict],
        inspector: SourceInspector,
        client: LSPClient,
        diagnostics: FileDiagnosticsMap,
    ) -> list[dict]:
        pending = list(symbols)
        while pending:
            symbol = pending.pop()
            pending.extend(symbol.get("children", []))
            if symbol["kind"] == NodeType.PROPERTY:
                symbol["kind"] = NodeType.METHOD
                position = symbol["selectionRange"]["start"]
                if inspector.declares_setter(file_path, position["line"], position["character"]):
                    # Getter and setter are separate declarations, despite the server's identical names.
                    symbol["name"] += "="
        return symbols

    def prepare_project(self, project_root: Path) -> None:
        binary = self.get_lsp_command(project_root)[0]
        ignore_manager = RepoIgnoreManager(project_root)
        for manifest in self._walk(project_root, ignore_manager):
            if manifest.name != "pubspec.yaml":
                continue
            config = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            uses_flutter = any(
                isinstance(value, dict) and value.get("sdk") == "flutter"
                for section in ("dependencies", "dev_dependencies")
                for value in (config.get(section) or {}).values()
            )
            command = [binary]
            if uses_flutter:
                snapshot = Path(binary).parents[2] / "flutter_tools.snapshot"
                if not snapshot.is_file():
                    raise RuntimeError("Managed Flutter SDK is incomplete. Run codeboarding-setup.")
                command.extend([str(snapshot), "--suppress-analytics"])
            try:
                subprocess.run(
                    [*command, "pub", "get"],
                    cwd=manifest.parent,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=300,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeError(
                    f"Dependency resolution failed for {manifest}; run '{' '.join(command)} pub get'."
                ) from error
