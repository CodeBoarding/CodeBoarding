"""C# language adapter using csharp-ls (Roslyn-based)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient

logger = logging.getLogger(__name__)


class CSharpAdapter(LanguageAdapter):

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
        """Fail fast if the .NET SDK is missing — csharp-ls needs dotnet+MSBuild to load Roslyn."""
        if shutil.which("dotnet") is None:
            raise RuntimeError(
                ".NET SDK not found on PATH. csharp-ls requires the .NET SDK "
                "to index C# projects. Install it from "
                "https://dotnet.microsoft.com/download and re-run the analysis."
            )
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
    def wait_for_workspace_ready(self) -> bool:
        return True

    @property
    def probe_before_open(self) -> bool:
        """csharp-ls loads all files from the .sln — didOpen before workspace load kills it."""
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
        if shutil.which("dotnet") is None:
            logger.info("Skipping dotnet restore: dotnet not on PATH")
            return
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

        env = os.environ.copy()
        env.update(self.get_lsp_env())
        try:
            result = subprocess.run(
                ["dotnet", "restore", str(target.name), "--nologo", "--verbosity", "minimal"],
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

    def get_lsp_env(self) -> dict[str, str]:
        """Set DOTNET_ROOT when not already in the environment.

        csharp-ls requires the .NET runtime to be discoverable. On systems
        where the SDK is installed via a package manager (e.g. Homebrew on
        macOS), the ``DOTNET_ROOT`` variable may not be set, causing
        csharp-ls to fail at startup.  This resolves the runtime location
        from the ``dotnet`` binary on PATH.
        """
        if os.environ.get("DOTNET_ROOT"):
            return {}
        dotnet = shutil.which("dotnet")
        if dotnet:
            dotnet_root = Path(dotnet).resolve().parent.parent / "libexec"
            if dotnet_root.is_dir():
                return {"DOTNET_ROOT": str(dotnet_root)}
        return {}

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces in reference tracking (similar to PHP modules)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Get all directory prefixes as packages (namespace-based, like PHP)."""
        return self._get_hierarchical_packages(source_files, project_root)
