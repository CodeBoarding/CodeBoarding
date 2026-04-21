"""C++ language adapter using clangd."""

from __future__ import annotations

import logging
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient

logger = logging.getLogger(__name__)

_OPERATOR_SYMBOL_CHARS = "<>=!+-*/%&|^~?,[]"


def _strip_template_args(name: str) -> str:
    """Strip balanced ``<...>`` template-arg blocks, preserving ``operator`` tokens.

    Why: ``operator<=>`` would otherwise collapse to ``operator`` via the
    balanced-bracket rule, merging distinct C++20 operator symbols.
    Unbalanced inputs return unchanged.
    """
    out: list[str] = []
    depth = 0
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if (
            depth == 0
            and ch == "o"
            and name.startswith("operator", i)
            and (i == 0 or not (name[i - 1].isalnum() or name[i - 1] == "_"))
        ):
            j = i + len("operator")
            while j < n and name[j] == " ":
                j += 1
            if j < n and (name[j].isalpha() or name[j] == "_"):
                # Keyword operator: ``operator new``, ``operator delete``.
                while j < n and (name[j].isalnum() or name[j] == "_"):
                    j += 1
            else:
                # Symbolic operator: consume adjacent punctuation.
                while j < n and name[j] in _OPERATOR_SYMBOL_CHARS:
                    j += 1
            out.extend(name[i:j])
            i = j
            continue
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
    """Flatten ``foo::Bar<T>`` to ``foo.Bar`` for qualified-name consumption."""
    return _strip_template_args(name).strip().replace("::", ".")


class CppAdapter(LanguageAdapter):
    """Static-analysis adapter for C/C++ projects backed by clangd."""

    @property
    def language(self) -> str:
        return "Cpp"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        # ``.h`` is included: clangd treats both C and C++ in one process
        # and we can't know per-file which dialect a header is.
        return (".cpp", ".cc", ".cxx", ".c++", ".ipp", ".tpp", ".hpp", ".hh", ".hxx", ".h++", ".h")

    @property
    def lsp_command(self) -> list[str]:
        return ["clangd"]

    @property
    def language_id(self) -> str:
        return "cpp"

    @property
    def references_per_query_timeout(self) -> int:
        """Gates the Phase-1.5 warmup probe so clangd finishes indexing before Phase 2."""
        return 60

    def phase1_request_timeout(self, probe_timeout: int) -> int | None:
        """Use the per-project probe_timeout for Phase 1 ``document_symbol`` calls.

        Why: on Windows, clangd continues background-indexing during Phase 1
        and a single request can block for minutes — the 60s default
        false-fails POCO-sized projects.
        """
        return probe_timeout

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Fail fast without a compilation database — clangd silently returns empty refs otherwise."""
        if not self._has_compilation_database(project_root):
            raise RuntimeError(
                f"No compile_commands.json or compile_flags.txt under {project_root}. "
                f"For CMake: regenerate with -DCMAKE_EXPORT_COMPILE_COMMANDS=ON. "
                f"For Bazel: hedronvision/bazel-compile-commands-extractor. "
                f"Simple projects: a compile_flags.txt at the root works."
            )
        return super().get_lsp_command(project_root)

    @staticmethod
    def _has_compilation_database(project_root: Path) -> bool:
        roots = [
            project_root,
            project_root / "build",
            project_root / "build" / "Debug",
            project_root / "build" / "Release",
            project_root / "cmake-build-debug",
            project_root / "cmake-build-release",
        ]
        return any((r / "compile_commands.json").is_file() or (r / "compile_flags.txt").is_file() for r in roots)

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """``fallbackFlags`` is top-level — nesting under ``"clangd"`` is an editor convention clangd ignores."""
        return {"fallbackFlags": ["-std=c++20"]}

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """Debounce — clangd publishes diagnostics asynchronously with no quiescence signal."""
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
        """Build names from namespace/class chain, not file path.

        Why: ``.hpp``/``.cpp`` split would otherwise give the same method
        two distinct qualified names, breaking cross-file references.
        No-parent case returns bare name — keeps dual-registration aliases
        shorter than the scoped canonical so CallGraph's longest-wins dedup
        picks the scoped entry.
        """
        sym = _strip_template_args(symbol_name).replace("::", ".").strip()

        if parent_chain:
            # Drop ``kind=STRING`` parents: clangd surfaces namespace-wrapper
            # macros (``FMT_BEGIN_NAMESPACE``, ``BOOST_NAMESPACE_BEGIN``) as
            # SymbolKind=15 and they'd prefix every qualified name.
            parents = [_normalize_cpp_parent(name) for name, kind in parent_chain if kind != int(NodeType.STRING)]
            parents = [p for p in parents if p]
            if parents:
                return ".".join(parents) + "." + sym
        return sym

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces so package-dependency tracking sees them (mirrors C#/PHP)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE
