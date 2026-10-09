"""Kotlin language adapter using JetBrains' kotlin-lsp."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import zipfile
from collections.abc import Sequence
from pathlib import Path

from static_analyzer.config import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import ErrorVerdict, LSPClient
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import KotlinDeclarations, SourceInspector
from static_analyzer.engine.utils import total_ram_gb
from static_analyzer.internal_references import parent_qualified_name, simple_name
from infra.tool_registry import user_data_dir

logger = logging.getLogger(__name__)

# The server imports the project's Gradle or Maven build after ``initialize`` has answered, and
# resolves nothing outside the open file until that import is done.
_IMPORT_TIMEOUT_SECONDS = 1800
# After the import, indexing runs in passes; analysis starts once none has been open for a few seconds.
_INDEXING_QUIET_SECONDS = 5
_INDEXING_TIMEOUT_SECONDS = 900
# Rounds of 50 definition requests.
_REQUEST_TIMEOUT_SECONDS = 120
_DRAIN_PROBE_ROUND_SECONDS = 60
# Heap for the server's JVM: a base for the IntelliJ platform plus the analysed sources.
_BASE_HEAP_GB = 4
_HEAP_GB_PER_SOURCE_MB = 1.5
_PACKAGE = re.compile(rb"^\s*package\s+([\w.`]+)", re.MULTILINE)
# Directories no Java source root is looked for in: build output, tool state and dependencies.
_NOT_SOURCES = {".git", ".gradle", ".idea", "build", "node_modules", "target"}
# The server's platform jars carry the Kotlin standard library it was built with.
_STDLIB_MODULE = "META-INF/kotlin-stdlib.kotlin_module"
_COMPANION = "Companion"


class KotlinAdapter(LanguageAdapter):

    def __init__(self) -> None:
        super().__init__()
        # Files declaring a top-level type named after the file, none of whose members shares a name
        # with the file's other top-level declarations, keyed by (project root, path). Why: the stem
        # folds into that type, which files those declarations beside the members.
        self._files_named_for_their_type: set[tuple[str, str]] = set()

    @property
    def language(self) -> str:
        return "Kotlin"

    @property
    def language_enum(self) -> Language:
        return Language.KOTLIN

    @property
    def lsp_command(self) -> list[str]:
        return ["intellij-server"]

    @property
    def language_id(self) -> str:
        return "kotlin"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Run the server over stdio, keeping its caches and indexes per project under the user's data directory."""
        return [str(self._launcher(project_root)), "--stdio", f"--system-path={_system_path(project_root)}"]

    def get_lsp_env(self, project_root: Path | None = None, source_files: Sequence[Path] = ()) -> dict[str, str]:
        """A heap sized to the sources. The launcher appends ``IJ_JAVA_OPTIONS`` after its own VM options, so it wins."""
        return {"IJ_JAVA_OPTIONS": f"-Xmx{_heap_size(source_files)}"}

    def get_lsp_default_timeout(self) -> int:
        return _REQUEST_TIMEOUT_SECONDS

    @property
    def wait_for_workspace_ready(self) -> bool:
        return True

    def validate_workspace_ready(self, client: LSPClient) -> None:
        """Wait for the build import. A failed import still leaves the sources analysable without libraries."""
        if not client.import_finished.wait(timeout=_IMPORT_TIMEOUT_SECONDS):
            raise RuntimeError(f"kotlin-lsp did not finish importing the project within {_IMPORT_TIMEOUT_SECONDS}s")
        if client.import_phase != "FINISHED":
            logger.warning(
                "kotlin-lsp could not import the build (%s); library types will not resolve", client.import_phase
            )
        if not client.wait_for_progress_quiet("indexing", _INDEXING_QUIET_SECONDS, _INDEXING_TIMEOUT_SECONDS):
            # Why: an incomplete index answers definitions with nothing, which would read as missing edges.
            raise RuntimeError(f"kotlin-lsp was still indexing after {_INDEXING_TIMEOUT_SECONDS}s")

    def workspace_folders(self, project_root: Path, source_files: Sequence[Path]) -> list[Path]:
        """A Kotlin Multiplatform project is read from its sources, without its build.

        Why: through the import of a multiplatform build the server resolves about half the calls
        it resolves from the bare sources.
        """
        if any("commonMain" in path.parts for path in source_files):
            return [self._sources_folder(project_root, source_files)]
        return []

    def fallback_workspace_folders(
        self, client: LSPClient, project_root: Path, source_files: Sequence[Path]
    ) -> list[Path]:
        """When no build could be imported (none found, or it failed), a workspace naming only the
        source roots, so calls still resolve across files; library types do not."""
        unimported = {"BLOCKED", "FAILED"} & set(client.import_folder_statuses)
        if not client.import_failed and client.import_phase != "FAILED" and not unimported:
            return []
        return [self._sources_folder(project_root, source_files)]

    def refine_document_symbols(self, file_path: Path, symbols: list[dict], inspector: SourceInspector) -> list[dict]:
        """Give objects, interfaces, enums and enum entries their kinds, and every callable its parameter types."""
        declarations = inspector.kotlin_declarations(file_path)
        return [self._refine(symbol, declarations, parent_kind=None) for symbol in symbols]

    def record_document_symbols(self, file_path: Path, symbols: list[dict], project_root: Path) -> None:
        key = (str(project_root), str(file_path))
        named = [sym for sym in symbols if self.is_class_like(sym.get("kind", 0)) and sym.get("name") == file_path.stem]
        members = {child.get("name") for child in named[0].get("children") or []} if len(named) == 1 else set()
        others = {sym.get("name") for sym in symbols if sym is not named[0]} if len(named) == 1 else set()
        if len(named) == 1 and not members & others:
            self._files_named_for_their_type.add(key)
        else:
            self._files_named_for_their_type.discard(key)

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build ``<directories>.<file stem>.<declaring declarations>.<symbol>``, from the repository root.

        Kotlin puts any number of top-level functions, properties and types in one file, so the
        stem is a segment of its own. A type named after its file takes the stem's place:
        ``Owner.kt``'s ``Owner`` is ``...owner.Owner``, not ``...owner.Owner.Owner``.
        """
        rel = file_path.relative_to(project_root)
        module = ".".join(rel.with_suffix("").parts)
        parents = [name for name, _ in parent_chain]
        if (str(project_root), str(file_path)) in self._files_named_for_their_type:
            if parents and parents[0] == rel.stem:
                parents = parents[1:]
            elif not parents and symbol_name == rel.stem:
                return module
        return ".".join((module, *parents, symbol_name))

    def get_package_for_file(self, file_path: Path, project_root: Path) -> str:
        """The directory holding the file, from the repository root, spelled as it is on disk."""
        return ".".join(file_path.relative_to(project_root).parent.parts)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        return {self.get_package_for_file(f, project_root) for f in source_files}

    def error_verdict(self, error: dict) -> ErrorVerdict:
        """An internal error is asked once more: the server answers while it is still indexing."""
        return ErrorVerdict.TRANSIENT

    @property
    def resolves_bases_by_definition(self) -> bool:
        """The server offers no type hierarchy, so each base a header names is resolved by definition."""
        return True

    @property
    def drain_probe_round_seconds(self) -> int | None:
        """kotlin-lsp can leave a request that arrives during a large didOpen burst unanswered."""
        return _DRAIN_PROBE_ROUND_SECONDS

    @property
    def constructor_calls_resolve_to_class(self) -> bool:
        return True

    def constructed_class(self, symbol: SymbolInfo) -> str | None:
        """A companion's ``invoke`` too: a call that names a class but is answered with its companion runs it."""
        if self.is_callable(symbol.kind) and simple_name(symbol.qualified_name) == "invoke":
            companion = parent_qualified_name(symbol.qualified_name.split("(", 1)[0])
            if simple_name(companion) == _COMPANION:
                return companion
        return super().constructed_class(symbol)

    def constructs(self, target: SymbolInfo, callee: str) -> bool:
        """A call naming the class, or a delegation with ``this`` / ``super``; for a companion, a call
        naming its class."""
        if target.name == _COMPANION and callee == simple_name(parent_qualified_name(target.qualified_name)):
            return True
        return callee in (target.name, "this", "super")

    def declared_apart(self, a: SymbolInfo, b: SymbolInfo) -> bool:
        """Two declarations of one file, the deeper-named one written outside the other and not naming
        it among its declaring parents.

        Why: a type declared in a file of its own name is named after the file, so the file's other
        top-level declarations read as its members.
        """
        if str(a.file_path) != str(b.file_path):
            return False
        outer, inner = (a, b) if len(a.qualified_name) < len(b.qualified_name) else (b, a)
        declaring = {name for name, _ in inner.parent_chain}
        return outer.name not in declaring and not (_encloses(a, b) or _encloses(b, a))

    @property
    def resolves_method_groups(self) -> bool:
        """Kotlin hands a function on only as a callable reference (``::speak``), already a call site."""
        return False

    def _launcher(self, project_root: Path) -> Path:
        launcher = shutil.which(super().get_lsp_command(project_root)[0])
        if launcher is None:
            raise RuntimeError("kotlin-lsp is not installed; run codeboarding-setup.")
        return Path(launcher)

    def _sources_folder(self, project_root: Path, source_files: Sequence[Path]) -> Path:
        """A folder holding a ``workspace.json`` of the source roots and the standard library."""
        folder = _system_path(project_root).parent / f"{_system_path(project_root).name}-sources"
        folder.mkdir(parents=True, exist_ok=True)
        stdlib = _copy_stdlib(self._launcher(project_root).resolve().parents[1], folder / "kotlin-stdlib.jar")
        # The repository's Java sources go in beside the Kotlin, for the Java types Kotlin code uses.
        sources = [*source_files, *_java_sources(project_root)]
        (folder / "workspace.json").write_text(json.dumps(_sources_workspace(project_root, sources, stdlib)))
        return folder

    def _refine(self, symbol: dict, declarations: KotlinDeclarations, parent_kind: int | None) -> dict:
        selection = symbol.get("selectionRange", symbol.get("range", {})).get("start", {})
        position = (selection.get("line", -1), selection.get("character", -1))
        refined = dict(symbol)
        kind = refined.get("kind", 0)
        if kind == NodeType.OBJECT:
            kind = refined["kind"] = int(NodeType.CLASS)
        # A class's ``const val`` and constructor ``val`` come as Constant / Variable, which the engine
        # reads as locals once nested; in Kotlin they are properties.
        if (
            kind in (NodeType.VARIABLE, NodeType.CONSTANT)
            and parent_kind is not None
            and self.is_class_like(parent_kind)
        ):
            kind = refined["kind"] = int(NodeType.PROPERTY)
        if kind in (NodeType.CLASS, NodeType.ENUM_MEMBER) and position in declarations.kinds:
            refined["kind"] = int(declarations.kinds[position])
        if self.is_callable(kind) and position in declarations.signatures:
            refined["name"] = f"{refined.get('name', '')}{declarations.signatures[position]}"
        refined["children"] = [
            self._refine(child, declarations, parent_kind=refined["kind"]) for child in symbol.get("children") or []
        ]
        return refined


def _system_path(project_root: Path) -> Path:
    """Where the server keeps one project's caches and indexes, so a later run starts warm."""
    digest = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:16]
    return user_data_dir() / "kotlin-lsp" / f"{project_root.name}-{digest}"


def _java_sources(project_root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, subdirectories, files in os.walk(project_root):
        subdirectories[:] = [name for name in subdirectories if name not in _NOT_SOURCES]
        found.extend(Path(directory) / name for name in files if name.endswith(".java"))
    return found


def _sources_workspace(project_root: Path, source_files: Sequence[Path], stdlib: Path | None) -> dict:
    """A kotlin-lsp ``workspace.json`` with one module whose source roots are where the files' packages
    start, depending on the standard library when there is one."""
    roots: set[Path] = set()
    for path in source_files:
        match = _PACKAGE.search(path.read_bytes()[:8192])
        segments = match.group(1).decode(errors="replace").replace("`", "").split(".") if match else []
        directory = path.parent
        if segments and list(directory.parts[-len(segments) :]) == segments:
            directory = Path(*directory.parts[: -len(segments)])
        roots.add(directory)
    outermost = sorted(
        root for root in roots if not any(other != root and root.is_relative_to(other) for other in roots)
    )
    dependencies: list[dict] = [{"type": "inheritedSdk"}, {"type": "moduleSource"}]
    libraries = []
    if stdlib is not None:
        dependencies.append({"type": "library", "name": "kotlin-stdlib", "scope": "compile"})
        libraries.append({"name": "kotlin-stdlib", "type": "java-imported", "roots": [{"path": str(stdlib)}]})
    return {
        "libraries": libraries,
        "modules": [
            {
                "name": project_root.name,
                "dependencies": dependencies,
                "contentRoots": [
                    {
                        "path": str(project_root),
                        "sourceRoots": [{"path": str(root), "type": "java-source"} for root in outermost],
                    }
                ],
            }
        ],
    }


def _copy_stdlib(install_root: Path, target: Path) -> Path | None:
    """The standard library, copied out of the server's platform jars.

    Why: without it no lambda parameter has a type, so a workspace of bare sources resolves nothing
    called on ``it`` in ``items.forEach { it.save() }``.
    """
    for jar in sorted((install_root / "lib").glob("*.jar")):
        try:
            with zipfile.ZipFile(jar) as source:
                names = source.namelist()
                if _STDLIB_MODULE not in names:
                    continue
                partial = target.with_name(f"{target.name}.partial")
                with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as copy:
                    for name in names:
                        if name.startswith(("kotlin/", "META-INF/kotlin-stdlib")):
                            copy.writestr(name, source.read(name))
        except (OSError, zipfile.BadZipFile):
            logger.warning("Could not read the Kotlin standard library from %s", jar, exc_info=True)
            return None
        partial.replace(target)
        return target
    logger.warning("No Kotlin standard library under %s; calls on lambda parameters will not resolve", install_root)
    return None


def _heap_size(source_files: Sequence[Path]) -> str:
    """Heap for the server, from the size of the sources it analyses, within half the machine's memory."""
    source_mb = sum(path.stat().st_size for path in source_files) / 1_000_000
    desired_gb = math.ceil(_BASE_HEAP_GB + _HEAP_GB_PER_SOURCE_MB * source_mb)
    ram_gb = total_ram_gb()
    if ram_gb is not None:
        desired_gb = min(desired_gb, int(ram_gb * 0.5))
    return f"{max(2, desired_gb)}G"


def _encloses(outer: SymbolInfo, inner: SymbolInfo) -> bool:
    return (outer.start_line, outer.start_char) <= (inner.start_line, inner.start_char) and (
        outer.end_line,
        outer.end_char,
    ) >= (inner.end_line, inner.end_char)
