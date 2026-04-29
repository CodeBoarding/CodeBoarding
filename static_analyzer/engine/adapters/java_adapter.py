"""Java language adapter using JDTLS."""

from __future__ import annotations

import logging
import os
import platform
import tempfile
from pathlib import Path

from static_analyzer.constants import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_constants import (
    CALLABLE_KINDS,
    CLASS_LIKE_KINDS,
    EdgeStrategy,
)
from static_analyzer.engine.utils import total_ram_gb
from static_analyzer.java_utils import create_jdtls_command, find_java_21_or_later
from utils import get_config

logger = logging.getLogger(__name__)


class JavaAdapter(LanguageAdapter):

    @property
    def wait_for_workspace_ready(self) -> bool:
        """JDTLS finishes project import asynchronously and only signals
        readiness via ``language/status`` (``ProjectStatus OK``); cross-file
        queries return empty until then.
        """
        return True

    @property
    def language(self) -> str:
        return "Java"

    @property
    def language_enum(self) -> Language:
        return Language.JAVA

    @property
    def lsp_command(self) -> list[str]:
        return ["jdtls"]

    @property
    def language_id(self) -> str:
        return "java"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Build the full JDTLS launch command.

        JDTLS cannot be started with a simple binary name — it requires a Java
        executable, the Equinox launcher JAR, a config directory, and a unique
        workspace data directory.  We resolve the jdtls_root from the tool
        registry config and delegate to ``create_jdtls_command``.
        """
        jdtls_root = self._find_jdtls_root()
        if jdtls_root is None:
            raise RuntimeError(
                "JDTLS installation not found. Run `python install.py` or " "set the jdtls_root in the tool config."
            )

        java_home = find_java_21_or_later()
        if java_home is None:
            raise RuntimeError("Java 21+ required to run JDTLS. Please install JDK 21 or later.")

        workspace_dir = Path(tempfile.mkdtemp(prefix="jdtls-workspace-"))
        heap_size = self._calculate_heap_size(project_root)
        return create_jdtls_command(jdtls_root, workspace_dir, java_home, heap_size)

    @staticmethod
    def _find_jdtls_root() -> Path | None:
        """Locate the JDTLS installation directory."""
        # 1. Check tool registry config (set by install.py / resolve_config)
        lsp_servers = get_config("lsp_servers")
        java_entry = lsp_servers.get("java", {})
        if root_str := java_entry.get("jdtls_root"):
            root = Path(root_str)
            if root.is_dir() and (root / "plugins").is_dir():
                return root

        # 2. Fallback to well-known locations (platform-specific)
        well_known: list[Path] = [
            Path.home() / ".jdtls",
            Path.home() / ".codeboarding" / "servers" / "bin" / "jdtls",
        ]
        if platform.system() == "Windows":
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            if local_app_data:
                well_known.append(Path(local_app_data) / "jdtls")
            program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
            well_known.append(Path(program_files) / "jdtls")
        else:
            well_known.append(Path("/opt/jdtls"))

        for location in well_known:
            if location.is_dir() and (location / "plugins").is_dir():
                logger.info(f"Found JDTLS at {location}")
                return location

        return None

    @staticmethod
    def _calculate_heap_size(project_root: Path) -> str:
        """Calculate appropriate JVM heap size based on file count and available system memory.

        Caps the heap at ~50% of physical RAM to avoid starving the OS and other processes.
        """
        jvm_files = (
            list(project_root.rglob("*.java")) + list(project_root.rglob("*.kt")) + list(project_root.rglob("*.groovy"))
        )
        file_count = len(jvm_files)
        if file_count < 100:
            desired_gb = 1
        elif file_count < 500:
            desired_gb = 2
        elif file_count < 2000:
            desired_gb = 4
        elif file_count < 5000:
            desired_gb = 6
        else:
            desired_gb = 8

        # Cap at 50% of available physical memory.
        ram_gb = total_ram_gb()
        if ram_gb is not None:
            desired_gb = min(desired_gb, int(ram_gb * 0.5))

        return f"{max(1, desired_gb)}G"

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        rel = file_path.relative_to(project_root)
        module = ".".join(rel.with_suffix("").parts)
        clean_name = self._clean_symbol_name(symbol_name)

        if parent_chain:
            # In Java, the filename IS the top-level class name (e.g., Dog.java -> module "...Dog").
            # Skip the first parent if it matches the last module component to avoid
            # doubled names like "core.Dog.Dog.speak()" -> "core.Dog.speak()".
            module_last = module.rsplit(".", 1)[-1] if "." in module else module
            effective_parents = list(parent_chain)
            if effective_parents and self._clean_symbol_name(effective_parents[0][0]) == module_last:
                effective_parents = effective_parents[1:]

            if effective_parents:
                clean_parents = ".".join(self._clean_symbol_name(name) for name, _ in effective_parents)
                return f"{module}.{clean_parents}.{clean_name}"
        return f"{module}.{clean_name}"

    @staticmethod
    def _clean_symbol_name(name: str) -> str:
        """Clean a JDTLS symbol name by stripping generics from params."""
        stripped = name.strip()
        paren_idx = stripped.find("(")
        if paren_idx == -1:
            return stripped

        depth = 0
        close_idx = -1
        for i in range(paren_idx, len(stripped)):
            if stripped[i] == "(":
                depth += 1
            elif stripped[i] == ")":
                depth -= 1
                if depth == 0:
                    close_idx = i
                    break

        if close_idx == -1:
            return stripped

        method_name = stripped[:paren_idx]
        params_raw = stripped[paren_idx + 1 : close_idx]
        suffix = stripped[close_idx + 1 :].strip()

        if suffix.startswith("<"):
            suffix = ""

        params = []
        for param in JavaAdapter._split_params(params_raw):
            param = param.strip()
            if not param:
                continue
            params.append(JavaAdapter._strip_generics(param))

        result = f"{method_name}({', '.join(params)})"
        if suffix:
            result += " " + suffix
        return result

    @staticmethod
    def _strip_generics(param: str) -> str:
        """Strip generic type parameters: 'List<Animal>' -> 'List'."""
        result = []
        depth = 0
        for ch in param:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            elif depth == 0:
                result.append(ch)
        return "".join(result).strip()

    @staticmethod
    def _split_params(raw: str) -> list[str]:
        """Split parameter list respecting angle brackets."""
        params = []
        depth = 0
        current: list[str] = []
        for ch in raw:
            if ch == "<":
                depth += 1
                current.append(ch)
            elif ch == ">":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                params.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            params.append("".join(current))
        return params

    def extract_package(self, qualified_name: str) -> str:
        parts = qualified_name.split(".")

        java_idx = None
        for i, part in enumerate(parts):
            if part == "java" and i >= 1 and parts[i - 1] == "main":
                java_idx = i
                break

        if java_idx is None:
            try:
                java_idx = parts.index("java")
            except ValueError:
                return parts[0]

        after_root = parts[java_idx + 1 :]
        if not after_root:
            return parts[0]

        pkg_parts = []
        for part in after_root:
            clean = part.split("(")[0]
            if clean in ("package-info", "module-info"):
                break
            if clean and clean[0].isupper():
                break
            pkg_parts.append(part)

        if pkg_parts:
            return ".".join(pkg_parts)
        return after_root[0]

    def get_workspace_settings(self) -> dict | None:
        # JDTLS defaults these to "ignore"; raising to "warning" is required
        # for unused-code diagnostics.
        return {
            "java": {
                "settings": {
                    "org.eclipse.jdt.core.compiler.problem.unusedImport": "warning",
                    "org.eclipse.jdt.core.compiler.problem.unusedLocal": "warning",
                    "org.eclipse.jdt.core.compiler.problem.unusedPrivateMember": "warning",
                    "org.eclipse.jdt.core.compiler.problem.deadCode": "warning",
                }
            }
        }

    @property
    def edge_strategy(self) -> EdgeStrategy:
        """Use definition-based edges — JDTLS serializes references requests."""
        return EdgeStrategy.DEFINITIONS

    def should_track_for_edges(self, symbol_kind: int) -> bool:
        return symbol_kind in (CALLABLE_KINDS | CLASS_LIKE_KINDS | {NodeType.VARIABLE, NodeType.CONSTANT})

    def get_package_for_file(self, file_path: Path, project_root: Path) -> str:
        """Get Java package from file path by stripping src/main/java/ prefix."""
        qname = self.build_qualified_name(file_path, "", 0, [], project_root)
        return self.extract_package(qname)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        packages: set[str] = set()
        for f in source_files:
            packages.add(self.get_package_for_file(f, project_root))
        return packages
