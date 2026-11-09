"""Java language adapter using JDTLS."""

from __future__ import annotations

import logging
import os
import platform
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from static_analyzer.config import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import ErrorVerdict
from static_analyzer.engine.lsp_constants import (
    CALLABLE_KINDS,
    CLASS_LIKE_KINDS,
)
from static_analyzer.engine.utils import total_ram_gb
from static_analyzer.java_utils import create_jdtls_command, find_java_21_or_later
from infra.utils import CODEBOARDING_DIR_NAME, get_config

logger = logging.getLogger(__name__)

# An entry with a body, as its opening tag and the rest up to its own closing tag. Self-closing entries never match.
_CLASSPATH_ENTRY = re.compile(
    rb"(<classpathentry\b[^>]*[^/]>)((?:(?!<classpathentry\b).)*?</classpathentry>)", re.DOTALL
)
_INCLUSION_FILTER = re.compile(rb'\s+including="[^"]*"')

# The languages a JVM source root can be named for, in ``src/<source set>/<language>``.


class JavaAdapter(LanguageAdapter):

    def __init__(self) -> None:
        super().__init__()
        # What the import wrote, and what ``lift_import_filters`` left in its place.
        self._filtered_classpaths: dict[Path, tuple[bytes, bytes]] = {}

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
            Path.home() / CODEBOARDING_DIR_NAME / "servers" / "bin" / "jdtls",
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
        """Build ``<directories>.<declaring types>.<symbol>``, from the repository root.

        Why no file stem: Java requires it to equal the top-level type, so folding it in
        made that type a sibling of its own members and CONTAINS could not link them.
        """
        segments = [self._clean_symbol_name(name) for name, _ in parent_chain]
        segments.append(self._clean_symbol_name(symbol_name))
        return ".".join(part for part in (self.get_package_for_file(file_path, project_root), *segments) if part)

    def get_package_for_file(self, file_path: Path, project_root: Path) -> str:
        """The directory holding the file, from the repository root, spelled as it is on disk."""
        return ".".join(file_path.relative_to(project_root).parent.parts)

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

    def lift_import_filters(self, project_root: Path) -> bool:
        """Drop the inclusion filters the build import put on source folders.

        Why: m2e applies the ``<includes>`` of a project's last ``compile`` execution to the whole
        source folder, so one narrow recompile step leaves every other file outside the project and
        calls into it unresolved. Why a restart: m2e rewrites ``.classpath`` on any change it sees.
        Why only entries m2e marks ``maven.pomderived``: a hand-written filter is the project's own source set.
        """
        lifted = False
        for classpath in project_root.rglob(".classpath"):
            if not classpath.is_file():
                continue
            original = classpath.read_bytes()
            unfiltered = _CLASSPATH_ENTRY.sub(self._without_inclusion_filter, original)
            if unfiltered != original:
                imported, _ = self._filtered_classpaths.get(classpath, (original, b""))
                self._filtered_classpaths[classpath] = (imported, unfiltered)
                classpath.write_bytes(unfiltered)
                lifted = True
        return lifted

    @contextmanager
    def session(self) -> Iterator[None]:
        try:
            yield
        finally:
            self._restore_import_filters()

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
    def expands_constructors(self) -> bool:
        """JDTLS resolves ``new Dog()`` to the class, so the constructor needs adding."""
        return True

    @property
    def resolves_method_groups(self) -> bool:
        """Java hands a method on only as a method reference (``Dog::speak``), already a call site;
        a bare argument or a field read never names a callable, so asking JDTLS would be pure cost."""
        return False

    def should_track_for_edges(self, symbol_kind: int) -> bool:
        return symbol_kind in (CALLABLE_KINDS | CLASS_LIKE_KINDS | {NodeType.VARIABLE, NodeType.CONSTANT})

    def error_verdict(self, error: dict) -> ErrorVerdict:
        """A crash in JDT's code selection is final for that call site, and its edge is left out with a warning.

        Why: asking again crashes the same way, so one site the compiler cannot walk must not cost the whole run.
        The first frame past the JDK's own decides: any java.lang exception raised from JDT's compiler or code
        assist (the selection engine, its parser recovering from code it misreads, or the resolution it runs) is
        the compiler's, a function of the source alone. One raised in JDT LS's own code, a JVM error such as running
        out of memory, a timeout or a lost server is not this, and still fails the run.
        """
        data = str(error.get("data", ""))
        if re.search(
            r"Caused by: java\.lang\.\w+Exception\b.*\n(?:\s*at java\.base/.*\n)*"
            r"\s*at org\.eclipse\.jdt\.internal\.(?:compiler|codeassist)\.",
            data,
        ):
            logger.warning(f"JDTLS crashed resolving a call site, leaving its edge out: {data}")
            return ErrorVerdict.DECLINED
        return ErrorVerdict.FAILURE

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        packages: set[str] = set()
        for f in source_files:
            packages.add(self.get_package_for_file(f, project_root))
        return packages

    def _restore_import_filters(self) -> None:
        for classpath, (imported, lifted) in self._filtered_classpaths.items():
            try:
                # Anything else is an edit made since, which outranks what the import wrote.
                if classpath.is_file() and classpath.read_bytes() == lifted:
                    classpath.write_bytes(imported)
            except OSError:
                logger.exception(f"Could not put back {classpath}; it keeps its source folder filters lifted")
        self._filtered_classpaths.clear()

    @staticmethod
    def _without_inclusion_filter(entry: re.Match[bytes]) -> bytes:
        tag, body = entry.group(1), entry.group(2)
        if b'kind="src"' not in tag or b'name="maven.pomderived"' not in body:
            return entry.group()
        return _INCLUSION_FILTER.sub(b"", tag) + body
