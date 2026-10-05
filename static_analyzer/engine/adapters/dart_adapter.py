"""Dart and Flutter analysis using the SDK's language server."""

import shutil
import subprocess
from pathlib import Path

import yaml

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.source_inspector import SourceInspector
from tool_registry.paths import dart_binary


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

    @property
    def resolves_value_references(self) -> bool:
        return True

    def get_lsp_command(self, project_root: Path) -> list[str]:
        binary = dart_binary()
        if not binary:
            raise RuntimeError("Dart SDK not found. Install Dart or Flutter and add its bin directory to PATH.")
        return [binary, "language-server"]

    def record_document_symbols(self, file_path: Path, symbols: list[dict], project_root: Path) -> None:
        """Preserve executable accessors and give setters identities distinct from their getters."""
        inspector = SourceInspector()
        pending = [(symbol, False) for symbol in symbols]
        while pending:
            symbol, in_class = pending.pop()
            position = symbol["selectionRange"]["start"]
            setter = inspector.declares_setter(file_path, position["line"], position["character"])
            getter = inspector.declares_getter(file_path, position["line"], position["character"])
            if getter or setter:
                symbol["kind"] = NodeType.METHOD if in_class else NodeType.FUNCTION
                if setter:
                    symbol["name"] += "(set)"
            pending.extend((child, self.is_class_like(symbol["kind"])) for child in symbol.get("children", []))

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
            command = binary
            if uses_flutter:
                flutter = shutil.which("flutter")
                if not flutter:
                    raise RuntimeError(f"Flutter SDK required by {manifest}. Install Flutter and add its bin to PATH.")
                command = flutter
            try:
                subprocess.run(
                    [command, "pub", "get"],
                    cwd=manifest.parent,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=300,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeError(f"Dependency resolution failed for {manifest}; run '{command} pub get'.") from error
