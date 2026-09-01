"""F# language adapter using FsAutoComplete."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.config import CLASS_TYPES, Language, NodeType
from static_analyzer.dotnet_sdk import DotnetSdkError, resolve_dotnet_sdk
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from tool_registry import (
    TOOL_REGISTRY,
    ToolKind,
    acquire_lock,
    get_servers_dir,
    install_package_manager_tools,
    package_manager_tool_is_current,
    package_manager_tool_path,
)

logger = logging.getLogger(__name__)

# Symbol kinds that carry a declared F# scope rather than a code container.
_SCOPE_KINDS = (NodeType.MODULE, NodeType.NAMESPACE)
_PROJECT_GLOBS: tuple[str, ...] = ("*.sln", "*.slnx", "*.fsproj")


def _bound(entry: dict, edge: str) -> tuple[int, int]:
    """Return the (line, character) position of one edge of a symbol's range."""
    point = entry.get("range", {}).get(edge, {})
    return point.get("line", 0), point.get("character", 0)


def _encloses(outer: dict, inner: dict) -> bool:
    """Whether *outer* spans a strictly wider range than *inner*."""
    outer_span = (_bound(outer, "start"), _bound(outer, "end"))
    inner_span = (_bound(inner, "start"), _bound(inner, "end"))
    if outer_span == inner_span:
        return False
    return outer_span[0] <= inner_span[0] and inner_span[1] <= outer_span[1]


def _name_let_bindings(entries: list[dict], inside_type: bool) -> None:
    """Rewrite module-level ``let`` bindings, reported as fields, into functions.

    A ``let`` inside a type really is a field or member and stays one.
    """
    for entry in entries:
        kind = entry.get("kind")
        if kind == NodeType.FIELD and not inside_type:
            entry["kind"] = NodeType.FUNCTION
        _name_let_bindings(entry.get("children", []), inside_type or kind in CLASS_TYPES)


class FSharpAdapter(LanguageAdapter):
    """Static-analysis adapter for F# projects backed by FsAutoComplete."""

    @property
    def language(self) -> str:
        return "FSharp"

    @property
    def language_enum(self) -> Language:
        return Language.FSHARP

    @property
    def lsp_command(self) -> list[str]:
        return ["fsautocomplete"]

    @property
    def language_id(self) -> str:
        return "fsharp"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Resolve the .NET SDK and ensure the managed FsAutoComplete install is current."""
        try:
            resolution = resolve_dotnet_sdk(project_root)
        except DotnetSdkError as exc:
            raise RuntimeError(str(exc)) from exc

        self._ensure_fsautocomplete_installed(project_root, resolution.dotnet_path, resolution.env)
        return super().get_lsp_command(project_root)

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build module-based qualified names for F#.

        Why F# cannot reuse the C# path-based scheme: a file declares its own
        ``namespace`` or ``module`` path, and that path need not match the
        directory layout — ShArc's ``Source/Layer2.Relief.Common/String.fs``
        declares ``module Layer2.Relief.Common.String``. The declared scope is
        therefore authoritative, and the path only serves files that declare
        none.

        FsAutoComplete reports a file's leading module or namespace with its
        full dotted path, but a module nested inside it only by its own short
        name, so an enclosing scope still has to be prefixed.
        """
        scope = ".".join(name for name, kind in parent_chain if kind in _SCOPE_KINDS)

        if symbol_kind in _SCOPE_KINDS:
            return f"{scope}.{symbol_name}" if scope else symbol_name

        if not scope:
            rel = file_path.relative_to(project_root)
            scope = ".".join(rel.with_suffix("").parts)

        containers = [name for name, kind in parent_chain if kind not in (NodeType.FILE, *_SCOPE_KINDS)]
        parts = [scope, *containers, symbol_name]
        return ".".join(part for part in parts if part)

    def normalize_symbols(self, symbols: list[dict]) -> list[dict]:
        """Rebuild the nesting FsAutoComplete omits and name F# functions as such.

        Why: it answers ``documentSymbol`` with a flat list carrying no
        ``children``, so every symbol would register at file level and lose its
        module path. The ranges still nest, and that is enough to rebuild the
        tree. It also reports every ``let`` binding as a field, which nothing
        downstream treats as callable — see ``_name_let_bindings``.
        """
        ordered = sorted(
            symbols,
            key=lambda entry: (_bound(entry, "start"), tuple(-value for value in _bound(entry, "end"))),
        )

        roots: list[dict] = []
        open_scopes: list[dict] = []
        for entry in ordered:
            node = dict(entry)
            node["children"] = []
            while open_scopes and not _encloses(open_scopes[-1], node):
                open_scopes.pop()
            if open_scopes:
                open_scopes[-1]["children"].append(node)
            else:
                roots.append(node)
            open_scopes.append(node)

        _name_let_bindings(roots, inside_type=False)
        return roots

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Make FsAutoComplete load the workspace on its own.

        Why: unlike csharp-ls it loads no project until asked, and every
        ``documentSymbol`` then fails with "Couldn't find <file> in
        LoadedProjects". The alternative is driving its non-standard
        ``fsharp/workspacePeek`` and ``fsharp/workspaceLoad`` requests.
        """
        return {"AutomaticWorkspaceInit": True}

    def get_workspace_settings(self) -> dict | None:
        """Repeat the workspace-init opt-in through the configuration channel,
        which is the route Ionide itself uses."""
        return {"FSharp": {"automaticWorkspaceInit": True}}

    def extract_package(self, qualified_name: str) -> str:
        """Extract the module path as all-but-last-two dot-separated components.

        For ``Layer2.Relief.Common.String.trim`` the package is
        ``Layer2.Relief.Common``.
        """
        return self._extract_deep_package(qualified_name)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Use all directory prefixes as packages, matching the deep module paths."""
        return self._get_hierarchical_packages(source_files, project_root)

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include modules: in F# a module, not a class, is the usual unit of code."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.MODULE

    @property
    def probe_before_open(self) -> bool:
        """FsAutoComplete loads every project of the workspace through Ionide.ProjInfo,
        so it must finish that load before bulk didOpen notifications arrive."""
        return True

    @property
    def interleave_did_open_with_symbols(self) -> bool:
        """Pair each didOpen with its own documentSymbol request.

        Why: FsAutoComplete type-checks a file lazily and answers
        documentSymbol only from what it has already checked, so a bulk open
        followed by bulk queries returns a fraction of the symbols — one
        module per file instead of its contents. Opening a file and querying
        it immediately gives each type-check a response barrier.
        """
        return True

    @property
    def workspace_owns_documents(self) -> bool:
        """FsAutoComplete answers position queries from the loaded projects, opened or not."""
        return True

    def get_lsp_default_timeout(self) -> int:
        """MSBuild project loading for large .NET solutions is slow."""
        return 120

    def get_probe_timeout_minimum(self) -> int:
        """Loading and type-checking every project of a large solution can exceed 5 minutes."""
        return 600

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """FsAutoComplete type-checks after the project load and publishes diagnostics
        asynchronously, with no enclosing readiness signal, so debounce the stream."""
        client.wait_for_diagnostics_quiesce(idle_seconds=2.0, max_wait=30.0)

    @property
    def fail_on_empty_symbols(self) -> bool:
        return True

    def prepare_project(self, project_root: Path) -> None:
        """Run ``dotnet restore`` so FsAutoComplete can resolve framework references.

        Why: the server loads projects through MSBuild, which needs
        ``obj/project.assets.json`` to find the reference assemblies. Without
        it every file type-checks against nothing and the analysis yields no
        symbols. Restore is idempotent and only writes under ``obj/``.
        """
        target = next((match for glob in _PROJECT_GLOBS for match in project_root.glob(glob)), None)
        if target is None:
            logger.debug("No solution/project file found at %s; skipping restore", project_root)
            return

        try:
            resolution = resolve_dotnet_sdk(project_root)
        except DotnetSdkError as exc:
            raise RuntimeError(str(exc)) from exc

        env = os.environ.copy()
        env.update(resolution.env)
        try:
            result = subprocess.run(
                [resolution.dotnet_path, "restore", str(target.name), "--nologo", "--verbosity", "minimal"],
                cwd=str(project_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                logger.warning(
                    "dotnet restore failed for %s (exit %d): %s",
                    target.name,
                    result.returncode,
                    (result.stderr or result.stdout)[-500:],
                )
            else:
                logger.info("dotnet restore completed for %s", target.name)
        except subprocess.TimeoutExpired:
            logger.warning("dotnet restore timed out after 600s for %s", target.name)
        except OSError as exc:
            logger.warning("dotnet restore could not be invoked: %s", exc)

    def get_lsp_env(self, project_root: Path | None = None) -> dict[str, str]:
        """Return the .NET environment FsAutoComplete needs to load projects."""
        if project_root is None:
            return {}
        try:
            return resolve_dotnet_sdk(project_root).env
        except DotnetSdkError as exc:
            raise RuntimeError(str(exc)) from exc

    def _ensure_fsautocomplete_installed(
        self, project_root: Path, dotnet_path: str, dotnet_env: dict[str, str]
    ) -> None:
        dep = next((d for d in TOOL_REGISTRY if d.key == "fsharp" and d.kind is ToolKind.PACKAGE_MANAGER), None)
        if dep is None:
            return

        servers_dir = get_servers_dir()
        if package_manager_tool_path(servers_dir, dep) is not None and package_manager_tool_is_current(
            servers_dir, dep
        ):
            return

        servers_dir.mkdir(parents=True, exist_ok=True)
        lock_path = servers_dir / ".download.lock"
        env = os.environ.copy()
        env.update(dotnet_env)
        with open(lock_path, "w") as lock_fd:
            acquire_lock(lock_fd)
            if package_manager_tool_is_current(servers_dir, dep):
                return
            install_package_manager_tools(servers_dir, [dep], manager_overrides={"dotnet": dotnet_path}, env=env)
        if not package_manager_tool_is_current(servers_dir, dep):
            raise RuntimeError(
                "fsautocomplete could not be installed. CodeBoarding needs FsAutoComplete and a .NET SDK "
                "to analyze F# projects."
            )
