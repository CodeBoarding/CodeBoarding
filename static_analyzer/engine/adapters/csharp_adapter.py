"""C# language adapter using csharp-ls (Roslyn-based)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.config import Language, NodeType
from static_analyzer.dotnet_sdk import DotnetSdkError, resolve_dotnet_sdk, system_dotnet_env
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import EdgeStrategy
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

_MULTI_TARGET_PROJECT_THRESHOLD = 25
_SINGLE_TARGET_FRAMEWORK_TARGETS = r"""<Project>
  <PropertyGroup Condition="'$(CodeBoardingOriginalDirectoryBuildTargetsPath)' == ''">
    <CodeBoardingDirectoryBuildTargetsPath>$([MSBuild]::GetPathOfFileAbove('Directory.Build.targets', '$(MSBuildProjectDirectory)'))</CodeBoardingDirectoryBuildTargetsPath>
  </PropertyGroup>
  <Import
    Project="$(CodeBoardingOriginalDirectoryBuildTargetsPath)"
    Condition="Exists('$(CodeBoardingOriginalDirectoryBuildTargetsPath)')" />
  <Import
    Project="$(CodeBoardingDirectoryBuildTargetsPath)"
    Condition="Exists('$(CodeBoardingDirectoryBuildTargetsPath)')" />
  <PropertyGroup Condition="'$(TargetFrameworks)' != '' and '$(CodeBoardingFoldMultiTargetFrameworks)' == 'true'">
    <CodeBoardingTargetFrameworks>$([System.Text.RegularExpressions.Regex]::Replace('$(TargetFrameworks)', '\s', ''))</CodeBoardingTargetFrameworks>
    <CodeBoardingTargetFrameworks>$([System.Text.RegularExpressions.Regex]::Replace('$(CodeBoardingTargetFrameworks)', ';+', ';'))</CodeBoardingTargetFrameworks>
    <CodeBoardingTargetFrameworks>;$([System.String]::Copy('$(CodeBoardingTargetFrameworks)').Trim(';'));</CodeBoardingTargetFrameworks>
    <CodeBoardingPreferredTargetFramework Condition="'$(BundledNETCoreAppTargetFrameworkVersion)' != ''">net$(BundledNETCoreAppTargetFrameworkVersion)</CodeBoardingPreferredTargetFramework>
    <TargetFrameworks>$([System.String]::Copy('$(CodeBoardingTargetFrameworks)').Trim(';').Split(';')[0])</TargetFrameworks>
    <TargetFrameworks Condition="'$(CodeBoardingPreferredTargetFramework)' != '' and $(CodeBoardingTargetFrameworks.Contains(';$(CodeBoardingPreferredTargetFramework);'))">$(CodeBoardingPreferredTargetFramework)</TargetFrameworks>
  </PropertyGroup>
</Project>
"""

_WORKSPACE_TARGET_FRAMEWORK_PROPS = r"""<Project TreatAsLocalProperty="TargetFramework">
  <PropertyGroup>
    <CodeBoardingWorkspaceTargetFramework>$(TargetFramework)</CodeBoardingWorkspaceTargetFramework>
    <CodeBoardingDirectoryBuildPropsPath Condition="'$(CodeBoardingOriginalDirectoryBuildPropsPath)' == ''">$([MSBuild]::GetPathOfFileAbove('Directory.Build.props', '$(MSBuildProjectDirectory)'))</CodeBoardingDirectoryBuildPropsPath>
  </PropertyGroup>
  <Import
    Project="$(CodeBoardingOriginalDirectoryBuildPropsPath)"
    Condition="Exists('$(CodeBoardingOriginalDirectoryBuildPropsPath)')" />
  <Import
    Project="$(CodeBoardingDirectoryBuildPropsPath)"
    Condition="Exists('$(CodeBoardingDirectoryBuildPropsPath)')" />
</Project>
"""


def _write_injected_import(name: str, content: str) -> Path:
    """Materialize an MSBuild import CodeBoarding injects via environment variable."""
    path = get_servers_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        # Per thread, not just per process: several engines can publish these
        # concurrently, and a shared temp name loses the race to os.replace.
        temporary_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary_path.write_text(content, encoding="utf-8")
        os.replace(temporary_path, path)
    return path


def _single_target_framework_env(project_root: Path) -> dict[str, str]:
    """Keep csharp-ls's workspace-wide TargetFramework from rewriting single-target projects.

    Why: csharp-ls folds every project's frameworks into one global MSBuild
    ``TargetFramework``. A global property outranks a project's own
    ``<TargetFramework>``, so a solution whose projects do not all share one
    framework has that property forced onto the odd ones out. Conditions keyed
    on ``$(TargetFramework)`` then evaluate against a framework the project does
    not target -- the idiomatic ``Condition="'$(TargetFramework)' !=
    'netstandard2.0'"`` guard around analyzer ``ProjectReference`` items inverts
    and the analyzer projects reference themselves, which sends Roslyn's
    solution load into unbounded growth. ``TreatAsLocalProperty`` demotes the
    property so each project sees its declared framework again. Roslyn retains
    its normal inner-build expansion for projects that genuinely multi-target.

    Folding a multi-target project down to a single framework stays behind
    ``_MULTI_TARGET_PROJECT_THRESHOLD``, because that fold discards frameworks a
    small solution may want analyzed. Demoting the property has no such cost: it
    only changes evaluation for a project whose declared framework the workspace
    value contradicts, so it applies to every solution.
    """
    targets_path = _write_injected_import("csharp-ls-single-target.targets", _SINGLE_TARGET_FRAMEWORK_TARGETS)
    props_path = _write_injected_import("csharp-ls-workspace-tfm.props", _WORKSPACE_TARGET_FRAMEWORK_PROPS)

    env = {"DirectoryBuildTargetsPath": str(targets_path), "DirectoryBuildPropsPath": str(props_path)}

    project_count = sum(1 for pattern in ("*.csproj", "*.fsproj") for _ in project_root.rglob(pattern))
    if project_count > _MULTI_TARGET_PROJECT_THRESHOLD:
        env["CodeBoardingFoldMultiTargetFrameworks"] = "true"

    original_targets_path = os.environ.get("DirectoryBuildTargetsPath")
    if original_targets_path:
        env["CodeBoardingOriginalDirectoryBuildTargetsPath"] = original_targets_path
    original_props_path = os.environ.get("DirectoryBuildPropsPath")
    if original_props_path:
        env["CodeBoardingOriginalDirectoryBuildPropsPath"] = original_props_path
    return env


class CSharpAdapter(LanguageAdapter):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._namespaces: dict[str, dict[str, str]] = {}
        """Per file, the declared namespaces keyed by their last segment.

        Why: csharp-ls gives a class only the last segment of its namespace, so the full
        name has to be carried from the namespace symbol that was walked just before it.
        """

    @property
    def include_references_on_declaration_line(self) -> bool:
        return True

    @property
    def language(self) -> str:
        return "CSharp"

    @property
    def language_enum(self) -> Language:
        return Language.CSHARP

    @property
    def lsp_command(self) -> list[str]:
        return ["csharp-ls"]

    @property
    def language_id(self) -> str:
        return "csharp"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Resolve the .NET SDK and ensure the managed csharp-ls install is current."""
        try:
            resolution = resolve_dotnet_sdk(project_root)
        except DotnetSdkError as exc:
            raise RuntimeError(str(exc)) from exc

        self._ensure_csharp_ls_installed(project_root, resolution.dotnet_path, resolution.env)
        return super().get_lsp_command(project_root)

    def _module_for(self, file_path: Path, parent_chain: list[tuple[str, int]], project_root: Path) -> str:
        """The declared namespace this symbol sits in, or the directory when it has none.

        A file may declare several namespaces, so the chain's own namespace segment selects
        which one. The directory fallback covers the global namespace and any file whose
        namespace symbol the server did not report.
        """
        declared = self._namespaces.get(str(file_path), {})
        # A nested declaration reaches its children as several namespace entries; joining
        # them is the resolved name, and it wins over any single segment.
        nested = ".".join(part for part, kind in parent_chain if kind == NodeType.NAMESPACE)
        if nested in declared:
            return declared[nested]
        for name, kind in reversed(parent_chain):
            if kind == NodeType.NAMESPACE and name in declared:
                # `setdefault` on the last segment: a full name always wins over a segment
                # that two namespaces in this file share.
                return declared[name]
        # No namespace in the chain means the symbol is not inside one -- a global type in a
        # file that also declares a namespace, or a file with none at all. Borrowing the
        # file's other namespace would put a global type inside it.
        rel = file_path.relative_to(project_root)
        return ".".join(part for part in rel.parent.parts if part not in ("src", "."))

    def _ensure_csharp_ls_installed(self, project_root: Path, dotnet_path: str, dotnet_env: dict[str, str]) -> None:
        dep = next((d for d in TOOL_REGISTRY if d.key == "csharp" and d.kind is ToolKind.PACKAGE_MANAGER), None)
        if dep is None:
            return

        servers_dir = get_servers_dir()
        managed_path = package_manager_tool_path(servers_dir, dep)
        if managed_path is not None and package_manager_tool_is_current(servers_dir, dep):
            return

        command = super().get_lsp_command(project_root)
        if managed_path is None and command and shutil.which(command[0]):
            return

        servers_dir.mkdir(parents=True, exist_ok=True)
        lock_path = servers_dir / ".download.lock"
        env = os.environ.copy()
        env.update(dotnet_env)
        with open(lock_path, "w") as lock_fd:
            acquire_lock(lock_fd)
            if package_manager_tool_is_current(servers_dir, dep):
                return
            install_package_manager_tools(
                servers_dir,
                [dep],
                manager_overrides={"dotnet": dotnet_path},
                env=env,
            )
        if not package_manager_tool_is_current(servers_dir, dep):
            raise RuntimeError(
                "csharp-ls could not be installed. CodeBoarding needs csharp-ls 0.24.0 and a .NET 10 SDK "
                "to analyze C# projects."
            )

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build ``<declared namespace>.<declaring types>.<symbol>`` for C#.

        Why the namespace: C# does not require it to match the directory, and it is the name
        the compiler uses. Why not the file stem: C# has no file scope, so a file may declare
        several top-level types and the stem would nest them under each other.

        csharp-ls gives the full namespace only on the namespace symbol's ``detail``; a
        class's chain carries the last segment, so the full name is recorded here for
        ``_module_for`` to read back.
        """
        if detail and symbol_kind == NodeType.NAMESPACE:
            # csharp-ls reports the *declared* name, not the resolved one: a namespace nested
            # inside another gives only its own segment, so `namespace Outer { namespace
            # Shared { ... } }` arrives as "Shared" and the outer prefix has to come from the
            # chain.
            enclosing = ".".join(part for part, kind in parent_chain if kind == NodeType.NAMESPACE)
            full = f"{enclosing}.{detail}" if enclosing else detail
            known = self._namespaces.setdefault(str(file_path), {})
            known[full] = full
            known.setdefault(full.rsplit(".", 1)[-1], full)
            return full

        module = self._module_for(file_path, parent_chain, project_root)

        # Skip File (kind=1) and Namespace (kind=3) — the namespace is the prefix already.
        code_parents = [name for name, kind in parent_chain if kind not in (NodeType.FILE, NodeType.NAMESPACE)]

        return ".".join(part for part in (module, *code_parents, symbol_name) if part)

    def get_package_for_file(self, file_path: Path, project_root: Path) -> str:
        """The namespace this file declares, so packages and qualified names agree.

        The inherited default is the directory. Since a name is now the declared namespace,
        that split the two apart: sibling files declaring ``Alpha`` and ``Beta`` counted as
        one package, hiding the call between them, while files in different directories
        sharing a namespace looked like a cross-package dependency.
        """
        declared = self.declared_namespaces(file_path)
        if declared:
            # Sorted first so a tie on length does not resolve by set iteration order.
            return min(sorted(declared), key=len)
        return super().get_package_for_file(file_path, project_root)

    def package_of(self, qualified_name: str, file_path: Path, project_root: Path) -> str:
        """The declared namespace this symbol sits in, not merely its file's first one."""
        holding = [
            namespace
            for namespace in self.declared_namespaces(file_path)
            if qualified_name == namespace or qualified_name.startswith(f"{namespace}.")
        ]
        if holding:
            return max(holding, key=len)
        # In none of them: a global type in a file that also declares namespaces. Handing it
        # whichever namespace happens to be shortest would place it somewhere it is not, so
        # it falls back to the directory, exactly as its name does.
        return LanguageAdapter.get_package_for_file(self, file_path, project_root)

    def declared_namespaces(self, file_path: Path) -> set[str]:
        """Every namespace this file declares.

        A file may declare more than one. ``get_package_for_file`` has to answer with a
        single name, so it takes the shortest; anything that can carry them all -- the
        package list, and edge classification, which knows the symbol -- should ask here
        instead, or a call from ``Alpha`` to ``Beta`` in one file reads as internal.
        """
        return set(self._namespaces.get(str(file_path), {}).values())

    def extract_package(self, qualified_name: str) -> str:
        """Extract namespace as all-but-last-two dot-separated components.

        For ``Services.Auth.AuthService.Login`` the package is ``Services.Auth``.
        """
        return self._extract_deep_package(qualified_name)

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Configure csharp-ls for static analysis.

        Settings are read from the ``csharp`` workspace configuration section.
        """
        return {
            "csharp": {
                "logLevel": "warning",
            },
        }

    def get_workspace_settings(self) -> dict | None:
        return {
            "csharp": {
                "logLevel": "warning",
            },
        }

    @property
    def probe_before_open(self) -> bool:
        """csharp-ls loads all files from the .sln — didOpen before workspace load kills it."""
        return True

    @property
    def workspace_owns_documents(self) -> bool:
        """csharp-ls answers position queries from the loaded solution, opened or not."""
        return True

    @property
    def edge_strategy(self) -> EdgeStrategy:
        """Definition-based edges: on a 3.5k-file workspace ~5% of csharp-ls
        references queries take 60-100s (some never return), so a
        references-based phase 2 never finishes."""
        return EdgeStrategy.DEFINITIONS

    @property
    def resolves_method_groups(self) -> bool:
        """Minimal-API routing (``app.MapGet("/items", GetAllItems)``) passes
        handlers as values, so the invocation walk alone would miss them."""
        return True

    @property
    def expands_virtual_dispatch(self) -> bool:
        """csharp-ls answers ``textDocument/implementation`` for interface members
        but returns nothing for abstract or virtual *class* members, so a call
        through a base-typed reference stops at the abstract declaration."""
        return True

    @property
    def resolves_collection_initializers(self) -> bool:
        """C# collection-initializer braces desugar to Add calls."""
        return True

    @property
    def resolves_iterated_types(self) -> bool:
        """csharp-ls answers typeDefinition, so a ``foreach`` over a repo
        collection can name the type it enumerates."""
        return True

    def get_lsp_default_timeout(self) -> int:
        """csharp-ls needs extra time to load Roslyn workspace for large solutions."""
        return 120

    def get_probe_timeout_minimum(self) -> int:
        """Roslyn workspace loading for large .NET solutions can exceed 5 minutes."""
        return 600

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """csharp-ls publishes diagnostics asynchronously after didOpen with
        no enclosing readiness signal — no ``$/progress`` end, no
        ``language/status``, just a burst of ``publishDiagnostics`` and
        then silence. The only correct synchronization is to debounce on
        the publishDiagnostics stream itself.

        Empirically 2s idle / 30s max covers both the edge-case fixture
        (8 files, ~1s of publishes) and large repos like Polly (~500 files,
        several seconds of publishes).
        """
        client.wait_for_diagnostics_quiesce(idle_seconds=2.0, max_wait=30.0)

    def prepare_project(self, project_root: Path) -> None:
        """Run ``dotnet restore`` so csharp-ls can resolve framework references.

        Why: csharp-ls relies on Roslyn / MSBuild to load the project, and
        MSBuild needs ``obj/project.assets.json`` (produced by restore) to
        find the .NET runtime reference assemblies. Without it, csharp-ls
        emits a flood of bogus ``CS0518: Predefined type System.X is not
        defined`` diagnostics for every file. Restore is idempotent and
        only writes under ``obj/`` (which we already gitignore).
        """
        # Find solution or csproj/fsproj at the project_root level
        target = next(iter(project_root.glob("*.sln")), None)
        if target is None:
            target = next(iter(project_root.glob("*.slnx")), None)
        if target is None:
            target = next(iter(project_root.glob("*.csproj")), None)
        if target is None:
            target = next(iter(project_root.glob("*.fsproj")), None)
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
        """Return the .NET environment needed by csharp-ls.

        With a project root, this may point at CodeBoarding's private SDK
        hive. Without one, preserve the branch's legacy Homebrew/system
        DOTNET_ROOT and DOTNET_ROLL_FORWARD behavior for older callers.
        """
        if project_root is not None:
            try:
                env = resolve_dotnet_sdk(project_root).env
            except DotnetSdkError as exc:
                raise RuntimeError(str(exc)) from exc
            env.update(_single_target_framework_env(project_root))
            return env
        dotnet = shutil.which("dotnet")
        env = system_dotnet_env(Path(dotnet)) if dotnet else {}
        if not os.environ.get("DOTNET_ROLL_FORWARD"):
            env["DOTNET_ROLL_FORWARD"] = "Major"
        return env

    @property
    def fail_on_empty_symbols(self) -> bool:
        return True

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces in reference tracking (similar to PHP modules)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Every prefix of every declared namespace, so a package matches the names built from it."""
        packages: set[str] = set()
        for source in source_files:
            for namespace in self.declared_namespaces(source) or {self.get_package_for_file(source, project_root)}:
                parts = namespace.split(".")
                packages.update(".".join(parts[:depth]) for depth in range(1, len(parts) + 1))
        return packages - {""}
