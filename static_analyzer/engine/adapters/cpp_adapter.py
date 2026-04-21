"""C++ language adapter using clangd."""

from __future__ import annotations

import logging
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient

logger = logging.getLogger(__name__)


def _strip_template_args(name: str) -> str:
    """Strip balanced ``<...>`` template argument blocks from a C++ symbol name.

    ``std::vector<int>`` becomes ``std::vector``; ``Foo<T, U<V>>::bar`` becomes
    ``Foo::bar``. Unbalanced inputs pass through unchanged so a malformed
    parent chain can't silently drop characters. The same routine handles
    the leading ``operator<<`` and ``operator<=>`` tokens by only stripping
    ``<`` that follows an identifier character.
    """
    out: list[str] = []
    depth = 0
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "<" and out and (out[-1].isalnum() or out[-1] == "_"):
            depth += 1
            i += 1
            continue
        if ch == ">" and depth > 0:
            depth -= 1
            i += 1
            continue
        if depth == 0:
            out.append(ch)
        i += 1
    if depth != 0:
        return name
    return "".join(out)


def _normalize_cpp_parent(name: str) -> str:
    """Reduce a clangd documentSymbol parent to a clean C++ scope name.

    clangd sometimes emits parents with inline qualifiers (``foo::Bar``) or
    template arguments (``Foo<T>``). Flatten ``::`` to ``.`` (our universal
    delimiter) and drop template arguments so downstream ``.split(".")``
    consumers get clean identifiers.
    """
    stripped = _strip_template_args(name).strip()
    return stripped.replace("::", ".")


class CppAdapter(LanguageAdapter):
    """Static-analysis adapter for C/C++ projects backed by clangd."""

    @property
    def language(self) -> str:
        return "Cpp"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        # Plain-C headers (``.h``) are included because C++ projects commonly
        # use them for declarations; clangd handles both C and C++ in one
        # process and we don't know per-file whether a .h is C or C++.
        return (
            ".cpp",
            ".cc",
            ".cxx",
            ".c++",
            ".ipp",
            ".tpp",
            ".hpp",
            ".hh",
            ".hxx",
            ".h++",
            ".h",
        )

    @property
    def lsp_command(self) -> list[str]:
        return ["clangd"]

    @property
    def language_id(self) -> str:
        return "cpp"

    @property
    def references_per_query_timeout(self) -> int:
        """Non-zero gates the Phase-1.5 warmup probe so clangd builds its
        background index (and cross-TU references become non-empty) before
        Phase 2 fans out queries.
        """
        return 60

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Fail fast if clangd has no compilation database.

        clangd needs a ``compile_commands.json`` (or a ``compile_flags.txt``)
        to resolve include paths and language-dialect flags; without one it
        silently returns empty cross-file references. CMake projects can
        generate the DB by passing ``-DCMAKE_EXPORT_COMPILE_COMMANDS=ON``.
        """
        if not self._has_compilation_database(project_root):
            raise RuntimeError(
                f"No compile_commands.json or compile_flags.txt found under "
                f"{project_root}. clangd requires a compilation database to "
                f"resolve C++ include paths. For CMake projects, regenerate "
                f"with -DCMAKE_EXPORT_COMPILE_COMMANDS=ON. For Bazel, use "
                f"hedronvision/bazel-compile-commands-extractor. For simple "
                f"projects, a compile_flags.txt at the project root also works."
            )
        return super().get_lsp_command(project_root)

    @staticmethod
    def _has_compilation_database(project_root: Path) -> bool:
        """True when a compile_commands.json or compile_flags.txt lives at
        the project root or a conventional build directory under it.

        Checks the root plus ``build/``, ``build/Debug``, ``build/Release``,
        ``cmake-build-debug``, ``cmake-build-release`` — the layouts CMake,
        meson, and clion emit by default.
        """
        roots = [
            project_root,
            project_root / "build",
            project_root / "build" / "Debug",
            project_root / "build" / "Release",
            project_root / "cmake-build-debug",
            project_root / "cmake-build-release",
        ]
        for root in roots:
            if (root / "compile_commands.json").is_file():
                return True
            if (root / "compile_flags.txt").is_file():
                return True
        return False

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Default to C++20 for files not covered by the compile database.

        Files without a matching entry in ``compile_commands.json`` (or
        projects that only have a ``compile_flags.txt``) fall back to
        these flags. ``-std=c++20`` avoids clangd tripping on modern
        syntax (concepts, modules) in fixture-style projects that don't
        ship an explicit standard flag.
        """
        return {
            "clangd": {
                "fallbackFlags": ["-std=c++20"],
            },
        }

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """Debounce on the publishDiagnostics stream.

        clangd processes files asynchronously and we'd snapshot an empty
        diagnostics map if harvested immediately after phase 2. It does
        not emit a distinct quiescence signal, so debounce on activity.
        """
        client.wait_for_diagnostics_quiesce(idle_seconds=2.0, max_wait=60.0)

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build C++ qualified names from the namespace/class chain, not the file path.

        C++ commonly splits declarations across ``.hpp`` / ``.cpp`` files;
        using the file path as module prefix would give the same method two
        different qualified names in its header vs source, breaking cross-
        file references. Instead we use the clangd ``documentSymbol`` parent
        chain, which reports the actual C++ scope (``namespace foo::bar``,
        ``class Baz``) identically from both files. Template arguments are
        stripped from class parents so specializations collapse to the
        template name (matching how LSP references resolve).

        Free top-level symbols (no namespace/class parent) return the bare
        symbol name. This also applies to the dual-registration alias form
        (empty chain even for namespaced members) — keeping the alias
        strictly shorter than the canonical scoped name so the CallGraph's
        longest-wins location dedup picks the scoped entry.
        """
        sym = _strip_template_args(symbol_name).replace("::", ".").strip()

        if parent_chain:
            # Drop ``kind=STRING`` parents. clangd emits namespace-wrapper
            # macros (``FMT_BEGIN_NAMESPACE``, ``BOOST_NAMESPACE_BEGIN``, …)
            # as SymbolKind=15 entries with the nested namespaces as
            # children — keeping the macro identifier in qualified names
            # would prefix every symbol with ``FMT_BEGIN_NAMESPACE.fmt.v11.…``
            # and shift the real namespace out of the first position.
            parents = [_normalize_cpp_parent(name) for name, kind in parent_chain if kind != int(NodeType.STRING)]
            parents = [p for p in parents if p]
            if parents:
                return ".".join(parents) + "." + sym

        # No parent chain. Two sources reach this branch:
        #   1. ``register_symbols`` dual-registration for an alias form — the
        #      adapter receives the original ``name`` (which may already
        #      carry ``Processor::process``-style scope) with an empty chain.
        #      Returning just the bare name keeps the alias STRICTLY shorter
        #      than the properly-scoped canonical name, so the graph's
        #      location-based "longest wins" dedup picks the canonical one.
        #   2. A genuinely free top-level symbol (no namespace, no class).
        #      The bare ``sym`` is still the right answer — file-path
        #      prefixing would cause the same free function to collide with
        #      itself between header and source files.
        return sym

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces so package-dependency tracking sees them.

        C++ organises code primarily by namespace, so the namespace symbol
        itself should appear in the reference map (mirrors C#/PHP).
        """
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE
