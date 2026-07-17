"""Rust language adapter using rust-analyzer."""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Collection
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import ImportDependency, ImportDependencyKind

logger = logging.getLogger(__name__)

# File stems implicit in the module path: ``mod.rs`` (directory module
# entry), ``lib.rs`` (library crate root), ``main.rs`` (binary crate root).
_IMPLICIT_MODULE_STEMS = {"mod", "lib", "main"}
RUST_DIRECTORY_MODULE_ENTRY_STEM = "mod"


def _skip_angle_block(s: str, start: int) -> int:
    """Return the index just past a balanced ``<...>`` block starting at *start*.

    Handles nested generics (``<T: Iterator<Item = u8>>``). Returns *start*
    unchanged if the block is malformed.
    """
    if start >= len(s) or s[start] != "<":
        return start
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "<":
            depth += 1
        elif s[i] == ">":
            depth -= 1
            if depth == 0:
                return i + 1
    return start


def _normalize_parent(name: str) -> str:
    """Reduce a rust-analyzer documentSymbol parent to a clean type name.

    Handles every impl header shape rust-analyzer emits:
    ``impl T``, ``impl<T> Foo<T>``, ``impl<T> Trait for Foo<T>``,
    ``impl<T: Bound> Foo<T> where ...``. Returns the implementing type
    with all generics and where clauses stripped so consumers can
    ``.split(".")``. Non-impl names pass through unchanged.
    """
    name = name.strip()
    if not name.startswith("impl"):
        return name
    # ``impl`` must be a standalone token: either followed by whitespace
    # (``impl Foo``) or by ``<`` for impl-level generics (``impl<T> Foo``).
    after_impl = name[len("impl") :]
    if after_impl and after_impl[0] not in (" ", "\t", "<"):
        return name
    # Skip the impl-level ``<...>`` block if present, then any whitespace.
    cursor = len("impl")
    cursor = _skip_angle_block(name, cursor)
    while cursor < len(name) and name[cursor].isspace():
        cursor += 1
    body = name[cursor:].strip()
    # Trait impls put the implementing type after ``for``.
    for_idx = body.find(" for ")
    if for_idx != -1:
        body = body[for_idx + len(" for ") :].strip()
    # Strip type-level generics and where clauses.
    for terminator in ("<", " where "):
        idx = body.find(terminator)
        if idx != -1:
            body = body[:idx].strip()
    return body or name


class RustAdapter(LanguageAdapter):
    """Static-analysis adapter for Rust projects backed by rust-analyzer."""

    @property
    def language(self) -> str:
        return "Rust"

    @property
    def language_enum(self) -> Language:
        return Language.RUST

    def resolve_import_target(
        self,
        declaration: ImportDependency,
        source_files: Collection[Path],
        project_root: Path,
    ) -> Path | None:
        """Resolve ``mod child;`` relative to its declaring Rust module."""
        if declaration.kind != ImportDependencyKind.MODULE:
            return super().resolve_import_target(declaration, source_files, project_root)

        source_path = Path(declaration.source_file)
        source = (source_path if source_path.is_absolute() else project_root / source_path).resolve()
        module_parts = tuple(part for part in declaration.declared_module.split("::") if part)
        if not module_parts:
            return None
        path_base = source.parent.joinpath(*module_parts).resolve()
        candidates = {(path if path.is_absolute() else project_root / path).resolve() for path in source_files}
        matches = {
            candidate
            for suffix in set(self.file_extensions)
            for candidate in (
                path_base.with_suffix(suffix),
                path_base / f"{RUST_DIRECTORY_MODULE_ENTRY_STEM}{suffix}",
            )
            if candidate in candidates
        }
        return matches.pop() if len(matches) == 1 else None

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
        """Opt into rust-analyzer's ``experimental/serverStatus`` notifications,
        which it only emits when the client advertises this capability.
        """
        return {"experimental": {"serverStatusNotification": True}}

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """Wait for rust-analyzer to flush diagnostics.

        Strategy: reset the ready signal and wait briefly for
        ``quiescent=True`` (the precise fence). If no signal arrives
        within 10s (e.g. rust-analyzer stayed quiescent for a tiny
        project), fall back to debouncing on publishDiagnostics so we
        don't block for the full 120s on a no-op.
        """
        client.reset_ready_signal()
        if not client.wait_for_server_ready(timeout=10):
            # No quiescent transition observed; debounce instead.
            client.wait_for_diagnostics_quiesce(idle_seconds=3.0, max_wait=60.0)

    def validate_workspace_ready(self, client: LSPClient) -> None:
        """Reject rust-analyzer workspaces that loaded with fatal health."""
        if client.server_health == "error":
            detail = client.server_health_message or "workspace health=error"
            raise RuntimeError(
                "rust-analyzer reported an unusable workspace. Cargo metadata or toolchain setup failed, "
                f"so references and call-graph edges would be incomplete: {detail}"
            )

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
        cargo_path = shutil.which("cargo")
        if cargo_path is None:
            raise RuntimeError(
                "cargo not found on PATH. rust-analyzer requires a Rust "
                "toolchain to index Cargo projects. Install one via "
                "https://rustup.rs/ and re-run the analysis."
            )
        self._check_cargo_usable(project_root, cargo_path)
        return super().get_lsp_command(project_root)

    def _check_cargo_usable(self, project_root: Path, cargo_path: str) -> None:
        """Reject broken Cargo installs before rust-analyzer returns empty edges."""
        try:
            subprocess.run(
                [cargo_path, "--version"], cwd=project_root, check=True, capture_output=True, text=True, timeout=30
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(
                "cargo is installed but failed to run. rust-analyzer requires a working Cargo toolchain "
                "to build references and call-graph edges. Fix cargo and re-run the analysis."
            ) from exc

        manifest = project_root / "Cargo.toml"
        if not manifest.exists():
            return
        try:
            subprocess.run(
                [cargo_path, "metadata", "--format-version", "1", "--no-deps", "--manifest-path", str(manifest)],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(
                "cargo metadata failed for this Rust project. rust-analyzer cannot reliably resolve "
                "references or call-graph edges until the Cargo workspace loads successfully."
            ) from exc

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Tune rust-analyzer for batch analysis: enable build scripts and
        proc macros (so generated code is indexed) and the full target graph.

        ``checkOnSave`` runs ``cargo check`` after didOpen and is what
        surfaces ``unused_imports`` / ``unused_variables`` / ``dead_code``
        diagnostics — without it the unused-code health check sees nothing
        for Rust. The cost is one ``cargo check`` per analysis run; on
        large workspaces this can dominate the analyzer wall time, but
        skipping it would silently break diagnostic collection.
        """
        return {
            "cargo": {
                "buildScripts": {"enable": True},
                "allTargets": True,
            },
            "procMacro": {"enable": True},
            "checkOnSave": True,
            "check": {"command": "check"},
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
