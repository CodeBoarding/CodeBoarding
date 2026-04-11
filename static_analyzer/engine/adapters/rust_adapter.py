"""Rust language adapter using rust-analyzer."""

from __future__ import annotations

import shutil
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.engine.language_adapter import LanguageAdapter

# File stems implicit in the module path: ``mod.rs`` (directory module
# entry), ``lib.rs`` (library crate root), ``main.rs`` (binary crate root).
_IMPLICIT_MODULE_STEMS = {"mod", "lib", "main"}


def _normalize_parent(name: str) -> str:
    """Strip the ``impl `` prefix and ``impl X for Y`` wrapper from a
    rust-analyzer documentSymbol parent name, leaving just the implementing
    type (e.g. ``"impl Speaker for Cat"`` becomes ``"Cat"``). Generics are
    stripped so consumers can ``.split(".")``. Non-impl names pass through.
    """
    name = name.strip()
    if not name.startswith("impl "):
        return name
    body = name[len("impl ") :].strip()
    for_idx = body.find(" for ")
    if for_idx != -1:
        body = body[for_idx + len(" for ") :].strip()
    angle_idx = body.find("<")
    if angle_idx != -1:
        body = body[:angle_idx].strip()
    return body or name


class RustAdapter(LanguageAdapter):
    """Static-analysis adapter for Rust projects backed by rust-analyzer."""

    @property
    def language(self) -> str:
        return "Rust"

    @property
    def references_per_query_timeout(self) -> int:
        """Non-zero gates the Phase-1.5 warmup probe so rust-analyzer builds
        its ``ide_db::search`` index before Phase 2 fans out queries."""
        return 60

    @property
    def wait_for_workspace_ready(self) -> bool:
        """Block on ``experimental/serverStatus`` quiescent before Phase 2.

        rust-analyzer's cross-file queries return empty until ``cargo metadata``
        finishes loading the workspace.
        """
        return True

    @property
    def extra_client_capabilities(self) -> dict:
        """Opt into rust-analyzer's ``experimental/serverStatus`` notifications.

        Required: without this advertisement rust-analyzer never emits the
        ``quiescent`` notification and ``wait_for_server_ready`` times out
        (verified empirically). Confined to this adapter so other LSPs
        don't see vendor-specific keys.
        """
        return {"experimental": {"serverStatusNotification": True}}

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".rs",)

    @property
    def lsp_command(self) -> list[str]:
        return ["rust-analyzer"]

    @property
    def language_id(self) -> str:
        return "rust"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Fail fast if cargo is missing.

        rust-analyzer needs ``cargo metadata`` to index any Cargo workspace
        and the toolchain is too large to bundle, so we mirror Java's
        pattern of requiring a user-installed toolchain.
        """
        if shutil.which("cargo") is None:
            raise RuntimeError(
                "cargo not found on PATH. rust-analyzer requires a Rust "
                "toolchain to index Cargo projects. Install one via "
                "https://rustup.rs/ and re-run the analysis."
            )
        return super().get_lsp_command(project_root)

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Tune rust-analyzer for batch analysis: enable build scripts and
        proc macros (so generated code is indexed) and the full target graph;
        disable ``checkOnSave`` (slow and unused).
        """
        return {
            "cargo": {
                "buildScripts": {"enable": True},
                "allTargets": True,
            },
            "procMacro": {"enable": True},
            "checkOnSave": False,
        }

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Collapse implicit module stems (``mod.rs``/``lib.rs``/``main.rs``)
        when building dotted qualified names. ``src/models/mod.rs::User``
        becomes ``src.models.User``, not ``src.models.mod.User``. Falls back
        to the file stem if stripping would leave no module parts (e.g. a
        bare ``mod.rs`` at the project root).
        """
        rel = file_path.relative_to(project_root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] in _IMPLICIT_MODULE_STEMS:
            parts.pop()
        module = ".".join(parts) if parts else rel.stem

        if parent_chain:
            parents = ".".join(_normalize_parent(name) for name, _ in parent_chain)
            return f"{module}.{parents}.{symbol_name}"
        return f"{module}.{symbol_name}"
