"""Swift language adapter using sourcekit-lsp."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient, MethodNotFoundError

logger = logging.getLogger(__name__)

SWIFT_BUILD_TIMEOUT_SECONDS = 300


@lru_cache(maxsize=1)
def resolve_sourcekit_lsp() -> str | None:
    """Resolve the absolute path to ``sourcekit-lsp``, or ``None`` if absent.

    1. ``shutil.which``: covers Linux installs that put the toolchain on PATH,
       Windows MSI installs, swift.org tarballs whose ``usr/bin`` is sourced,
       and recent macOS (~26+) where Apple ships ``/usr/bin/sourcekit-lsp``
       as an ``xcselect`` shim alongside ``/usr/bin/swift``.
    2. macOS fallback via ``xcrun --find sourcekit-lsp``: reaches the binary
       inside Xcode / Command Line Tools on older macOS where the
       ``/usr/bin`` shim is absent. Most macOS users with Xcode installed
       hit this case.

    Cached for one process — the toolchain layout doesn't move at runtime
    and the same lookup is hit by ``get_lsp_command`` and the install
    summary.
    """
    path = shutil.which("sourcekit-lsp")
    if path:
        return path
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["xcrun", "--find", "sourcekit-lsp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    candidate = result.stdout.strip()
    if not candidate:
        return None
    return candidate if Path(candidate).is_file() else None


class SwiftAdapter(LanguageAdapter):
    """Static-analysis adapter for Swift projects backed by sourcekit-lsp.

    sourcekit-lsp ships *inside* the Swift toolchain (next to ``swift`` /
    ``swiftc``) rather than as a standalone release, so unlike clangd or
    rust-analyzer there is nothing to download — we require a user-installed
    toolchain. Resolution prefers PATH; on macOS we fall back to
    ``xcrun --find sourcekit-lsp`` so Xcode/CLT users don't need to manually
    splice the Developer dir into their shell PATH.
    """

    @property
    def language(self) -> str:
        return "Swift"

    @property
    def language_enum(self) -> Language:
        return Language.SWIFT

    @property
    def lsp_command(self) -> list[str]:
        return ["sourcekit-lsp"]

    @property
    def language_id(self) -> str:
        return "swift"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Resolve ``sourcekit-lsp``'s absolute path or fail with a clear error.

        On macOS the binary commonly lives inside Xcode / CLT without a
        ``/usr/bin`` shim; ``resolve_sourcekit_lsp`` reaches it through
        ``xcrun --find``. When it returns ``None`` we surface a targeted
        install message instead of letting ``subprocess.Popen`` die with
        ``FileNotFoundError``.

        The resolved path is spliced into the tool-registry-built command so
        the spawn works even when ``tool_registry.manifest`` had nothing on
        PATH to bind to.
        """
        if shutil.which("swift") is None:
            raise RuntimeError(
                "Swift toolchain not found on PATH. sourcekit-lsp ships with "
                "the Swift toolchain and is required to index Swift projects. "
                "Install Swift from https://swift.org/install/ (or Xcode on macOS), "
                "make sure swift and sourcekit-lsp are on PATH, then re-run the analysis."
            )
        resolved = resolve_sourcekit_lsp()
        if resolved is None:
            raise RuntimeError(
                "sourcekit-lsp could not be located"
                + (" or via 'xcrun --find sourcekit-lsp'" if platform.system() == "Darwin" else "")
                + ". It ships with the full Swift toolchain and is required to index "
                "Swift projects. Install a full Swift toolchain from "
                "https://swift.org/install/ (or Xcode on macOS), ensure its usr/bin "
                "directory is on PATH, then re-run the analysis."
            )
        cmd = super().get_lsp_command(project_root)
        # ``tool_registry`` may have left ``sourcekit-lsp`` as a bare name when
        # nothing on PATH matched (the xcrun-only case). Splice the resolved
        # absolute path so spawn doesn't fall back to PATH resolution at the
        # OS level — which would just fail again.
        if cmd and not Path(cmd[0]).is_absolute():
            cmd = [resolved, *cmd[1:]]
        return cmd

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Use the SwiftPM target name as the top-level module.

        SwiftPM convention: ``<package>/Sources/<Target>/...`` and
        ``<package>/Tests/<TestTarget>/...``. With ``project_root`` set to
        the package directory (by ``SwiftConfigScanner``), stripping the
        leading ``Sources``/``Tests`` segment makes the next path component
        the SwiftPM target — which matches how Swift symbols actually
        resolve at compile time, and what users expect to see as cluster
        labels in the diagram.
        """
        rel = file_path.relative_to(project_root)
        parts = list(rel.with_suffix("").parts)
        if len(parts) >= 2 and parts[0] in ("Sources", "Tests"):
            parts = parts[1:]
        module = ".".join(parts) if parts else rel.stem
        if parent_chain:
            parents = ".".join(name for name, _ in parent_chain)
            return f"{module}.{parents}.{symbol_name}"
        return f"{module}.{symbol_name}"

    def get_package_for_file(self, file_path: Path, project_root: Path) -> str:
        """Mirror ``build_qualified_name``'s SwiftPM stripping for package labels.

        Without this, root-level packages would still include the
        ``Sources.`` prefix in cluster names.
        """
        try:
            rel = file_path.relative_to(project_root)
        except ValueError:
            return "external"
        parts = list(rel.parent.parts)
        if parts and parts[0] in ("Sources", "Tests"):
            parts = parts[1:]
        if parts and parts[0] != ".":
            return ".".join(parts)
        return rel.stem

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """sourcekit-lsp publishes diagnostics asynchronously after didOpen
        without a quiescent signal we can read here — debounce on the
        publishDiagnostics stream itself, same pattern as csharp-ls.
        """
        client.wait_for_diagnostics_quiesce(idle_seconds=2.0, max_wait=30.0)

    def wait_for_references_ready(self, client: LSPClient) -> None:
        """Wait for SourceKit-LSP to load the index used by references."""
        try:
            logger.info("Synchronizing SourceKit-LSP index before references queries...")
            client.workspace_synchronize(index=True, timeout=300)
            return
        except MethodNotFoundError:
            logger.info("SourceKit-LSP workspace/synchronize unsupported; trying legacy workspace/_pollIndex")
        except Exception as e:
            logger.warning("SourceKit-LSP workspace/synchronize failed; trying legacy poll: %s", e)

        try:
            client.workspace_poll_index(timeout=300)
        except MethodNotFoundError:
            logger.warning("SourceKit-LSP index synchronization is unsupported; references may be incomplete")
        except Exception as e:
            logger.warning("SourceKit-LSP legacy index poll failed; references may be incomplete: %s", e)

    @property
    def empty_references_retry_attempts(self) -> int:
        return 3

    @property
    def empty_references_retry_delay(self) -> float:
        return 5.0

    def prepare_project(self, project_root: Path) -> None:
        """Run ``swift build`` so sourcekit-lsp has an index store to query.

        Why: sourcekit-lsp's cross-file features (definition, references,
        workspace symbols) read from an indexstore-db populated as a side
        effect of compilation. Without it, queries silently return empty.
        For SwiftPM projects ``swift build`` writes the store to
        ``.build/<arch>/<config>/index/store``, which sourcekit-lsp picks
        up via the bundled BSP server.

        Best-effort: a build failure (missing deps, syntax errors) is
        logged but doesn't abort the run — diagnostics are still useful
        and single-file symbols still work.
        """
        if not (project_root / "Package.swift").exists():
            logger.debug("No Package.swift at %s; skipping swift build (Xcode-only projects unsupported)", project_root)
            return
        if shutil.which("swift") is None:
            logger.info("Skipping swift build: swift not on PATH")
            return

        env = os.environ.copy()
        env.update(self.get_lsp_env())
        # First runs on a fresh checkout pull dependencies and do a full debug
        # compile; surface that to the user up front so a multi-minute wait
        # doesn't look like the analyzer is hung.
        logger.info(
            "Running 'swift build' in %s to populate the sourcekit-lsp index store. "
            "First runs may take several minutes while SwiftPM resolves dependencies; subsequent runs are incremental.",
            project_root,
        )
        try:
            result = subprocess.run(
                ["swift", "build"],
                cwd=str(project_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=SWIFT_BUILD_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                logger.warning(
                    "swift build failed for %s (exit %d); sourcekit-lsp will have a partial or stale index: %s",
                    project_root,
                    result.returncode,
                    (result.stderr or result.stdout)[-500:],
                )
            else:
                logger.info("swift build completed for %s; index store populated", project_root)
        except subprocess.TimeoutExpired:
            logger.warning(
                "swift build timed out after %ds for %s; proceeding with partial index",
                SWIFT_BUILD_TIMEOUT_SECONDS,
                project_root,
            )
        except OSError as e:
            logger.warning("Failed to invoke swift build for %s: %s", project_root, e)
