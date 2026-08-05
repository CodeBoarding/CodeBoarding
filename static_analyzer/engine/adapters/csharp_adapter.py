"""C# language adapter using csharp-ls (Roslyn-based)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language, NodeType
from static_analyzer.dotnet_sdk import DotnetSdkError, resolve_dotnet_sdk, system_dotnet_env
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
    user_data_dir,
)

logger = logging.getLogger(__name__)

# MSBuild file that rewrites every multi-targeted project to a single target framework.
#
# Why: before opening a workspace, csharp-ls folds the per-project target-framework sets
# together pairwise and only deduplicates once, at the end. Each project that declares
# ``<TargetFrameworks>`` (plural) roughly doubles the work, so a solution with ~30 or more
# of them takes hours-to-years to load and our readiness probe times out with no C# result.
# One framework per project keeps that fold linear.
#
# The .NET SDK imports this after the project body, so it also catches multi-targeting that
# only a ``Directory.Build.props`` declares and no csproj mentions. Prefer the SDK's own
# current framework when the project offers it: positional picks land on .NET Framework or a
# mobile target for the many repos that order their list newest-first.
SINGLE_TARGET_FRAMEWORK_TARGETS = r"""<Project>
  <PropertyGroup Condition="'$(TargetFrameworks)' != ''">
    <_CbTfms>$([System.Text.RegularExpressions.Regex]::Replace('$(TargetFrameworks)', '\s', ''))</_CbTfms>
    <_CbTfms>$([System.Text.RegularExpressions.Regex]::Replace('$(_CbTfms)', ';+', ';'))</_CbTfms>
    <_CbTfms>;$([System.Text.RegularExpressions.Regex]::Replace('$(_CbTfms)', '^;|;$', ''));</_CbTfms>
    <_CbSdkTfm Condition="'$(BundledNETCoreAppTargetFrameworkVersion)' != ''">net$(BundledNETCoreAppTargetFrameworkVersion)</_CbSdkTfm>
    <_CbTfm>$([System.Text.RegularExpressions.Regex]::Replace('$(_CbTfms.Substring(1))', ';.*', ''))</_CbTfm>
    <_CbTfm Condition="'$(_CbSdkTfm)' != '' and $(_CbTfms.Contains(';$(_CbSdkTfm);'))">$(_CbSdkTfm)</_CbTfm>
  </PropertyGroup>
  <PropertyGroup Condition="'$(_CbTfm)' != ''">
    <TargetFramework Condition="'$(TargetFramework)' == ''">$(_CbTfm)</TargetFramework>
    <TargetFrameworks></TargetFrameworks>
  </PropertyGroup>
</Project>
"""


class CSharpAdapter(LanguageAdapter):

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
        """Build namespace-based qualified names for C#.

        csharp-ls returns: File (kind=1) > Namespace (kind=3) > Class > Members.
        The namespace's ``detail`` has the full namespace, but only the
        namespace symbol itself receives it -- children get ``detail=""``.

        Strategy: use namespace detail when available (for namespace
        symbols themselves), otherwise reconstruct from file path,
        skipping ``src/`` prefix and deduplicating filename/class.
        """
        # Namespace symbol itself — detail has the full namespace
        if detail and symbol_kind == NodeType.NAMESPACE:
            return detail

        # Build from file path, stripping 'src' prefix
        rel = file_path.relative_to(project_root)
        parts = [p for p in rel.with_suffix("").parts if p != "src"]
        module = ".".join(parts)

        # Filter parents: skip File (kind=1) and Namespace (kind=3) —
        # the namespace is already encoded in the file path for C#
        code_parents = [name for name, kind in parent_chain if kind not in (NodeType.FILE, NodeType.NAMESPACE)]

        if code_parents:
            # Deduplicate first parent if it matches filename
            module_last = module.rsplit(".", 1)[-1] if "." in module else module
            if code_parents[0] == module_last:
                code_parents = code_parents[1:]
            if code_parents:
                return f"{module}.{'.'.join(code_parents)}.{symbol_name}"

        # Deduplicate filename/class (one type per file convention)
        module_last = module.rsplit(".", 1)[-1] if "." in module else module
        if symbol_name == module_last:
            return module
        return f"{module}.{symbol_name}"

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
                env = dict(resolve_dotnet_sdk(project_root).env)
            except DotnetSdkError as exc:
                raise RuntimeError(str(exc)) from exc
        else:
            dotnet = shutil.which("dotnet")
            env = system_dotnet_env(Path(dotnet)) if dotnet else {}
            if not os.environ.get("DOTNET_ROLL_FORWARD"):
                env["DOTNET_ROLL_FORWARD"] = "Major"

        # Both hooks are needed: MSBuild imports the cross-targeting one while
        # evaluating a project that still declares several frameworks, and the
        # common one once csharp-ls reopens it pinned to the single framework.
        targets = str(self._write_single_target_framework_targets())
        env["CustomAfterMicrosoftCommonTargets"] = targets
        env["CustomAfterMicrosoftCommonCrossTargetingTargets"] = targets
        return env

    def _write_single_target_framework_targets(self) -> Path:
        """Materialize ``SINGLE_TARGET_FRAMEWORK_TARGETS`` and return its path."""
        path = user_data_dir() / "msbuild" / "CodeBoarding.SingleTargetFramework.targets"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written per run and shared across concurrent analyses, so swap it in
        # atomically -- MSBuild reading a half-written file fails every project.
        scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        scratch.write_text(SINGLE_TARGET_FRAMEWORK_TARGETS, encoding="utf-8")
        os.replace(scratch, path)
        return path

    @property
    def fail_on_empty_symbols(self) -> bool:
        return True

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces in reference tracking (similar to PHP modules)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Get all directory prefixes as packages (namespace-based, like PHP)."""
        return self._get_hierarchical_packages(source_files, project_root)
