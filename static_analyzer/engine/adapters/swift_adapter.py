"""Swift language adapter using sourcekit-lsp."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient

logger = logging.getLogger(__name__)


class SwiftAdapter(LanguageAdapter):
    """Static-analysis adapter for Swift projects backed by sourcekit-lsp.

    sourcekit-lsp ships *inside* the Swift toolchain (next to ``swift`` /
    ``swiftc``) rather than as a standalone release, so unlike clangd or
    rust-analyzer there is nothing to download — we require a user-installed
    toolchain and resolve the binary from PATH. Mirrors Rust's cargo check
    and Go's go check.
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
        """Fail fast if the Swift toolchain or sourcekit-lsp is missing.

        Why: sourcekit-lsp ships *with* the Swift toolchain but a few install
        layouts (some ``swiftly``/``asdf`` shims, the macOS CLT shim,
        partially-installed toolchains) expose ``swift`` without
        ``sourcekit-lsp`` on PATH. Checking both turns an opaque
        ``FileNotFoundError`` at spawn into a clear install message.
        """
        if shutil.which("swift") is None:
            raise RuntimeError(
                "Swift toolchain not found on PATH. sourcekit-lsp ships with "
                "the Swift toolchain and is required to index Swift projects. "
                "Install one from https://swift.org/install/ (or Xcode on macOS) "
                "and re-run the analysis."
            )
        if shutil.which("sourcekit-lsp") is None:
            raise RuntimeError(
                "swift is on PATH but sourcekit-lsp is not. This usually means a "
                "partial toolchain install (some swiftly/asdf shims, macOS Command "
                "Line Tools without Xcode). Install a full Swift toolchain from "
                "https://swift.org/install/ (or Xcode on macOS) so sourcekit-lsp "
                "is reachable, and re-run the analysis."
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
                timeout=600,
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
            logger.warning("swift build timed out after 600s for %s; proceeding with partial index", project_root)
        except OSError as e:
            logger.warning("Failed to invoke swift build for %s: %s", project_root, e)
