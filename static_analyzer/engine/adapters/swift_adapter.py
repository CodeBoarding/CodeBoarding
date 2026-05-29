"""Swift language adapter using sourcekit-lsp."""

from __future__ import annotations

import shutil
from pathlib import Path

from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


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
        """Fail fast if the Swift toolchain is missing.

        Why: sourcekit-lsp is bundled with the Swift toolchain. If ``swift``
        is absent, sourcekit-lsp is too, and indexing would either fail to
        spawn or silently return empty results. A clear error at launch is
        preferable to a confusing downstream failure.
        """
        if shutil.which("swift") is None:
            raise RuntimeError(
                "Swift toolchain not found on PATH. sourcekit-lsp ships with "
                "the Swift toolchain and is required to index Swift projects. "
                "Install one from https://swift.org/install/ (or Xcode on macOS) "
                "and re-run the analysis."
            )
        return super().get_lsp_command(project_root)
